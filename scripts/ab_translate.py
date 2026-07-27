"""번역 모델 A/B. GPU 없이 돈다 (검출은 이미 끝난 픽스처를 쓴다).

    python scripts/ab_translate.py --models claude-opus-5 gemini-3.6-flash
    python scripts/ab_translate.py --pages 3 --style literal --out test/ab

`test/fixtures/order_cases.json` 의 OCR 원문을 그대로 태운다. 검출을 다시 돌리지
않으므로 GPU 도 모델 가중치도 필요 없고, 모델 사이 입력이 **완전히 동일**하다.

**번역 품질은 사람이 봐야 한다.** `--out` 에 모델별 결과를 나란히 놓은 마크다운을
떨군다. 원문 / 각 모델 번역이 한 표에 있어 바로 비교할 수 있다. 자동으로 잴 수 있는
것은 지연과 토큰뿐이다.

읽기 순서 교정은 재지 않는다 — 실측에서 LLM 이 XY-cut 보다 나빴고 그래서 뺐다
(translate.py 모듈 주석).

비용은 페이지당 1센트 미만이다. 52장 전부 태워도 모델당 $0.5 수준.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mtl_service.translate import TranslateResult, get_translator  # noqa: E402
from mtl_shared.models import Region  # noqa: E402

FIXTURE = Path(__file__).resolve().parent.parent / "test/fixtures/order_cases.json"

#: 기본 비교 대상. 품질 티어 위주로 골랐다 — 가격 차이가 1권에 $1.5 라
#: (PROGRESS.md §C) 저가 모델을 넣어 아끼는 것보다 품질 차이를 보는 게 중요하다.
DEFAULT_MODELS = ["claude-sonnet-5", "gemini-3.6-flash"]

#: ($/1M 입력, $/1M 출력). 캐시 할인은 빼고 정가로 잡는다 — 상한 추정이다.
#: 2026-07 기준. Sonnet 5 는 2026-08-31 까지 도입가 $2/$10.
PRICES = {
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "gemini-3.6-flash": (1.5, 7.5),
    "gemini-3.1-pro": (2.0, 12.0),
}


def load_pages(limit: int | None) -> list[dict]:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    pages = data["pages"]
    # 정답 읽기 순서가 있는 페이지를 앞에 둔다 — 순서 교정을 잴 수 있는 건 이쪽뿐이다.
    pages.sort(key=lambda p: not p.get("verified"))
    return pages[:limit] if limit else pages


def regions_of(page: dict) -> list[Region]:
    return [
        Region(
            id=r["detected_order"],
            text=r["text"],
            bbox=tuple(r["bbox"]),
            is_bubble=r["is_bubble"],
            vertical=r["vertical"],
            mask_rle="",
            fill_rgb=None,
            fill_confidence=0.0,
        )
        for r in page["regions"]
    ]



def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    ap.add_argument("--style", default="natural", choices=["natural", "literal"])
    ap.add_argument("--pages", type=int, default=4, help="태울 페이지 수 (기본 4)")
    ap.add_argument("--effort", default="medium", help="Anthropic 모델의 effort")
    ap.add_argument("--out", type=Path, default=Path("test/ab"))
    args = ap.parse_args()

    pages = load_pages(args.pages)
    print(f"{len(pages)}장 × {len(args.models)}모델 = {len(pages) * len(args.models)}회 호출\n")

    translators = {}
    for model in args.models:
        try:
            kwargs = {"effort": args.effort} if model.startswith("claude-") else {}
            translators[model] = get_translator(model, **kwargs)
        except Exception as exc:  # 키 없음 등 — 나머지 모델은 계속 돌린다
            print(f"! {model} 건너뜀: {exc}", file=sys.stderr)

    if not translators:
        print("돌릴 모델이 없다.", file=sys.stderr)
        return 1

    results: dict[str, dict[str, TranslateResult]] = {m: {} for m in translators}

    for page in pages:
        regions = regions_of(page)
        print(f"--- {page['page']}  region {len(regions)}")

        for model, tr in translators.items():
            try:
                res = tr.translate(regions, args.style)
            except Exception as exc:
                print(f"    {model:22s} 실패: {exc}", file=sys.stderr)
                continue
            results[model][page["page"]] = res
            print(
                f"    {model:22s} {res.latency_ms:>6d}ms  "
                f"in {res.input_tokens:>5d} (캐시 {res.cached_tokens:>5d}) out {res.output_tokens:>5d}"
            )
            time.sleep(0.2)  # 레이트리밋 여유

    # --- 집계 -------------------------------------------------------------
    print(
        f"\n{'모델':22s} {'지연 중앙값':>10s} {'입력':>7s} {'출력':>7s} {'200쪽 1권':>10s}"
    )
    for model in translators:
        rs = list(results[model].values())
        if not rs:
            continue
        lat = statistics.median(r.latency_ms for r in rs)
        tin = statistics.mean(r.input_tokens for r in rs)
        tout = statistics.mean(r.output_tokens for r in rs)
        price = PRICES.get(model)
        cost = (
            f"${(tin * price[0] + tout * price[1]) / 1e6 * 200:.2f}" if price else "—"
        )
        print(f"{model:22s} {lat:>9.0f}ms {tin:>7.0f} {tout:>7.0f} {cost:>10s}")

    write_report(args.out, pages, results, args.style)
    return 0


def write_report(out_dir: Path, pages, results, style: str) -> None:
    """모델별 번역을 나란히 놓은 마크다운. 품질은 사람이 봐야 한다."""
    out_dir.mkdir(parents=True, exist_ok=True)
    models = [m for m in results if results[m]]
    lines = [f"# 번역 A/B — 문체 `{style}`", ""]

    for page in pages:
        name = page["page"]
        if not any(name in results[m] for m in models):
            continue
        lines += [f"## {name}", "", "| id | 원문 | " + " | ".join(models) + " |",
                  "|---|---|" + "---|" * len(models)]
        by_model = {m: results[m][name].by_id() for m in models if name in results[m]}
        for r in page["regions"]:
            rid = r["detected_order"]
            cells = [by_model.get(m, {}).get(rid).ko if by_model.get(m, {}).get(rid) else ""
                     for m in models]
            lines.append(f"| {rid} | {r['text']} | " + " | ".join(cells) + " |")
        lines.append("")
        for m in models:
            res = results[m].get(name)
            if res:
                lines.append(
                    f"- `{m}` — {res.latency_ms}ms, 입력 {res.input_tokens}"
                    f"(캐시 {res.cached_tokens}), 출력 {res.output_tokens}"
                )
        lines.append("")

    path = out_dir / f"report_{style}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n→ {path}")


if __name__ == "__main__":
    raise SystemExit(main())
