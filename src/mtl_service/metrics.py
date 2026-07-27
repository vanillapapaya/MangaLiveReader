"""구조화 로그. DESIGN.md §9.4, §14 ("성능 계측을 처음부터 넣는다").

각 단계 소요 시간을 JSONL 로 남겨, 예산 초과 시 어느 구간인지 즉시 특정한다.
나중에 붙이면 어디가 느린지 추측하게 된다.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_lock = threading.Lock()


def record(path: Path | None, **fields: Any) -> None:
    """한 줄 기록. `path` 가 None 이면 아무것도 하지 않는다.

    기록 실패가 요청을 죽이면 안 된다. 계측은 보조 기능이다.
    """
    if path is None:
        return
    line = json.dumps(
        {"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), **fields},
        ensure_ascii=False,
    )
    try:
        with _lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
    except OSError:
        pass
