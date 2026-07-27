# 벤더링 고지

이 디렉터리는 **comic-text-detector** (https://github.com/dmMaze/comic-text-detector) 의
추론 코드를 최소한으로 발췌·정리한 것이다. 그 코드는 다시 **YOLOv5**
(https://github.com/ultralytics/yolov5) 를 포함하고 있다.

- comic-text-detector — GPL-3.0
- YOLOv5 (v6.x) — GPL-3.0

따라서 이 디렉터리의 코드는 **GPL-3.0** 이다. 이 프로젝트는 배포하지 않는 개인용
도구이므로(DESIGN.md §15) 그대로 두지만, 배포하게 되면 라이선스 정리가 먼저다.

## 왜 벤더링했나

comic-text-detector 는 pip 패키지가 아니다. `setup.py`/`pyproject.toml` 이 없어
`pip install git+...` 가 되지 않고, 저장소를 통째로 쓰면 `torchsummary`,
`requests` 등 추론에 불필요한 의존성과 학습 스크립트가 따라온다.
가중치 로드에 실제로 필요한 모듈 정의만 옮겼다.

## 원본 대비 변경

| 항목 | 변경 |
|---|---|
| DBHead (`text_det` 헤드) | **제외**. 텍스트 라인 폴리곤은 M1 응답에 쓰지 않는다. 체크포인트의 `text_det` 키는 로드하지 않고 버린다 |
| yolov5 모듈 | 체크포인트 cfg 가 실제로 참조하는 것만 남김 (`Conv`, `Bottleneck`, `C3`, `SPPF`, `Concat`, `nn.Upsample`, `Detect`) |
| `parse_model` | 모르는 모듈 이름은 조용히 넘기지 않고 예외 |
| `check_version` (pkg_resources) | 제거. torch >= 2.7 을 요구하므로 분기가 죽은 코드다 |
| NMS | 학습·오토라벨링 경로(`labels`, `multi_label`, `merge`) 제거 |
| 후처리 | `group_output` / `TextBlock` 미사용. 박스 병합·읽기순서·`is_bubble` 은 DESIGN.md §8 규칙대로 `mtl_service` 쪽에서 직접 한다 |

## 알아둘 것

**yolo 클래스는 말풍선 여부가 아니라 언어다.** 원본 `utils/textblock.py` 의
`LANG_LIST = ['eng', 'ja', 'unknown']` 이고 체크포인트 cfg 는 `nc=2` 다.
즉 클래스 0 = 영문, 1 = 일문. DESIGN.md §3.1 이 "comic-text-detector 가
`is_bubble` 을 산출한다"고 적었지만 사실이 아니다. `is_bubble` 은
`mtl_service/mask.py` 에서 마스크 외곽 배경의 균일도·명도·채도로 판정한다.
