"""번역. DESIGN.md §8.4.

페이지당 **1회** 호출한다. 말풍선마다 부르면 문맥이 끊겨 대명사·경어·화자가 어긋나고,
호출 수만큼 지연이 곱해진다.

프로바이더 중립이다. `DESIGN.md` 는 Anthropic 을 전제했지만, 일본어→한국어 만화
번역 품질은 공개 벤치마크로 답이 안 나온다 — 같은 페이지를 양쪽에 태워 직접 비교해야
한다(`scripts/ab_translate.py`). 두 API 모두 "시스템 프롬프트 + 사용자 메시지 +
JSON 스키마 강제"라 인터페이스가 거의 같아서 추상화 비용이 작다.

**읽기 순서는 여기서 건드리지 않는다.** `DESIGN.md` §8.2 는 LLM 재배열 교정을
예고했지만 **실측에서 역효과였다.** 4장 45 region 기준 XY-cut 36/45 인데 LLM 을
태우면 Sonnet 5 는 26/45, Gemini 3.6 Flash 는 33/45 로 내려간다. 특히
`yanmaga_002535` 는 XY-cut 이 10/10 맞힌 것을 Sonnet 이 2/10 으로 망가뜨렸다.

두 방향 모두 실패했다. 좌표를 안 주면 판단 근거가 없어 입력 순서를 그대로 베끼고,
좌표와 기하 규칙(펼침면 분할 → 칸 행 → 우→좌)을 주면 **XY-cut 을 LLM 더러 다시
구현하라는 요구**가 되어 결정론적 알고리즘보다 못한다. LLM 의 강점은 기하가 아니라
대사 흐름인데, 그걸 쓰려면 좌표가 필요하고 좌표를 주면 기하로 풀려 든다.

순서는 `order.py` 가 낸 것을 그대로 쓴다. 칸 경계 미인식(§6)은 칸 검출로 풀 문제지
번역기로 덮을 문제가 아니다.

API 키는 환경변수 또는 키 파일에서 온다 (`ANTHROPIC_API_KEY` / `GEMINI_API_KEY`).
`service.toml` 에는 넣지 않는다 — 설정 파일은 커밋되고 키는 커밋되면 안 된다.
찾는 경로와 이유는 `env.py` 참조.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Protocol

from mtl_shared.models import Region

from .env import load_local_env
from .jsonstream import ArrayStreamer

# ---------------------------------------------------------------------------
# 프롬프트
# ---------------------------------------------------------------------------

#: 두 문체 모두에 공통으로 붙는 규칙. 여기가 번역 품질의 대부분을 정한다.
_COMMON = """\
너는 일본 만화를 한국어로 옮기는 번역가다. 한 페이지의 모든 원문을 한 번에 받는다.

지켜야 할 것:

- **화자를 문체로 드러내라.** 일본어는 「〜だぜ」「〜ですわ」「〜じゃ」 같은 역할어로
  인물을 구분한다. 한국어에 같은 장치가 없으므로 어미·호칭·존댓말 높낮이로 옮긴다.
  같은 인물은 페이지 안에서 문체를 유지한다.
- **주어를 함부로 넣지 마라.** 일본어는 주어를 생략한다. 원문에 없는 주어를 넣으면
  칸 안 인물 관계를 틀리게 단정하게 된다. 한국어도 생략이 자연스러우면 생략한다.
- **말풍선 길이를 의식해라.** 번역문이 원문보다 훨씬 길면 화면에 안 들어간다.
  원문 글자 수의 1.5배를 넘기지 마라.
- **효과음은 소리로 옮긴다.** 「ダン」→「쾅」처럼. 뜻으로 풀어 쓰지 마라.
- OCR 오인식이 섞여 있다. 앞뒤가 이어지지 않는 토막은 억지로 해석하지 말고
  `note` 에 `ocr_noise` 를 적고 최선의 추정을 넣어라.
