"""박스 병합 회귀 확인. GPU 없이 돈다.

    python scripts/check_merge.py

`test/fixtures/merge_cases.json` 은 실물 52장의 **병합 전** yolo 박스와 정답 병합
결과다. 검출기를 다시 돌리지 않으므로 GPU 가 필요 없다.
`detect._should_merge` / `merge_boxes` 를 건드리면 이걸 먼저 돌릴 것.

`expected_merged` 는 눈으로 검수한 스냅샷이다. 정답 오라클이 아니라 회귀 기준선으로
쓴다 — 병합 규칙을 건드렸을 때 무엇이 달라지는지 드러나면 그때 개별로 판정한다.
52장 기준 병합은 19건이고, 그중 13건은 서로 겹치는 중복 검출(병합이 명백히 옳음),
6건은 간격 2-29px 의 줄·열 병합이다.

펼침면 경계를 넘는 병합은 따로 본다. 그건 읽기 순서를 직접 망가뜨린다 —
`order.sort_regions` 가 페이지를 먼저 가르는데 박스가 경계에 걸쳐 있으면 가를 수 없다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mtl_service import detect  # noqa: E402
from mtl_service.config import load  # noqa: E402
from mtl_service.order import SPREAD_ASPECT  # noqa: E402

FIXTURE = Path(__file__).resolve().parent.parent / "test/fixtures/merge_cases.json"


def spread_gutter(boxes: list[tuple[int, ...]], width: int) -> float | None:
    """펼침면 가운데 1/3 에서 가장 넓은 세로 빈 띠의 중앙. 없으면 None."""
    spans = sorted((b[0], b[2]) for b in boxes)
    best, reach = None, spans[0][1]
    for start, end in spans[1:]:
        if start > reach:
            mid = (reach + start) / 2
            if width / 3 <= mid <= width * 2 / 3:
                if best is None or start - reach > best[1]:
                    best = (mid, start - reach)
        reach = max(reach, end)
    return best[0] if best else None


def main() -> int:
    cfg = load()
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))

    failed: list[str] = []
    crossings = 0

    for page in data["pages"]:
        raw = [tuple(b) for b in page["raw_boxes"]]
        expected = {tuple(b) for b in page["expected_merged"]}
        max_gap = cfg.detect.merge_gap_max_ratio * min(page["width"], page["height"])
        got = set(detect.merge_boxes(raw, cfg.detect.merge_gap_ratio, max_gap))

        ok = got == expected
        print(f"{'OK  ' if ok else '틀림'} {page['page']:30s} {len(raw)} → {len(got)}개")
        if not ok:
            failed.append(page["page"])
            for box in sorted(got - expected):
                srcs = [
                    b
                    for b in raw
                    if b[0] >= box[0] and b[1] >= box[1] and b[2] <= box[2] and b[3] <= box[3]
                ]
                print(f"       붙지 말아야 할 병합 {box} ← {srcs}")
            if "note" in page:
                print(f"       ※ {page['note']}")

        # 펼침면 경계를 넘는 박스
        if page["width"] > page["height"] * SPREAD_ASPECT:
            cut = spread_gutter(raw, page["width"])
            if cut is not None:
                over = [b for b in got if b[0] < cut < b[2]]
                if over:
                    crossings += len(over)
                    print(f"       ! 펼침면 경계(x={cut:.0f})를 넘는 박스 {over}")

    print(f"\n펼침면 경계를 넘는 박스: {crossings}개")
    if failed:
        print(f"틀린 페이지: {', '.join(failed)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
