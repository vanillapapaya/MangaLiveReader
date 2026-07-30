"""페이지 캐시. GPU·모델·네트워크 없이 돈다.

캐시의 핵심 성질은 두 가지다 — **OCR 과 번역이 분리 저장되는가**(모드를 바꿔도 OCR 을
재실행하지 않아야 한다), 그리고 **근사 매칭이 스크롤 흔들림을 흡수하는가**.
"""

from __future__ import annotations

import time

import pytest

from mtl_service.cache import PageCache, hamming

OCR = [{"id": 1, "text": "なんだこれは", "is_bubble": True}]
KO = [{"id": 1, "ko": "이게 뭐야", "note": ""}]


@pytest.fixture
def cache(tmp_path):
    c = PageCache(tmp_path / "pages.sqlite3", fuzzy_hamming=3, retention_days=90)
    yield c
    c.close()


def test_없으면_None(cache) -> None:
    assert cache.get("0" * 16, "jumpplus", "natural") is None


def test_ocr_만_넣으면_번역은_None(cache) -> None:
    """정상 상태다 — 검출은 끝났고 번역만 남은 경우."""
    cache.put_ocr("a" * 16, "jumpplus", OCR)
    hit = cache.get("a" * 16, "jumpplus", "natural")
    assert hit is not None
    assert hit.ocr == OCR
    assert hit.translation is None
    assert not hit.fuzzy


def test_모드가_다르면_OCR_만_준다(cache) -> None:
    """`natural` 로 읽은 페이지를 `literal` 로 다시 볼 때 OCR 을 재실행하지 않는다."""
    cache.put_ocr("b" * 16, "jumpplus", OCR)
    cache.put_translation("b" * 16, "natural", KO)

    same = cache.get("b" * 16, "jumpplus", "natural")
    assert same is not None and same.translation == KO

    other = cache.get("b" * 16, "jumpplus", "literal")
    assert other is not None
    assert other.ocr == OCR, "OCR 은 모드와 무관하게 재사용해야 한다"
    assert other.translation is None


def test_OCR_갱신이_번역을_지우지_않는다(cache) -> None:
    cache.put_ocr("c" * 16, "jumpplus", OCR)
    cache.put_translation("c" * 16, "natural", KO)
    cache.put_ocr("c" * 16, "jumpplus", [{"id": 1, "text": "갱신됨"}])
    hit = cache.get("c" * 16, "jumpplus", "natural")
    assert hit is not None and hit.translation == KO


# ---------------------------------------------------------------------------
# 근사 매칭
# ---------------------------------------------------------------------------


def test_해밍_거리() -> None:
    assert hamming("00", "00") == 0
    assert hamming("00", "01") == 1
    assert hamming("00", "ff") == 8
    # 길이가 다르면 비교 불가 — 우연히 가깝게 나오면 안 된다
    assert hamming("00", "0000") > 64


def test_1비트_차이는_같은_페이지로_본다(cache) -> None:
    """스크롤이 몇 px 어긋나면 phash 하위 비트가 흔들린다."""
    cache.put_ocr("f0f0f0f0f0f0f0f0", "jumpplus", OCR)
    hit = cache.get("f0f0f0f0f0f0f0f1", "jumpplus", "natural")
    assert hit is not None
    assert hit.fuzzy
    assert hit.phash == "f0f0f0f0f0f0f0f0"


def test_문턱을_넘으면_남남이다(cache) -> None:
    cache.put_ocr("0000000000000000", "jumpplus", OCR)
    assert cache.get("00000000000000ff", "jumpplus", "natural") is None


def test_프로필이_다르면_안_섞인다(cache) -> None:
    """사이트가 다르면 같은 해시라도 다른 페이지다."""
    cache.put_ocr("abcdefabcdefabcd", "jumpplus", OCR)
    assert cache.get("abcdefabcdefabce", "yanmaga", "natural") is None


# ---------------------------------------------------------------------------
# 문맥 · 정리
# ---------------------------------------------------------------------------


def test_직전_페이지_원문(cache) -> None:
    cache.put_ocr(
        "d" * 16, "jumpplus", [{"id": i, "text": f"대사{i}"} for i in range(1, 15)]
    )
    texts = cache.previous_texts("d" * 16, "jumpplus", limit=10)
    assert len(texts) == 10, "최대 10개로 자른다 (§8.4)"
    assert texts[0] == "대사1"


def test_보존기간_지난_항목_정리(tmp_path) -> None:
    c = PageCache(tmp_path / "p.sqlite3", retention_days=90)
    c.put_ocr("e" * 16, "jumpplus", OCR)
    # 91일 전으로 되돌린다
    old = int(time.time()) - 91 * 86400
    with c._lock:
        c._db.execute("UPDATE page_cache SET created_at = ?", (old,))
        c._db.commit()
    assert c.purge_expired() == 1
    assert c.get("e" * 16, "jumpplus", "natural") is None
    c.close()


def test_통계(cache) -> None:
    cache.put_ocr("1" * 16, "jumpplus", OCR)
    cache.put_ocr("2" * 16, "jumpplus", OCR)
    cache.put_translation("1" * 16, "natural", KO)
    assert cache.stats() == {"pages": 2, "translated": 1}


def test_캡처_크기가_다르면_캐시를_안_쓴다(cache) -> None:
    """저장된 bbox 는 그 캡처의 좌표계에 묶여 있다.

    phash 는 퍼지 매칭이라 크기가 다른 캡처도 같은 행에 붙는다. 그대로 돌려주면
    박스가 엉뚱한 자리에 그려진다 — 자동 번역에서 실제로 났다 (같은 행에 298,920
    바이트와 409,896바이트 캡처가 붙었다).
    """
    cache.put_ocr("a" * 16, "jumpplus", OCR, (1200, 1800))

    assert cache.get("a" * 16, "jumpplus", "natural", (1200, 1800)) is not None
    assert cache.get("a" * 16, "jumpplus", "natural", (1400, 2100)) is None, (
        "크기가 다르면 없는 것으로 쳐야 한다"
    )
    assert cache.get("a" * 16, "jumpplus", "natural") is not None, (
        "크기를 안 주면 예전처럼 그냥 준다"
    )


def test_크기를_모르는_옛_행은_그대로_쓴다(cache) -> None:
    """예전 DB 에는 크기 칸이 없다. 지우는 것보다 한 번 어긋나는 편이 낫고,
    다음 저장에서 값이 채워진다."""
    cache.put_ocr("b" * 16, "jumpplus", OCR)  # 크기 없이 저장 (옛 동작)

    assert cache.get("b" * 16, "jumpplus", "natural", (1200, 1800)) is not None
