"""스트리밍 JSON 에서 배열 원소를 완성되는 대로 뽑는다.

번역 응답은 `{"regions": [{...}, {...}, ...]}` 한 덩어리다. 다 받고 파싱하면 9초를
그대로 기다리게 되므로, 스트림을 흘려받으며 **완성된 원소부터** SSE 로 중계한다.

JSONL 로 바꾸면 줄 단위로 쉽게 자를 수 있지만 그러면 **JSON 스키마 강제를 포기**하게
된다. 스키마 강제는 응답 형태를 API 가 보장해 주는 장치라 포기할 이유가 없다.
대신 여는/닫는 중괄호를 세어 원소 경계를 찾는다 — 문자열 안의 중괄호와 이스케이프만
제대로 다루면 되는 작은 상태 기계다.

`json.JSONDecoder.raw_decode` 로 흉내낼 수도 있지만, 그건 잘린 입력에서 예외를
던지므로 매 청크마다 예외를 잡아야 하고 어디까지 소비했는지 알기 어렵다.
"""

from __future__ import annotations

import json
from typing import Any, Iterator


class ArrayStreamer:
    """`{"<key>": [ ... ]}` 안의 원소를 완성되는 대로 낸다.

    >>> s = ArrayStreamer("regions")
    >>> list(s.feed('{"regions": [{"id": 1, "ko": "가"}'))
    [{'id': 1, 'ko': '가'}]
    >>> list(s.feed(', {"id": 2, "ko": "나"}]}'))
    [{'id': 2, 'ko': '나'}]
    """

    def __init__(self, key: str) -> None:
        self._marker = f'"{key}"'
        self._buf = ""
        #: 배열 시작 `[` 을 아직 못 찾았으면 False.
        self._in_array = False
        #: 현재 원소의 시작 위치. None 이면 원소 밖(쉼표·공백 구간).
        self._start: int | None = None
        self._depth = 0
        self._in_string = False
        self._escaped = False
        #: 이미 훑은 위치. 청크가 올 때마다 여기서부터 이어 본다.
        self._pos = 0

    def feed(self, chunk: str) -> Iterator[dict[str, Any]]:
        """청크를 넣고, 이번에 완성된 원소들을 낸다."""
        self._buf += chunk

        if not self._in_array:
            # `"regions"` 뒤의 첫 `[` 를 찾는다. 값 안에 같은 문자열이 나올 일은
            # 없다 — 키는 스키마가 강제하고 우리가 만든 이름이다.
            m = self._buf.find(self._marker)
            if m < 0:
                return
            b = self._buf.find("[", m)
            if b < 0:
                return
            self._in_array = True
            self._pos = b + 1

        while self._pos < len(self._buf):
            ch = self._buf[self._pos]
            self._pos += 1

            if self._in_string:
                if self._escaped:
                    self._escaped = False
                elif ch == "\\":
                    self._escaped = True
                elif ch == '"':
                    self._in_string = False
                continue

            if ch == '"':
                self._in_string = True
            elif ch == "{":
                if self._depth == 0:
                    self._start = self._pos - 1
                self._depth += 1
            elif ch == "}":
                self._depth -= 1
                if self._depth == 0 and self._start is not None:
                    raw = self._buf[self._start : self._pos]
                    self._start = None
                    try:
                        yield json.loads(raw)
                    except json.JSONDecodeError:
                        # 스키마가 강제된 출력이라 여기 오면 안 되지만, 와도
                        # 스트림을 죽이지는 않는다. 나머지 원소는 계속 나온다.
                        pass
            elif ch == "]" and self._depth == 0:
                # 배열 끝. 뒤에 오는 것(닫는 중괄호 등)은 볼 필요가 없다.
                self._pos = len(self._buf)
                return

    @property
    def text(self) -> str:
        """지금까지 받은 원문 전체. 스트림이 끝난 뒤 통째로 파싱할 때 쓴다."""
        return self._buf