- **`kind` 로 분류해라.** 독자가 효과음·잡문을 숨길 수 있어야 한다.
  - `dialogue` — 인물이 말하거나 생각하는 것
  - `sfx` — 효과음. 그림 위에 그려 넣은 소리 (「ドン」「ザワザワ」)
  - `narration` — **이야기의 일부인** 지문. 나레이션 상자, 화 제목, 장면 설명
    (「一日は銃声から始まる」「休み時間」), 배경의 간판·표지판
  - `extra` — **이야기 밖의** 글. 사이트 UI, 광고·홍보문, 작가명·판권, 다음 화 안내
  `narration` 과 `extra` 를 잘 갈라라. 독자는 `extra` 만 숨긴다 — 나레이션을 숨기면
  이야기가 끊긴다. 판단 기준은 "이걸 못 읽으면 내용을 놓치는가" 다.

  판단 근거는 **원문 자체다.** 더듬는 말투(「い、家で…」), 물음, 호칭, 종결어미가
  있으면 인물이 말하는 것이다. 위치 정보는 주지 않으니 글만 보고 정해라.

출력 예시 — `ko` 에는 **번역 문장**이 들어간다. 분류 이름을 넣는 것이 아니다.
`note` 는 비워 두는 것이 기본이다.

```json
{"regions": [
  {"id": 0, "ko": "또 여기서 만날 줄이야", "kind": "dialogue", "note": ""},
  {"id": 1, "ko": "쾅", "kind": "sfx", "note": ""},
  {"id": 2, "ko": "그리고 사흘 뒤", "kind": "narration", "note": ""}
]}
```

"""

PROMPTS = {
    "natural": _COMMON
    + """
문체: **자연스럽게.** 한국 독자가 처음부터 한국어로 그려진 만화를 읽는 느낌이어야
한다. 일본어 어순을 그대로 끌고 오지 말고, 한국어에서 그 상황에 실제로 쓰는 말로
옮겨라. 관용구는 대응하는 한국어 관용구로 바꾼다.
""",
    "literal": _COMMON
    + """
