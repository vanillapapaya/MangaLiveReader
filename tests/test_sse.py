"""`/read` SSE 스트림. DESIGN.md §4.1.

캐시 적중 경로만 검증한다 — 그 경로는 GPU 도 네트워크도 타지 않으므로 CI 에서 돈다.
검출·OCR·번역 자체는 각각 `scripts/debug_page.py` 와 `scripts/ab_translate.py` 몫이다.

여기서 지키려는 것은 **와이어 규약**이다: 이벤트 이름과 순서, 프레임 형식, 그리고
번역이 실패해도 원문 이벤트는 이미 나갔다는 것. 클라이언트(M3)가 이 규약에 붙는다.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi", reason="fastapi 미설치")

from mtl_service import app as app_module  # noqa: E402
from mtl_service.cache import PageCache  # noqa: E402

OCR_ROWS = [
    {
        "id": 1,
        "text": "なんだこれは",
        "bbox": [10, 20, 30, 40],
        "is_bubble": True,
        "vertical": True,
        "mask_rle": "",
        "fill_rgb": None,
        "fill_confidence": 0.9,
    }
]
KO_ROWS = [{"id": 1, "ko": "이게 뭐야", "note": None}]


@pytest.fixture
def client(tmp_path, monkeypatch):
    """모듈 전역 캐시를 임시 DB 로 갈아끼운다. 실제 캐시를 더럽히지 않기 위해서다."""
    from fastapi.testclient import TestClient

    cache = PageCache(tmp_path / "t.sqlite3", fuzzy_hamming=3, retention_days=90)
    monkeypatch.setattr(app_module, "_cache", cache)
    # **개인 설정이 테스트에 새어 들어오지 않게 한다.** `app.cfg` 는 import 시점에
    # `service.toml` + `service.local.toml` 을 읽는다. 개발자가 자기 기계에서 인증을
    # 켜 두면 여기 요청이 전부 401 이 되어, 라우팅 테스트가 인증 테스트로 변한다.
    # (실제로 그렇게 깨졌다.) 인증은 아래 전용 테스트에서 따로 본다.
    monkeypatch.setattr(app_module.cfg.server, "auth_disabled", True)
    # **`with` 를 쓰지 않는다.** 컨텍스트 매니저로 쓰면 lifespan 이 돌아 GPU 예열이
    # 시작되고(모델 로드 수 초), 종료 시 모듈 전역 `_gpu` 가 닫혀서 다음 테스트가
    # "cannot schedule new futures after shutdown" 으로 죽는다. 라우팅만 볼 것이므로
    # lifespan 은 태우지 않는다.
    yield TestClient(app_module.app), cache
    cache.close()


def parse_sse(body: str) -> list[tuple[str, dict]]:
    """`event:`/`data:` 쌍을 순서대로 뽑는다."""
    out = []
    event = None
    for line in body.splitlines():
        if line.startswith("event: "):
            event = line[7:]
        elif line.startswith("data: ") and event:
            out.append((event, json.loads(line[6:])))
            event = None
    return out


#: 캐시 키의 `mode` 에는 **모델이 함께 실린다** (`app.py` 의 `cache_mode`).
#: 모델을 바꿔도 옛 모델의 번역이 나오면 안 되기 때문이다. 테스트도 같은 규칙으로
#: 심어야 적중한다 — 여기가 어긋나면 "캐시가 왜 안 맞지" 로 시간을 버린다.
def cache_mode(mode: str = "natural") -> str:
    from mtl_service.app import cfg, resolve_model

    return f"{mode}|{resolve_model(None)}"


def post(client, phash: str, mode: str = "natural", prev: str | None = None):
    return client.post(
        "/read",
        files={"image": ("p.jpg", _tiny_jpeg(), "image/jpeg")},
        data={
            "meta": json.dumps(
                {
                    "phash": phash,
                    "profile": "jumpplus",
                    "mode": mode,
                    "prev_page_phash": prev,
                    "include_sfx": True,
                }
            )
        },
    )


def _tiny_jpeg() -> bytes:
    import cv2
    import numpy as np

    ok, buf = cv2.imencode(".jpg", np.zeros((8, 8, 3), np.uint8))
    assert ok
    return buf.tobytes()


# ---------------------------------------------------------------------------


def test_캐시_적중이면_GPU_없이_전부_흘린다(client) -> None:
    c, cache = client
    cache.put_ocr("a" * 16, "jumpplus", OCR_ROWS)
    cache.put_translation("a" * 16, cache_mode(), KO_ROWS)

    events = parse_sse(post(c, "a" * 16).text)
    names = [e for e, _ in events]
    assert names == ["cached", "ocr", "translation", "done"]

    assert events[0][1]["hit"] is True
    assert events[1][1]["regions"] == OCR_ROWS
    assert events[2][1]["ko"] == "이게 뭐야"


def test_원문이_번역보다_먼저_나간다(client) -> None:
    """§4.1 의 핵심. 번역이 9초 걸려도 원문 핀은 먼저 떠야 한다."""
    c, cache = client
    cache.put_ocr("b" * 16, "jumpplus", OCR_ROWS)
    cache.put_translation("b" * 16, cache_mode(), KO_ROWS)

    names = [e for e, _ in parse_sse(post(c, "b" * 16).text)]
    assert names.index("ocr") < names.index("translation")


def test_번역_실패해도_원문은_나가고_done_으로_끝난다(client, monkeypatch) -> None:
    """번역기가 없거나 API 가 죽어도 스트림을 죽이지 않는다."""
    c, cache = client
    cache.put_ocr("c" * 16, "jumpplus", OCR_ROWS)  # 번역은 없음

    # `get_translator(model, api_key)` 로 바뀌었다 (모델 선택, 그다음 확장이 보내는
    # 키). 인자를 안 받으면 TypeError 가 나고, 그건 번역 실패가 아니라 스트림 전체를
    # 죽여 `done` 이 안 나간다. 이 테스트가 두 번 다 그것을 잡았다.
    async def no_translator(model=None, api_key=None):
        return None

    monkeypatch.setattr(app_module, "get_translator", no_translator)

    events = parse_sse(post(c, "c" * 16).text)
    names = [e for e, _ in events]
    assert "ocr" in names
    assert "error" in names
    assert names[-1] == "done", "에러가 나도 done 으로 닫아야 클라이언트가 상태를 정리한다"
    err = next(d for e, d in events if e == "error")
    assert err["stage"] == "translate"


def test_모드가_다르면_번역을_다시_한다(client, monkeypatch) -> None:
    """`natural` 캐시가 있어도 `literal` 요청이면 번역 이벤트는 새로 만들어야 한다."""
    c, cache = client
    cache.put_ocr("d" * 16, "jumpplus", OCR_ROWS)
    cache.put_translation("d" * 16, cache_mode(), KO_ROWS)

    called: list[str] = []

    async def fake(regions, parsed, *_):
        called.append(parsed.mode)
        yield "region", {"id": 1, "ko": "직역문", "note": None}

    monkeypatch.setattr(app_module, "_translate_stream", fake)

    events = parse_sse(post(c, "d" * 16, mode="literal").text)
    assert called == ["literal"], "OCR 은 재사용하되 번역은 다시 돌려야 한다"
    tr = next(d for e, d in events if e == "translation")
    assert tr["ko"] == "직역문"


def test_잘못된_meta_는_400(client) -> None:
    """스트림을 열기 전에 거절한다. 200 + error 이벤트면 성공으로 오인된다."""
    c, _ = client
    r = c.post(
        "/read",
        files={"image": ("p.jpg", _tiny_jpeg(), "image/jpeg")},
        data={"meta": "{깨진 json"},
    )
    assert r.status_code == 400


def test_번역이_도착하는_대로_흘러간다(client, monkeypatch) -> None:
    """스트리밍의 요점 — 마지막 말풍선을 기다리지 않고 완성된 것부터 내보낸다."""
    c, cache = client
    cache.put_ocr("e" * 16, "jumpplus", OCR_ROWS)

    async def three(regions, parsed, *_):
        for i in (1, 2, 3):
            yield "region", {"id": i, "ko": f"번역{i}", "note": None}

    monkeypatch.setattr(app_module, "_translate_stream", three)

    events = parse_sse(post(c, "e" * 16).text)
    trs = [d for e, d in events if e == "translation"]
    assert [t["id"] for t in trs] == [1, 2, 3]
    assert [e for e, _ in events][-1] == "done"

    # 부분 결과라도 캐시에 남아야 다음 열람이 빠르다
    hit = cache.get("e" * 16, "jumpplus", cache_mode())
    assert hit is not None and hit.translation is not None
    assert len(hit.translation) == 3


def test_중간에_끊겨도_받은_만큼은_캐시한다(client, monkeypatch) -> None:
    c, cache = client
    cache.put_ocr("f" * 16, "jumpplus", OCR_ROWS)

    async def partial(regions, parsed, *_):
        yield "region", {"id": 1, "ko": "첫번째", "note": None}
        yield "error", "연결 끊김"

    monkeypatch.setattr(app_module, "_translate_stream", partial)

    events = parse_sse(post(c, "f" * 16).text)
    names = [e for e, _ in events]
    assert names == ["cached", "ocr", "translation", "error", "done"]
    hit = cache.get("f" * 16, "jumpplus", cache_mode())
    assert hit is not None and hit.translation == [
        {"id": 1, "ko": "첫번째", "note": None}
    ]


def test_sse_프레임_형식() -> None:
    frame = app_module.sse("ocr", {"a": 1})
    assert frame == 'event: ocr\ndata: {"a": 1}\n\n'
    # data 는 반드시 한 줄이어야 한다 — 개행이 섞이면 프레임이 쪼개진다
    assert app_module.sse("x", {"t": "여러\n줄"}).count("\n") == 3


def test_인증을_켜면_토큰_없이는_막힌다(client, monkeypatch) -> None:
    """개인 설정이 새어 들어와 테스트가 깨진 적이 있다. 그 동작을 여기서 못 박는다."""
    c, _ = client
    monkeypatch.setattr(app_module.cfg.server, "auth_disabled", False)
    monkeypatch.setattr(app_module.cfg.server, "auth_token", "비밀")

    assert post(c, "a" * 16).status_code == 401
    assert c.get("/health").status_code == 200, "상태 확인은 토큰 없이 된다"
