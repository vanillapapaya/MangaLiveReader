"""픽스처 회귀. GPU·모델 없이 돈다.

`scripts/check_order.py` / `check_merge.py` 와 같은 것을 잰다. 스크립트는 사람이 읽는
보고서(어디가 어떻게 틀렸는지)를 내고, 여기는 CI 가 잡을 수 있게 단언만 한다.
자세한 배경은 두 스크립트의 모듈 주석에 있다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mtl_service import detect, order
from mtl_service.config import load
from mtl_shared.models import Region

FIXTURES = Path(__file__).resolve().parent.parent / "test/fixtures"


def _pages(name: str) -> list[dict]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))["pages"]


def _regions(page: dict) -> list[Region]:
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


def _sorted_ids(page: dict) -> list[int]:
    cfg = load()
    return [
        r.id
        for r in order.sort_regions(
            _regions(page), (page["width"], page["height"]), cfg.order.min_gap_ratio
        )
    ]


# ---------------------------------------------------------------------------
# 읽기 순서
# ---------------------------------------------------------------------------

_ORDER_PAGES = _pages("order_cases.json")
_VERIFIED = [p for p in _ORDER_PAGES if p.get("verified")]
_SNAPSHOTS = [p for p in _ORDER_PAGES if not p.get("verified")]


@pytest.mark.parametrize("page", _VERIFIED, ids=lambda p: p["page"])
def test_읽기_순서_검증페이지(page: dict) -> None:
    """칸 구조를 눈으로 보고 정답을 매긴 페이지. 여기가 틀리면 진짜 회귀다.

    `comic-fuz_030111` 은 알려진 실패라 xfail 이다 — XY-cut 이 칸 테두리를 모르고
    글자 배치의 빈 띠만 봐서 칸 안을 가로로 자른다 (PROGRESS.md §6).
    """
    if page["page"] == "comic-fuz_030111.png":
        pytest.xfail("칸 경계 미인식. PROGRESS.md §6")
    assert _sorted_ids(page) == page["expected_order"]


@pytest.mark.parametrize("page", _SNAPSHOTS, ids=lambda p: p["page"])
def test_읽기_순서_스냅샷(page: dict) -> None:
    """정답 오라클이 아니라 회귀 기준선. 달라졌으면 눈으로 판정하고 픽스처를 갱신할 것."""
    assert _sorted_ids(page) == page["expected_order"]


def test_검증페이지가_충분히_있다() -> None:
    """스냅샷만 남으면 이 파일은 아무것도 보증하지 못한다."""
    assert len(_VERIFIED) >= 4


# ---------------------------------------------------------------------------
# 박스 병합
# ---------------------------------------------------------------------------

_MERGE_PAGES = _pages("merge_cases.json")


@pytest.mark.parametrize("page", _MERGE_PAGES, ids=lambda p: p["page"])
def test_박스_병합(page: dict) -> None:
    cfg = load()
    raw = [tuple(b) for b in page["raw_boxes"]]
    max_gap = cfg.detect.merge_gap_max_ratio * min(page["width"], page["height"])
    got = set(detect.merge_boxes(raw, cfg.detect.merge_gap_ratio, max_gap))
    assert got == {tuple(b) for b in page["expected_merged"]}


@pytest.mark.parametrize("page", _MERGE_PAGES, ids=lambda p: p["page"])
def test_병합이_펼침면_경계를_넘지_않는다(page: dict) -> None:
    """경계를 넘는 박스가 하나라도 있으면 `sort_regions` 가 페이지를 가르지 못한다.

    실제로 `yanmaga_002535` 가 그것 하나 때문에 읽기 순서가 틀렸었다 (PROGRESS.md §7).
    """
    if page["width"] <= page["height"] * order.SPREAD_ASPECT:
        pytest.skip("펼침면이 아니다")

    raw = [tuple(b) for b in page["raw_boxes"]]
    spans = sorted((b[0], b[2]) for b in raw)
    gutter, reach = None, spans[0][1]
    for start, end in spans[1:]:
        mid = (reach + start) / 2
        if start > reach and page["width"] / 3 <= mid <= page["width"] * 2 / 3:
            if gutter is None or start - reach > gutter[1]:
                gutter = (mid, start - reach)
        reach = max(reach, end)
    if gutter is None:
        pytest.skip("가운데 홈을 못 찾았다")

    cfg = load()
    max_gap = cfg.detect.merge_gap_max_ratio * min(page["width"], page["height"])
    cut = gutter[0]
    crossing = [b for b in detect.merge_boxes(raw, cfg.detect.merge_gap_ratio, max_gap)
                if b[0] < cut < b[2]]
    assert crossing == []
