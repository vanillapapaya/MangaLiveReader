# Manga Live Reader

일본 만화 뷰어 화면을 캡처해 OCR + 번역 결과를 **원래 말풍선 위에 겹쳐** 보여 준다.
번역본을 따로 만들지 않는다 — 보고 있는 화면에 그대로 얹는다.

두 조각이다:

| | 무엇을 하는가 | 어디서 도는가 |
|---|---|---|
| **mtl-service** | 말풍선 검출 · OCR · 번역 | 자기 GPU 가 있는 기계 (파이썬) |
| **확장** | 화면 캡처 · 오버레이 표시 | 크롬 (압축해제 로드) |

**서버는 아무도 호스팅하지 않는다.** 각자 자기 기계에 띄우고 자기 API 키를 넣는다.
키는 서비스 쪽 파일에만 있고 확장은 키를 모른다.

> ### 처음이라면 → **[QUICKSTART.md](QUICKSTART.md)**
> 명령을 복사해 붙이기만 하면 되는 설치 안내다. 30분.
> **이 문서(README)는 기술 문서다** — 무엇이 왜 그런지를 적는다.

- `QUICKSTART.md` — 따라만 하면 되는 설치 (처음 쓰는 사람용)
- `DESIGN.md` — 무엇을 왜 만드는가
- `PROGRESS.md` — 어디까지 왔고 무엇이 남았는가
- `extension/README.md` — 확장 내부

주석과 문서 곳곳이 **`DEVLOG.md`**(개발일지)를 가리키는데 그 파일은 공개하지 않는다 —
저자에게 쓰는 글이라 다듬지 않은 채로 두고 싶었다. 설계에 실제로 필요한 결론은
`DESIGN.md` 와 각 모듈 주석에 옮겨 두었다.

---

## 필요한 것

