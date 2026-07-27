"""번역 응답의 `kind` 분류. 네트워크 없이 돈다.

`kind` 는 클라이언트가 효과음·잡문을 숨기는 근거다 (`DESIGN.md` §8.4). 여기가
틀리면 대사가 화면에서 사라진다 — 사용자가 볼 수 있는 가장 나쁜 실패다.

이 파일은 **파싱과 방어**만 본다. 분류 품질(모델이 실제로 잘 나누는가)은 실물
페이지로 재는 것이고 그 결과는 `DEVLOG.md` 에 있다.
"""

from __future__ import annotations

from mtl_service.translate import RESPONSE_SCHEMA, TranslatedRegion, _kind, _parse
from mtl_shared.models import Region


def _region(rid: int, text: str, *, is_bubble: bool = True) -> Region:
    return Region(
        id=rid,
        text=text,
        bbox=(0, 0, 10, 10),
        is_bubble=is_bubble,
        vertical=True,
        fill_rgb=(255, 255, 255),
        fill_confidence=1.0,
        mask_rle="",
    )


def test_네_가지_분류를_그대로_읽는다() -> None:
    for k in ("dialogue", "sfx", "narration", "extra"):
        assert _kind({"kind": k}) == k


def test_모르는_값은_dialogue_로_떨어진다() -> None:
    """숨기지 않는 쪽으로 떨어뜨린다.

    잘못 숨겨 대사가 사라지는 것이 효과음이 섞이는 것보다 훨씬 나쁘다. 스키마가
    강제하지만 프로바이더가 바뀌거나 스트리밍이 잘리면 뚫릴 수 있다.
    """
    assert _kind({}) == "dialogue"
    assert _kind({"kind": None}) == "dialogue"
    assert _kind({"kind": "caption"}) == "dialogue"  # v4 의 옛 이름
    assert _kind({"kind": "SFX"}) == "dialogue"  # 대문자도 모르는 값이다


def test_응답_파싱이_kind_를_싣는다() -> None:
    payload = {
        "regions": [
            {"id": 0, "ko": "안녕", "kind": "dialogue", "note": ""},
            {"id": 1, "ko": "쾅", "kind": "sfx", "note": ""},
        ]
    }
    res = _parse(payload, "m", 100, (1, 2, 0))
    assert [r.kind for r in res.regions] == ["dialogue", "sfx"]


def test_kind_가_빠진_응답도_대사로_살아남는다() -> None:
    res = _parse({"regions": [{"id": 0, "ko": "안녕"}]}, "m", 100, (1, 2, 0))
    assert res.regions[0].kind == "dialogue"


def test_기본값은_dialogue() -> None:
    assert TranslatedRegion(id=0, ko="안녕").kind == "dialogue"


def test_스키마가_네_값만_받는다() -> None:
    props = RESPONSE_SCHEMA["properties"]["regions"]["items"]
    assert props["properties"]["kind"]["enum"] == ["dialogue", "sfx", "narration", "extra"]
    assert "kind" in props["required"]


def test_모델_입력에_is_bubble_을_넣지_않는다() -> None:
    """틀린 힌트를 주면 모델이 원문보다 그걸 믿는다.

    `is_bubble` 은 스크린톤 배경 위 말풍선을 오판한다. 입력에 남겨 두었더니 명백한
    대사 4개가 그 힌트 하나 때문에 전부 잘못 분류됐다 (`DEVLOG.md`).
    "참고만 하라" 는 프롬프트로는 안 막힌다 — 아예 안 주는 것이 유일한 해결이었다.
    """
    from mtl_service.translate import build_input

    text = build_input([_region(0, "テスト", is_bubble=False)])
    assert "bubble" not in text
    assert "テスト" in text
