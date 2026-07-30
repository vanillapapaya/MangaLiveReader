"""docs/_page.png 을 **진짜 파이프라인**에 태워 docs/demo-result.json 을 만든다.

    MTL_ENV_FILE=<키파일> ~/.venvs/mlr/bin/python docs/run_demo.py

검출(comic-text-detector) → OCR(manga-ocr) → 읽기 순서 → 번역까지 서비스와 같은
코드를 그대로 부른다. HTTP 는 타지 않는다 (`scripts/debug_page.py` 와 같은 방식).

결과 JSON 을 `docs/demo.html` 이 읽어 박스를 놓는다 — 그래서 스크린샷의 박스 위치·
원문·번역이 전부 실물이다. 그림(칸·말풍선)만 손으로 그린 것이다.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from mtl_service import detect, ocr, order, translate  # noqa: E402
from mtl_service.config import load  # noqa: E402
from mtl_shared.models import Region  # noqa: E402


def main() -> int:
    cfg = load()
    img_path = ROOT / (sys.argv[1] if len(sys.argv) > 1 else "docs/_page.png")
    img = cv2.imread(str(img_path))
    if img is None:
        raise SystemExit(f"이미지를 못 읽었다: {img_path}")
    h, w = img.shape[:2]
    print(f"입력 {w}×{h}")

    t0 = time.perf_counter()
    det = detect.detect(img, cfg)
    t1 = time.perf_counter()
    print(f"검출 {len(det.regions)}개 · {det.detect_ms}ms (마스크 {det.mask_ms}ms)")

    got = ocr.run(img, det.regions, cfg)
    t2 = time.perf_counter()
    regions = order.sort_regions(got.regions, (w, h), cfg.order.min_gap_ratio)
    for i, r in enumerate(regions):
        r.id = i
    print(f"OCR {got.ocr_ms}ms · 남은 region {len(regions)}개")
    for r in regions:
        print(f"  #{r.id} {'말풍선' if r.is_bubble else '밖   '} {r.bbox} {r.text}")

    translator = translate.get_translator(cfg.api.model_fast, effort=cfg.api.effort)
    res = translator.translate(regions, "natural")
    t3 = time.perf_counter()
    print(f"번역 {int((t3 - t2) * 1000)}ms · {res.model}")

    by_id = {t.id: t for t in res.regions}
    out = {
        "source": "docs/_page.png",
        "model": res.model,
        "timings_ms": {
            "detect": det.detect_ms,
            "ocr": got.ocr_ms,
            "translate": int((t3 - t2) * 1000),
        },
        "regions": [
            {
                "id": r.id,
                "bbox": list(r.bbox),
                "is_bubble": r.is_bubble,
                "ja": r.text,
                "ko": (by_id.get(r.id).ko if r.id in by_id else ""),
                "kind": (by_id.get(r.id).kind if r.id in by_id else "line"),
            }
            for r in regions
        ],
    }
    stem = "demo" if img_path.name == "_page.png" else img_path.stem.lstrip("_")
    dest = ROOT / "docs" / f"{stem}-result.json"
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    # file:// 에서는 fetch 가 막힌다. demo.html 이 <script src> 로 읽게 JS 로도 낸다.
    js = ROOT / "docs" / f"{stem}-result.js"
    js.write_text(
        "// docs/run_demo.py 가 만든다. 손으로 고치지 말 것.\n"
        f"window.MLR_RESULT_{stem.upper()} = " + json.dumps(out, ensure_ascii=False, indent=1) + ";\n",
        encoding="utf-8",
    )
    print(f"→ {dest}\n→ {js}")
    for r in out["regions"]:
        print(f"  #{r['id']} {r['ja']} → {r['ko']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
