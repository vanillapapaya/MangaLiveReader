"""트레이 아이콘으로 서비스를 관리한다. `MangaLiveReader.vbs` 가 pythonw 로 부른다.

콘솔 창 없이 서비스를 띄우고 트레이에 아이콘을 남긴다. 우클릭 메뉴로 재시작·
로그 열기·종료. 서비스가 죽으면 알림을 띄우고 트레이는 남아 있으므로 메뉴에서
다시 띄울 수 있다.

**설치는 하지 않는다.** venv 가 없으면 `MangaLiveReader.cmd`(부트스트랩)를 대신
띄우고 물러난다 — 7GB 를 받는 과정은 창이 보여야 하고, 첫 실행에는 API 키를
물어봐야 한다.

로그는 `logs/service.log` 에 누적한다. pythonw 는 stdout 이 없어서 이 파일이
없으면 기동 실패 원인을 볼 방법이 아예 없다.
"""

from __future__ import annotations

import ctypes
import os
import socket
import subprocess
import sys
import threading
import time
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = ROOT / ".venv" / "Scripts" / "python.exe"
BOOTSTRAP = ROOT / "MangaLiveReader.cmd"
LOG_PATH = ROOT / "logs" / "service.log"
ICON_PNG = ROOT / "extension" / "icons" / "icon128.png"

#: 두 번 켜지는 것을 막는 자물쇠. 포트를 잡는 쪽이 이긴다.
INSTANCE_PORT = 50816
#: subprocess 를 콘솔 창 없이 띄운다 (Windows).
CREATE_NO_WINDOW = 0x08000000

state: dict = {"proc": None, "log": None, "stop": False, "icon": None}


def message_box(text: str, title: str = "MangaLiveReader", icon: int = 0x40) -> None:
    ctypes.windll.user32.MessageBoxW(0, text, title, icon | 0x1000)


def service_port() -> int:
    """설정에서 포트를 읽는다. service.local.toml 이 service.toml 을 덮는다."""
    port = 8788
    for name in ("service.toml", "service.local.toml"):
        path = ROOT / name
        if not path.exists():
            continue
        try:
            with path.open("rb") as f:
                port = tomllib.load(f).get("server", {}).get("port", port)
        except (OSError, tomllib.TOMLDecodeError):
            pass  # 설정이 깨졌으면 서비스 기동이 알아서 실패한다. 여기서는 기본값.
    return port


def service_endpoint() -> tuple[str, int]:
    """서비스가 뜰 주소. **기동 코드에게 직접 물어본다.**

    루프백이라고 가정하면 안 된다 — 설정에 따라 Tailscale 주소나 `bind_host` 에
    적은 주소에 뜬다. 같은 규칙을 여기서 다시 구현하면 언젠가 갈린다.
    """
    port = service_port()
    try:
        from mtl_service.__main__ import resolve_host
        from mtl_service.config import load

        return resolve_host(load().server), port
    except Exception:  # noqa: BLE001 — 설정이 깨졌으면 기동이 알아서 알려 준다
        return "127.0.0.1", port


def service_already_up(host: str, port: int) -> bool:
    """다른 창이 이미 서비스를 띄워 뒀는가.

    아래 `acquire_single_instance` 는 **트레이가 둘 켜지는 것**만 막는다. 콘솔
    런처(`MangaLiveReader.cmd`)로 띄워 둔 서비스는 그 자물쇠를 잡지 않으므로,
    그 상태에서 바탕화면 아이콘을 누르면 트레이가 서비스를 하나 더 띄운다.
    두 번째는 포트를 못 잡고 죽고, 트레이는 「서비스가 멈췄습니다」를 띄운다 —
    멀쩡히 돌고 있는데도.

    **붙어 보는 것 말고는 방법이 없다.** 처음에는 그 포트에 bind 를 시도해 봤는데,
    윈도우는 특정 주소가 잡혀 있어도 와일드카드 bind 를 허용한다 (유닉스와 다르다).
    Tailscale 주소에 떠 있는 서비스를 상대로 실측했더니 그대로 「안 떠 있음」이
    나왔다.
    """
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def acquire_single_instance() -> socket.socket | None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", INSTANCE_PORT))
        sock.listen(1)
        return sock
    except OSError:
        sock.close()
        return None


