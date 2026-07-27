r"""API 키를 파일에서 읽는다.

키는 `service.toml` 에 넣지 않는다 — 설정 파일은 커밋되고 키는 커밋되면 안 된다.
환경변수로 줘도 되지만, 이 프로젝트에서는 그게 생각보다 번거롭다:

- venv 가 **Windows 파이썬**이라 WSL 에서 `export` 해도 넘어가지 않는다.
  `WSLENV` 에 등재해야 하고, 그러려면 셸에 키를 입력하게 되어 기록에 남는다
- Windows 사용자 환경변수로 넣으면 보이지만 GUI 나 PowerShell 이 필요하고,
  설정 후 `wsl --shutdown` 을 해야 반영된다. SSH 로만 붙어 있으면 둘 다 못 한다

그래서 파일 경로를 둔다. 찾는 순서:

1. `$MTL_ENV_FILE` — 명시적 지정
2. `~/.config/mangalivereader/env` — **권장**
3. `<프로젝트 루트>/.env.local` — 편의용 폴백

**유닉스 권한으로는 못 막는다.** 읽는 쪽이 Windows 프로세스라 WSL 홈(ext4)에 두는
것은 의미가 없고, `/mnt/c` 는 metadata 없는 drvfs 라 `chmod 600` 이 조용히 무시된다
(실측: `-rwxrwxrwx` 그대로였다). 실제로 보호하는 것은 **Windows ACL** 이다.
그래서 2번이 권장이다 — `C:\Users\<사용자>` 이하는 다른 사용자 계정이 읽을 수 없게
ACL 이 걸려 있지만, `C:\code\...` 는 기본적으로 Users 그룹에 읽기가 열려 있다.
같은 계정으로 도는 프로그램은 어느 쪽이든 읽을 수 있다 — 그게 이 방식의 한계다.

WSL 셸에서 편집할 때는 같은 파일을 `/mnt/c/Users/...` 로 연다 (`wsl_path()`).

**이미 설정된 환경변수를 덮어쓰지 않는다.** 운영에서 환경변수로 주입해 놓고
개발용 파일이 남아 있어도 조용히 뒤집히지 않는다.

의존성을 늘리지 않으려고 직접 파싱한다. `python-dotenv` 의 보간·따옴표 규칙 같은
것은 지원하지 않는다. 여기 들어가는 건 API 키뿐이다.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: Windows 사용자 프로필 이하. ACL 로 다른 계정과 격리된다.
PREFERRED = Path.home() / ".config" / "mangalivereader" / "env"
#: 편의용 폴백. 프로젝트 폴더는 보통 Users 그룹에 읽기가 열려 있다.
PROJECT_LOCAL = ROOT / ".env.local"

#: 어느 파일에서 왔는지. `check_keys.py` 가 보여준다.
loaded_from: dict[str, Path] = {}


def candidates() -> list[Path]:
    override = os.environ.get("MTL_ENV_FILE")
    paths = [Path(override)] if override else []
    return paths + [PREFERRED, PROJECT_LOCAL]


def find_env_file() -> Path | None:
    """실제로 존재하는 첫 후보."""
    return next((p for p in candidates() if p.exists()), None)


def wsl_path(path: Path) -> str:
    """`C:\\Users\\x` → `/mnt/c/Users/x`. WSL 셸에서 편집할 때 쓸 경로.

    이 프로세스는 Windows 파이썬이라 경로를 Windows 형태로 들고 있는데, 사용자는
    WSL 셸에서 그 파일을 연다. 안내에 Windows 경로를 그대로 찍으면 `nano` 가
    엉뚱한 파일을 만든다.
    """
    text = str(path)
    if len(text) > 2 and text[1] == ":":
        return f"/mnt/{text[0].lower()}/" + text[3:].replace("\\", "/")
    return text


def load_local_env(path: Path | None = None) -> Path | None:
    """키 파일을 읽어 `os.environ` 에 채운다. 없으면 조용히 넘어간다.

    읽은 파일 경로를 돌려준다 (없으면 None).
    """
    path = path or find_env_file()
    if path is None or not path.exists():
        return None

    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # **줄 내용을 예외 메시지에 절대 넣지 않는다.** 그 줄에 키가 들어 있고,
        # 예외는 로그·터미널·크래시 리포트로 새어 나간다. 실제로 한 번 샜다.
        if "=" not in line:
            raise ValueError(
                f"{path}:{lineno} 형식이 'KEY=값' 이 아니다 (내용은 찍지 않는다)"
            )

        key, _, value = line.partition("=")
        key = key.strip()
        # 값을 감싼 따옴표만 벗긴다. 붙여넣을 때 같이 딸려오는 경우가 흔하다.
        value = value.strip().strip("\"'")
        if not key:
            raise ValueError(f"{path}:{lineno} 변수명이 비었다")
        if key in os.environ:
            continue  # 환경변수가 이긴다
        os.environ[key] = value
        loaded_from[key] = path
    return path
