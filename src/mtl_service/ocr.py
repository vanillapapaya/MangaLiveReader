"""OCR 단계. DESIGN.md §8.3.

manga-ocr 은 이미지 한 장씩만 받는 `__call__` 을 제공한다. 그대로 루프를 돌면
VisionEncoderDecoder 의 generate 오버헤드가 박스 수만큼 곱해진다 (§14: "OCR은
배치 추론. 루프 안에서 하나씩 호출 금지"). 그래서 processor/tokenizer/model 을
직접 잡아 배치로 넣는다. 후처리는 manga-ocr 의 `post_process` 를 그대로 쓴다.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import cv2
import numpy as np
import torch
from PIL import Image

from mtl_shared.models import Region

from .config import Config

_lock = threading.Lock()
_ocr = None  # manga_ocr.MangaOcr


def is_loaded() -> bool:
    return _ocr is not None


def get_ocr(cfg: Config):
    """manga-ocr 싱글톤. 생성자가 예제 이미지로 1회 워밍업까지 해준다."""
    global _ocr
    if _ocr is None:
        with _lock:
            if _ocr is None:
                from manga_ocr import MangaOcr

                _ocr = MangaOcr(force_cpu=cfg.models.device == "cpu")
    return _ocr


@dataclass(slots=True)
class OcrOutput:
    #: 원문이 붙고, 오검출로 버려진 region 은 빠진 리스트
    regions: list[Region]
    ocr_ms: int


def crop(img_bgr: np.ndarray, bbox: tuple[int, int, int, int], padding: int) -> Image.Image:
    """bbox 를 패딩만큼 넓혀 잘라 PIL 이미지로. manga-ocr 과 같은 그레이 변환."""
    im_h, im_w = img_bgr.shape[:2]
    x, y, w, h = bbox
    x1, y1 = max(0, x - padding), max(0, y - padding)
    x2, y2 = min(im_w, x + w + padding), min(im_h, y + h + padding)
    patch = img_bgr[y1:y2, x1:x2, ::-1]  # BGR → RGB
    return Image.fromarray(patch).convert("L").convert("RGB")


# ---------------------------------------------------------------------------
# 배치 크기 버킷
#
# generate 는 **배치 크기가 처음 보는 값일 때마다** CUDA 커널 자동튜닝을 다시
# 돈다. 실측(RTX 5080, 다른 워크로드와 GPU 공유 중이라 절대값은 흔들린다):
#
#   최초 배치 generate     8-24초        ← 이 값만은 경합으로 설명이 안 된다
#   예열된 배치            12박스 95ms
#
# 페이지마다 말풍선 수가 다르니 크기를 그대로 쓰면 세션 내내 새 shape 를
# 만난다. 몇 개 버킷으로 올림해 더미로 채우면 shape 가 고정되고, 예열도
# 유한한 개수로 끝난다. 배치 안의 이미지끼리는 서로 영향이 없으므로 더미를
# 섞어도 결과가 달라지지 않는다 — 낭비되는 건 연산뿐이다.
# ---------------------------------------------------------------------------

_BUCKET_STEPS = (1, 2, 4, 8, 16, 32, 64)

#: 패딩용. 흰 이미지는 빈 문자열로 나와 생성 길이를 늘리지 않는다.
_PAD_IMAGE = Image.new("RGB", (64, 128), (255, 255, 255))


def buckets(batch_size: int) -> list[int]:
    return [b for b in _BUCKET_STEPS if b < batch_size] + [batch_size]


def _bucket_for(n: int, batch_size: int) -> int:
    for b in buckets(batch_size):
        if n <= b:
            return b
    return batch_size


def _recognize(
    mocr, crops: list[Image.Image], batch_size: int, max_text_length: int
) -> list[str]:
    """크롭 묶음을 한 번의 generate 로 처리한다. 배치는 버킷 크기로 패딩된다.

    `max_text_length` 로 생성을 끊는다. 자기회귀라 **배치에서 가장 긴 출력이 배치
    전체를 붙잡는다** — 실측에서 region 2개짜리 페이지가 11개짜리보다 2배 느렸다.
    상한이 없으면(원래 `max_length=300`) 깨알 소문자 뭉치 하나가 페이지 지연을
    혼자 결정한다. manga-ocr 토크나이저는 거의 글자 단위라 토큰 수 ≈ 글자 수다.
    """
    from manga_ocr.ocr import post_process

    n = len(crops)
    padded = crops + [_PAD_IMAGE] * (_bucket_for(n, batch_size) - n)

    pixel_values = mocr.processor(padded, return_tensors="pt").pixel_values
    with torch.inference_mode():
        generated = mocr.model.generate(
            pixel_values.to(mocr.model.device),
            # +2 는 디코더 시작 토큰과 EOS 몫.
            max_length=max_text_length + 2,
        )
    decoded = mocr.tokenizer.batch_decode(generated[:n], skip_special_tokens=True)
    return [post_process(t) for t in decoded]


#: 이보다 열이 적으면 쪼개지 않는다. 짧은 말풍선은 통짜가 더 정확하고 빠르다.
_SPLIT_MIN_COLUMNS = 3

#: 중앙값 폭의 이 비율보다 좁은 열은 후리가나(루비)로 보고 버린다.
#: 실측: 본문 열 30-33px, 후리가나 열 16-17px (중앙값의 52%).
_RUBY_WIDTH_RATIO = 0.65

#: 잉크가 이 비율(열 최대치 대비)을 넘으면 글자가 있는 열로 본다.
_COLUMN_INK_RATIO = 0.03

#: 이보다 좁은 덩어리는 획 파편으로 보고 무시한다.
_MIN_COLUMN_PX = 6


def split_columns(patch_bgr: np.ndarray) -> list[tuple[int, int]] | None:
    """세로쓰기 블록을 글자 열로 쪼갠다. 쪼갤 수 없으면 None.

    **왜 쪼개는가.** manga-ocr 은 말풍선 하나 분량의 짧은 블록으로 학습됐다. 여러 열이
    빽빽한 큰 말풍선을 통째로 넣으면 앞부분만 맞고 뒤로 갈수록 자기회귀가 드리프트해
    반복·붕괴한다. 실측(7열 90자 말풍선):

        통짜   …おねが何をおね！！！          ← 후반이 무너진다
        열별   …あるかと思いますが何卒よろしく / おねがいしまままま…   ← 거의 정확

    확대해도 안 고쳐진다 (1배·2배·3배 모두 같게 무너졌다). 길이 자체가 원인이다.

    열은 잉크의 세로 투영에서 빈 띠로 가른다. 세로쓰기라 열 사이가 확실히 비어 있다.
    """
    gray = cv2.cvtColor(patch_bgr, cv2.COLOR_BGR2GRAY)
    ink = (gray < 128).astype(np.uint8)
    col = ink.sum(axis=0)
    if col.max() == 0:
        return None
    on = col > max(1, col.max() * _COLUMN_INK_RATIO)

    runs: list[tuple[int, int]] = []
    start: int | None = None
    for i, v in enumerate(on):
        if v and start is None:
            start = i
        elif not v and start is not None:
            if i - start >= _MIN_COLUMN_PX:
                runs.append((start, i))
            start = None
    if start is not None and len(on) - start >= _MIN_COLUMN_PX:
        runs.append((start, len(on)))

    if len(runs) < _SPLIT_MIN_COLUMNS:
        return None

    # 후리가나 열을 버린다. 남기면 「かんだしろやま」 같은 읽기가 본문 사이에 끼어들어
    # 번역기가 문장으로 착각한다.
    widths = sorted(b - a for a, b in runs)
    med = widths[len(widths) // 2]
    body = [(a, b) for a, b in runs if (b - a) >= med * _RUBY_WIDTH_RATIO]
    if len(body) < _SPLIT_MIN_COLUMNS:
        return None

    # 세로쓰기는 오른쪽 열부터 읽는다.
    return sorted(body, key=lambda r: -r[0])


def _column_crops(img_bgr: np.ndarray, bbox: tuple[int, int, int, int], padding: int):
    """region 하나를 열 크롭 여러 개로. 못 쪼개면 통짜 하나."""
    im_h, im_w = img_bgr.shape[:2]
    x, y, w, h = bbox
    x1, y1 = max(0, x - padding), max(0, y - padding)
    x2, y2 = min(im_w, x + w + padding), min(im_h, y + h + padding)
    patch = img_bgr[y1:y2, x1:x2]
    if patch.size == 0:
        return [crop(img_bgr, bbox, padding)]

    cols = split_columns(patch)
    if cols is None:
        return [crop(img_bgr, bbox, padding)]

    out = []
    for a, b in cols:
        sub = patch[:, max(0, a - 2) : min(patch.shape[1], b + 2)]
        if sub.size == 0:
            continue
        out.append(Image.fromarray(sub[:, :, ::-1]).convert("L").convert("RGB"))
    return out or [crop(img_bgr, bbox, padding)]


def warmup(cfg: Config) -> None:
    """모든 버킷을 예열한다. `MangaOcr` 생성자는 한 장짜리만 태우고 끝낸다."""
    mocr = get_ocr(cfg)
    for size in buckets(cfg.ocr.batch_size):
        _recognize(mocr, [_PAD_IMAGE] * size, cfg.ocr.batch_size, cfg.ocr.max_text_length)


def run(img_bgr: np.ndarray, regions: list[Region], cfg: Config) -> OcrOutput:
    """정렬된 region 들에 원문을 채운다. 빈 결과는 오검출로 보고 버린다."""
    t0 = time.perf_counter()
    if not regions:
        return OcrOutput(regions=[], ocr_ms=0)

    mocr = get_ocr(cfg)

    # region 하나가 크롭 여러 개(열)로 갈릴 수 있다. 어느 크롭이 어느 region 것인지
    # 기억해 두었다가 나중에 이어 붙인다.
    crops: list[Image.Image] = []
    spans: list[tuple[int, int]] = []
    for r in regions:
        parts = (
            _column_crops(img_bgr, r.bbox, cfg.ocr.crop_padding)
            if r.vertical
            else [crop(img_bgr, r.bbox, cfg.ocr.crop_padding)]
        )
        spans.append((len(crops), len(crops) + len(parts)))
        crops.extend(parts)

    pieces: list[str] = []
    for start in range(0, len(crops), cfg.ocr.batch_size):
        chunk = crops[start : start + cfg.ocr.batch_size]
        pieces.extend(_recognize(mocr, chunk, cfg.ocr.batch_size, cfg.ocr.max_text_length))

    texts = ["".join(pieces[a:b]) for a, b in spans]

    kept: list[Region] = []
    for region, text in zip(regions, texts, strict=True):
        if len(text) < cfg.ocr.min_text_length:
            continue
        # 상한까지 쏟아냈다 = 모델이 끝을 못 찾았다. 말풍선 밖이면 십중팔구
        # 깨알 소문자 뭉치(약관·홍보문·줄거리)라 원문 자체가 쓰레기다. §8.3 의
        # min_text_length 와 대칭이다.
        #
        # **말풍선 안이면 자르지 않는다.** 진짜 긴 대사를 통째로 잃는 것이 잘린 원문을
        # 남기는 것보다 나쁘다. 실측 정상 대사 최장 26자로 상한 32 에 여유가 있다.
        if not region.is_bubble and len(text) >= cfg.ocr.max_text_length:
            continue
        region.text = text
        kept.append(region)

    return OcrOutput(regions=kept, ocr_ms=int((time.perf_counter() - t0) * 1000))
