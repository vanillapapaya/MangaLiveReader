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
    created_at  INTEGER,
    -- 이 행을 만든 캡처의 크기. **좌표가 이 좌표계에 묶여 있다.**
    -- 크기가 다른 캡처에 이 박스를 얹으면 자리가 어긋난다 (cache.get 참조).
    img_w       INTEGER,
    img_h       INTEGER,
    -- 뷰어 사각형(전송 이미지 좌표). 좌표를 되돌리는 기준이다.
    viewer_json TEXT
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
    #: 이 좌표가 묶여 있는 뷰어 사각형 (전송 이미지 좌표). 모르면 None.
    viewer: list[int] | None = None


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
            # 예전 DB 에는 크기 칸이 없다. 있으면 그냥 두고 없으면 더한다 —
            # 값이 NULL 인 행은 크기를 모르므로 조회에서 쓰지 않는다.
            have = {r[1] for r in self._db.execute("PRAGMA table_info(page_cache)")}
            for col in ("img_w", "img_h", "viewer_json"):
                if col not in have:
                    kind = "TEXT" if col.endswith("_json") else "INTEGER"
                    self._db.execute(f"ALTER TABLE page_cache ADD COLUMN {col} {kind}")
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # -- 조회 --------------------------------------------------------------

    def get(
        self, phash: str, profile: str, mode: str, size: tuple[int, int] | None = None
    ) -> CacheHit | None:
        """`phash` 에 해당하는 캐시. 없으면 None.

        `mode` 가 저장된 것과 다르면 OCR 만 살리고 번역은 None 으로 준다 —
        그래야 호출자가 검출·OCR 을 건너뛰고 번역만 다시 돌린다.

        **`size` 가 저장된 캡처 크기와 다르면 없는 것으로 친다.** 저장된 bbox 는
        그 캡처의 좌표계에 묶여 있는데, phash 는 퍼지 매칭이라 크기가 다른 캡처도
        같은 행에 붙는다. 그대로 돌려주면 박스가 엉뚱한 자리에 그려진다.

        실제로 그렇게 났다 — 자동 번역에서 스크롤로 잘리는 범위가 달라지자
        같은 행에 298,920바이트와 409,896바이트 캡처가 붙었고, 박스가 어긋난 채
        나왔다. 「갱신」을 누르면 맞는 이유가 그것이다 (캐시를 버리고 다시 잰다).
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
        viewer = json.loads(row["viewer_json"]) if row["viewer_json"] else None
        # 크기가 다른 캡처에는 좌표를 그대로 못 쓴다.
        #
        # **기준 사각형이 있으면 클라이언트가 환산한다** (content.js `fromCachedFrame`).
        # 없으면 — 예전 DB 행이거나 뷰어를 못 찾은 캡처 — 어긋나느니 없는 것으로 친다.
        if size and row["img_w"] and row["img_h"]:
            if (int(row["img_w"]), int(row["img_h"])) != (int(size[0]), int(size[1])):
                if not viewer:
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
            viewer=viewer,
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

    def put_ocr(
        self,
        phash: str,
        profile: str,
        ocr: list[dict[str, Any]],
        size: tuple[int, int] | None = None,
        viewer: list[int] | None = None,
    ) -> None:
        """OCR 결과를 넣는다. 이미 있으면 번역은 건드리지 않고 OCR 만 갱신한다.

        `size` 는 이 좌표를 만든 캡처의 크기다. 조회할 때 같은 크기인지 본다.
        """
        with self._lock:
            self._db.execute(
                """
                INSERT INTO page_cache
                    (phash, profile, ocr_json, created_at, img_w, img_h, viewer_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(phash) DO UPDATE SET
                    ocr_json    = excluded.ocr_json,
                    profile     = excluded.profile,
                    img_w       = excluded.img_w,
                    img_h       = excluded.img_h,
                    viewer_json = excluded.viewer_json
                """,
                (
                    phash,
                    profile,
                    json.dumps(ocr, ensure_ascii=False),
                    int(time.time()),
                    int(size[0]) if size else None,
                    int(size[1]) if size else None,
                    json.dumps(viewer) if viewer else None,
                ),
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