# ---------------------------------------------------------------------------
# 서비스 프로세스
# ---------------------------------------------------------------------------
def spawn() -> subprocess.Popen:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log = LOG_PATH.open("a", encoding="utf-8", buffering=1)
    log.write(f"\n\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} 기동 ===\n")
    state["log"] = log
    return subprocess.Popen(
        [str(PY), "-X", "utf8", "-m", "mtl_service"],
        cwd=str(ROOT),
        stdout=log,
        stderr=subprocess.STDOUT,
        creationflags=CREATE_NO_WINDOW,
    )


def server_loop() -> None:
    """서비스를 띄우고 지켜본다. 죽으면 알리고 idle 로 기다린다."""
    while not state["stop"]:
        proc = spawn()
        state["proc"] = proc
        code = proc.wait()

        log = state.get("log")
        if log:
            log.write(f"=== 종료 {code} ===\n")
            log.close()

        if state["stop"]:
            break

        # 재기동을 자동으로 하지 않는다. 기동 거부는 대개 설정 문제(바인딩 주소,
        # 인증 토큰)라 다시 띄워도 같은 자리에서 또 죽는다. 무한 루프가 된다.
        notify("서비스가 멈췄습니다", f"종료 코드 {code}. 메뉴의 「로그 열기」로 원인을 봅니다.")
        state["proc"] = None
        while not state["stop"] and state["proc"] is None:
            time.sleep(0.5)


def notify(title: str, message: str) -> None:
    icon = state.get("icon")
    if icon is None:
        return
    try:
        icon.notify(message, title)
    except Exception:  # noqa: BLE001 — 알림은 있으면 좋은 것이지 필수가 아니다
        pass


# ---------------------------------------------------------------------------
# 메뉴
# ---------------------------------------------------------------------------
def is_running() -> bool:
    proc = state.get("proc")
    return proc is not None and proc.poll() is None


def on_restart(icon, item) -> None:
    proc = state.get("proc")
    if proc is not None and proc.poll() is None:
        proc.terminate()  # server_loop 이 받아서 idle 로 간다
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    threading.Thread(target=lambda: state.__setitem__("proc", spawn()), daemon=True).start()


def on_open_log(icon, item) -> None:
    if LOG_PATH.exists():
        os.startfile(str(LOG_PATH))  # noqa: S606 — 우리가 만든 고정 경로
    else:
        notify("로그 없음", "아직 기록된 것이 없습니다.")


def on_quit(icon, item) -> None:
    state["stop"] = True
    proc = state.get("proc")
    if proc is not None and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    icon.stop()


def main() -> int:
    if not PY.exists():
        # 아직 설치 전이다. 설치는 창이 보여야 하므로 부트스트랩에 넘긴다.
        if BOOTSTRAP.exists():
            os.startfile(str(BOOTSTRAP))  # noqa: S606
        else:
            message_box(
                f"파이썬 환경이 없습니다:\n{PY}\n\nMangaLiveReader.cmd 를 먼저 실행하세요.",
                icon=0x10,
            )
        return 1

    try:
        import pystray
        from PIL import Image
    except ImportError:
        message_box(
            "트레이 아이콘에 필요한 pystray 가 없습니다.\n\n"
            "MangaLiveReader.cmd 를 한 번 실행하면 설치됩니다.\n"
            "(또는  uv sync --extra launcher )",
            icon=0x10,
        )
        return 1

    lock = acquire_single_instance()
    if lock is None:
        message_box("이미 실행 중입니다.\n트레이 아이콘을 확인하세요.")
        return 0

    host, port = service_endpoint()
    if service_already_up(host, port):
        message_box(
            f"서비스가 이미 http://{host}:{port} 에 떠 있습니다.\n\n"
            "콘솔 창(MangaLiveReader.cmd)으로 켜 두셨다면 그 창을 닫은 뒤\n"
            "다시 눌러 주세요. 트레이로 관리하려면 그쪽 하나만 띄웁니다."
        )
        return 0

    image = Image.open(ICON_PNG)

    threading.Thread(target=server_loop, daemon=True).start()

    icon = pystray.Icon(
        "mangalivereader",
        image,
        f"MangaLiveReader · {host}:{port}",
        menu=pystray.Menu(
            pystray.MenuItem(
                lambda item: "켜짐" if is_running() else "꺼짐",
                lambda icon, item: None,
                enabled=False,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("재시작", on_restart),
            pystray.MenuItem("로그 열기", on_open_log, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("종료", on_quit),
        ),
    )
    state["icon"] = icon
    icon.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
