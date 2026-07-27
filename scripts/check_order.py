"""읽기 순서 회귀 확인. GPU 없이 돈다.

    python scripts/check_order.py [--verbose]

`test/fixtures/order_cases.json` 은 2026-07-27 실물 52장 중 칸 구조가 복잡한 13장의
검출 결과다. 검출을 다시 돌리지 않으므로 GPU 도, 모델 가중치도 필요 없다.
`order.py` 를 건드리면 이걸 먼저 돌릴 것.

페이지는 두 종류다. **검증(verified)** 은 칸 구조를 눈으로 보고 정답을 매긴 것이라
정확도가 의미를 갖는다. **스냅샷** 은 현재 출력을 그대로 둔 회귀 기준선이라 정답
오라클이 아니다 — 여기서 나는 차이는 "틀렸다"가 아니라 "달라졌으니 보라"는 뜻이다.

두 가지를 잰다.

- **위치 정확도** — 몇 번째 자리가 정답과 같은가. 하나만 밀려도 뒤가 전부 틀리는
  가혹한 지표다
- **쌍 정확도** — 두 region 의 앞뒤 관계가 맞은 비율. 국소적으로 얼마나 맞는지

"""

from __future__ import annotations

import argparse
import json
import sys
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mtl_service import order  # noqa: E402
from mtl_service.config import load  # noqa: E402
from mtl_shared.models import Region  # noqa: E402

FIXTURE = Path(__file__).resolve().parent.parent / "test/fixtures/order_cases.json"


def pairwise_accuracy(got: list[int], expected: list[int]) -> tuple[int, int]:
    rank = {rid: i for i, rid in enumerate(expected)}
    ok = sum(1 for a, b in combinations(got, 2) if rank[a] < rank[b])
    return ok, len(expected) * (len(expected) - 1) // 2


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--verbose", action="store_true", help="틀린 자리를 낱낱이 찍는다")
    args = ap.parse_args()

    cfg = load()
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))

    pos_ok = pos_total = pair_ok = pair_total = 0
    snap_changed: list[str] = []
    failed: list[str] = []

    for page in data["pages"]:
        regions = [
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
        expected = page["expected_order"]
        got = [
            r.id
            for r in order.sort_regions(
                regions, (page["width"], page["height"]), cfg.order.min_gap_ratio
            )
        ]

        hits = sum(1 for a, b in zip(got, expected) if a == b)
        p_ok, p_total = pairwise_accuracy(got, expected)
        verified = page.get("verified", False)
        if verified:
            # 스냅샷 페이지는 정답 오라클이 아니라서 정확도 합계에 넣지 않는다.
            pos_ok += hits
            pos_total += len(expected)
            pair_ok += p_ok
            pair_total += p_total

        kind = "검증" if verified else "스냅"
        mark = "OK  " if hits == len(expected) else ("틀림" if verified else "달라짐")
        print(
            f"{mark} [{kind}] {page['page']:28s} 위치 {hits}/{len(expected)}  쌍 {p_ok}/{p_total}"
        )
        if hits != len(expected):
            (failed if verified else snap_changed).append(page["page"])
            if "note" in page:
                print(f"       ※ {page['note']}")
            if args.verbose:
                by_id = {r.id: r.text for r in regions}
                for i, (a, b) in enumerate(zip(got, expected), start=1):
                    if a != b:
                        print(f"       {i}번째: {by_id[a][:24]!r} ← 정답 {by_id[b][:24]!r}")

    print(
        f"\n검증 페이지 합계  위치 {pos_ok}/{pos_total} = {100 * pos_ok / pos_total:.0f}%"
        f"   쌍 {pair_ok}/{pair_total} = {100 * pair_ok / pair_total:.0f}%"
    )
    if failed:
        print(f"틀린 페이지: {', '.join(failed)}")
    if snap_changed:
        print(f"스냅샷이 달라진 페이지(판정 필요): {', '.join(snap_changed)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
