"""comic-text-detector 추론 코드 벤더링본. GPL-3.0 — NOTICE.md 를 먼저 읽을 것.

바깥에서는 `ComicTextDetector` 와 `RawDetection` 만 쓴다.
"""

from .net import LANGUAGES, ComicTextDetector, RawDetection

__all__ = ["LANGUAGES", "ComicTextDetector", "RawDetection"]
