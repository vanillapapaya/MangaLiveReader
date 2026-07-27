"""마스크 인코딩과 L3 안전도 계산. DESIGN.md §4.1, §7.4, §8.1.

검출기가 주는 마스크는 **글자 획**의 마스크다(말풍선 윤곽이 아니다). 그래서
마스크 바로 바깥은 말풍선 바탕색이고, 그 색이 곧 `fill_rgb` 다. 같은 픽셀들의
균일도·명도·채도를 보면 "말풍선 안의 글자"인지 "그림 위의 효과음"인지도
갈린다 — `is_bubble` 을 여기서 판정하는 이유다.

DESIGN.md §3.1 은 comic-text-detector 가 `is_bubble` 을 산출한다고 적었지만
사실이 아니다. 그 모델의 클래스는 언어(eng/ja)다. ctd/NOTICE.md 참조.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

# ---------------------------------------------------------------------------
# 판정 임계값
#
# 전부 M1 검증(DESIGN.md §11)에서 실제 페이지를 보고 조일 값들이다. 흩뿌리지
# 않고 여기 모아둔다.
# ---------------------------------------------------------------------------

#: fill_rgb 를 재는 마스크 외곽 링의 두께(px). DESIGN.md §4.1 이 3px 로 못박았다.
RING_PX = 3

#: is_bubble 통계를 재는 링의 두께(px). fill_rgb 링보다 넓게 봐야 "그림 위"인지
#: 알 수 있다. 다만 **bbox 를 넓히는 방식은 쓰지 않는다** — 말풍선 가장자리에
#: 붙은 글자에서는 확장 영역이 풍선 밖으로 새어나가 멀쩡한 말풍선을 효과음으로
#: 오판한다. 글자 획 주위만 본다.
BG_RING_PX = 12

#: 말풍선으로 인정할 배경 조건
BUBBLE_MIN_VALUE = 150.0  # 중앙값 밝기 (0-255)
BUBBLE_MAX_SATURATION = 0.25  # 중앙값 색의 채도 (0-1)
BUBBLE_MAX_STDDEV = 40.0  # 배경 밝기 표준편차. 그림 위면 크게 튄다

# 한계 1: 검은 말풍선에 흰 글자인 경우 밝기 조건에 걸려 효과음으로 분류된다.
# 그 region 은 L3 대신 L2 로 그려지는데, L2 는 그림을 훼손하지 않으므로
# 안전한 실패다. 반대 방향(그림 위 텍스트를 말풍선으로 오판)이 훨씬 위험하다.
#
# 한계 2 (2026-07-27, 미해결): **스크린톤 배경 위의 말풍선이 오분류된다.**
# 점무늬가 링에 섞여 bg_stddev 가 문턱을 넘는다. sunday-webry 한 페이지에서 말풍선
# 5개가 False 로 나왔고, `include_sfx = false` 로 필터하면 대사가 통째로 사라진다.
#
# **문턱 조정으로는 못 고친다.** 네 가지를 재봤고 전부 실패했다 (DEVLOG.md 참조):
# 블러 후 표준편차 / 중앙값 이탈 비율 / IQR / 링 폭 스윕 / flood fill 갇힘 정도.
# 진짜 효과음(False 가 맞는 것)의 값 범위가 오분류된 말풍선 범위 **안에** 들어와서
# 어떤 문턱을 잡아도 한쪽이 깨진다. 둘 다 "가까이는 균일, 멀어지면 튄다" 는 같은
# 모양이라 그렇다 — 말풍선 경계를 실제로 분할하지 않는 한 답이 없고, 검출기는
# 글자 획 마스크만 준다.
#
# 따라서 **`is_bubble` 을 하드 필터로 쓰지 말 것.** 힌트로만 취급한다.
# `include_sfx` 는 켜 두는 쪽이 안전하다 — 효과음이 섞이는 것보다 대사가 빠지는
# 쪽이 훨씬 나쁘다.

#: fill_confidence 감점 기준 (DESIGN.md §7.4)
FILL_RATIO_PENALTY_FROM = 0.9  # 마스크가 bbox 를 이 비율 넘게 먹으면 검출 실패로 본다
SATURATION_PENALTY_FROM = 0.10
SATURATION_PENALTY_GAIN = 3.0
STDDEV_PENALTY_FROM = 12.0
STDDEV_PENALTY_GAIN = 1.0 / 60.0


@dataclass(slots=True)
class MaskStats:
    """한 region 의 마스크 파생값."""

    #: bbox 상대 좌표, 행 우선 RLE
    rle: str
    #: 마스크 외곽 링의 중앙값 색 (R, G, B)
    fill_rgb: tuple[int, int, int]
    #: L3 안전도 0-1
    fill_confidence: float
    #: 말풍선 안의 글자인가
    is_bubble: bool
    #: 원문이 세로쓰기인가
    vertical: bool


# ---------------------------------------------------------------------------
# RLE
# ---------------------------------------------------------------------------


def encode_rle(mask: np.ndarray) -> str:
    """불리언 마스크를 행 우선 RLE 문자열로.

    형식: 런 길이를 공백으로 이은 십진수 열. **항상 배경(0) 런으로 시작**하고
    이후 전경/배경이 번갈아 나온다. 첫 픽셀이 전경이면 선두에 `0` 이 온다.
    길이 합은 `w * h` 와 정확히 같다.

    >>> encode_rle(np.array([[0, 1, 1], [1, 0, 0]], dtype=bool))
    '1 3 2'
    """
    flat = np.asarray(mask, dtype=bool).ravel()
    if flat.size == 0:
        return ""
    # 값이 바뀌는 지점 사이의 거리 = 런 길이
    change = np.flatnonzero(np.diff(flat))
    bounds = np.concatenate(([-1], change, [flat.size - 1]))
    runs = np.diff(bounds)
    if flat[0]:  # 전경으로 시작하면 길이 0 짜리 배경 런을 앞에 끼운다
        runs = np.concatenate(([0], runs))
    return " ".join(str(int(r)) for r in runs)


def decode_rle(rle: str, width: int, height: int) -> np.ndarray:
    """`encode_rle` 의 역. 클라이언트와 테스트가 쓴다."""
    out = np.zeros(width * height, dtype=bool)
    if not rle:
        return out.reshape(height, width)
    pos = 0
    for i, token in enumerate(rle.split(" ")):
        run = int(token)
        if i % 2:  # 홀수 번째 = 전경
            out[pos : pos + run] = True
        pos += run
    return out.reshape(height, width)


# ---------------------------------------------------------------------------
# 방향
# ---------------------------------------------------------------------------

#: 종횡비가 이보다 치우치면 투영 분석 없이 그것만으로 방향을 정한다
_ASPECT_DECISIVE = 1.8
#: 투영값이 최대치의 이 비율 미만이면 "빈 줄"로 센다
_GAP_LEVEL = 0.05


def detect_vertical(mask: np.ndarray) -> bool:
    """글자 마스크로 세로쓰기 여부를 판정한다.

    세로쓰기는 글자가 세로 열로 쌓이므로 **열 방향 투영에 빈 구간이 많고**,
    가로쓰기는 행 방향 투영에 빈 구간이 많다. 한 줄짜리라 투영이 무의미한
    경우는 종횡비로 가른다. 애매하면 세로 — 일본 만화의 기본값이다.
    """
    h, w = mask.shape[:2]
    if h == 0 or w == 0:
        return True

    if h >= w * _ASPECT_DECISIVE:
        return True
    if w >= h * _ASPECT_DECISIVE:
        return False

    m = np.asarray(mask, dtype=bool)
    col = m.sum(axis=0).astype(np.float64)
    row = m.sum(axis=1).astype(np.float64)
    if col.max() == 0 or row.max() == 0:
        return True

    gaps_x = float((col < col.max() * _GAP_LEVEL).mean())
    gaps_y = float((row < row.max() * _GAP_LEVEL).mean())
    if gaps_x == gaps_y:
        return True
    return gaps_x > gaps_y


# ---------------------------------------------------------------------------
# 색과 안전도
# ---------------------------------------------------------------------------


def _saturation(rgb: tuple[int, int, int]) -> float:
    hi, lo = max(rgb), min(rgb)
    return 0.0 if hi == 0 else (hi - lo) / hi


def analyze(
    img_bgr: np.ndarray,
    text_mask: np.ndarray,
    bbox: tuple[int, int, int, int],
) -> MaskStats:
    """한 region 의 마스크·색·안전도를 한 번에 낸다.

    `img_bgr` 과 `text_mask` 는 **페이지 전체** 이고, `bbox` 는 (x, y, w, h).
    """
    x, y, w, h = bbox
    im_h, im_w = img_bgr.shape[:2]

    local = np.asarray(text_mask[y : y + h, x : x + w], dtype=bool)
    rle = encode_rle(local)
    vertical = detect_vertical(local)
    mask_ratio = float(local.mean()) if local.size else 0.0

    # 두 링을 같은 패치에서 뽑는다. 패치는 넓은 쪽 링이 잘리지 않을 만큼만.
    pad = BG_RING_PX + 1
    px1, py1 = max(0, x - pad), max(0, y - pad)
    px2, py2 = min(im_w, x + w + pad), min(im_h, y + h + pad)
    patch = img_bgr[py1:py2, px1:px2]
    glyphs = np.asarray(text_mask[py1:py2, px1:px2], dtype=bool)

    # --- fill_rgb: 마스크 외곽 3px 링의 중앙값 (DESIGN.md §4.1) ---
    ring = _dilate(glyphs, RING_PX) & ~glyphs
    # --- 통계용: 더 넓은 링. 글자 안티에일리어싱을 피하려 1px 만 비운다 ---
    bg_sel = _dilate(glyphs, BG_RING_PX) & ~_dilate(glyphs, 1)

    if not bg_sel.any():
        # 박스는 잡혔는데 마스크가 비었다 (검출기가 획을 못 집었다).
        # 패치 전체를 배경으로 보고 넘어간다.
        bg_sel = np.ones_like(glyphs)

    fill_rgb = _median_rgb(patch, ring) or _median_rgb(patch, bg_sel) or (255, 255, 255)

    bg_pixels = patch[bg_sel]
    if bg_pixels.size:
        gray = bg_pixels.astype(np.float64) @ (0.114, 0.587, 0.299)  # BGR 가중치
        bg_value = float(np.median(gray))
        bg_stddev = float(gray.std())
    else:
        bg_value, bg_stddev = 255.0, 0.0

    saturation = _saturation(fill_rgb)
    is_bubble = (
        bg_value >= BUBBLE_MIN_VALUE
        and saturation <= BUBBLE_MAX_SATURATION
        and bg_stddev <= BUBBLE_MAX_STDDEV
    )

    return MaskStats(
        rle=rle,
        fill_rgb=fill_rgb,
        fill_confidence=fill_confidence(
            is_bubble=is_bubble,
            mask_ratio=mask_ratio,
            saturation=saturation,
            bg_stddev=bg_stddev,
        ),
        is_bubble=is_bubble,
        vertical=vertical,
    )


def _dilate(mask: np.ndarray, radius: int) -> np.ndarray:
    kernel = np.ones((radius * 2 + 1, radius * 2 + 1), np.uint8)
    return cv2.dilate(mask.astype(np.uint8), kernel, iterations=1).astype(bool)


def _median_rgb(patch_bgr: np.ndarray, selection: np.ndarray) -> tuple[int, int, int] | None:
    if patch_bgr.size == 0 or not selection.any():
        return None
    px = patch_bgr[selection]
    b, g, r = (int(np.median(px[:, i])) for i in range(3))
    return (r, g, b)


def fill_confidence(
    *,
    is_bubble: bool,
    mask_ratio: float,
    saturation: float,
    bg_stddev: float,
) -> float:
    """L3 안전도. DESIGN.md §7.4 의 세 감점 항목을 그대로 옮긴 것.

    클라이언트는 이 값이 0.8 미만이면 **그 region 만** L2 로 강등한다.
    """
    if not is_bubble:
        # 효과음은 그림 위에 있다. 채우면 그림이 사라진다.
        return 0.0

    score = 1.0
    if mask_ratio > FILL_RATIO_PENALTY_FROM:
        # 마스크가 박스를 통째로 먹었다 = 글자를 못 집어냈다
        score -= (mask_ratio - FILL_RATIO_PENALTY_FROM) / (1.0 - FILL_RATIO_PENALTY_FROM)
    score -= max(0.0, (saturation - SATURATION_PENALTY_FROM) * SATURATION_PENALTY_GAIN)
    score -= max(0.0, (bg_stddev - STDDEV_PENALTY_FROM) * STDDEV_PENALTY_GAIN)
    return round(max(0.0, min(1.0, score)), 3)
