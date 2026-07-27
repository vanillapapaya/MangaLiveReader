"""서비스와 클라이언트가 공유하는 요청/응답 모델.

DESIGN.md §4 API 계약의 코드 표현. 좌표는 전부 **전송된 이미지 좌표계** 기준이며,
화면 좌표 환산은 클라이언트 책임이다 (§4.1, §6).
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Literal

RenderMode = Literal["off", "pin", "label", "fill"]
TranslateMode = Literal["natural", "literal"]


# --------------------------------------------------------------------------
# 요청
# --------------------------------------------------------------------------


@dataclass(slots=True)
class ReadMeta:
    """POST /read 의 `meta` 필드."""

    phash: str
    profile: str
    mode: TranslateMode = "natural"
    series_hint: str | None = None
    prev_page_phash: str | None = None
    include_sfx: bool = True
    #: True 면 캐시를 **읽지도 쓰지도 않는다.**
    #:
    #: 읽지 않는 이유: 사용자가 "다시 읽어" 라고 한 것은 캐시를 무시하고 다시 하라는
    #: 뜻이다. 캐시를 주면 예전에 잘못 읽은 결과를 충실히 돌려줘 "고쳤는데 여전하다"
    #: 가 된다 — 실제로 그 일이 났다.
    #:
    #: 쓰지 않는 이유: 이 플래그가 붙는 요청은 페이지의 **일부 크롭**인데, 부분 크롭은
    #: 대부분 흰 바탕이라 지각 해시가 밋밋하다. 다른 말풍선끼리도 가까워져 엉뚱한
    #: 캐시 적중을 만든다 (실측 거리 14, 문턱 20).
    no_cache: bool = False
    #: True 면 이 페이지의 낡은 캐시를 **지우고** 다시 읽는다. 새 결과는 저장한다.
    #: `no_cache`(읽지도 쓰지도 않음)와 달리 전체 페이지 갱신용이다.
    refresh: bool = False

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ReadMeta:
        known = {f for f in cls.__slots__}
        return cls(**{k: v for k, v in d.items() if k in known})


# --------------------------------------------------------------------------
# 응답
# --------------------------------------------------------------------------


@dataclass(slots=True)
class Region:
    """검출된 텍스트 영역 하나. DESIGN.md §4.1 region 필드 표 참조."""

    id: int
    text: str
    bbox: tuple[int, int, int, int]  # x, y, w, h

    #: False면 효과음/배경 텍스트. L3에서 절대 채우지 않는다.
    is_bubble: bool = True
    #: 원문이 세로쓰기였는가. 번역 조판 방향과 L2 라벨 배치를 결정한다.
    vertical: bool = True

    #: 말풍선 마스크 (bbox 기준 상대 좌표, 행 우선 RLE). L3 전용이나 항상 내려보낸다(§4.5).
    mask_rle: str | None = None
    #: 마스크 외곽 3px 링의 중앙값 색. 클라이언트는 원본 픽셀을 이미 버렸다.
    fill_rgb: tuple[int, int, int] | None = None
    #: L3 안전도. < 0.8 이면 그 region만 L2로 강등한다 (§7.4).
    fill_confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["bbox"] = list(self.bbox)
        if self.fill_rgb is not None:
            d["fill_rgb"] = list(self.fill_rgb)
        return d


@dataclass(slots=True)
class Translation:
    id: int
    ko: str
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Timings:
    """단계별 소요 시간(ms). DESIGN.md §14 — 계측은 처음부터 넣는다."""

    detect: int = 0
    mask: int = 0
    order: int = 0
    ocr: int = 0
    translate: int = 0
    total: int = 0

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(slots=True)
class ReadResult:
    """M1의 JSON 응답 본문. M2에서 SSE 이벤트로 쪼개진다."""

    regions: list[Region] = field(default_factory=list)
    translations: list[Translation] = field(default_factory=list)
    reordered: bool = False
    cached: bool = False
    timings: Timings = field(default_factory=Timings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "regions": [r.to_dict() for r in self.regions],
            "translations": [t.to_dict() for t in self.translations],
            "reordered": self.reordered,
            "cached": self.cached,
            "timings": self.timings.to_dict(),
        }
