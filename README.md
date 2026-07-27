# Manga Live Reader

일본 만화 뷰어 화면을 캡처해 OCR + 번역 결과를 화면 위에 겹쳐 보여 준다.

- `DESIGN.md` — 무엇을 왜 만드는가
- `PROGRESS.md` — 어디까지 왔고 무엇이 남았는가
- `DEVLOG.md` — 무엇을 재봤고 무엇이 나왔는가
- `REVIEW.md` — 사용자 판단이 필요한 것

---

## 실행

서비스(GPU 연산)와 브라우저 확장(캡처·표시) 두 조각이다. 서비스를 먼저 띄운다.

### 1. 서비스

**어디서 띄우느냐에 따라 설정 두 개가 같이 움직인다.** 별도의 "모드" 같은 건 없고
그냥 서로 맞아야 하는 값들이다. 어긋나면 서비스는 멀쩡히 뜨는데 확장만 못 붙는다.

| | WSL | 윈도우 |
|---|---|---|
| 실행 | `./run-service.sh` | `run-service.cmd` |
| `service.toml` `dev_bind_loopback` | `true` | `false` |
| 확장 옵션 화면 주소 | `http://127.0.0.1:8788/read` | `http://<서비스-머신-tailscale-주소>:8788/read` |
| 맥북에서 붙나 | ✗ | ✓ |

**WSL 로는 맥북에서 못 붙는다.** WSL 은 NAT 뒤(172.30.x)라 윈도우의 Tailscale
인터페이스가 안 보이고, `bind_tailscale_only` 가 주소를 못 찾아 기동을 거부한다.

**`py -X utf8 -m mtl_service` 를 쓰지 마라.** `py` 는 시스템 파이썬이라 이 프로젝트의
의존성이 없다. `run-service.cmd` 가 venv 를 쓴다.

기동하면 이렇게 뜬다:

```
키 파일: /mnt/c/Users/<사용자>/.config/mangalivereader/env (윈도우 프로필)
GPU: NVIDIA GeForce RTX 5080 · torch 2.11.0+cu128
mtl-service → http://127.0.0.1:8788
```

**확인**

```bash
curl -s http://127.0.0.1:8788/health          # WSL 쪽
curl -s http://<서비스-머신-tailscale-주소>:8788/health     # 윈도우 쪽
# {"status":"ok","models_loaded":false,"gpu":"NVIDIA GeForce RTX 5080",...}
```

`models_loaded` 는 첫 요청 때 true 가 된다. 미리 올리려면 `curl -X POST .../warmup`.

### 2. 브라우저 확장

`chrome://extensions` → **개발자 모드** 켜기 → **압축해제된 확장 프로그램을 로드** →
이 저장소의 `extension/` 폴더.

만화 페이지에서 **`Alt+Shift+M`** (또는 툴바 아이콘).

한 번 읽고 나면 오른쪽 위에 **버튼 패널**이 뜬다. 단축키를 몰라도 마우스로 다 된다.

| 버튼 | 단축키 | |
|---|---|---|
| 번역 | `Alt+Shift+M` | 이 페이지를 캡처해 번역 (뷰어 자동 탐지) |
| 영역 | `Alt+Shift+D` | 읽을 영역을 드래그로 고르기 (자동 탐지 실패용) |
| 자동 | — | **페이지가 넘어가면 알아서 다시 읽는다** |
| 라벨 | `Alt+Shift+L` | 라벨 전체 펼치기/접기 |
| 효과음 | `Alt+Shift+S` | 숨긴 효과음·잡문 보기 |
| 상태 | — | 왼쪽 위 상태줄 켜기/끄기 |
| 음성 | — | 번역을 읽기 순서대로 소리내어 읽는다 |

켜짐/꺼짐이 있는 버튼(자동·라벨·효과음)은 켜져 있으면 주황색이 된다.

라벨은 기본적으로 마우스를 올려야 보인다. 전부 펼치면 아래쪽 말풍선을 가린다.

**자동**은 기본이 꺼짐이다. 켜면 클릭·키·휠·DOM 변화를 신호로 삼아 0.7초 조용해진 뒤
화면을 캡처해 **해시가 달라졌을 때만** 읽는다 — 같은 페이지를 다시 읽지 않는다.

자세한 것은 `extension/README.md`.

