"""모델 가중치를 내려받는다. DESIGN.md §11 M1 준비 단계.

    uv run python scripts/fetch_models.py

- comic-text-detector 가중치(`comictextdetector.pt`)를 `service.toml` 의
  `[models].detector_weights` 경로에 받는다.
- manga-ocr 가중치를 HuggingFace 캐시에 미리 받아둔다. 받지 않아도 첫 기동 시
  자동으로 받지만, 그때 3-5초가 아니라 1분 이상 걸린다.

둘 다 멱등하다. 이미 있고 해시가 맞으면 건너뛴다.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import tomllib
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# dmMaze/comic-text-detector 의 배포본. 원 저장소 릴리스는 사라졌고
# manga-image-translator 릴리스가 같은 파일을 계속 호스팅한다.
DETECTOR_URL = (
    "https://github.com/zyddnys/manga-image-translator/releases/download/"
    "beta-0.3/comictextdetector.pt"
)
DETECTOR_SHA256 = "1f90fa60aeeb1eb82e2ac1167a66bf139a8a61b8780acd351ead55268540cccb"

MANGA_OCR_REPO = "kha-white/manga-ocr-base"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, dest: Path) -> None:
    """진행률을 찍으며 임시 파일로 받고, 완료 후에만 제자리로 옮긴다."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "manga-live-reader/0.1"})
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 (고정 URL)
        total = int(resp.headers.get("Content-Length") or 0)
        got = 0
        with tmp.open("wb") as f:
            while chunk := resp.read(1 << 20):
                f.write(chunk)
                got += len(chunk)
                pct = f"{got * 100 / total:5.1f}%" if total else f"{got >> 20}MB"
                print(f"\r  {pct}  {got >> 20}/{total >> 20}MB", end="", flush=True)
    print()
    tmp.replace(dest)


def fetch_detector(dest: Path, *, force: bool) -> int:
    print(f"[detector] {dest}")
    if dest.exists() and not force:
        digest = sha256_of(dest)
        if digest == DETECTOR_SHA256:
            print("  이미 있음 (해시 일치). 건너뜀")
            return 0
        print(f"  해시 불일치 → 다시 받는다\n    있음: {digest}\n    기대: {DETECTOR_SHA256}")

    download(DETECTOR_URL, dest)
    digest = sha256_of(dest)
    if digest != DETECTOR_SHA256:
        print(
            f"  ! 받은 파일 해시가 기대값과 다르다\n"
            f"    받음: {digest}\n"
            f"    기대: {DETECTOR_SHA256}\n"
            f"  배포본이 교체됐을 수 있다. 확인 후 scripts/fetch_models.py 의 "
            f"DETECTOR_SHA256 을 갱신할 것.",
            file=sys.stderr,
        )
        return 1
    print("  완료 (해시 일치)")
    return 0


def fetch_manga_ocr() -> int:
    print(f"[manga-ocr] {MANGA_OCR_REPO}")
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print("  huggingface_hub 없음. 건너뜀 (첫 기동 시 자동으로 받는다)")
        return 0
    path = snapshot_download(MANGA_OCR_REPO)
    print(f"  완료 → {path}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true", help="이미 있어도 다시 받는다")
    ap.add_argument("--detector-only", action="store_true")
    args = ap.parse_args()

    with (ROOT / "service.toml").open("rb") as f:
        cfg = tomllib.load(f)
    dest = ROOT / cfg["models"]["detector_weights"]

    rc = fetch_detector(dest, force=args.force)
    if rc == 0 and not args.detector_only:
        rc = fetch_manga_ocr()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
