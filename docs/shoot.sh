#!/usr/bin/env bash
# README 용 그림 두 장을 찍는다. **윈도우 크롬을 헤드리스로 쓴다** — WSL 에는
# 화면도 크롬도 없다. 경로는 크롬에 넘길 것이라 윈도우 형식이어야 한다.
#
#   demo.png     합성 만화 페이지 + 진짜 오버레이 (docs/demo.html)
#   options.png  진짜 옵션 화면. 값은 찍을 때만 채운다 — 확장 밖에서 열면
#                chrome.storage 가 없어 칸이 전부 비기 때문이다
set -euo pipefail
cd "$(dirname "$0")/.."
CHROME="/mnt/c/Program Files/Google/Chrome/Application/chrome.exe"
WIN='C:\code\MangaLiveReader'

shoot() {  # shoot <파일> <가로,세로> <결과>
  "$CHROME" --headless --disable-gpu --hide-scrollbars \
    --screenshot="$WIN\\docs\\$3" --window-size="$2" "file:///$WIN\\$1" 2>&1 |
    grep -viE "^\[|DevTools|Fontconfig" || true
}

shoot 'docs\demo.html' 1280,1200 demo.png

# 옵션 화면은 실물에서 파생시킨다 — 복사본을 두면 언젠가 어긋난다.
{
  cat extension/options.html
  cat <<'JS'
<script>
  document.getElementById("url").value = "http://127.0.0.1:8788/read";
  document.getElementById("token").value = "";
  document.getElementById("autosites").value = "comic-walker.com\nyanmaga.jp";
  document.getElementById("autositeson").checked = true;
  document.getElementById("autopaths").value = "viewer, episode, chapter";
  document.getElementById("ttsurl").value = "";
</script>
JS
} > docs/.shot-options.html
shoot 'docs\.shot-options.html' 860,1180 options.png
rm -f docs/.shot-options.html

ls -l docs/demo.png docs/options.png
