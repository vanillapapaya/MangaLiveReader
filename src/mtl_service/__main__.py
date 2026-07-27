"""서비스 기동. DESIGN.md §4.4, §9.2.

    uv run mtl-service
    pythonw.exe -m mtl_service      # 작업 스케줄러 등록용 (§9.2)

바인딩 주소는 절대 `0.0.0.0` 이 되지 않는다. Tailscale 인터페이스를 못 찾으면
기동을 거부한다 — 만화 뷰어 화면을 통째로 받는 엔드포인트를 LAN 에 여는 것은
설정 실수 한 번으로 일어나면 안 되는 일이다.
"""

from __future__ import annotations

import ipaddress
import socket
import subprocess
import sys

import uvicorn

from .config import ServerConfig, load

#: Tailscale 이 쓰는 CGNAT 대역
_TAILSCALE_NET = ipaddress.ip_network("100.64.0.0/10")


def find_tailscale_ip() -> str | None:
    """이 머신의 Tailscale IPv4 주소. 못 찾으면 None."""
    try:
        out = subprocess.run(
            ["tailscale", "ip", "-4"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        ).stdout.strip()
        for line in out.splitlines():
            if _in_tailscale_net(line.strip()):
                return line.strip()
    except (OSError, subprocess.SubprocessError):
        pass  # CLI 가 PATH 에 없을 수 있다. 아래 폴백으로 간다.

    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    except socket.gaierror:
        return None
    for info in infos:
        addr = info[4][0]
        if _in_tailscale_net(addr):
            return addr
    return None


def _in_tailscale_net(addr: str) -> bool:
    try:
        return ipaddress.ip_address(addr) in _TAILSCALE_NET
    except ValueError:
        return False


def resolve_host(server: ServerConfig) -> str:
    if server.dev_bind_loopback:
        return "127.0.0.1"
    if server.bind_tailscale_only:
        ip = find_tailscale_ip()
        if ip is None:
            raise SystemExit(
                "Tailscale 인터페이스를 찾지 못해 기동을 거부한다.\n"
                "  · tailscale status 로 연결 확인\n"
                "  · 개발 중이라면 service.toml 의 [server].dev_bind_loopback = true"
            )
        return ip
    raise SystemExit(
        "service.toml [server]: bind_tailscale_only 와 dev_bind_loopback 이 둘 다 false 다.\n"
        "0.0.0.0 폴백은 없다 (DESIGN.md §4.4). 둘 중 하나를 켤 것."
    )


def main() -> int:
    cfg = load()
    host = resolve_host(cfg.server)

    if cfg.server.auth_disabled:
        print("! 인증이 꺼져 있다 (service.toml [server].auth_disabled)", file=sys.stderr)

    print(f"mtl-service → http://{host}:{cfg.server.port}")
    uvicorn.run(
        "mtl_service.app:app",
        host=host,
        port=cfg.server.port,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
