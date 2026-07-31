"""FastAPI 앱. DESIGN.md §4.

`POST /read` 는 `text/event-stream` 이다 (§4.1). 이벤트 순서:

    cached → ocr → translation* → done

`ocr` 을 번역보다 먼저 흘리는 것이 요점이다. 검출+OCR 은 0.3초인데 번역은 9초라,
한 방으로 돌려주면 9초 동안 화면에 아무것도 없다. 원문 핀(L1)을 먼저 띄우고 번역이
도착하는 대로 채운다.

**GPU 작업과 번역은 다른 자원이다.** 검출·OCR 은 상주 단일 워커에서, 번역은 네트워크
I/O 라 별도 스레드에서 돈다. 번역이 9초 걸리는 동안 GPU 워커는 다음 페이지를 처리할
수 있다.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import AsyncIterator, Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Any

import cv2
import numpy as np
import torch
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from mtl_shared.models import ReadMeta, ReadResult, Region, Timings

from . import detect, metrics, ocr, order, translate
from .cache import PageCache
from .config import Config, load

cfg: Config = load()

_cache = PageCache(
    cfg.cache.path,
    fuzzy_hamming=cfg.cache.fuzzy_hamming,
    retention_days=cfg.cache.retention_days,
)

#: 번역기는 **모델당 하나**를 들고 있는다. SDK 클라이언트가 커넥션 풀을 쥐고 있어
#: 매번 만들면 소켓이 샌다. 모델을 바꿔 가며 쓸 수 있어야 해서(설정에서 전환)
#: 하나가 아니라 모델별로 캐시한다. 키가 없는 모델은 계속 만들어지지 않고,
#: 번역 없이 OCR 만 흘린다 (M1 동작으로 자연스럽게 강등된다).
_translators: dict[str, translate.Translator] = {}
_translator_lock = asyncio.Lock()

# ---------------------------------------------------------------------------
# 같은 페이지를 동시에 두 번 번역하지 않는다
#
# 실측 로그에서 **같은 이미지를 3초 안에 네 번** 번역한 것이 나왔다 (한 번에 11초,
# 전부 돈이 나간다). 자동 감지와 손동작이 겹치거나 페이지를 빨리 넘기면 그렇게 된다.
#
# 먼저 온 것이 끝날 때까지 나머지는 기다렸다가 **그 결과를 캐시에서 받는다.**
# 지연은 어차피 첫 번째를 기다리는 것과 같고, 비용은 한 번만 든다.
# ---------------------------------------------------------------------------
_inflight: dict[tuple[str, str, str], asyncio.Event] = {}


#: 클라이언트가 아무 문자열이나 보내는 것을 그대로 쓰면 안 된다. 아는 것만 받는다.
ALLOWED_MODELS = (
    "claude-sonnet-5", "claude-opus-5", "claude-haiku-4-5", "gemini-3.6-flash", "gpt-5.2",
)
#: 확장이 보내는 특별한 값. 실제 이름은 `[api].local_model` 이 정한다.
LOCAL_MODEL = "local"


def resolve_model(requested: str | None) -> str:
    """요청이 고른 모델. 모르는 값이면 설정값으로 떨어진다."""
    if requested == LOCAL_MODEL and cfg.api.local_model and cfg.api.local_base_url:
        return cfg.api.local_model
    if requested and requested in ALLOWED_MODELS:
        return requested
    return cfg.api.model_quality


def base_url_for(model: str) -> str | None:
    """로컬 모델이면 엔드포인트, 아니면 None."""
    return cfg.api.local_base_url or None if model == cfg.api.local_model else None


#: 확장이 보낸 키로 만든 번역기를 몇 개까지 들고 있을지. 기기 몇 대면 충분하다.
_BYOK_CACHE_MAX = 8


def _cache_key(name: str, api_key: str | None) -> str:
    """캐시 키. **키 값 자체를 쓰지 않는다** — 키가 로그·예외에 섞여 나갈 수 있다.

    앞 16자만 써도 충돌은 실질적으로 없고(2^64), 원문은 복원되지 않는다.
    """
    if not api_key:
        return name
    return f"{name}|{hashlib.sha256(api_key.encode()).hexdigest()[:16]}"


async def get_translator(
    model: str | None = None, api_key: str | None = None
) -> translate.Translator | None:
    """번역기를 준다. 없으면 None (OCR 만 돌린다).

    확장이 키를 보내면(`X-Api-Key`) **키 해시까지 넣어 캐시한다.**

    처음에는 아예 캐시하지 않았다 — 모델 이름으로만 캐시하면 남의 키로 부르게
    되기 때문이다. 그런데 그러면 요청마다 SDK 클라이언트가 새로 생기고, 그 안의
    HTTP 커넥션 풀도 매번 새로 열린다. 키를 키의 일부로 넣으면 둘 다 만족한다.
    """
    name = resolve_model(model)
    key = _cache_key(name, api_key)
    got = _translators.get(key)
    if got is not None:
        return got
    async with _translator_lock:
        if key in _translators:
            return _translators[key]
        try:
            kwargs = {"effort": cfg.api.effort} if name.startswith("claude-") else {}
            _translators[key] = translate.get_translator(
                name, api_key=api_key, base_url=base_url_for(name), **kwargs
            )
        except Exception as exc:  # 키 없음 등
            print(f"[warn] {name} 번역기를 못 만들었다 — OCR 만 제공한다: {exc}")
            return None
        # 보내 온 키로 만든 것만 개수를 묶는다. 기기가 늘어도 무한정 쌓이지 않게.
        byok = [k for k in _translators if "|" in k]
        while len(byok) > _BYOK_CACHE_MAX:
            _translators.pop(byok.pop(0), None)
    return _translators[key]

# ---------------------------------------------------------------------------
# GPU 전용 상주 워커
#
# GPU 작업(워밍업 포함)은 **전부 이 스레드 하나에서** 돈다.
#
# - GPU 는 하나다. 요청을 동시에 처리해봐야 서로 밀어낼 뿐이고, VRAM 사용량만
#   예측 불가능해진다 (DESIGN.md §9.3 ComfyUI 공존).
# - 워밍업도 같은 워커에 태운다. 일회성 스레드에서 예열하면 예열 상태가
#   그 스레드의 수명에 묶일 여지가 있고, 이득도 없다.
# - 이벤트 루프를 막지 않으므로 긴 /read 처리 중에도 /health 는 즉답한다
#   (실측 2.9ms).
# ---------------------------------------------------------------------------

_gpu = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gpu")


async def on_gpu(fn: Callable[..., Any], *args: Any) -> Any:
    return await asyncio.get_running_loop().run_in_executor(_gpu, fn, *args)


#: 번역은 네트워크 I/O 다. GPU 워커를 막으면 안 되므로 별도 풀에서 돈다.
_net = ThreadPoolExecutor(max_workers=4, thread_name_prefix="net")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # 보존 기간이 지난 캐시를 치운다. **기동 때 한 번이면 충분하다** — 크론이나
    # 타이머를 걸 만큼 급한 일이 아니고(하루 이틀 늦게 지워져도 무해하다), 서비스는
    # 만화를 읽을 때마다 새로 뜬다. 실행 중 무한정 쌓이는 것만 막으면 된다.
    try:
        gone = _cache.purge_expired()
        if gone:
            print(f"만료 캐시 {gone}개 정리 (보존 {cfg.cache.retention_days}일)")
    except Exception as exc:  # 캐시 정리 실패로 서비스가 안 뜨면 안 된다
        print(f"[warn] 캐시 정리 실패: {exc}")

    if cfg.models.resident:
        # await 하지 않는다. 3-5초 동안 /health 가 막히면 클라이언트가 서비스를
        # 죽은 것으로 오인한다. 워커는 계속 살아 있으므로 예열이 유지된다.
        _gpu.submit(warm)
    yield
    _gpu.shutdown(wait=False)
    _net.shutdown(wait=False)
    _cache.close()


app = FastAPI(title="mtl-service", version="0.1.0", lifespan=lifespan)


# ---------------------------------------------------------------------------
# 인증 (DESIGN.md §4.4)
# ---------------------------------------------------------------------------


def require_token(x_auth_token: str | None = Header(default=None)) -> None:
    """공유 시크릿 검증.

    서비스는 Tailscale 인터페이스에만 바인딩되므로 이 토큰은 이중 방어다.
    실수로 다른 인터페이스에 열렸을 때 마지막 방어선이 된다.
    """
    if cfg.server.auth_disabled:
        return
    if not cfg.server.auth_token:
        raise HTTPException(500, "auth_token 이 설정되지 않았다. service.toml 확인")
    if x_auth_token != cfg.server.auth_token:
        raise HTTPException(401, "X-Auth-Token 불일치")


# ---------------------------------------------------------------------------
# 모델 상주 (DESIGN.md §9.3, §14)
# ---------------------------------------------------------------------------


def warm() -> dict[str, int]:
    """모델을 로드하고 **실제 추론 경로까지 한 번 태운다**.

    로드만 해두는 것으로는 부족하다. 검출기도 OCR 도 첫 추론에서 CUDA 커널
    자동튜닝 비용을 크게 낸다 (OCR 배치는 최대 24초). 그 값을 첫 페이지가
    치르면 §1 의 2초 예산이 무의미해진다.
    """
    t0 = time.perf_counter()
    detector = detect.get_detector(cfg)
    detector(np.zeros((1400, 1000, 3), np.uint8))
    t1 = time.perf_counter()
    ocr.warmup(cfg)
    t2 = time.perf_counter()
    return {
        "detector_ms": int((t1 - t0) * 1000),
        "ocr_ms": int((t2 - t1) * 1000),
        "total_ms": int((t2 - t0) * 1000),
    }


# ---------------------------------------------------------------------------
# 엔드포인트
# ---------------------------------------------------------------------------


@app.get("/health")
def health() -> dict[str, object]:
    """DESIGN.md §4.2. 클라이언트가 기동 시 호출해 모델 상태를 확인한다."""
    gpu = None
    vram = 0
    if torch.cuda.is_available():
        gpu = torch.cuda.get_device_name(0)
        vram = int(torch.cuda.memory_reserved(0) / 1e6)
    return {
        "status": "ok",
        "models_loaded": detect.is_loaded() and ocr.is_loaded(),
        "gpu": gpu,
        "vram_used_mb": vram,
    }


@app.post("/warmup", dependencies=[Depends(require_token)])
async def warmup() -> dict[str, int]:
    """DESIGN.md §4.3. 모델을 강제 로드한다. 응답까지 3-5초."""
    return await on_gpu(warm)


@app.post("/cache/purge", dependencies=[Depends(require_token)])
async def cache_purge(body: dict[str, Any] | None = None) -> dict[str, int]:
    """캐시를 지운다.

    `{"all": true}` → 전부.
    `{"phash": "...", "profile": "..."}` → 그 화면과 같은 것으로 보이는 행만.

    페이지 단위 지우기는 **조회와 같은 퍼지 기준**을 쓴다. 정확 일치만 지우면
    캡처가 매번 몇 비트씩 달라서 정작 보고 있는 페이지의 행이 안 지워진다.
    """
    body = body or {}
    if body.get("all"):
        return {"deleted": _cache.purge_all()}
    phash, profile = body.get("phash"), body.get("profile")
    if not phash or not profile:
        raise HTTPException(400, "phash 와 profile 이 필요하다 (또는 all=true)")
    return {"deleted": _cache.purge_near(str(phash), str(profile))}


def sse(event: str, data: object) -> str:
    """SSE 한 프레임. `data` 는 한 줄 JSON 이어야 한다 — 개행이 들어가면 프레임이 깨진다."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/read", dependencies=[Depends(require_token)])
