#!/usr/bin/env bash
# WSL 에서 서비스를 띄운다. 윈도우의 `py -X utf8 -m mtl_service` 와 같은 것.
#
# 환경이 둘로 갈린다:
#
#   .venv              — 윈도우용 (Scripts/python.exe). cmd 에서 쓴다
#   ~/.venvs/mlr       — WSL 용 (bin/python). 이 스크립트가 쓴다
#
# **리눅스 venv 는 /mnt/c 에 두지 않는다.** drvfs 라 파일 하나하나가 느린데 venv 는
# 수만 개다. 같은 venv 를 /mnt/c 와 ext4 에 각각 두고 재 봤다:
#
#   /mnt/c/code/MangaLiveReader/.venv-linux   import torch  35.9초
#   ~/.venvs/mlr                              import torch   2.0초
#
# user+sys 는 양쪽 다 ~2초다. 33초가 전부 I/O 대기였다. 소스는 /mnt/c 에 그대로
# 둬도 된다 — 파일이 몇십 개뿐이라 티가 안 난다.
set -euo pipefail

cd "$(dirname "$0")"

VENV="${MLR_VENV:-$HOME/.venvs/mlr}"
if [[ ! -x "$VENV/bin/python" ]]; then
  echo "리눅스 환경이 없다. 먼저:" >&2
  echo "  UV_PROJECT_ENVIRONMENT=$VENV uv sync --python 3.11" >&2
  exit 1
fi

# API 키 파일 찾기. WSL 의 `~` 는 윈도우 프로필이 아니라서 `env.py` 의 기본 경로
# (`~/.config/mangalivereader/env`) 가 비어 있다. 키를 복사하면 두 군데가 되어
# 나중에 하나만 갈게 되므로, 윈도우 쪽 파일을 그대로 가리킨다.
if [[ -z "${MTL_ENV_FILE:-}" && ! -f "$HOME/.config/mangalivereader/env" ]]; then
  for cand in \
    "/mnt/c/Users/$USER/.config/mangalivereader/env" \
    "/mnt/c/Users/Vanillapapaya/.config/mangalivereader/env"
  do
    if [[ -f "$cand" ]]; then
      export MTL_ENV_FILE="$cand"
      echo "키 파일: $cand (윈도우 프로필)"
      break
    fi
  done
fi
if [[ -z "${MTL_ENV_FILE:-}" && ! -f "$HOME/.config/mangalivereader/env" ]] \
   && [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
  echo "! API 키를 못 찾았다. 번역 없이 OCR 만 돈다." >&2
  echo "  MTL_ENV_FILE 로 지정하거나 ~/.config/mangalivereader/env 에 둘 것" >&2
fi

# GPU 를 못 잡으면 CPU 로 조용히 떨어져 OCR 이 10배 느려진다. 먼저 알려 준다.
if ! "$VENV/bin/python" -c "
import sys, torch
if not torch.cuda.is_available():
    sys.exit(1)
print(f'GPU: {torch.cuda.get_device_name(0)} · torch {torch.__version__}')
"; then
  echo "! CUDA 를 못 잡았다. CPU 로 돌면 OCR 이 10배 느리다." >&2
  echo "  · nvidia-smi 가 WSL 에서 도는지" >&2
  echo "  · torch 가 cu128 빌드인지 (기본 PyPI 휠에는 sm_120 커널이 없다)" >&2
fi

# `src` 레이아웃이라 설치된 패키지를 쓴다. uv sync 가 editable 로 넣어 준다.
exec "$VENV/bin/python" -m mtl_service "$@"
