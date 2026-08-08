@echo off
rem ---------------------------------------------------------------------------
rem MangaLiveReader bootstrap launcher. Double-click this file.
rem
rem Installs whatever is missing (uv, Python 3.11, venv, model weights, API key)
rem and then starts the service. Every step is idempotent, so the second run
rem just starts the service in a few seconds.
rem
rem KEEP THIS FILE ASCII-ONLY. cmd.exe reads batch files in the console code
rem page (cp949 on Korean Windows); UTF-8 Korean here renders as mojibake and
rem changing the code page mid-file breaks GOTO. All Korean UI lives in
rem scripts\setup.ps1, which PowerShell reads as UTF-8 (the file has a BOM).
rem ---------------------------------------------------------------------------
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\setup.ps1" %*
