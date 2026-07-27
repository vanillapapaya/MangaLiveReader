"""`service.toml` 로더. DESIGN.md §12.

상대 경로는 전부 **프로젝트 루트 기준**으로 해석해 절대 경로로 만든다. 서비스는
작업 스케줄러가 임의의 작업 디렉터리에서 띄우므로(§9.2) 상대 경로를 그대로
들고 다니면 모델 가중치를 못 찾는다.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: src/mtl_service/config.py → 프로젝트 루트
ROOT = Path(__file__).resolve().parents[2]

DEFAULT_CONFIG_PATH = ROOT / "service.toml"


def _resolve(value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else ROOT / p


@dataclass(slots=True)
class ServerConfig:
    #: 기동 시 Tailscale 인터페이스 IP를 찾아 거기에만 바인딩한다 (§4.4).
    #: 못 찾으면 기동을 거부한다. 0.0.0.0 폴백은 없다.
    bind_tailscale_only: bool = True
    #: M1 개발용. True면 bind_tailscale_only 보다 우선해 루프백만 연다.
    dev_bind_loopback: bool = True
    port: int = 8788
    auth_token: str = ""
    auth_disabled: bool = False


@dataclass(slots=True)
class ModelsConfig:
    resident: bool = True
    ttl_seconds: int = 0
    device: str = "cuda"
    detector_weights: Path = ROOT / "models" / "comictextdetector.pt"
    detector_input_size: int = 1024


@dataclass(slots=True)
class DetectConfig:
    min_area_ratio: float = 0.0005
    merge_gap_ratio: float = 0.6
    #: 병합 허용 간격의 절대 상한 (페이지 짧은 변 대비). detect._should_merge 참조
    merge_gap_max_ratio: float = 0.025
    #: yolo confidence 하한
    detect_threshold: float = 0.35
    #: NMS IoU 임계
    nms_threshold: float = 0.35
    #: 글자 마스크 이진화 임계 (0-1)
    mask_threshold: float = 0.3
    #: yolo 가 놓친 마스크 덩어리를 region 으로 승격시킬지 (detect.py `_promote` 참조)
    mask_promote: bool = True
    #: 획을 덩어리로 잇는 팽창 반경 (페이지 짧은 변 대비 비율)
    mask_dilate_ratio: float = 0.010
    #: 승격 후보 bbox 안의 실제 글자 픽셀 비율 하한. 그림 가장자리 노이즈를 떨군다
    mask_min_ink_ratio: float = 0.05
    #: 승격 후보의 마스크 원본값 평균 하한 (0-1). 진짜 글자와 그림 오검출을 가르는 신호
    mask_promote_confidence: float = 0.7
    #: 후보 넓이가 yolo 박스와 이 비율 넘게 겹치면 중복으로 보고 버린다
    mask_max_overlap: float = 0.3


@dataclass(slots=True)
class OcrConfig:
    batch_size: int = 16
    crop_padding: int = 4
    min_text_length: int = 2
    #: 생성 상한(글자). 지연을 묶고, 상한에 부딪힌 결과를 쓰레기 신호로 쓴다.
    #:
    #: 처음엔 32 였다. 8장 실측에서 정상 대사 최장이 26 자라 여유가 있다고 봤는데
    #: **표본이 짧은 대사만 담고 있었다.** 같은 8장을 64 로 다시 읽으니 32 에 걸려
    #: 잘린 region 이 3개 나왔고 그중 둘은 멀쩡한 대사다:
    #:
    #:   「現実はどうだ！運動部やエリート共だけがモテる狩猟採集社会並の階級構造じゃねえか！」 (40자)
    #:   「俺はオダクとしてオタクのエリートになってみせる！なり可愛い彼女を作りこの陰の状態から脱出」 (44자)
    #:
    #: 여러 줄짜리 긴 대사가 앞부분만 번역되고 있었다. 실측 최장은 60자.
    max_text_length: int = 256


@dataclass(slots=True)
class OrderConfig:
    min_gap_ratio: float = 0.015


@dataclass(slots=True)
class ApiConfig:
    model_fast: str = "claude-sonnet-5"
    model_quality: str = "claude-sonnet-5"
    #: Anthropic 모델의 `effort`. Haiku 4.5 처럼 안 받는 모델에는 자동으로 안 보낸다.
    effort: str = "low"


@dataclass(slots=True)
class CacheConfig:
    path: Path = ROOT / "cache" / "pages.sqlite3"
    retention_days: int = 90
    fuzzy_hamming: int = 3


@dataclass(slots=True)
class MetricsConfig:
    #: 빈 문자열이면 파일 기록을 하지 않는다.
    jsonl_path: Path | None = ROOT / "logs" / "metrics.jsonl"


@dataclass(slots=True)
class Config:
    server: ServerConfig = field(default_factory=ServerConfig)
    models: ModelsConfig = field(default_factory=ModelsConfig)
    detect: DetectConfig = field(default_factory=DetectConfig)
    ocr: OcrConfig = field(default_factory=OcrConfig)
    order: OrderConfig = field(default_factory=OrderConfig)
    api: ApiConfig = field(default_factory=ApiConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)
    metrics: MetricsConfig = field(default_factory=MetricsConfig)


def _section(raw: dict[str, Any], name: str) -> dict[str, Any]:
    return dict(raw.get(name) or {})


def load(path: str | Path | None = None) -> Config:
    """설정 파일을 읽는다. 파일이 없으면 전부 기본값.

    알 수 없는 키는 조용히 무시하지 않고 예외를 낸다. 오타 난 설정이 기본값으로
    조용히 동작하면 "왜 안 먹지"로 시간을 버린다.
    """
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    raw: dict[str, Any] = {}
    if cfg_path.exists():
        with cfg_path.open("rb") as f:
            raw = tomllib.load(f)

    def build(cls: type, name: str, **overrides: Any) -> Any:
        data = _section(raw, name)
        data.update(overrides)
        known = set(cls.__slots__)
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"{cfg_path.name} [{name}] 알 수 없는 키: {sorted(unknown)}")
        return cls(**data)

    models_raw = _section(raw, "models")
    cache_raw = _section(raw, "cache")
    metrics_raw = _section(raw, "metrics")

    jsonl = metrics_raw.get("jsonl_path", "logs/metrics.jsonl")

    return Config(
        server=build(ServerConfig, "server"),
        models=build(
            ModelsConfig,
            "models",
            **(
                {"detector_weights": _resolve(models_raw["detector_weights"])}
                if "detector_weights" in models_raw
                else {}
            ),
        ),
        detect=build(DetectConfig, "detect"),
        ocr=build(OcrConfig, "ocr"),
        order=build(OrderConfig, "order"),
        api=build(ApiConfig, "api"),
        cache=build(
            CacheConfig,
            "cache",
            **({"path": _resolve(cache_raw["path"])} if "path" in cache_raw else {}),
        ),
        metrics=MetricsConfig(jsonl_path=_resolve(jsonl) if jsonl else None),
    )
