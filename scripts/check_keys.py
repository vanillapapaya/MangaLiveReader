"""API 키가 서비스 프로세스에서 보이는지 확인한다. **키 값은 절대 찍지 않는다.**

    uv run python scripts/check_keys.py

이 프로젝트의 venv 는 Windows 파이썬이다. WSL 쉘에서 `export` 한 변수는
`WSLENV` 에 등재하지 않으면 Windows 프로세스로 넘어가지 않는다 — 그래서 "분명
export 했는데 키가 없다" 는 상황이 생긴다. 이 스크립트는 실제로 번역기가 도는
프로세스에서 보이는지를, 값은 찍지 않고 확인한다.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mtl_service.env import (  # noqa: E402
    PREFERRED,
    candidates,
    wsl_path,
    load_local_env,
    loaded_from,
)

#: (환경변수, 흔한 접두사, 어디에 쓰는지)
#:
#: 접두사는 **참고용 힌트일 뿐 검증이 아니다.** 실측에서 정상 동작하는 Gemini 키가
#: `AIza` 로 시작하지 않았다 — 발급 시기에 따라 형식이 바뀐다. 접두사가 다르다고
#: 경고하면 멀쩡한 키를 의심하게 만든다. 진짜 검증은 API 를 한 번 호출해 보는 것뿐이라
#: `--probe` 로 뺐다.
KEYS = [
    ("ANTHROPIC_API_KEY", "sk-ant-", "claude-* 모델"),
    ("GEMINI_API_KEY", "AIza", "gemini-* 모델"),
]


def describe_lines(path: Path) -> None:
    """줄 모양만 보여준다. **값은 절대 찍지 않는다** — 변수명과 길이만."""
    print(f"\n{path} 의 줄 모양 (값은 가림):")
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line:
            print(f"  {lineno:>2d}  (빈 줄)")
        elif line.startswith("#"):
            print(f"  {lineno:>2d}  주석")
        elif "=" not in line:
            # 어느 변수를 붙여야 하는지까지 알려준다. 접두사를 "포함하는지"만
            # 보므로 값은 드러나지 않는다.
            guess = next((n for n, pre, _ in KEYS if pre in line), None)
            hint = f"  ← 줄 맨 앞에 '{guess}=' 를 붙일 것" if guess else ""
            print(f"  {lineno:>2d}  ✗ '=' 가 없다  (길이 {len(line)}){hint}")
        else:
            key, _, value = line.partition("=")
            name = key.strip()
            ok = name.replace("_", "").isalnum() and not name[:1].isdigit()
            mark = "✓" if ok else "✗"
            note = "" if ok else "  ← 변수명에 공백/기호가 섞였다"
            print(f"  {lineno:>2d}  {mark} {name!r} = <{len(value.strip())}자>{note}")


def probe() -> None:
    """실제 API 를 한 번씩 불러 본다. 키 유효성과 잔액까지 확인하는 유일한 방법."""
    print("\n최소 호출로 확인:")
    try:
        import anthropic

        c = anthropic.Anthropic()
        c.messages.create(
            model="claude-opus-5", max_tokens=8,
            messages=[{"role": "user", "content": "OK"}],
        )
        print("  ✓ Anthropic")
    except Exception as exc:
        print(f"  ✗ Anthropic — {type(exc).__name__}: {str(exc)[:160]}")
    try:
        from google import genai

        # 클라이언트를 변수에 묶어야 한다. 임시 객체로 두면 호출 도중 GC 되면서
        # 내부 httpx 클라이언트가 닫히고 "client has been closed" 가 난다.
        client = genai.Client()
        client.interactions.create(model="gemini-3.6-flash", input="OK")
        print("  ✓ Gemini")
    except Exception as exc:
        print(f"  ✗ Gemini — {type(exc).__name__}: {str(exc)[:160]}")


def main() -> int:
    do_probe = "--probe" in sys.argv
    print(f"프로세스: {sys.executable}")
    try:
        used = load_local_env()
    except ValueError as exc:
        print(f"\n키 파일을 못 읽었다: {exc}")
        broken = next((p for p in candidates() if p.exists()), None)
        if broken:
            describe_lines(broken)
            print("\n각 줄은 'KEY=값' 형태여야 한다. 메모는 '#' 로 시작할 것.")
        return 1
    print("키 파일 후보 (위에서부터 찾는다):")
    for path in candidates():
        mark = "←읽음" if path == used else ("있음  " if path.exists() else "없음  ")
        print(f"  {mark} {path}")
    if used is not None and used != PREFERRED:
        print(f"  ! 프로젝트 폴더는 보통 Users 그룹에 읽기가 열려 있다.")
        print(f"    민감하면 {wsl_path(PREFERRED)} 로 옮길 것 (ACL 로 격리됨).")
    print()
    missing = 0
    for name, prefix, use in KEYS:
        value = os.environ.get(name, "")
        if not value:
            print(f"✗ {name:20s} 없음                  ({use})")
            missing += 1
            continue
        # 길이와 접두사만 본다. 값은 찍지 않는다.
        src = loaded_from.get(name)
        origin = src.name if src else "환경변수"
        note = "" if value.startswith(prefix) else f"  (접두사 {prefix!r} 아님 — 정상일 수 있다)"
        print(f"✓ {name:20s} {len(value)}자, {origin}  ({use}){note}")
        if value != value.strip():
            print(f"  ! 앞뒤 공백이 붙어 있다 — 따옴표나 개행이 섞였을 수 있다")

    if missing:
        print(
            "\n없는 키가 있다. WSL 셸에서:\n"
            f"  mkdir -p {wsl_path(PREFERRED.parent)}\n"
            f"  nano {wsl_path(PREFERRED)}\n"
            "에디터에 한 줄씩 적는다:\n"
            "  ANTHROPIC_API_KEY=sk-ant-...\n"
            "  GEMINI_API_KEY=AIza...\n"
            "에디터로 넣으면 셸 기록에 남지 않는다. 자세한 건 PROGRESS.md 의 'API 키' 항목."
        )
    if do_probe and not missing:
        probe()
    elif not missing:
        print("\n키가 실제로 통하는지는 --probe 로 확인 (각 프로바이더에 1회씩 호출).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