---

## 환경

### 개발·실행 환경이 둘이다

`uv sync` 는 플랫폼별 휠을 받으므로 윈도우와 WSL 이 venv 를 공유할 수 없다.

| | 경로 | |
|---|---|---|
| 윈도우 | `.venv/Scripts/python.exe` | cmd 에서 `py -X utf8 -m ...` |
| WSL | `~/.venvs/mlr/bin/python` | `run-service.sh` 가 쓴다 |

**리눅스 venv 를 `/mnt/c` 에 두지 마라.** drvfs 라 `import torch` 가 35.9초 걸린다
(ext4 는 2.0초 — 차이가 전부 I/O 대기다). 소스는 `/mnt/c` 에 둬도 된다.

처음 만들 때:

```bash
UV_PROJECT_ENVIRONMENT=~/.venvs/mlr uv sync --python 3.11
```

`--python 3.11` 을 빼면 안 된다 — WSL 기본이 3.14 인데 프로젝트는 `<3.12` 다.

### API 키

`service.toml` 에 넣지 않는다 (커밋되는 파일이다). `env.py` 가 이 순서로 찾는다:

1. `$MTL_ENV_FILE`
2. `~/.config/mangalivereader/env`
3. `<프로젝트 루트>/.env.local`

환경변수가 파일보다 우선한다. 형식은 `KEY=값` 한 줄씩.

**WSL 의 `~` 는 윈도우 프로필이 아니다.** 키가 윈도우 쪽에 있으면 `run-service.sh`
가 `/mnt/c/Users/$USER/.config/mangalivereader/env` 를 찾아 `MTL_ENV_FILE` 로 잡아
준다. 복사하지 마라 — 두 군데가 되면 나중에 하나만 갈게 된다.

확인: `python scripts/check_keys.py`

### 테스트

```bash
~/.venvs/mlr/bin/python -m pytest -q     # WSL
.venv\Scripts\python.exe -X utf8 -m pytest -q   # 윈도우
```

GPU·네트워크 없이 도는 것만 있다. 파이프라인 전체 검증은 `scripts/debug_page.py`.

---

## 다른 기기에서 쓰기 (맥북 등)

서비스는 GPU 가 있는 서비스 머신 에서만 돈다. 다른 기기는 확장만 깔면 된다 —
캡처와 오버레이는 브라우저가 하고 연산은 전부 원격이다.

위 표의 **윈도우** 열대로 맞추면 된다. 그 밖에 이미 해 둔 것:

- **윈도우 인바운드 허용** — `mtl-service (Tailscale)` 규칙.
  Tailscale 인터페이스 · Private 프로필 · TCP 8788.
  지우려면 `Remove-NetFirewallRule -DisplayName 'mtl-service (Tailscale)'`

  **프로그램 경로로 좁히면 안 된다.** venv 의 `python.exe` 는 기반 인터프리터로
  넘겨 실행되므로 프로세스 이미지 경로가 **시스템 python** 으로 잡힌다. 규칙을
  `.venv\Scripts\python.exe` 로 걸었더니 서비스는 멀쩡히 뜨는데 맥북만 조용히
  막혔다 — 진단하기 가장 나쁜 실패다.
- **확장 `host_permissions`** — `manifest.json` 에 Tailscale 주소가 들어 있다.
  주소를 바꾸면 여기도 고치고 확장을 다시 읽어야 한다 (옵션 화면만 바꾸면 CORS 로 막힌다)

**인증은 아직 꺼져 있다** (`auth_disabled = true`). 외부에는 안 열리지만 같은 tailnet 의
다른 기기(아이패드, archlinux, 다른 맥)는 전부 붙을 수 있다. 켜려면
`service.toml` 의 `auth_token` 을 정하고 `auth_disabled = false` 로 바꾼 뒤 확장 옵션
화면에 같은 값을 넣는다.

**Retina(dpr=2)는 아직 검증 안 됐다.** 좌표 계산은 `devicePixelRatio` 를 타고 들어가
있지만(`background.js` 의 `cropAndNormalize`, `content.js` 의 `toCss`) 2배 화면에서
돌려 본 적이 없다. 라벨이 말풍선에서 **절반만큼 밀리면** dpr 을 두 번 곱하거나
빠뜨린 것이다.
