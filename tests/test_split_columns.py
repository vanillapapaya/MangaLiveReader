"""세로쓰기 블록의 열 분할. GPU·모델 없이 돈다.

manga-ocr 은 말풍선 하나 분량의 짧은 블록으로 학습됐다. 여러 열이 빽빽한 큰
말풍선을 통째로 넣으면 뒤로 갈수록 자기회귀가 드리프트해 반복·붕괴한다
(`DEVLOG.md` 의 실측). 그래서 열로 쪼개 넣는다.

여기서 고정하는 것은 **쪼개는 규칙**이다 — 몇 개로 쪼개는지, 후리가나를 버리는지,
읽는 방향이 오른쪽부터인지. OCR 품질 자체는 실물로 재는 것이고 코드로 못 고정한다.
"""

from __future__ import annotations

import numpy as np
import pytest

from mtl_service.ocr import split_columns


def block(columns: list[tuple[int, int]], height: int = 200, width: int = 240) -> np.ndarray:
    """흰 바탕에 검은 세로 띠를 그린다. `columns` 는 (시작 x, 폭)."""
    img = np.full((height, width, 3), 255, np.uint8)
    for x, w in columns:
        img[10 : height - 10, x : x + w] = 0
    return img


def test_열이_적으면_쪼개지_않는다() -> None:
    """짧은 말풍선은 통짜가 더 정확하고 빠르다. 쪼갤 이유가 없다."""
    assert split_columns(block([(20, 30), (80, 30)])) is None


def test_세_열부터_쪼갠다() -> None:
    cols = split_columns(block([(20, 30), (80, 30), (140, 30)]))
    assert cols is not None
    assert len(cols) == 3


def test_오른쪽_열부터_읽는다() -> None:
    """세로쓰기는 우→좌다. 순서가 뒤집히면 문장이 통째로 거꾸로 이어붙는다."""
    cols = split_columns(block([(20, 30), (80, 30), (140, 30)]))
    starts = [a for a, _ in cols]
    assert starts == sorted(starts, reverse=True)


def test_후리가나_열은_버린다() -> None:
    """루비를 남기면 「かんだしろやま」 같은 읽기가 본문 사이에 끼어들어
    번역기가 문장으로 착각한다. 실측에서 본문 30-33px, 루비 16-17px 였다."""
    # 본문 30px 셋 + 루비 14px 둘
    img = block([(10, 30), (48, 14), (70, 30), (108, 14), (130, 30)])
    cols = split_columns(img)
    assert cols is not None
    assert len(cols) == 3
    assert all(b - a >= 25 for a, b in cols)


def test_빈_이미지는_None() -> None:
    assert split_columns(np.full((100, 100, 3), 255, np.uint8)) is None


def test_획_파편은_열로_치지_않는다() -> None:
    """2-3px 짜리 조각까지 열로 세면 한 글자가 여러 조각으로 갈린다."""
    img = block([(10, 30), (50, 2), (70, 30), (130, 30)])
    cols = split_columns(img)
    assert cols is not None
    assert len(cols) == 3


@pytest.mark.parametrize("gap", [4, 8, 20])
def test_열_간격이_달라도_같은_개수를_찾는다(gap: int) -> None:
    img = block([(10, 30), (40 + gap, 30), (70 + 2 * gap, 30)], width=200)
    cols = split_columns(img)
    assert cols is not None and len(cols) == 3
