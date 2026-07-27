"""페이지 캐시. DESIGN.md §8.5.

**OCR 과 번역을 분리 저장한다.** `natural` 로 읽은 페이지를 나중에 `literal` 로 다시
볼 때 검출·OCR 을 재실행하지 않기 위해서다. 그래서 `put_ocr` 과 `put_translation` 이
따로 있고, 번역만 없는 상태(= OCR 캐시 적중, 번역 미스)가 정상 상태다.

이미지는 저장하지 않는다. phash 만 키로 쓴다.

**근사 매칭.** 뷰어를 스크롤하면 같은 페이지라도 캡처가 몇 px 어긋나 phash 가 달라진다.
정확 일치가 없으면 해밍 거리 `fuzzy_hamming` 이내를 찾는다. phash 는 64비트라
정수로 바꿔 XOR + popcount 로 잰다 — 행 수가 수천 단위라 전수 조회로 충분하다.

`sqlite3` 연결 하나를 락으로 감싼다. GPU 작업은 상주 단일 워커에서 돌고(§14) 캐시
접근은 그보다 훨씬 가볍다 — 연결 풀을 만들 이유가 없다.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS page_cache (
    phash       TEXT PRIMARY KEY,
    profile     TEXT,
    ocr_json    TEXT,
    trans_json  TEXT,
    mode        TEXT,
    created_at  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_created ON page_cache(created_at);
"""


@dataclass(slots=True)
class CacheHit:
    phash: str
    ocr: list[dict[str, Any]]
    #: 번역이 아직 없거나 다른 모드로 저장돼 있으면 None.
    translation: list[dict[str, Any]] | None
    #: 정확 일치가 아니라 근사 매칭이었는가. 계측·디버깅용.
    fuzzy: bool


def hamming(a: str, b: str) -> int:
    """16진 phash 두 개의 해밍 거리. 길이가 다르면 비교 불가로 보고 크게 준다."""
    if len(a) != len(b):
        return 1 << 30
    try:
        return bin(int(a, 16) ^ int(b, 16)).count("1")
    except ValueError:
        return 1 << 30


class PageCache:
    def __init__(self, path: Path, fuzzy_hamming: int = 3, retention_days: int = 90) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fuzzy = fuzzy_hamming
        self._retention = retention_days
        self._lock = threading.Lock()
        # 워커 스레드와 요청 핸들러가 같은 연결을 쓴다. 직렬화는 _lock 이 맡는다.
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.executescript(_SCHEMA)
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # -- 조회 --------------------------------------------------------------

    def get(self, phash: str, profile: str, mode: str) -> CacheHit | None:
        """`phash` 에 해당하는 캐시. 없으면 None.

        `mode` 가 저장된 것과 다르면 OCR 만 살리고 번역은 None 으로 준다 —
        그래야 호출자가 검출·OCR 을 건너뛰고 번역만 다시 돌린다.
        """
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM page_cache WHERE phash = ?", (phash,)
            ).fetchone()
            fuzzy = False
            if row is None:
                row = self._nearest(phash, profile)
                fuzzy = row is not None

        if row is None:
            return None
        return CacheHit(
            phash=row["phash"],
            ocr=json.loads(row["ocr_json"]) if row["ocr_json"] else [],
            translation=(
                json.loads(row["trans_json"])
                if row["trans_json"] and row["mode"] == mode
                else None
            ),
            fuzzy=fuzzy,
        )

    def _nearest(self, phash: str, profile: str) -> sqlite3.Row | None:
        """해밍 거리 `fuzzy` 이내에서 가장 가까운 행. 호출자가 락을 쥐고 있어야 한다.

        같은 프로필 안에서만 찾는다. 사이트가 다르면 같은 해시라도 다른 페이지다.
        """
        best, best_d = None, self._fuzzy + 1
        for row in self._db.execute(
            "SELECT * FROM page_cache WHERE profile = ?", (profile,)
        ):
            d = hamming(phash, row["phash"])
            if d < best_d:
                best, best_d = row, d
        return best

    # -- 저장 --------------------------------------------------------------

    def put_ocr(self, phash: str, profile: str, ocr: list[dict[str, Any]]) -> None:
        """OCR 결과를 넣는다. 이미 있으면 번역은 건드리지 않고 OCR 만 갱신한다."""
        with self._lock:
            self._db.execute(
                """
                INSERT INTO page_cache (phash, profile, ocr_json, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(phash) DO UPDATE SET
                    ocr_json = excluded.ocr_json,
                    profile  = excluded.profile
                """,
                (phash, profile, json.dumps(ocr, ensure_ascii=False), int(time.time())),
            )
            self._db.commit()

    def put_translation(
        self, phash: str, mode: str, translation: list[dict[str, Any]]
    ) -> None:
        """번역 결과를 넣는다. OCR 행이 먼저 있어야 한다."""
        with self._lock:
            self._db.execute(
                "UPDATE page_cache SET trans_json = ?, mode = ? WHERE phash = ?",
                (json.dumps(translation, ensure_ascii=False), mode, phash),
            )
            self._db.commit()

    # -- 문맥 --------------------------------------------------------------

    def previous_texts(self, phash: str, profile: str, limit: int = 10) -> list[str]:
        """직전 페이지 원문. 번역 문맥으로 쓴다 (§8.4).

        만화 대사는 페이지를 넘어 이어진다. 주어를 생략한 문장의 화자를 직전 문맥
        없이는 놓친다. 클라이언트가 `prev_page_phash` 를 보내면 여기서 꺼낸다.
        """
        hit = self.get(phash, profile, mode="")
        if hit is None:
            return []
        return [r["text"] for r in hit.ocr if r.get("text")][:limit]

    # -- 정리 --------------------------------------------------------------

    def purge_all(self) -> int:
        """전부 지운다. 지운 행 수를 돌려준다."""
        with self._lock:
            n = self._db.execute("SELECT count(*) FROM page_cache").fetchone()[0]
            self._db.execute("DELETE FROM page_cache")
            self._db.commit()
        return int(n)

    def purge_near(self, phash: str, profile: str) -> int:
        """`phash` 와 같은 화면으로 보이는 행을 지운다.

        정확 일치만 지우면 소용이 없다 — 캡처는 매번 몇 비트씩 달라서, 사용자가
        보고 있는 그 페이지의 행이 정확 일치로는 안 잡힌다. 조회와 **같은 기준**
        (`fuzzy` 이내)으로 지워야 "이 페이지 캐시를 지웠다" 가 실제로 성립한다.
        """
        with self._lock:
            rows = self._db.execute(
                "SELECT rowid, phash FROM page_cache WHERE profile = ?", (profile,)
            ).fetchall()
            doomed = [
                r["rowid"] for r in rows if hamming(r["phash"], phash) <= self._fuzzy
            ]
            if doomed:
                self._db.executemany(
                    "DELETE FROM page_cache WHERE rowid = ?", [(d,) for d in doomed]
                )
                self._db.commit()
        return len(doomed)

    def purge_expired(self) -> int:
        """보존 기간이 지난 항목을 지운다. 지운 행 수를 돌려준다."""
        cutoff = int(time.time()) - self._retention * 86400
        with self._lock:
            cur = self._db.execute("DELETE FROM page_cache WHERE created_at < ?", (cutoff,))
            self._db.commit()
            return cur.rowcount

    def stats(self) -> dict[str, int]:
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) AS n, "
                "SUM(trans_json IS NOT NULL) AS translated FROM page_cache"
            ).fetchone()
        return {"pages": row["n"] or 0, "translated": row["translated"] or 0}