async def read(
    image: UploadFile = File(...),
    meta: str = Form(...),
    x_api_key: str | None = Header(default=None),
) -> StreamingResponse:
    """DESIGN.md §4.1. 좌표는 전부 **전송된 이미지 좌표계** 기준으로 돌려준다.

    입력 검증은 스트림을 열기 **전에** 끝낸다. 잘못된 요청에 200 + `event: error` 를
    주면 클라이언트가 성공으로 오인하기 쉽다 — 400 으로 즉답하는 게 맞다.
    """
    try:
        parsed = ReadMeta.from_dict(json.loads(meta))
    except (json.JSONDecodeError, TypeError) as exc:
        raise HTTPException(400, f"meta 파싱 실패: {exc}") from exc

    payload = await image.read()
    img = cv2.imdecode(np.frombuffer(payload, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(400, "이미지 디코드 실패")

    return StreamingResponse(
        _read_events(img, parsed, len(payload), x_api_key),
        media_type="text/event-stream",
        # 프록시가 버퍼링하면 스트리밍이 의미를 잃는다. Tailscale 직결이라 지금은
        # 프록시가 없지만, 나중에 하나라도 끼면 조용히 망가진다.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _read_events(
    img: np.ndarray, parsed: ReadMeta, upload_bytes: int, api_key: str | None = None
):
    """`cached` → `ocr` → `translation`* → `done` 순서로 흘린다."""
    t_start = time.perf_counter()
    try:
        # `refresh` 는 "이 페이지를 캐시 무시하고 다시" 다. 조회를 건너뛰는 것만으로는
        # 부족하다 — 낡은 행을 안 치우면 다음 조회에서 그게 다시 이긴다. 새 결과는
        # 저장한다 (그래야 다음부터 캐시가 먹는다). `no_cache` 와 다른 점이다.
        if parsed.refresh and not parsed.no_cache:
            _cache.purge_near(parsed.phash, parsed.profile)
        skip_read = parsed.no_cache or parsed.refresh

        # **캐시 키에 모델을 넣는다.** 안 넣으면 모델을 바꿔도 옛 모델의 번역이
        # 그대로 나온다 — 바꾼 보람이 없고, 비교도 못 한다.
        #
        # `mode` 칸에 함께 실어 스키마를 안 바꾼다. 한 phash 행에는 번역이 하나만
        # 들어가므로, 모델을 오가면 그때마다 다시 번역한다 (그게 맞다 — 다른 모델의
        # 결과를 보여 달라고 한 것이니).
        model_name = resolve_model(parsed.model)
        cache_mode = f"{parsed.mode}|{model_name}"

        size = (int(img.shape[1]), int(img.shape[0]))
        hit = None if skip_read else _cache.get(parsed.phash, parsed.profile, cache_mode, size)
        yield sse("cached", {"hit": hit is not None, "fuzzy": bool(hit and hit.fuzzy)})

        if hit is not None:
            regions = hit.ocr
            timings = Timings(detect=0, mask=0, order=0, ocr=0, translate=0, total=0)
        else:
            result = await on_gpu(pipeline, img, parsed)
            regions = [r.to_dict() for r in result.regions]
            timings = result.timings
            # `no_cache` 는 **읽지도 쓰지도 않는다.**
            #
            # 이 플래그는 손으로 고른 읽기(영역 지정·다시 읽기)에 붙는데, 그건
            # 페이지의 **일부 크롭**이다. 부분 크롭은 대부분 흰 바탕이라 지각 해시가
            # 밋밋해서, 서로 다른 말풍선끼리도 해밍 거리가 가까워진다 — 실측 캐시에서
            # 다른 두 부분 영역이 거리 14 로 붙어 있었다(문턱 20). 페이지 전체는
            # 서로 100 이상 벌어지므로 안전하지만 부분 크롭은 아니다.
            #
            # 게다가 같은 크롭을 다시 요청할 일도 없다. 쓰면 위험만 늘고 이득이 없다.
            if not parsed.no_cache:
                _cache.put_ocr(parsed.phash, parsed.profile, regions, size, parsed.viewer)

        # **캐시에서 왔으면 기준 사각형을 같이 준다.** 클라이언트가 그것으로 지금
        # 화면에 맞게 환산한다 (다시 번역하지 않는다). 새로 잰 좌표는 이번 캡처의
        # 좌표계라 기준이 필요 없다.
        cached_viewer = hit.viewer if (hit and hit.ocr and not skip_read) else None
        yield sse("ocr", {"regions": regions, "cached_viewer": cached_viewer})

        # **번역할 것만 남긴다.** 「영역」·「다시 읽기」는 검출기에 문맥을 주려고 고른
        # 것보다 3배 넓게 잘라 보낸다. 예전에는 그 안을 전부 번역하고 클라이언트가
        # 범위 밖을 버렸다 — **버릴 것에 돈을 냈다.**
        #
        # 중심으로 판정한다 (클라이언트의 `inClip` 과 같은 규칙) — 경계에 걸친
        # 말풍선을 버리면 정작 고른 것이 빠진다.
        to_translate = regions
        if parsed.clip:
            cx, cy, cw, ch = parsed.clip
            to_translate = [
                r
                for r in regions
                if cx <= r["bbox"][0] + r["bbox"][2] / 2 <= cx + cw
                and cy <= r["bbox"][1] + r["bbox"][3] / 2 <= cy + ch
            ]

        # -- 번역 -----------------------------------------------------------
        translate_ms = 0
        collected: list[dict[str, object]] = []
        usage: dict[str, object] | None = None
        if hit is not None and hit.translation is not None:
            for tr in hit.translation:
                yield sse("translation", tr)
        elif to_translate:
            key = (parsed.phash, parsed.profile, cache_mode)

            # 같은 페이지가 이미 번역 중이면 기다렸다가 그 결과를 쓴다.
            waiting = _inflight.get(key)
            if waiting is not None and not parsed.no_cache:
                try:
                    await asyncio.wait_for(waiting.wait(), timeout=90)
                except (TimeoutError, asyncio.TimeoutError):
                    pass
                again = _cache.get(parsed.phash, parsed.profile, cache_mode, size)
                if again is not None and again.translation is not None:
                    for tr in again.translation:
                        yield sse("translation", tr)
                    timings.translate = 0
                    timings.total = int((time.perf_counter() - t_start) * 1000)
                    yield sse("done", {"reordered": False, "timings": timings.to_dict()})
                    return

            done_evt = asyncio.Event()
            _inflight[key] = done_evt
            t0 = time.perf_counter()
            try:
                async for kind, payload in _translate_stream(to_translate, parsed, api_key):
                    if kind == "usage":
                        usage = payload
                        continue
                    if kind == "error":
                        # 번역만 실패한 것이다. 원문은 이미 흘렸으므로 스트림을 죽이지
                        # 않고 알리기만 한다 — 클라이언트는 L1 핀으로 원문을 읽는다.
                        yield sse("error", {"stage": "translate", "message": payload})
                        break
                    collected.append(payload)
                    yield sse("translation", payload)
            finally:
                # **`finally` 여야 한다.** 클라이언트가 스트림을 끊으면(페이지를 넘기거나
                # 새 읽기가 시작되면) 위 루프가 중단되는데, 예전에는 그때
                # `put_translation` 이 영영 안 불렸다. 그 페이지는 OCR 만 캐시되고
                # 번역은 **매번 새로** — 실측에서 같은 이미지를 3초에 네 번 번역한
                # 원인이다. 받은 데까지라도 저장한다.
                if collected and not parsed.no_cache:
                    _cache.put_translation(parsed.phash, cache_mode, collected)
                _inflight.pop(key, None)
                done_evt.set()
            translate_ms = int((time.perf_counter() - t0) * 1000)

        timings.translate = translate_ms
        timings.total = int((time.perf_counter() - t_start) * 1000)
        yield sse("done", {"reordered": False, "timings": timings.to_dict()})

        metrics.record(
            cfg.metrics.jsonl_path,
            phash=parsed.phash,
            profile=parsed.profile,
            regions=len(regions),
            upload_bytes=upload_bytes,
            cached=hit is not None,
            translated=len(collected),
            # **왜 캐시를 안 썼는지 남긴다.** 같은 화면·같은 크기인데 `cached=false`
            # 가 찍히는 일이 있었는데, 그 원인이 요청 쪽 플래그인지 조회 실패인지
            # 로그만으로는 갈라지지 않았다.
            no_cache=parsed.no_cache,
            refresh=parsed.refresh,
            has_viewer=parsed.viewer is not None,
            **(
                {
                    "tokens_in": usage["in"],
                    "tokens_out": usage["out"],
                    "tokens_cached": usage["cached"],
                    "model": usage["model"],
                }
                if usage
                else {}
            ),
            **timings.to_dict(),
        )
    except Exception as exc:  # 여기까지 오면 스트림을 닫는다 (§4.1)
        yield sse("error", {"stage": "read", "message": f"{type(exc).__name__}: {exc}"})


async def _translate_stream(
    regions: list[dict[str, object]], parsed: ReadMeta, api_key: str | None = None
):
    """번역 스트림을 async 로 중계한다. `("region", {...})` 또는 `("error", "메시지")`.

    SDK 스트림은 **동기 제너레이터**다. 이벤트 루프에서 직접 소진하면 9초 동안 서버
    전체가 멈춘다. 별도 스레드에서 돌리고 큐로 건네받는다 — 스레드가 `put` 할 때는
    `call_soon_threadsafe` 를 써야 한다 (asyncio.Queue 는 스레드 안전하지 않다).
    """
    translator = await get_translator(parsed.model, api_key)
    if translator is None:
        yield "error", "번역기가 없다 (API 키 미설정)"
        return

    previous: list[str] = []
    if parsed.prev_page_phash:
        previous = _cache.previous_texts(parsed.prev_page_phash, parsed.profile)

    stubs = [
        Region(
            id=int(r["id"]),
            text=str(r["text"]),
            bbox=tuple(r["bbox"]),  # type: ignore[arg-type]
            is_bubble=bool(r["is_bubble"]),
            vertical=bool(r["vertical"]),
            mask_rle="",
            fill_rgb=None,
            fill_confidence=0.0,
        )
        for r in regions
    ]

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    _DONE = object()

    def pump() -> None:
        try:
            stream = translator.translate_stream(stubs, parsed.mode, previous)
            for tr in stream:
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    (
                        "region",
                        {
                            "id": tr.id,
                            "ko": tr.ko,
                            # 클라이언트는 이걸로 효과음·잡문을 숨긴다. `is_bubble` 은
                            # 스크린톤 배경에서 틀리므로 필터로 쓰면 안 된다 (DEVLOG).
                            "kind": tr.kind,
                            "note": tr.note or None,
                        },
                    ),
                )
            # 스트림을 다 소진해야 토큰 수가 채워진다. **비용이 보여야 한다** —
            # 안 보이면 오늘처럼 모르는 사이에 샌다.
            res = getattr(stream, "result", None)
            if res is not None:
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    (
                        "usage",
                        {
                            "in": res.input_tokens,
                            "out": res.output_tokens,
                            "cached": res.cached_tokens,
                            "model": res.model,
                        },
                    ),
                )
        except Exception as exc:
            loop.call_soon_threadsafe(
                queue.put_nowait, ("error", f"{type(exc).__name__}: {exc}")
            )
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, _DONE)

    _net.submit(pump)
    while True:
        item = await queue.get()
        if item is _DONE:
            return
        yield item


