"""M1 검증 도구. DESIGN.md §11.

    uv run python scripts/debug_page.py <이미지...> [--out debug/]

이미지를 서비스 파이프라인에 그대로 통과시키고,

- 박스 + 읽기 순서 번호 + is_bubble / vertical / fill_confidence 를 얹은 PNG
- 글자 마스크 PNG
- OCR 원문 표 (stdout)

를 낸다. 검출률·읽기 순서·is_bubble 분류를 눈으로 확인하는 용도다.
HTTP 를 타지 않으므로 서비스를 띄우지 않아도 된다.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mtl_service import detect, ocr, order  # noqa: E402
from mtl_service.config import load  # noqa: E402

#: 라벨용 일본어/한국어 폰트 후보. 없으면 기본 비트맵 폰트로 떨어진다.
_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\malgun.ttf",
    r"C:\Windows\Fonts\msgothic.ttc",
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
]

BUBBLE_COLOR = (60, 200, 60)
SFX_COLOR = (60, 60, 240)

#: DESIGN.md §5.4 전송 전 정규화. 서비스는 이 과정을 거친 이미지만 보므로,
#: 검증도 같은 바이트로 해야 수치가 실제 서비스 수치가 된다.
#:
#: §5.4 는 짧은 변을 [1200, 1600] 안으로만 밀어넣고 그 안이면 손대지 않는다.
#: §12 `[capture] target_short_side = 1400` 은 이 대역 안의 목표값이라 여기서는
#: 쓰지 않는다 — 이미 대역 안인 이미지를 1400 으로 리샘플하면 손실만 생긴다.
NORMALIZE_MIN_SHORT_SIDE = 1200
NORMALIZE_MAX_SHORT_SIDE = 1600
NORMALIZE_JPEG_QUALITY = 88


def normalize(img_bgr: np.ndarray) -> tuple[np.ndarray, float]:
    """§5.4 를 그대로 적용하고 (이미지, 적용한 스케일) 을 돌려준다."""
    h, w = img_bgr.shape[:2]
    short = min(h, w)
    if short > NORMALIZE_MAX_SHORT_SIDE:
        scale = NORMALIZE_MAX_SHORT_SIDE / short
    elif short < NORMALIZE_MIN_SHORT_SIDE:
        scale = NORMALIZE_MIN_SHORT_SIDE / short
    else:
        scale = 1.0
    if scale != 1.0:
        interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_LANCZOS4
        img_bgr = cv2.resize(img_bgr, None, fx=scale, fy=scale, interpolation=interp)

    ok, buf = cv2.imencode(".jpg", img_bgr, [cv2.IMWRITE_JPEG_QUALITY, NORMALIZE_JPEG_QUALITY])
    if not ok:
        raise RuntimeError("JPEG 인코딩 실패")
    return cv2.imdecode(buf, cv2.IMREAD_COLOR), scale


def _font(size: int) -> ImageFont.ImageFont:
    for path in _FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def annotate(img_bgr: np.ndarray, regions: list) -> Image.Image:
    canvas = Image.fromarray(img_bgr[:, :, ::-1]).convert("RGB")
    draw = ImageDraw.Draw(canvas, "RGBA")
    font = _font(18)
    small = _font(13)

    for region in regions:
        x, y, w, h = region.bbox
        color = BUBBLE_COLOR if region.is_bubble else SFX_COLOR
        draw.rectangle([x, y, x + w, y + h], outline=color, width=3)

        tag = f"{region.id}"
        draw.rectangle([x, y - 22, x + 10 + 12 * len(tag), y], fill=(*color, 220))
        draw.text((x + 4, y - 21), tag, fill=(255, 255, 255), font=font)

        info = f"{'縦' if region.vertical else '横'} c={region.fill_confidence:.2f}"
        draw.text((x + 2, y + h + 2), info, fill=color, font=small)
        # fill_rgb 견본
        if region.fill_rgb:
            draw.rectangle([x + w - 16, y + h + 2, x + w, y + h + 18], fill=tuple(region.fill_rgb))
    return canvas


def process(path: Path, out_dir: Path, cfg, do_normalize: bool = True) -> None:
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        print(f"! 읽을 수 없다: {path}", file=sys.stderr)
        return

    raw_shape = img.shape[:2]
    norm_scale = 1.0
    if do_normalize:
        img, norm_scale = normalize(img)

    t0 = time.perf_counter()
    detected = detect.detect(img, cfg)
    ordered = order.sort_regions(
        detected.regions, (img.shape[1], img.shape[0]), cfg.order.min_gap_ratio
    )
    order.assign_ids(ordered)
    result = ocr.run(img, ordered, cfg)
    order.assign_ids(result.regions)
    total = int((time.perf_counter() - t0) * 1000)

    print(f"\n=== {path.name}  {img.shape[1]}x{img.shape[0]} ===")
    if do_normalize:
        print(
            f"정규화 §5.4: {raw_shape[1]}x{raw_shape[0]} → x{norm_scale:.3f} + JPEG q"
            f"{NORMALIZE_JPEG_QUALITY}"
        )
    print(
        f"detect {detected.detect_ms}ms · mask {detected.mask_ms}ms · "
        f"ocr {result.ocr_ms}ms · total {total}ms"
    )
    print(f"검출 {len(detected.regions)}개 → OCR 통과 {len(result.regions)}개")
    print(f"{'id':>3} {'bbox':>22} {'bub':>4} {'ver':>4} {'conf':>5} {'fill':>14}  text")
    for r in result.regions:
        fill = str(r.fill_rgb)
        print(
            f"{r.id:>3} {str(r.bbox):>22} {str(r.is_bubble):>4} {str(r.vertical):>4} "
            f"{r.fill_confidence:>5.2f} {fill:>14}  {r.text}"
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    stem = path.stem
    annotate(img, result.regions).save(out_dir / f"{stem}.boxes.png")
    Image.fromarray(detected.text_mask).save(out_dir / f"{stem}.mask.png")
    print(f"→ {out_dir / (stem + '.boxes.png')}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("images", nargs="+", type=Path)
    ap.add_argument("--out", type=Path, default=Path("debug"))
    ap.add_argument(
        "--raw",
        action="store_true",
        help="§5.4 정규화를 건너뛰고 원본을 그대로 넣는다 (기본은 정규화 적용)",
    )
    args = ap.parse_args()

    cfg = load()
    # 예열하지 않으면 첫 이미지의 소요 시간이 커널 자동튜닝 비용에 묻힌다.
    print("예열 중...", flush=True)
    from mtl_service.app import warm

    print("예열", warm())

    for path in args.images:
        process(path, args.out, cfg, do_normalize=not args.raw)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
