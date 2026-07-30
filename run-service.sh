#!/usr/bin/env bash
# 서비스를 띄운다. **WSL 셸 하나에서 두 경우를 다 처리한다.**
#
# 어디에 바인딩하느냐에 따라 실행할 파이썬이 다르다:
#
#   dev_bind_loopback = true   → 127.0.0.1 에 바인딩 → 리눅스 venv (WSL 안에서 실행)
#   dev_bind_loopback = false  → Tailscale 주소에 바인딩 → 윈도우 venv
#
# **WSL 은 Tailscale 주소에 바인딩할 수 없다.** NAT 뒤(172.30.x)라 윈도우의 Tailscale
# 인터페이스가 안 보이고, `find_tailscale_ip()` 가 None 을 내며 기동을 거부한다.
# 그래서 그 경우에는 이 스크립트가 **윈도우 파이썬을 대신 띄운다** — WSL 셸에서
# 그대로 실행되고 로그도 여기로 나오며 Ctrl+C 도 먹는다.
#
# 환경이 둘로 갈리는 이유:
#
#   .venv          — 윈도우용 (Scripts/python.exe)
#   ~/.venvs/mlr   — WSL 용 (bin/python)
#
# `uv sync` 는 플랫폼별 휠을 받으므로 하나를 공유할 수 없다.
#
# **리눅스 venv 는 /mnt/c 에 두지 않는다.** drvfs 라 파일 하나하나가 느린데 venv 는
# 수만 개다. 같은 venv 를 두 곳에 두고 `import torch` 를 재 봤다:
#
#   /mnt/c/code/MangaLiveReader/.venv-linux   35.9초
#   ~/.venvs/mlr                               2.0초
#
# user+sys 는 양쪽 다 ~2초다. 33초가 전부 I/O 대기였다. 소스는 /mnt/c 에 둬도 된다 —
# 파일이 몇십 개뿐이라 티가 안 난다.
set -euo pipefail

cd "$(dirname "$0")"

# ---------------------------------------------------------------------------
# 설정을 보고 어느 파이썬으로 띄울지 정한다
# ---------------------------------------------------------------------------
# service.local.toml 이 service.toml 을 덮는다 (config.py 와 같은 규칙). 파일 순서가
# 곧 우선순위다 — 뒤에 오는 것을 `tail -1` 이 집는다. 여기서 놓치면 설정은 Tailscale
# 바인딩인데 WSL 파이썬으로 띄워 기동이 거부된다.
loopback=$(grep -hE '^\s*dev_bind_loopback\s*=' service.toml service.local.toml 2>/dev/null \
  | tail -1 | grep -c 'true' || true)

if [[ "$loopback" == "1" ]]; then
  MODE="wsl"
  PY="${MLR_VENV:-$HOME/.venvs/mlr}/bin/python"
  HINT="UV_PROJECT_ENVIRONMENT=${MLR_VENV:-$HOME/.venvs/mlr} uv sync --python 3.11"
else
  MODE="windows"
  PY=".venv/Scripts/python.exe"
  HINT="uv sync   (윈도우 cmd 에서)"
fi

if [[ ! -x "$PY" ]]; then
  echo "$MODE 환경이 없다. 먼저:" >&2
  echo "  $HINT" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# API 키
#
# WSL 의 `~` 는 윈도우 프로필이 아니라서 `env.py` 의 기본 경로가 비어 있다.
# 키를 복사하면 두 군데가 되어 나중에 하나만 갈게 되므로, 윈도우 쪽 파일을 가리킨다.
# (윈도우 파이썬으로 띄우는 경우에는 env.py 가 알아서 찾으므로 필요 없다.)
# ---------------------------------------------------------------------------
if [[ "$MODE" == "wsl" && -z "${MTL_ENV_FILE:-}" && ! -f "$HOME/.config/mangalivereader/env" ]]; then
  # 윈도우 사용자명은 WSL 사용자명과 다를 수 있다. cmd 에게 직접 물어본다.
  win_home=$(cmd.exe /c 'echo %USERPROFILE%' 2>/dev/null | tr -d '\r')
  win_home=${win_home//\\//}                       # C:\Users\x → C:/Users/x
  win_home=/mnt/${win_home:0:1}${win_home:2}       # → /mnt/c/Users/x
  for cand in \
    "$win_home/.config/mangalivereader/env" \
    "/mnt/c/Users/$USER/.config/mangalivereader/env"
  do
    if [[ -f "$cand" ]]; then
      export MTL_ENV_FILE="$cand"
      echo "키 파일: $cand (윈도우 프로필)"
      break
    fi
  done
  if [[ -z "${MTL_ENV_FILE:-}" && -z "${ANTHROPIC_API_KEY:-}" ]]; then
    echo "! API 키를 못 찾았다. 번역 없이 OCR 만 돈다." >&2
    echo "  MTL_ENV_FILE 로 지정하거나 ~/.config/mangalivereader/env 에 둘 것" >&2
  fi
fi

# ---------------------------------------------------------------------------
# GPU 를 못 잡으면 CPU 로 조용히 떨어져 OCR 이 10배 느려진다. 먼저 알려 준다.
# ---------------------------------------------------------------------------
if ! "$PY" -X utf8 -c "
import sys, torch
if not torch.cuda.is_available():
    sys.exit(1)
print(f'GPU: {torch.cuda.get_device_name(0)} · torch {torch.__version__}')
"; then
  echo "! CUDA 를 못 잡았다. CPU 로 돌면 OCR 이 10배 느리다." >&2
  echo "  · nvidia-smi 가 도는지" >&2
  echo "  · torch 가 cu128 빌드인지 (기본 PyPI 휠에는 sm_120 커널이 없다)" >&2
fi

echo "실행: $MODE ($PY)"
# `-X utf8` 은 윈도우에서 필수다. 없으면 로그의 한글이 cp949 로 깨진다.
exec "$PY" -X utf8 -m mtl_service "$@"
