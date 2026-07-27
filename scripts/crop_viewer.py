"""전체 페이지 스크린샷에서 만화 뷰어 밴드만 잘라낸다.

    python scripts/crop_viewer.py test/pages/*.png --out test/cropped/

`test/pages/` 의 원본은 브라우저 "전체 페이지 캡처"라 상단에 뷰어가 있고 그 아래는
댓글·추천작·광고·푸터다. 그대로 파이프라인에 넣으면 사이트 UI 글자가 전부 검출돼
검출 재현율(DESIGN.md §1) 측정이 망가진다. 런타임 입력은 `[profiles.*]`
`capture_region` 으로 잘린 만화 영역뿐이므로(§5.4) 검증 입력도 같아야 한다.

두 단계로 자른다.

1. **세로** — 뷰어 밴드 하단(`viewer_bottom`). 페이지 아래 끝과 사이트 본문 사이를
   지난다. 행 단위 표준편차에서 **처음 나오는 넓은 빈 띠**를 찾는다. 뷰어는 항상
   페이지 최상단에 있고 그 아래에 댓글·추천작·광고가 오므로, 첫 틈이 곧 경계다.
2. **가로+세로** — 그 안에서 그림이 실제로 그려진 영역(`content_bbox`).

2번이 없으면 검출률이 무너진다. 예로 comic-walker 는 만화가 좌측 900px 에만 있고
나머지 1400px 은 빈 페이지다. 검출기는 입력을 `detector_input_size`(1024)로 줄이므로
만화가 400px 남짓으로 쪼그라들어 글자를 놓친다. 실제 런타임의 `capture_region` 은
만화 영역만 잡으니(§12) 여백을 남기면 불공정한 검증이 된다.

1번 자동 탐지는 손으로 확인한 네 사이트 값을 5-43px 오차로 재현한다(오히려 더 조이는
쪽). 다만 만화와 사이트 본문 사이가 거의 붙어 있으면 첫 틈을 지나쳐 본문 한복판을
자른다 — comic-fuz 가 그렇다(간격 10px). 그런 사이트만 `VIEWER_BOTTOM_OVERRIDE` 에
적는다. 새 사이트를 넣었는데 대지에서 사이트 UI 글자가 보이면 여기에 한 줄 추가할 것.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

#: 자동 탐지가 실패하는 사이트만 손으로 적는다. 원본 캡처 해상도(폭 2312px)에 묶인 값.
VIEWER_BOTTOM_OVERRIDE = {
    # 만화 끝 1360 → 사이트 본문(화수 제목 + 공유 버튼) 시작 1380. 간격이 20px 뿐이라
    # 첫 틈 탐지가 지나치고 본문 한복판(1686)을 자른다.
    "comic-fuz.com": 1375,
}

#: 행 표준편차가 이 비율(99 분위수 대비) 아래면 "빈 줄"로 본다.
ROW_STD_RATIO = 0.4
#: 빈 줄이 이만큼 연속되어야 뷰어와 사이트 본문 사이의 틈으로 인정한다.
#: 만화 칸 사이 가로 홈보다는 넓어야 하고, 뷰어-본문 간격보다는 좁아야 한다.
MIN_BAND_GAP = 30


def viewer_bottom(img_bgr: np.ndarray, site: str) -> int:
    """뷰어 밴드가 끝나는 행. 틈이 없으면 이미지 전체가 뷰어다(yanmaga)."""
    if site in VIEWER_BOTTOM_OVERRIDE:
        return min(VIEWER_BOTTOM_OVERRIDE[site], img_bgr.shape[0])

    std = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32).std(axis=1)
    idx = np.flatnonzero(std > ROW_STD_RATIO * np.percentile(std, 99))
    if idx.size == 0:
        return img_bgr.shape[0]
    gaps = np.flatnonzero(np.diff(idx) > MIN_BAND_GAP)
    return int(idx[gaps[0]]) if gaps.size else img_bgr.shape[0]


#: "그림 있음" 판정 문턱. 절대값이 아니라 그 이미지 최대 대비의 비율로 잡는다.
#: 측정해 보면 세 계층이 뚜렷하게 갈린다 — 만화 60-125, 빈 페이지 11, 뷰어
#: 가장자리 UI(화살표·버튼) 19-42. 0.4 배면 만화만 남는다.
CONTENT_STD_RATIO = 0.4
#: 그림 영역 사이의 이 이하 간격은 이어진 것으로 본다 (펼침면 사이 흰 여백 등).
CONTENT_GAP = 120
#: 잘라낸 뒤 사방에 남기는 여백(px).
CONTENT_MARGIN = 12


def _content_span(std: np.ndarray) -> tuple[int, int]:
    """1차원 표준편차 프로파일에서 가장 긴 '그림' 구간의 [시작, 끝)."""
    # 최대값 대신 99 분위수 — 페이지 테두리 한 줄에 문턱이 끌려가지 않게.
    idx = np.flatnonzero(std > CONTENT_STD_RATIO * np.percentile(std, 99))
    if idx.size == 0:
        return 0, std.size
    # CONTENT_GAP 이하 간격을 메워 조각난 구간을 잇는다.
    breaks = np.flatnonzero(np.diff(idx) > CONTENT_GAP)
    starts = np.concatenate([[0], breaks + 1])
    ends = np.concatenate([breaks, [idx.size - 1]])
    best = np.argmax(idx[ends] - idx[starts])
    return int(idx[starts[best]]), int(idx[ends[best]]) + 1


def content_bbox(img_bgr: np.ndarray) -> tuple[int, int, int, int]:
    """그림이 실제로 그려진 영역. 뷰어 좌우 여백과 가장자리 UI 버튼을 떨군다."""
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    h, w = gray.shape
    x0, x1 = _content_span(gray.std(axis=0))
    y0, y1 = _content_span(gray.std(axis=1))
    return (
        max(0, x0 - CONTENT_MARGIN),
        max(0, y0 - CONTENT_MARGIN),
        min(w, x1 + CONTENT_MARGIN),
        min(h, y1 + CONTENT_MARGIN),
    )


def site_of(path: Path) -> str | None:
    """`2026-07-27 00.24.21 shonenjumpplus.com cb2b9084c2d6.png` → 사이트명."""
    parts = path.stem.split(" ")
    return parts[2] if len(parts) >= 3 else None


def short_name(path: Path) -> str:
    parts = path.stem.split(" ")
    site = parts[2].replace("www.", "").removesuffix(".com").removesuffix(".jp")
    return f"{site}_{parts[1].replace('.', '')}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("images", nargs="+", type=Path)
    ap.add_argument("--out", type=Path, default=Path("test/cropped"))
    ap.add_argument("--contact", type=Path, help="검수용 대지(contact sheet) 경로")
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    tiles: list[np.ndarray] = []

    for path in args.images:
        site = site_of(path)
        if site is None:
            print(f"! 파일명에서 사이트를 못 읽었다: {path.name}", file=sys.stderr)
            continue
        img = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if img is None:
            print(f"! 읽을 수 없다: {path}", file=sys.stderr)
            continue

        bottom = viewer_bottom(img, site)
        band = img[:bottom]
        x0, y0, x1, y1 = content_bbox(band)
        crop = band[y0:y1, x0:x1]
        dst = args.out / f"{short_name(path)}.png"
        cv2.imwrite(str(dst), crop)
        print(
            f"{path.name}\n  {img.shape[1]}x{img.shape[0]} → 밴드 {band.shape[1]}x{bottom}"
            f" → 내용 {crop.shape[1]}x{crop.shape[0]} @({x0},{y0})  {dst}"
        )

        if args.contact:
            scale = min(620 / crop.shape[1], 440 / crop.shape[0])
            t = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            tile = np.full((460, 640, 3), 40, np.uint8)
            tile[: t.shape[0], : t.shape[1]] = t
            cv2.putText(tile, dst.stem, (4, 454), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1)
            tiles.append(tile)

    if args.contact and tiles:
        rows = [np.hstack(tiles[i : i + 2]) for i in range(0, len(tiles) - 1, 2)]
        if len(tiles) % 2:
            rows.append(np.hstack([tiles[-1], np.full((460, 640, 3), 40, np.uint8)]))
        args.contact.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(args.contact), np.vstack(rows))
        print(f"→ 대지: {args.contact}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