- **파이썬 3.11** (3.12 이상은 안 된다 — `requires-python = ">=3.11,<3.12"`)
- [`uv`](https://docs.astral.sh/uv/)
- **NVIDIA GPU 를 권한다.** CPU 로도 돌지만 OCR 이 10배 느려 실사용이 안 된다
  (`[models].device = "cpu"` 로 바꾸면 된다)
- **번역 API 키 하나** — Anthropic 또는 Google(Gemini). 200쪽 한 권에
  claude-sonnet-5 가 $1.64, gemini-3.6-flash 가 $2.44 로 재봤다

RTX 50 시리즈(sm_120)라면 torch 가 **cu128 빌드**여야 한다. `pyproject.toml` 이
그 인덱스를 못 박아 두었으니 `uv sync` 를 쓰면 알아서 맞는다. 기본 PyPI 휠에는
sm_120 커널이 없어 `no kernel image is available` 로 죽는다.

## 시작하기

```bash
uv sync                                     # 3.11 이 기본이 아니면 --python 3.11
uv run python scripts/fetch_models.py       # 검출기·OCR 가중치 (저장소에 없다)
```

키를 파일 하나에 넣는다. **`service.toml` 에 넣지 않는다** — 커밋되는 파일이다.

```bash
mkdir -p ~/.config/mangalivereader
printf 'ANTHROPIC_API_KEY=sk-ant-...\n' > ~/.config/mangalivereader/env
# 또는 GEMINI_API_KEY=...  (확인: uv run python scripts/check_keys.py)
```

띄운다:

```bash
uv run mtl-service
```

```
GPU: NVIDIA GeForce RTX 5080 · torch 2.11.0+cu128
mtl-service → http://127.0.0.1:8788
```

확인:

```bash
curl -s http://127.0.0.1:8788/health
# {"status":"ok","models_loaded":false,"gpu":"NVIDIA GeForce RTX 5080",...}
```

`models_loaded` 는 첫 요청 때 true 가 된다. 미리 올리려면 `curl -X POST .../warmup`.

### 확장

`chrome://extensions` → **개발자 모드** 켜기 → **압축해제된 확장 프로그램을 로드** →
이 저장소의 `extension/` 폴더.

기본 서비스 주소가 `http://127.0.0.1:8788/read` 라 **같은 기계에 띄웠으면 옵션 화면을
건드릴 필요가 없다.** 만화 페이지에서 `Alt+Shift+M` (또는 툴바 아이콘).

## 쓰는 법

한 번 읽고 나면 오른쪽 위에 **버튼 패널**이 뜬다. 단축키를 몰라도 마우스로 다 된다.

| 버튼 | 단축키 | |
|---|---|---|
| 번역 | `Alt+Shift+M` | 이 페이지를 캡처해 번역 (뷰어 자동 탐지) |
| 갱신 | `Alt+Shift+R` | 캐시를 지우고 다시 번역 (`Shift`+「번역」 클릭도 같다) |
| 영역 | `Alt+Shift+D` | 읽을 영역을 드래그로 고르기 (자동 탐지 실패용) |
| 자동 | — | **페이지가 넘어가면 알아서 다시 읽는다** |
| 라벨 | `Alt+Shift+L` | 라벨 전체 펼치기/접기 |
| 효과음 | `Alt+Shift+S` | 효과음·잡문도 보이기 |
| 음성 | `Alt+Shift+P` | 원문 소리내어 읽기 |
| 숨김 | `` ` `` (누르는 동안) | 오버레이를 감춰 원문 그림을 본다. 버튼은 고정 숨김 |
| 상태 | — | 왼쪽 위 상태줄 켜기/끄기 |

켜짐/꺼짐이 있는 버튼(자동·라벨·효과음)은 켜져 있으면 주황색이 된다.
라벨은 기본적으로 마우스를 올려야 보인다 — 전부 펼치면 아래쪽 말풍선을 가린다.

**자동**은 기본이 꺼짐이다. 켜면 클릭·키·휠·DOM 변화를 신호로 삼아 0.7초 조용해진 뒤
화면을 캡처해 **해시가 달라졌을 때만** 읽는다 — 같은 페이지를 다시 읽지 않는다.

**음성은 기본이 브라우저 내장 음성이다.** 학습된 목소리를 쓰려면 자기 음성 서버를
띄워 옵션 화면에 주소를 넣는다 — 서버도 음색 모델도 이 저장소에 없다. 필요한 것은
엔드포인트 두 개뿐이다 (`extension/README.md`).

자세한 것은 `extension/README.md`.

## 설정

`service.toml` 이 전부다. 값마다 왜 그 값인지가 주석에 붙어 있다 (대부분 실측 근거).

**이 파일을 고치지 말고 `service.local.toml` 을 만든다.** 커밋되지 않고, 적은 절의
적은 키만 이긴다:

```toml
[models]
device = "cpu"

[api]
model_fast = "gemini-3.6-flash"
model_quality = "gemini-3.6-flash"
```

키와 시크릿은 이 파일에도 넣지 않는다. 찾는 순서는 (`src/mtl_service/env.py`):

1. `$MTL_ENV_FILE`
2. `~/.config/mangalivereader/env` — **권장**
3. `<프로젝트 루트>/.env.local`

환경변수가 파일보다 우선한다. 형식은 `KEY=값` 한 줄씩. 여기 들어가는 것:
`ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, 그리고 아래의 `MTL_AUTH_TOKEN`.

### 인증

기본은 **루프백 바인딩 + 인증 없음**이다. 이 기계 안에서만 붙으므로 안전하다.
공유 시크릿은 `service.toml` 이 아니라 `MTL_AUTH_TOKEN` 으로 준다 — 켜는 절차는
아래 「서비스 여는 법 ②」에 있다.

인증을 켜 놓고 토큰이 없으면 **기동을 거부한다.** 요청마다 500 을 내는 것보다
기동에서 막는 편이 낫다.

## 서비스 여는 법

바인딩은 **루프백 아니면 Tailscale 주소** 둘뿐이다. `0.0.0.0` 폴백은 없고, Tailscale
주소를 못 찾으면 기동을 거부한다 — 만화 화면을 통째로 받는 엔드포인트를 설정 실수
한 번으로 LAN 에 열지 않기 위한 것이다 (`__main__.py`).

### 1. 같은 기계에서만 (기본)

아무 설정도 필요 없다. 위 「시작하기」 그대로 `uv run mtl-service` → 확장은 기본값
`http://127.0.0.1:8788/read` 로 붙는다.

### 2. 다른 기기에서 (맥북 · 아이패드 · 다른 PC)

서비스는 GPU 가 있는 기계에서만 돈다. 다른 기기는 **확장만 깔면 된다** — 캡처와
오버레이는 브라우저가 하고 연산은 전부 원격이다.

[Tailscale](https://tailscale.com) 로 두 기기를 같은 tailnet 에 넣은 뒤:

**① 서비스 쪽** — `service.local.toml`:

```toml
[server]
dev_bind_loopback = false     # Tailscale 주소에만 바인딩
auth_disabled = false         # 밖에서 붙으면 인증을 켠다
```

토큰을 만들어 키 파일에 넣는다 (`service.toml` 에 적지 않는다):

```bash
printf 'MTL_AUTH_TOKEN=%s\n' "$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')" \
  >> ~/.config/mangalivereader/env
```

띄우고 주소를 확인한다 — 기동 로그가 바인딩한 주소를 그대로 찍는다:

```
mtl-service → http://100.x.y.z:8788
```

**② 방화벽** (서비스 기계가 윈도우일 때만). PowerShell 관리자 권한:

```powershell
New-NetFirewallRule -DisplayName 'mtl-service (Tailscale)' `
  -Direction Inbound -Protocol TCP -LocalPort 8788 `
  -InterfaceAlias 'Tailscale' -Profile Private -Action Allow
```

인터페이스 이름은 `Get-NetAdapter` 로 확인한다 (보통 `Tailscale`).

> **프로그램 경로로 좁히면 안 된다.** venv 의 `python.exe` 는 기반 인터프리터로 넘겨
> 실행되므로 프로세스 이미지 경로가 **시스템 python** 으로 잡힌다. 규칙을
> `.venv\Scripts\python.exe` 로 걸었더니 서비스는 멀쩡히 뜨는데 다른 기기만 조용히
> 막혔다 — 진단하기 가장 나쁜 실패다.

**③ 클라이언트 쪽** — 확장 옵션 화면에 넣는다:

| 칸 | 값 |
|---|---|
| 서비스 주소 | `http://100.x.y.z:8788/read` (기동 로그의 주소) |
| 인증 토큰 | 위에서 만든 값 |

저장할 때 확장이 **그 주소의 권한을 요청한다** (`optional_host_permissions`).
허용하지 않으면 CORS 로 막힌다 — 주소만 바꾸고 권한을 안 주는 것이 흔한 실패다.

**④ 확인**은 클라이언트 기기에서:

```bash
curl -s http://100.x.y.z:8788/health
curl -s -X POST http://100.x.y.z:8788/warmup -H 'X-Auth-Token: <토큰>'
```

`/health` 는 토큰이 없어도 되지만 `/warmup`·`/read`·`/cache/purge` 는 필요하다.
토큰이 틀리면 401 이 온다.

### 3. 재부팅해도 알아서 뜨게

**리눅스** — `~/.config/systemd/user/mtl-service.service`:

```ini
[Unit]
Description=mtl-service
After=network-online.target

[Service]
WorkingDirectory=%h/MangaLiveReader
ExecStart=%h/MangaLiveReader/.venv/bin/python -m mtl_service
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

```bash
systemctl --user enable --now mtl-service
loginctl enable-linger $USER     # 로그인 없이도 돌게 한다. 빠뜨리면 재부팅 후 안 뜬다
journalctl --user -u mtl-service -f
```

**윈도우** — 작업 스케줄러. 콘솔 창을 띄우지 않으려면 `pythonw.exe` 를 쓴다:

```cmd
schtasks /Create /TN mtl-service /SC ONLOGON /RL LIMITED /F ^
  /TR "cmd /c cd /d C:\경로\MangaLiveReader && if not exist logs mkdir logs && .venv\Scripts\pythonw.exe -X utf8 -m mtl_service >> logs\service.log 2>&1"
```

`pythonw` 는 콘솔이 없어 출력이 사라지므로 **로그를 파일로 남겨야 한다.**
Tailscale 바인딩을 쓴다면 `tailscaled` 가 먼저 올라와야 하니, 작업 속성에서
**실패 시 재시도**를 켜 두는 편이 안전하다.

### 4. 인터넷에 여는 것 — 권하지 않는다

각자 자기 기계에 띄우는 것이 이 프로젝트의 전제다 (`DESIGN.md` §15). 그래도 열려면
앞단에 프록시(Tailscale Funnel, cloudflared)를 두게 되는데, **그때 서비스는 프록시가
붙었는지 알 방법이 없다.** 최소한 이것들이 먼저 필요하다:

- `auth_disabled = false` — 안 켜면 아무나 네 API 크레딧을 태울 수 있다
- 동시 요청 상한 — GPU 워커가 하나라(`app.py`) 큐가 무한히 쌓인다. **아직 없다**
- 지출 상한 — 비용은 로그로만 남고 막는 것이 없다. **아직 없다**
- `/read` 는 SSE 다. 프록시가 버퍼링하면 점진 표시가 죽는다

### 잘 안 될 때

| 증상 | 원인 |
|---|---|
| `Tailscale 인터페이스를 찾지 못해 기동을 거부한다` | `tailscale status` 확인. WSL 안에서는 윈도우의 Tailscale 이 안 보인다 (아래 저자 환경 절) |
| `인증이 켜져 있는데 토큰이 없다` | `MTL_AUTH_TOKEN` 이 서비스 프로세스에 안 보인다. `scripts/check_keys.py` |
| 401 | 확장 옵션의 토큰이 서버 값과 다르다 |
| 다른 기기에서 응답 없음 | ② 방화벽. 서비스는 멀쩡히 떠 있고 클라이언트만 막힌다 |
| CORS 오류 | 주소를 바꾼 뒤 옵션 화면에서 「저장」을 눌러 권한을 허용하지 않았다 |
| 번역 없이 원문만 온다 | API 키가 없다. 기동 로그에 경고가 뜬다 |
| OCR 이 10배 느리다 | CUDA 를 못 잡아 CPU 로 떨어졌다. 기동 로그의 `GPU:` 줄을 볼 것 |

**Retina(dpr=2)는 아직 검증 안 됐다.** 좌표 계산은 `devicePixelRatio` 를 타고 들어가
있지만(`background.js` 의 `cropAndNormalize`, `content.js` 의 `toCss`) 2배 화면에서
돌려 본 적이 없다. 라벨이 말풍선에서 **절반만큼 밀리면** dpr 을 두 번 곱하거나
빠뜨린 것이다.

## 음성 서버 여는 법 (선택)

원문을 **학습된 목소리로** 읽게 하는 기능이다. 안 띄우면 브라우저 내장 음성으로
돌아간다 — 기능이 사라지지는 않는다.

**서버도 음색 모델도 이 저장소에 없다.** 서버는 따로 있다:

> **[gpt-sovits-server](https://github.com/vanillapapaya/gpt-sovits-server)**
> — GPT-SoVITS 에 얇은 HTTP 껍데기를 씌워 목소리를 이름으로 고르게 한다. 아래 계약을
> 그대로 구현한 것이라 받아서 띄우면 바로 붙는다.

음색 가중치는 어느 쪽에도 없다 — 각자 학습하거나 구해서 넣는다. 다른 엔진을 쓰고
싶으면 아래 두 엔드포인트만 맞추면 된다.

### 확장이 기대하는 것은 엔드포인트 두 개뿐이다

```
GET  /voices
  → {"voices": ["anon-jp", "soyo-jp"],
     "default": "anon-jp",                     # 없어도 된다
     "info": {"anon-jp": {"description": "옵션 화면에 보일 설명"}}}   # 없어도 된다

POST /tts
  ← {"text": "…", "language": "Japanese" | "Korean", "voice": "anon-jp"}
  → 오디오 바이트 (Web Audio 로 디코드되면 된다. wav 로 실측했다)
```

`text` 만 필수다. `voice` 는 옵션 화면에서 「(서버 기본값)」을 고르면 빠진다.
`language` 는 확장이 원문(일본어)/번역문(한국어) 중 무엇을 읽느냐에 따라 정한다.

**stock GPT-SoVITS `api_v2.py` 로는 그대로 안 된다.** 그쪽은 `/voices` 가 없고
`/tts` 바디가 다르다 (`text_lang`, `ref_audio_path`, `prompt_text` …). 음색을 이름
하나로 고르는 개념이 없으니 껍데기가 필요하다. 골격은 이 정도다:

```python
# 계약만 보여 주는 골격이다. 합성 호출은 자기 엔진에 맞춘다.
from fastapi import FastAPI
from fastapi.responses import Response
from pydantic import BaseModel

VOICES = {"anon-jp": {"description": "…", "language": "Japanese"}}  # 자기 프리셋
app = FastAPI()

class TTSRequest(BaseModel):
    text: str
    language: str = "Japanese"
    voice: str | None = None

@app.get("/voices")
def voices():
    return {"voices": list(VOICES), "default": next(iter(VOICES)), "info": VOICES}

@app.post("/tts")
def tts(req: TTSRequest):
    wav = synthesize(req.text, req.language, req.voice or next(iter(VOICES)))
    return Response(wav, media_type="audio/wav")
```

프리셋을 바꿀 때마다 서버를 고치지 않으려면 YAML/TOML 한 장으로 빼는 것이 낫다 —
음색마다 모델 가중치·참조 음성·참조 텍스트·언어를 적어 두고 `/voices` 가 그것을
그대로 내놓게 한다. 위 저장소가 그 구조다.

**모델을 미리 로드해 둘 것.** 요청마다 가중치를 올리면 첫 말풍선이 수십 초가 된다.

**바인딩을 좁힐 것.** `0.0.0.0` 에 열면 tailnet 뿐 아니라 집 LAN 에도 열린다. 인증이
없는 서버라면 Tailscale 주소나 루프백에만 붙인다.

### 확장에 연결

옵션 화면 「음성」 칸에 주소(`http://<기계>:9880`)를 넣고 **「목록 불러오기」**.
`/voices` 가 응답하면 목소리 목록이 채워진다. `Alt+Shift+P` 로 읽는다.

- **CORS 헤더는 필요 없다.** 합성 요청은 background 워커가 하므로 확장 출처로 나간다
- 서버가 실패하면 **조용히 내장 음성으로 떨어진다** — 소리가 아예 안 나는 것이 제일
  나쁘다. 서버를 쓰고 있는지 확인하려면 목소리가 바뀌었는지 들으면 된다
- **별 기계에 두는 것을 권한다.** GPU 경합은 실측에서 OCR 지연을 13배 흔들었다
  합성은 RTF 0.25 라 재생이 합성보다 4배 느려서, 한 칸만 미리
  합성해 두면 말풍선 사이가 끊기지 않는다

## 개발

```bash
uv run pytest -q
```

GPU·네트워크 없이 도는 것만 있다. 파이프라인 전체 검증은 `scripts/debug_page.py`.

`scripts/` 에 재보기용 도구가 있다 — `ab_translate.py`(모델 A/B),
`check_merge.py`·`check_order.py`(픽스처 회귀), `send_page.py`, `crop_viewer.py`.

만화 캡처는 저작물이라 저장소에 넣지 않는다. 정답 표는 좌표만 담은
`test/fixtures/*.json` 으로 남긴다.

## 이 저장소를 만든 환경 (윈도우 + WSL)

여기부터는 저자 환경 메모다. 다른 환경이면 필요 없다.

### 개발·실행 환경이 둘이다

`uv sync` 는 플랫폼별 휠을 받으므로 윈도우와 WSL 이 venv 를 공유할 수 없다.

| | 경로 | |
|---|---|---|
| 윈도우 | `.venv/Scripts/python.exe` | `run-service.cmd` |
| WSL | `~/.venvs/mlr/bin/python` | `run-service.sh` 가 쓴다 |

**리눅스 venv 를 `/mnt/c` 에 두지 마라.** drvfs 라 `import torch` 가 35.9초 걸린다
(ext4 는 2.0초 — 차이가 전부 I/O 대기다). 소스는 `/mnt/c` 에 둬도 된다.

```bash
UV_PROJECT_ENVIRONMENT=~/.venvs/mlr uv sync --python 3.11
```

`--python 3.11` 을 빼면 안 된다 — WSL 기본이 3.14 인데 프로젝트는 `<3.12` 다.

**WSL 셸에서 그냥 `uv sync` / `uv run` 을 하지 마라.** 기본 대상이 `.venv` 인데
그것은 윈도우용이다 — 리눅스 휠로 덮어써서 윈도우 쪽을 부순다.
`UV_PROJECT_ENVIRONMENT` 를 주거나 경로를 박아 부른다.

**`py -X utf8 -m mtl_service` 를 쓰지 마라.** `py` 는 시스템 파이썬이라 이 프로젝트의
의존성이 없다. 윈도우에서는 `-X utf8` 이 필수다 — 없으면 로그의 한글이 cp949 로 깨진다.

venv 가 둘이라 `uv run` 이 어느 쪽을 집는지 헷갈린다. 테스트는 경로를 박아 부른다:

```bash
~/.venvs/mlr/bin/python -m pytest -q            # WSL
.venv\Scripts\python.exe -X utf8 -m pytest -q   # 윈도우 cmd
```

### `run-service.sh` 는 어느 파이썬으로 띄울지 스스로 고른다

| `dev_bind_loopback` | 바인딩 | 실제로 실행되는 것 | 맥북에서 |
|---|---|---|---|
| `true` | `127.0.0.1` | 리눅스 venv (WSL 안) | ✗ |
| `false` | Tailscale 주소 | **윈도우 venv** (WSL 셸에서 대신 띄운다) | ✓ |

**WSL 은 Tailscale 주소에 바인딩할 수 없다** — NAT 뒤(172.30.x)라 윈도우의 Tailscale
인터페이스가 안 보이고, `bind_tailscale_only` 가 주소를 못 찾아 기동을 거부한다.
그래서 그 경우 스크립트가 윈도우 파이썬을 대신 띄운다. WSL 셸에서 그대로 돌고 로그도
여기로 나오며 Ctrl+C 도 먹는다.

**WSL 의 `~` 는 윈도우 프로필이 아니다.** 키가 윈도우 쪽에 있으면 `run-service.sh` 가
`/mnt/c/Users/<사용자>/.config/mangalivereader/env` 를 찾아 `MTL_ENV_FILE` 로 잡아
준다. 복사하지 마라 — 두 군데가 되면 나중에 하나만 갈게 된다.

### 윈도우 인바운드 허용 — 이 기계에는 이미 걸려 있다

`mtl-service (Tailscale)` 규칙 (Tailscale 인터페이스 · Private 프로필 · TCP 8788).
새로 만드는 방법은 위 「서비스 여는 법 ②」에 있다.

지우려면 `Remove-NetFirewallRule -DisplayName 'mtl-service (Tailscale)'`

---

## 라이선스

**GPL-3.0-or-later** (`LICENSE`).

고른 것이 아니라 정해진 것이다 — `src/mtl_service/ctd/` 가
[comic-text-detector](https://github.com/dmMaze/comic-text-detector)(GPL-3.0)와
그 안의 [YOLOv5](https://github.com/ultralytics/yolov5) v6.x(GPL-3.0) 추론 코드를
발췌한 것이고, 서비스가 그것을 직접 import 한다. `src/mtl_service/ctd/NOTICE.md` 에
무엇을 어떻게 옮겼는지 적어 두었다.

모델 가중치는 저장소에 없다. `scripts/fetch_models.py` 가 원 배포처에서 받는다 —
각자의 라이선스를 따른다 (comic-text-detector, `kha-white/manga-ocr-base`).

## 이용 범위

- 정식 구독 또는 사이트가 무료로 공개한 회차를 열람하는 중에만 쓴다
- 번역 결과와 캡처 이미지를 외부에 배포하지 않는다
- 언어 장벽 때문에 읽지 못하는 것을 읽기 위한 보조 수단이다. 정식 번역판이 있는
  작품은 그쪽을 이용하는 것이 낫다
- 각 사이트 약관을 확인할 책임은 쓰는 사람에게 있다. 라이선스는 그것까지 보장해
  주지 않는다