def pipeline(img: np.ndarray, parsed: ReadMeta) -> ReadResult:
    """검출 → 정렬 → OCR. **GPU 워커 스레드에서만** 호출할 것."""
    t0 = time.perf_counter()
    detected = detect.detect(img, cfg)

    t1 = time.perf_counter()
    ordered = order.sort_regions(
        detected.regions, (img.shape[1], img.shape[0]), cfg.order.min_gap_ratio
    )
    if not parsed.include_sfx:
        # is_bubble 값 자체는 응답에 그대로 싣는다 (§8.1). 여기서는 필터만.
        ordered = [r for r in ordered if r.is_bubble]
    order.assign_ids(ordered)
    t2 = time.perf_counter()

    result_regions = ocr.run(img, ordered, cfg)
    # OCR 에서 버려진 region 이 있으면 번호에 구멍이 난다. 다시 매긴다.
    order.assign_ids(result_regions.regions)

    total_ms = int((time.perf_counter() - t0) * 1000)
    return ReadResult(
        regions=result_regions.regions,
        translations=[],  # M2
        reordered=False,
        cached=False,
        timings=Timings(
            detect=detected.detect_ms,
            mask=detected.mask_ms,
            order=int((t2 - t1) * 1000),
            ocr=result_regions.ocr_ms,
            translate=0,
            total=total_ms,
        ),
    )
