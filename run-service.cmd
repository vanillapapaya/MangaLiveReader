@echo off
rem Run the service on Windows (for MacBook / other devices on the tailnet).
rem
rem Use this instead of `py -X utf8 -m mtl_service`: `py` is the system Python
rem and lacks this project's dependencies.
rem
rem Note: the venv's python.exe hands off to the base interpreter, so the running
rem process image path is the SYSTEM python, not .venv\Scripts\python.exe.
rem That is why the firewall rule 'mtl-service (Tailscale)' must NOT be scoped by
rem program - it is scoped by interface (Tailscale) + profile (Private) + port.
rem
rem To run under WSL instead: run-service.sh + dev_bind_loopback = true

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo No Windows venv. Run: uv sync
  exit /b 1
)

rem -X utf8 is required, otherwise Korean log output breaks under cp949.
.venv\Scripts\python.exe -X utf8 -m mtl_service %*
