"""돌고 있는 서비스에 페이지를 보내고 SSE 스트림을 눈으로 본다.

    # 터미널 1
    uv run mtl-service
    # 터미널 2
    uv run python scripts/send_page.py test/cropped/yanmaga_002528.png

이벤트마다 **요청 시작 이후 경과 시간**을 찍는다. 원문이 언제 뜨고 번역이 언제
채워지는지가 이 도구의 요점이다 — `timings` 만 봐서는 체감을 알 수 없다.

`--phash` 를 같게 주고 두 번 돌리면 캐시 적중 경로를 볼 수 있다. `--mode literal` 로
바꾸면 OCR 은 재사용하고 번역만 다시 도는 것을 확인할 수 있다 (§8.5).

**WSL 에서 `127.0.0.1` 은 Windows 루프백이 아니다.** 이 스크립트는 venv 의 Windows
파이썬으로 도니 그대로 붙는다. WSL 쪽 `curl` 로는 안 된다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import cv2
import requests


def normalized_jpeg(path: Path, quality: int = 88) -> bytes:
    """§5.4 정규화를 흉내낸다 — 클라이언트가 보낼 바이트와 같게 만든다."""
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        raise SystemExit(f"읽을 수 없다: {path}")
    h, w = img.shape[:2]
    short = min(h, w)
    scale = 1600 / short if short > 1600 else (1200 / short if short < 1200 else 1.0)
    if scale != 1.0:
        interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_LANCZOS4
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=interp)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise SystemExit("JPEG 인코딩 실패")
    return buf.tobytes()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("image", type=Path)
    ap.add_argument("--url", default="http://127.0.0.1:8788/read")
    ap.add_argument("--profile", default="yanmaga")
    ap.add_argument("--mode", default="natural", choices=["natural", "literal"])
    ap.add_argument("--phash", help="생략하면 파일 내용에서 만든다 (같은 파일 = 같은 해시)")
    ap.add_argument("--prev-phash", help="직전 페이지 phash. 번역 문맥으로 쓰인다 (§8.4)")
    ap.add_argument("--no-sfx", action="store_true", help="효과음 제외")
    ap.add_argument("--token", help="X-Auth-Token (auth_disabled = false 일 때)")
    args = ap.parse_args()

    payload = normalized_jpeg(args.image)
    # 파일 내용 해시를 phash 대신 쓴다. 진짜 phash 는 클라이언트(M3) 몫이고,
    # 여기서는 "같은 파일이면 같은 키" 만 성립하면 캐시 경로를 볼 수 있다.
    phash = args.phash or hashlib.sha256(payload).hexdigest()[:16]

    meta = {
        "phash": phash,
        "profile": args.profile,
        "mode": args.mode,
        "prev_page_phash": args.prev_phash,
        "include_sfx": not args.no_sfx,
    }
    headers = {"X-Auth-Token": args.token} if args.token else {}

    print(f"{args.image.name}  {len(payload) / 1024:.0f}KB  phash={phash}  mode={args.mode}")
    t0 = time.perf_counter()
    try:
        resp = requests.post(
            args.url,
            files={"image": (args.image.name, payload, "image/jpeg")},
            data={"meta": json.dumps(meta)},
            headers=headers,
            stream=True,
            timeout=180,
        )
    except requests.ConnectionError:
        print(f"서비스에 붙지 못했다: {args.url}\n  다른 터미널에서 `uv run mtl-service` 를 띄울 것.")
        return 1

    if resp.status_code != 200:
        print(f"HTTP {resp.status_code}  {resp.text[:300]}")
        return 1

    first_translation: int | None = None
    event = None
    for line in resp.iter_lines(decode_unicode=True):
        if not line:
            continue
        if line.startswith("event: "):
            event = line[7:]
            continue
        if not line.startswith("data: ") or event is None:
            continue
        data = json.loads(line[6:])
        el = int((time.perf_counter() - t0) * 1000)

        if event == "ocr":
            print(f"[{el:>6}ms] ocr          region {len(data['regions'])}개")
            for r in data["regions"]:
                flag = "" if r["is_bubble"] else "  (효과음/배경)"
                print(f"           {r['id']:>3}  {r['text'][:34]}{flag}")
        elif event == "translation":
            if first_translation is None:
                first_translation = el
            note = f"  [{data['note']}]" if data.get("note") else ""
            print(f"[{el:>6}ms] translation  {data['id']:>3}  {data['ko'][:34]}{note}")
        elif event == "done":
            t = data["timings"]
            print(
                f"[{el:>6}ms] done         검출 {t['detect']}ms · OCR {t['ocr']}ms · "
                f"번역 {t['translate']}ms"
            )
        else:
            print(f"[{el:>6}ms] {event:<12} {json.dumps(data, ensure_ascii=False)[:80]}")

    if first_translation is not None:
        print(f"\n첫 번역까지 {first_translation}ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