문체: **직역에 가깝게.** 원문의 구조와 표현을 최대한 남긴다. 일본어를 배우는 독자가
원문과 대조할 수 있어야 한다. 관용구는 직역하고 필요하면 `note` 에 뜻을 적어라.
""",
}

#: 응답 스키마. 두 프로바이더가 같은 JSON 을 내도록 강제한다.
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "regions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "description": "입력에서 받은 region id"},
                    "ko": {
                        "type": "string",
                        "description": (
                            "한국어 번역문. **분류 이름이 아니라 실제 번역 문장이다.** "
                            "여기에 'dialogue' 같은 값을 넣으면 안 된다"
                        ),
                    },
                    "kind": {
                        "type": "string",
                        "enum": ["dialogue", "sfx", "narration", "extra"],
                        "description": "대사 / 효과음 / 이야기 지문 / 이야기 밖의 글",
                    },
                    "note": {
                        "type": "string",
                        "description": (
                            "특이사항만 짧게. **기본은 빈 문자열이다** — 번역 이유나 "
                            "해설을 쓰는 칸이 아니다. OCR 오인식은 'ocr_noise'"
                        ),
                    },
                },
                "required": ["id", "ko", "kind", "note"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["regions"],
    "additionalProperties": False,
}


def build_input(regions: list[Region], previous: list[str] | None = None) -> str:
    """페이지 하나를 사용자 메시지로 만든다.

    `previous` 는 직전 페이지 원문이다 (§8.4). 만화 대사는 페이지를 넘어 이어지므로
    직전 문맥 없이는 주어를 생략한 문장의 화자를 놓친다. 번역 대상이 아니라 문맥일
    뿐이라는 것을 모델이 알도록 따로 표시해서 넣는다.

    `id` 순서가 곧 읽기 순서다 (`order.py` 가 정한 것). 모델은 이 순서대로 읽고
    문맥을 잇는다.

    **`is_bubble` 은 넣지 않는다.** 원래는 넣었다 — 말풍선 밖 글자는 나레이션·효과음·
    제목이라 문체가 달라야 하니까. 그런데 그 값이 스크린톤 배경 위에서 틀리고
    (`DEVLOG.md` 의 `is_bubble` 절, 배경 통계로는 고칠 수 없다는 것까지 확인),
    **틀린 힌트를 모델이 그대로 따라간다.** sunday-webry 실측에서 「い、家で録ったやつ
    …」「そ、そうだったありがー」 같은 명백한 대사 4개가 `bubble: false` 하나 때문에
    전부 `caption` 으로 분류됐다. 프롬프트로 "참고만 하라" 고 적어도 안 먹었다.

    빼고 나면 모델은 원문만 보고 판단한다 — 더듬는 말투, 물음, 종결어미. 그쪽이
    링 균일도보다 훨씬 정확한 신호다. 입력 토큰도 준다.

    **좌표는 넣지 않는다.** 순서 교정 실험 때 정규화 bbox 를 넣어 봤는데(모듈 주석
    참조) 입력 토큰만 40% 늘고 순서는 오히려 나빠졌다. 번역만 할 거면 필요 없다.
    나중에 화자 혼동(반말/존댓말이 뒤집히는 등)이 보이면 그때 다시 시도해 볼 것.
    """
    lines: list[str] = []
    if previous:
        lines.append("# 직전 페이지 원문 (문맥용, 번역하지 마라)")
        lines += [f"# {t}" for t in previous]
        lines.append("# 여기부터 번역 대상")
    lines += [
        json.dumps(
            {"id": r.id, "ja": r.text, "vertical": r.vertical},
            ensure_ascii=False,
        )
        for r in regions
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 결과
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TranslatedRegion:
    id: int
    ko: str
    #: "dialogue" | "sfx" | "narration" | "extra". 모델이 원문을 보고 정한다.
    #: 클라이언트는 이걸로 효과음·잡문을 숨긴다 — `is_bubble` 이 아니라.
    kind: str = "dialogue"
    note: str = ""


@dataclass(slots=True)
class TranslateResult:
    regions: list[TranslatedRegion]
    model: str
    latency_ms: int
    input_tokens: int
    output_tokens: int
    cached_tokens: int = 0

    def by_id(self) -> dict[int, TranslatedRegion]:
        return {r.id: r for r in self.regions}


@dataclass(slots=True)
class TranslationStream:
    """번역 스트림. 소진하면서 region 을 하나씩 받고, 끝나면 `result` 가 채워진다.

    한 번에 다 받아 파싱하면 페이지당 9초를 그대로 기다린다. 완성된 말풍선부터
    흘려야 첫 번역이 2-3초에 뜬다 (§4.1 의 SSE `translation` 이벤트).
    """

    _iter: Iterator[TranslatedRegion]
    #: 스트림을 다 소진한 뒤에만 채워진다. 그 전에는 None.
    result: TranslateResult | None = field(default=None)

    def __iter__(self) -> Iterator[TranslatedRegion]:
        return self._iter


class Translator(Protocol):
    """프로바이더 구현이 만족해야 하는 최소 인터페이스."""

    model: str

    def translate(
        self, regions: list[Region], style: str, previous: list[str] | None = None
    ) -> TranslateResult: ...

    def translate_stream(
        self, regions: list[Region], style: str, previous: list[str] | None = None
    ) -> TranslationStream: ...


#: 스키마가 강제하지만 방어한다. 모르는 값이 오면 숨기지 않는 쪽으로 떨어뜨린다 —
#: 잘못 숨겨 대사가 사라지는 것이 효과음이 섞이는 것보다 나쁘다.
_KINDS = frozenset({"dialogue", "sfx", "narration", "extra"})


def _kind(obj: dict) -> str:
    k = obj.get("kind")
    return k if k in _KINDS else "dialogue"


def _parse(payload: dict, model: str, latency_ms: int, usage: tuple[int, int, int]) -> TranslateResult:
    regions = [
        TranslatedRegion(
            id=int(r["id"]), ko=r["ko"], kind=_kind(r), note=r.get("note", "")
        )
        for r in payload.get("regions", [])
    ]
    return TranslateResult(
        regions=regions,
        model=model,
        latency_ms=latency_ms,
        input_tokens=usage[0],
        output_tokens=usage[1],
        cached_tokens=usage[2],
    )


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------

#: `effort` / adaptive thinking 을 받는 모델. Haiku 4.5 와 Sonnet 4.5 는 거부한다.
_ANTHROPIC_EFFORT_MODELS = ("claude-opus-", "claude-sonnet-5", "claude-sonnet-4-6", "claude-fable-")


class AnthropicTranslator:
    """Anthropic Messages API. 키는 `ANTHROPIC_API_KEY` 에서 SDK 가 직접 읽는다."""

    def __init__(
        self, model: str, effort: str = "medium", max_tokens: int = 8000,
        api_key: str | None = None,
    ) -> None:
        import anthropic

        load_local_env()
        if not (api_key or os.environ.get("ANTHROPIC_API_KEY")):
            raise RuntimeError(
                "ANTHROPIC_API_KEY 가 없다. 환경변수나 키 파일에 넣을 것 "
                "(scripts/check_keys.py 로 확인)."
            )
        self.model = model
        self._effort = effort
        self._max_tokens = max_tokens
        self._client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()

    def _request_kwargs(self, regions: list[Region], style: str, previous) -> dict:
        output_config: dict = {"format": {"type": "json_schema", "schema": RESPONSE_SCHEMA}}
        extra: dict = {}
        # 구형 모델(Haiku 4.5 등)은 effort/adaptive 를 400 으로 거부한다.
        if self.model.startswith(_ANTHROPIC_EFFORT_MODELS):
            output_config["effort"] = self._effort
            extra["thinking"] = {"type": "adaptive"}

        return dict(
            model=self.model,
            max_tokens=self._max_tokens,
            system=[
                # 시스템 프롬프트는 페이지마다 같으므로 캐시한다. 캐시 읽기는 정가의
                # 0.1 배다. 최소 캐시 길이가 모델마다 다르다는 점에 주의 —
                # Haiku 4.5 는 4096 토큰이라 이 프롬프트는 조용히 캐시되지 않는다.
                {"type": "text", "text": PROMPTS[style], "cache_control": {"type": "ephemeral"}}
            ],
            messages=[{"role": "user", "content": build_input(regions, previous)}],
            output_config=output_config,
            **extra,
        )

    def translate_stream(
        self, regions: list[Region], style: str, previous: list[str] | None = None
    ) -> TranslationStream:
        kwargs = self._request_kwargs(regions, style, previous)
        stream = TranslationStream(_iter=iter(()))

        def run() -> Iterator[TranslatedRegion]:
            t0 = time.perf_counter()
            parser = ArrayStreamer("regions")
            seen: set[int] = set()
            with self._client.messages.stream(**kwargs) as sse:
                for text in sse.text_stream:
                    for obj in parser.feed(text):
                        rid = int(obj["id"])
                        if rid in seen:  # 방어: 같은 id 를 두 번 그리지 않는다
                            continue
                        seen.add(rid)
                        yield TranslatedRegion(
                            id=rid,
                            ko=obj.get("ko", ""),
                            kind=_kind(obj),
                            note=obj.get("note", ""),
                        )
                final = sse.get_final_message()

            if final.stop_reason == "refusal":
                raise RuntimeError(f"거부됨: {final.stop_details}")
            u = final.usage
            stream.result = TranslateResult(
                regions=[],  # 이미 스트림으로 나갔다
                model=self.model,
                latency_ms=int((time.perf_counter() - t0) * 1000),
                input_tokens=u.input_tokens,
                output_tokens=u.output_tokens,
                cached_tokens=u.cache_read_input_tokens or 0,
            )

        stream._iter = run()
        return stream

    def translate(
        self, regions: list[Region], style: str, previous: list[str] | None = None
    ) -> TranslateResult:
        """비스트리밍. A/B 스크립트처럼 전체 결과가 필요한 쪽이 쓴다."""
        stream = self.translate_stream(regions, style, previous)
        collected = list(stream)
        res = stream.result
        assert res is not None, "스트림을 다 소진했으면 result 가 있어야 한다"
        res.regions = collected
        return res


# ---------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------


class GeminiTranslator:
    """Google Gemini API. 키는 `GEMINI_API_KEY` 에서 SDK 가 직접 읽는다."""

    def __init__(self, model: str, api_key: str | None = None) -> None:
        from google import genai

        load_local_env()
        if not (api_key or os.environ.get("GEMINI_API_KEY")):
            raise RuntimeError(
                "GEMINI_API_KEY 가 없다. 환경변수나 키 파일에 넣을 것 "
                "(scripts/check_keys.py 로 확인)."
            )
        self.model = model
        # 클라이언트는 **반드시 들고 있어야 한다.** `genai.Client().interactions...`
        # 처럼 임시 객체로 쓰면 호출 도중 GC 되면서 내부 httpx 클라이언트가 닫히고
        # "Cannot send a request, as the client has been closed" 가 난다.
        self._client = genai.Client(api_key=api_key) if api_key else genai.Client()

    def translate(
        self, regions: list[Region], style: str, previous: list[str] | None = None
    ) -> TranslateResult:
        t0 = time.perf_counter()
        interaction = self._client.interactions.create(
            model=self.model,
            system_instruction=PROMPTS[style],
            input=build_input(regions, previous),
            response_format={
                "type": "text",
                "mime_type": "application/json",
                "schema": RESPONSE_SCHEMA,
            },
        )
        latency_ms = int((time.perf_counter() - t0) * 1000)
        return _parse(
            json.loads(interaction.output_text),
            self.model,
            latency_ms,
            _gemini_usage(interaction),
        )

    def translate_stream(
        self, regions: list[Region], style: str, previous: list[str] | None = None
    ) -> TranslationStream:
        """완성된 말풍선부터 하나씩 흘린다 (Anthropic 쪽과 같은 방식).

        예전에는 다 받은 뒤 한꺼번에 냈다. 전체 시간은 비슷하지만 **첫 번역이 뜨는
        시각**이 다르다 — 실측에서 스트리밍이 9.8초 → 4.8초로 줄였다.

        SDK 이벤트 구조 (실측):

            StepStart   {step: {type: "thought"}}      ← 사고 단계
            StepDelta   {delta: {signature: "..."}}    ← 텍스트가 아니다. 거른다
            StepStop
            StepStart   {step: {type: "model_output"}}
            StepDelta   {delta: {text: "{\n  \"regions\":", type: "text"}}
            StepDelta   {delta: {text: " [...]"}}
            StepStop
            InteractionCompletedEvent {interaction: {... usage ...}}

        `delta.type == "text"` 만 받아 `ArrayStreamer` 에 흘린다 — Anthropic 과 같은
        파서다. 사고 델타를 같이 넣으면 JSON 이 깨진다.
        """
        stream = TranslationStream(_iter=iter(()))

        def run() -> Iterator[TranslatedRegion]:
            t0 = time.perf_counter()
            parser = ArrayStreamer("regions")
            seen: set[int] = set()
            usage = (0, 0, 0)
            sse = self._client.interactions.create(
                model=self.model,
                system_instruction=PROMPTS[style],
                input=build_input(regions, previous),
                response_format={
                    "type": "text",
                    "mime_type": "application/json",
                    "schema": RESPONSE_SCHEMA,
                },
                stream=True,
            )
            for ev in sse:
                delta = getattr(ev, "delta", None)
                if delta is not None and getattr(delta, "type", None) == "text":
                    for obj in parser.feed(getattr(delta, "text", "") or ""):
                        rid = int(obj["id"])
                        if rid in seen:  # 방어: 같은 id 를 두 번 그리지 않는다
                            continue
                        seen.add(rid)
                        yield TranslatedRegion(
                            id=rid,
                            ko=obj.get("ko", ""),
                            kind=_kind(obj),
                            note=obj.get("note", ""),
                        )
                # 마지막 이벤트에 사용량이 실려 온다.
                got = getattr(ev, "interaction", None)
                if got is not None:
                    usage = _gemini_usage(got)

            stream.result = TranslateResult(
                regions=[],  # 이미 흘려보냈다
                model=self.model,
                latency_ms=int((time.perf_counter() - t0) * 1000),
                input_tokens=usage[0],
                output_tokens=usage[1],
                cached_tokens=usage[2],
            )

        stream._iter = run()
        return stream


def _gemini_usage(interaction: object) -> tuple[int, int, int]:
    """(입력, 출력, 캐시) 토큰.

    **사고 토큰을 출력에 더한다.** Gemini 는 `total_thought_tokens` 를 따로 두는데
    과금은 출력으로 된다. Anthropic 은 `output_tokens` 에 이미 포함해서 준다 —
    더하지 않으면 두 프로바이더 비용을 같은 잣대로 못 잰다.
    """
    usage = getattr(interaction, "usage", None) or getattr(interaction, "usage_metadata", None)
    if usage is None:
        return (0, 0, 0)

    def pick(*names: str) -> int:
        for n in names:
            v = getattr(usage, n, None)
            if isinstance(v, int):
                return v
        return 0

    return (
        pick("total_input_tokens", "input_tokens", "prompt_token_count"),
        pick("total_output_tokens", "output_tokens", "candidates_token_count")
        + pick("total_thought_tokens"),
        pick("total_cached_tokens", "cached_input_tokens", "cached_content_token_count"),
    )


# ---------------------------------------------------------------------------
# OpenAI
#
# ChatGPT 와 **OpenAI 호환 로컬 서버**(Ollama, LM Studio, vLLM)가 같은 길로 온다.
#
# 로컬(Ollama · gemma4:e4b)로는 실측했다 — 스키마 6/6, 페이지당 10-12초.
# **OpenAI 본가는 미검증이다** (키가 없어 호출을 못 해 봤다).
# ---------------------------------------------------------------------------


class OpenAITranslator:
    """OpenAI Chat Completions. 키는 `OPENAI_API_KEY` 또는 인자."""

    def __init__(
        self, model: str, api_key: str | None = None, base_url: str | None = None
    ) -> None:
        from openai import OpenAI

        load_local_env()
        # 로컬 엔드포인트(Ollama 등)는 키를 안 본다. SDK 가 빈 키를 거부하므로 채워 준다.
        if base_url and not api_key:
            api_key = os.environ.get("OPENAI_API_KEY") or "local"
        if not (api_key or os.environ.get("OPENAI_API_KEY")):
            raise RuntimeError(
                "OPENAI_API_KEY 가 없다. 환경변수나 키 파일에 넣을 것 "
                "(scripts/check_keys.py 로 확인)."
            )
        self.model = model
        self._client = OpenAI(
            **({"api_key": api_key} if api_key else {}),
            **({"base_url": base_url} if base_url else {}),
        )

    def translate(
        self, regions: list[Region], style: str, previous: list[str] | None = None
    ) -> TranslateResult:
        t0 = time.perf_counter()
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": PROMPTS[style]},
                {"role": "user", "content": build_input(regions, previous)},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "translation", "schema": RESPONSE_SCHEMA, "strict": False},
            },
        )
        latency = int((time.perf_counter() - t0) * 1000)
        payload = json.loads(resp.choices[0].message.content or "{}")
        u = resp.usage
        usage = (
            getattr(u, "prompt_tokens", 0) or 0,
            getattr(u, "completion_tokens", 0) or 0,
            getattr(getattr(u, "prompt_tokens_details", None), "cached_tokens", 0) or 0,
        )
        return _parse(payload, self.model, latency, usage)

    def translate_stream(
        self, regions: list[Region], style: str, previous: list[str] | None = None
    ) -> TranslationStream:
        """완성된 말풍선부터 하나씩 흘린다 (Anthropic·Gemini 와 같은 방식).

        델타가 그냥 문자열 조각이라 이쪽이 가장 단순하다 — `choices[0].delta.content`
        를 그대로 `ArrayStreamer` 에 밀어 넣으면 된다. 사고 델타를 걸러 낼 일도 없다.

        **usage 를 받으려면 따로 청해야 한다.** OpenAI 호환 스트림은 기본적으로
        usage 를 안 준다. `stream_options={"include_usage": True}` 를 붙이면 마지막
        청크에 실려 오는데, 로컬 서버 중에는 이 옵션을 모르는 것도 있다 — 그때는
        토큰 수가 0 으로 남을 뿐 번역은 정상이다.
        """
        stream = TranslationStream(_iter=iter(()))

        def run() -> Iterator[TranslatedRegion]:
            t0 = time.perf_counter()
            parser = ArrayStreamer("regions")
            seen: set[int] = set()
            usage = (0, 0, 0)
            sse = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": PROMPTS[style]},
                    {"role": "user", "content": build_input(regions, previous)},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "translation", "schema": RESPONSE_SCHEMA, "strict": False,
                    },
                },
                stream=True,
                stream_options={"include_usage": True},
            )
            for chunk in sse:
                for choice in chunk.choices or ():
                    piece = getattr(choice.delta, "content", None)
                    if not piece:
                        continue
                    for obj in parser.feed(piece):
                        rid = int(obj["id"])
                        if rid in seen:  # 방어: 같은 id 를 두 번 그리지 않는다
                            continue
                        seen.add(rid)
                        yield TranslatedRegion(
                            id=rid,
                            ko=obj.get("ko", ""),
                            kind=_kind(obj),
                            note=obj.get("note", ""),
                        )
                u = getattr(chunk, "usage", None)
                if u is not None:
                    usage = (
                        getattr(u, "prompt_tokens", 0) or 0,
                        getattr(u, "completion_tokens", 0) or 0,
                        getattr(getattr(u, "prompt_tokens_details", None), "cached_tokens", 0) or 0,
                    )

            stream.result = TranslateResult(
                regions=[],  # 이미 흘려보냈다
                model=self.model,
                latency_ms=int((time.perf_counter() - t0) * 1000),
                input_tokens=usage[0],
                output_tokens=usage[1],
                cached_tokens=usage[2],
            )

        stream._iter = run()
        return stream


# ---------------------------------------------------------------------------
# 선택
# ---------------------------------------------------------------------------


#: 모델 이름 앞머리 → 키 환경변수. 확장이 보낸 키를 어느 칸에서 꺼낼지도 이걸 따른다.
PROVIDER_ENV = {
    "claude-": "ANTHROPIC_API_KEY",
    "gemini-": "GEMINI_API_KEY",
    "gpt-": "OPENAI_API_KEY",
}


def provider_of(model: str) -> str:
    """`claude-sonnet-5` → `claude-`. 모르면 빈 문자열."""
    for prefix in PROVIDER_ENV:
        if model.startswith(prefix):
            return prefix
    return ""


def get_translator(
    model: str, api_key: str | None = None, base_url: str | None = None, **kwargs
) -> Translator:
    """모델 이름으로 프로바이더를 고른다.

    `api_key` 를 주면 환경변수 대신 그것을 쓴다 (확장이 보낸 키).
    `base_url` 은 OpenAI 호환 로컬 엔드포인트용 (Ollama 등).
    """
    if model.startswith("claude-"):
        return AnthropicTranslator(model, api_key=api_key, **kwargs)
    if model.startswith("gemini-"):
        return GeminiTranslator(model, api_key=api_key)
    if model.startswith("gpt-") or base_url:
        # base_url 이 있으면 이름이 무엇이든 OpenAI 호환으로 본다 (gemma-4-12b 등).
        return OpenAITranslator(model, api_key=api_key, base_url=base_url)
    raise ValueError(f"어느 프로바이더인지 모르겠다: {model!r}")
