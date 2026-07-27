"""`mask.encode_rle` / `decode_rle` 왕복. GPU·모델 없이 돈다.

RLE 는 `Region.mask_rle` 로 클라이언트에 나가고 오버레이 L3(칠하기)가 그대로 쓴다.
여기서 한 픽셀이 어긋나면 화면에 글자 가장자리가 남는다 (DESIGN.md §7.4).
"""

from __future__ import annotations

import numpy as np
import pytest

from mtl_service.mask import decode_rle, encode_rle


def _roundtrip(mask: np.ndarray) -> np.ndarray:
    h, w = mask.shape
    return decode_rle(encode_rle(mask), w, h)


@pytest.mark.parametrize(
    "mask",
    [
        pytest.param(np.zeros((4, 5), bool), id="전부-배경"),
        pytest.param(np.ones((4, 5), bool), id="전부-전경"),
        pytest.param(np.array([[0, 1, 1], [1, 0, 0]], bool), id="문서-예시"),
        pytest.param(np.eye(6, dtype=bool), id="대각선"),
        pytest.param(np.array([[1]], bool), id="전경-한칸"),
        pytest.param(np.array([[0]], bool), id="배경-한칸"),
    ],
)
def test_왕복이_원본과_같다(mask: np.ndarray) -> None:
    assert np.array_equal(_roundtrip(mask), mask)


def test_난수_왕복() -> None:
    rng = np.random.default_rng(20260727)
    for _ in range(50):
        h, w = rng.integers(1, 40, size=2)
        mask = rng.random((h, w)) < rng.uniform(0.05, 0.95)
        assert np.array_equal(_roundtrip(mask), mask)


def test_런_길이_합이_픽셀_수와_같다() -> None:
    """클라이언트가 런을 순서대로 채우므로 합이 어긋나면 그림이 밀린다."""
    rng = np.random.default_rng(7)
    mask = rng.random((13, 17)) < 0.4
    assert sum(int(x) for x in encode_rle(mask).split()) == mask.size


def test_전경으로_시작하면_길이0_배경런이_앞에_온다() -> None:
    """형식이 '항상 배경 런으로 시작'이라 클라이언트가 색을 번갈아 칠할 수 있다."""
    assert encode_rle(np.array([[1, 1, 0]], bool)).split()[0] == "0"


def test_빈_마스크는_빈_문자열() -> None:
    assert encode_rle(np.zeros((0, 0), bool)) == ""
    assert decode_rle("", 3, 2).shape == (2, 3)
    assert not decode_rle("", 3, 2).any()
