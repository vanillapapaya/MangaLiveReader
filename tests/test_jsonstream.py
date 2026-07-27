"""스트리밍 JSON 파서. 네트워크 없이 돈다.

번역 스트리밍 전체가 이 파서 위에 서 있다. 여기서 원소를 하나라도 흘리거나 중복해서
내면 말풍선이 비거나 두 번 그려진다. 청크 경계가 **어디서 잘리든** 같은 결과가
나와야 한다 — API 는 토큰 단위로 끊어 보내므로 경계를 고를 수 없다.
"""

from __future__ import annotations

import json

import pytest

from mtl_service.jsonstream import ArrayStreamer

PAYLOAD = {
    "regions": [
        {"id": 1, "ko": "이게 뭐야", "note": ""},
        {"id": 2, "ko": "괄호 {가} 섞인 대사", "note": "ocr_noise"},
        {"id": 3, "ko": '따옴표 "안" 과 역슬래시 \\ 포함', "note": ""},
    ]
}
TEXT = json.dumps(PAYLOAD, ensure_ascii=False)


def drain(chunks: list[str]) -> list[dict]:
    s = ArrayStreamer("regions")
    out: list[dict] = []
    for c in chunks:
        out.extend(s.feed(c))
    return out


def test_한_번에_넣기() -> None:
    assert drain([TEXT]) == PAYLOAD["regions"]


@pytest.mark.parametrize("size", [1, 2, 3, 5, 7, 13, 64])
def test_어떤_청크_크기여도_같다(size: int) -> None:
    """API 는 토큰 단위로 끊어 보낸다. 경계를 우리가 고를 수 없다."""
    chunks = [TEXT[i : i + size] for i in range(0, len(TEXT), size)]
    assert drain(chunks) == PAYLOAD["regions"]


def test_문자열_안_중괄호에_속지_않는다() -> None:
    """`"괄호 {가} 섞인 대사"` 의 중괄호를 원소 경계로 세면 안 된다."""
    got = drain([TEXT])
    assert got[1]["ko"] == "괄호 {가} 섞인 대사"


def test_이스케이프된_따옴표를_문자열_끝으로_보지_않는다() -> None:
    got = drain([TEXT])
    assert got[2]["ko"] == '따옴표 "안" 과 역슬래시 \\ 포함'


def test_완성된_것만_즉시_나온다() -> None:
    """9초를 기다리지 않는 이유가 이것이다 — 첫 원소가 끝나면 바로 낸다."""
    s = ArrayStreamer("regions")
    first = list(s.feed('{"regions": [{"id": 1, "ko": "가", "note": ""}'))
    assert first == [{"id": 1, "ko": "가", "note": ""}]
    # 아직 안 닫힌 두 번째 원소는 나오면 안 된다
    assert list(s.feed(', {"id": 2, "ko": "나')) == []
    assert list(s.feed('", "note": ""}]}')) == [{"id": 2, "ko": "나", "note": ""}]


def test_배열_시작_전에는_아무것도_안_낸다() -> None:
    s = ArrayStreamer("regions")
    assert list(s.feed('{"other": {"id": 9}, ')) == []
    assert list(s.feed('"regions": [{"id": 1, "ko": "가", "note": ""}]}')) == [
        {"id": 1, "ko": "가", "note": ""}
    ]


def test_빈_배열() -> None:
    assert drain(['{"regions": []}']) == []


def test_배열_뒤_내용은_무시한다() -> None:
    s = ArrayStreamer("regions")
    out = list(s.feed('{"regions": [{"id": 1, "ko": "가", "note": ""}], "x": {"y": 1}}'))
    assert out == [{"id": 1, "ko": "가", "note": ""}]


def test_깨진_원소는_건너뛰고_계속한다() -> None:
    """스키마가 강제되어 있어 올 일이 없지만, 와도 스트림을 죽이지 않는다."""
    s = ArrayStreamer("regions")
    out = list(s.feed('{"regions": [{"id": ,}, {"id": 2, "ko": "나", "note": ""}]}'))
    assert out == [{"id": 2, "ko": "나", "note": ""}]


def test_원문을_그대로_보관한다() -> None:
    s = ArrayStreamer("regions")
    list(s.feed(TEXT))
    assert json.loads(s.text) == PAYLOAD
