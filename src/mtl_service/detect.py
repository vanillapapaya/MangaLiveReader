"""검출 단계. DESIGN.md §8.1.

모델은 싱글톤이다 (§14). 요청 핸들러에서 로드하는 코드가 들어가면 설계 목표가
무너진다.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import cv2
import numpy as np

from mtl_shared.models import Region

from . import mask as mask_utils
from .config import Config
from .ctd import ComicTextDetector

_lock = threading.Lock()
_detector: ComicTextDetector | None = None


def is_loaded() -> bool:
    return _detector is not None


def get_detector(cfg: Config) -> ComicTextDetector:
    """검출기 싱글톤. 최초 호출에서만 가중치를 읽는다 (3-5초)."""
    global _detector
    if _detector is None:
        with _lock:
            if _detector is None:
                weights = cfg.models.detector_weights
                if not weights.exists():
                    raise FileNotFoundError(
                        f"검출기 가중치가 없다: {weights}\n"
                        f"  uv run python scripts/fetch_models.py"
                    )
                _detector = ComicTextDetector(
                    weights,
                    device=cfg.models.device,
                    input_size=cfg.models.detector_input_size,
                )
    return _detector


@dataclass(slots=True)
class DetectOutput:
    #: 읽기 순서 정렬 전. `id` 는 아직 채워지지 않았다(전부 0).
    regions: list[Region]
    #: 페이지 전체 글자 마스크 (uint8 0-255). OCR·디버그 시각화가 쓴다.
    text_mask: np.ndarray
    detect_ms: int
    mask_ms: int


def detect(img_bgr: np.ndarray, cfg: Config) -> DetectOutput:
    """페이지 하나에서 텍스트 영역을 뽑는다."""
    detector = get_detector(cfg)

    t0 = time.perf_counter()
    raw = detector(
        img_bgr,
        conf_threshold=cfg.detect.detect_threshold,
        nms_threshold=cfg.detect.nms_threshold,
    )
    t1 = time.perf_counter()

    im_h, im_w = img_bgr.shape[:2]
    min_area = cfg.detect.min_area_ratio * im_h * im_w

    boxes = [tuple(int(v) for v in b) for b in raw.boxes]
    boxes = [b for b in boxes if (b[2] - b[0]) * (b[3] - b[1]) >= min_area]
    max_gap = cfg.detect.merge_gap_max_ratio * min(im_h, im_w)
    boxes = merge_boxes(boxes, cfg.detect.merge_gap_ratio, max_gap)

    binary_mask = raw.mask > (cfg.detect.mask_threshold * 255)

    if cfg.detect.mask_promote:
        boxes = boxes + _promote(raw.mask, binary_mask, boxes, cfg, min_area)

    regions: list[Region] = []
    for x1, y1, x2, y2 in boxes:
        bbox = (x1, y1, x2 - x1, y2 - y1)
        stats = mask_utils.analyze(img_bgr, binary_mask, bbox)
        regions.append(
            Region(
                id=0,
                text="",
                bbox=bbox,
                is_bubble=stats.is_bubble,
                vertical=stats.vertical,
                mask_rle=stats.rle,
                fill_rgb=stats.fill_rgb,
                fill_confidence=stats.fill_confidence,
            )
        )
    t2 = time.perf_counter()

    return DetectOutput(
        regions=regions,
        text_mask=raw.mask,
        detect_ms=int((t1 - t0) * 1000),
        mask_ms=int((t2 - t1) * 1000),
    )


# ---------------------------------------------------------------------------
# 마스크 승격
# ---------------------------------------------------------------------------


def _promote(
    raw_mask: np.ndarray,
    binary_mask: np.ndarray,
    boxes: list[tuple[int, ...]],
    cfg: Config,
    min_area: float,
) -> list[tuple[int, int, int, int]]:
    """yolo 가 박스를 안 준 마스크 덩어리를 region 후보로 올린다.

    실물 8장에서 효과음(「ダッ!!」「ジャキ」「ダン」「ピンポーン」…)이 하나도 검출되지
    않았다. `detect_threshold` 를 0.35 → 0.12 로 낮춰도 검출 수가 그대로였다. 임계값
    문제가 아니라 **yolo 블록 헤드가 그런 글자를 제안하지 않는 것**이다. 반면 세그멘테이션
    헤드가 낸 `binary_mask` 에는 전부 선명히 찍혀 있다. 그래서 마스크 쪽에서 줍는다.

    마스크 연결요소는 글자 획 하나하나라 그대로는 못 쓴다. 팽창으로 획을 이어 덩어리를
    만든 뒤, 이미 yolo 박스가 덮은 곳과 잉크가 옅은 노이즈를 걸러낸다.

    팽창 반경은 페이지 짧은 변에 비례한다. 효과음 글자는 크고 본문은 작지만, 작은 글자는
    획 간격도 같이 좁아서 하나의 반경으로 둘 다 이어진다.

    노이즈를 가르는 신호는 **이진화 전 마스크 원본값**이다. 실측하면 진짜 글자는 평균
    193-221, 그림 가장자리 오검출은 85-142 로 뚜렷하게 갈린다. `mask_threshold`(0.3)로
    이진화한 뒤에는 이 차이가 사라지므로 `raw_mask` 를 따로 받는다.
    """
    h, w = binary_mask.shape
    residual = binary_mask.copy()
    for x1, y1, x2, y2 in boxes:
        residual[y1:y2, x1:x2] = False
    if not residual.any():
        return []

    radius = max(2, int(cfg.detect.mask_dilate_ratio * min(h, w)))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (radius * 2 + 1,) * 2)
    blobs = cv2.dilate(residual.astype(np.uint8), kernel)

    n, _, stats, _ = cv2.connectedComponentsWithStats(blobs, connectivity=8)

    promoted: list[tuple[int, int, int, int]] = []
    for i in range(1, n):
        bx, by, bw, bh, _ = stats[i]
        if bw * bh < min_area:
            continue

        # 팽창 전 마스크 기준 잉크 비율. 그림 가장자리에서 새어 나온 얼룩은 여기서 죽는다.
        patch = residual[by : by + bh, bx : bx + bw]
        if not patch.any() or patch.mean() < cfg.detect.mask_min_ink_ratio:
            continue

        ink_strength = raw_mask[by : by + bh, bx : bx + bw][patch].mean()
        if ink_strength < cfg.detect.mask_promote_confidence * 255:
            continue

        # yolo 박스와 겹치면 중복이다. residual 로 지웠어도 팽창이 박스 안까지 번진다.
        box = (int(bx), int(by), int(bx + bw), int(by + bh))
        if _overlap_ratio(box, boxes) > cfg.detect.mask_max_overlap:
            continue

        promoted.append(box)

    return promoted


def _overlap_ratio(box: tuple[int, int, int, int], others: list[tuple[int, ...]]) -> float:
    """`box` 와 `others` 의 겹침 정도. 어느 한쪽 기준으로든 많이 겹치면 크게 나온다.

    양쪽 넓이로 각각 나눠 보는 것이 중요하다. 후보 넓이만 기준으로 삼으면, 팽창으로
    부푼 큰 덩어리가 작은 yolo 박스를 통째로 삼켜도 비율이 작게 나와 통과한다.
    실제로 `shonenjumpplus_002430` 의 「休み時間」이 그렇게 두 번 검출됐다.
    """
    x1, y1, x2, y2 = box
    area = (x2 - x1) * (y2 - y1)
    if area <= 0:
        return 0.0

    best = 0.0
    for ox1, oy1, ox2, oy2 in others:
        iw = min(x2, ox2) - max(x1, ox1)
        ih = min(y2, oy2) - max(y1, oy1)
        if iw <= 0 or ih <= 0:
            continue
        other_area = (ox2 - ox1) * (oy2 - oy1)
        inter = iw * ih
        best = max(best, inter / area, inter / other_area if other_area > 0 else 0.0)
    return best


# ---------------------------------------------------------------------------
# 박스 병합 (DESIGN.md §8.1)
# ---------------------------------------------------------------------------

#: 이웃으로 인정하려면 직교 방향으로 이만큼은 겹쳐야 한다 (짧은 변 대비 비율).
#: 없으면 대각선으로 떨어진 남남 박스가 붙는다.
_MIN_OVERLAP_RATIO = 0.5


def _should_merge(
    a: tuple[int, ...], b: tuple[int, ...], gap_ratio: float, max_gap: float
) -> bool:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ha, hb = ay2 - ay1, by2 - by1
    wa, wb = ax2 - ax1, bx2 - bx1

    # 양수면 겹친 길이, 음수면 벌어진 간격
    ox = min(ax2, bx2) - max(ax1, bx1)
    oy = min(ay2, by2) - max(ay1, by1)

    if ox > 0 and oy > 0:
        return True

    # 허용 간격은 **벌어진 방향의 박스 크기**에서 뽑되, `max_gap` 으로 절대 상한을
    # 씌운다 (service.toml [detect].merge_gap_ratio / merge_gap_max_ratio).
    #
    # 비례항만 쓰면 큰 박스가 멀리까지 손을 뻗는다. 실측 52장에서 간격 병합 30건 중
    # 24건이 30px 를 넘었고, 눈으로 확인한 것은 전부 오류였다 — `ichicomi_030024` 는
    # 서로 다른 세 칸의 외침 말풍선을 페이지 높이만큼 한 박스로 묶었다.
    #
    # 그렇다고 비례항을 조이면 정당한 줄 병합이 같이 죽는다 (`ichicomi_030013` 의
    # 가로 2줄은 간격 12px 인데 박스 높이가 34px 뿐이라 허용치도 같이 작아진다).
    # 한 말풍선 안 줄·열 간격은 박스 크기가 아니라 **페이지 해상도**에 묶이는 값이라,
    # 절대 상한이 비례항보다 잘 맞는다.
    tol_x = min(gap_ratio * min(wa, wb), max_gap)
    tol_y = min(gap_ratio * min(ha, hb), max_gap)

    if oy > 0 and -ox <= tol_x and oy >= _MIN_OVERLAP_RATIO * min(ha, hb):
        return True  # 나란한 세로 열
    if ox > 0 and -oy <= tol_y and ox >= _MIN_OVERLAP_RATIO * min(wa, wb):
        return True  # 위아래로 쌓인 가로 줄
    return False


def merge_boxes(
    boxes: list[tuple[int, int, int, int]], gap_ratio: float, max_gap: float
) -> list[tuple[int, int, int, int]]:
    """같은 말풍선 안에서 줄 단위로 쪼개진 박스를 합친다.

    쪼갠 채로 OCR 에 넣으면 세로쓰기 줄 순서가 뒤집힌다 (DESIGN.md §8.1).
    합친 박스가 또 다른 박스와 이웃이 될 수 있으므로 변화가 없을 때까지 돈다.
    """
    current = list(boxes)
    while True:
        merged: list[tuple[int, int, int, int]] = []
        used = [False] * len(current)
        changed = False

        for i, box in enumerate(current):
            if used[i]:
                continue
            acc = box
            used[i] = True
            for j in range(i + 1, len(current)):
                if used[j]:
                    continue
                if _should_merge(acc, current[j], gap_ratio, max_gap):
                    o = current[j]
                    acc = (
                        min(acc[0], o[0]),
                        min(acc[1], o[1]),
                        max(acc[2], o[2]),
                        max(acc[3], o[3]),
                    )
                    used[j] = True
                    changed = True
            merged.append(acc)

        current = merged
        if not changed:
            return current
