// 드래그로 영역 고르기
//
// `content.js` 에서 갈라 나온 파일이다. **모듈이 아니다** — MV3 콘텐츠
// 스크립트는 manifest 의 `js` 배열 순서대로 같은 전역에서 실행된다.
// 그래서 import 없이 서로의 함수를 그냥 부른다. 순서를 바꾸면 깨진다.


// ---------------------------------------------------------------------------
// 드래그로 영역 고르기
// ---------------------------------------------------------------------------

const SELECT_ID = "mlr-select";

/** 드래그가 이것보다 작으면 클릭으로 보고 취소한다. */
const MIN_DRAG_PX = 40;

/**
 * 화면을 덮고 드래그를 받아 그 사각형으로 읽는다.
 *
 * `probeViewer()` 는 기하 휴리스틱이라 반드시 틀리는 사이트가 나온다 — 뷰어가
 * 200px 미만 타일로 쪼개져 있거나, `img`/`canvas` 가 아닌 배경 이미지로 그려지거나,
 * 광고가 만화보다 크거나. 그때 손으로 집을 수단이 없으면 그 사이트는 통째로 못 쓴다.
 *
 * 좌표계는 `probeViewer()` 와 같다 — 뷰포트 CSS 픽셀. `toCss()` 가 그대로 쓴다.
 *
 * **덮개는 포인터 이벤트를 먹는다.** `#mlr-overlay` 와 달리 통과시키면 안 된다 —
 * canvas 뷰어는 자기 드래그 핸들러(페이지 넘김)를 갖고 있어서 그쪽이 먼저 먹는다.
 */
function startSelection() {
  cancelSelection(); // 두 번 눌러도 하나만 뜨게

  const box = document.createElement("div");
  box.id = SELECT_ID;
  box.innerHTML =
    '<div class="mlr-sel-rect" hidden></div>' +
    '<div class="mlr-sel-hint">읽을 영역을 드래그하세요 · Esc 취소</div>';
  (fsElement() ?? document.documentElement).appendChild(box);

  const rectEl = box.querySelector(".mlr-sel-rect");
  let start = null;

  const draw = (x, y) => {
    const l = Math.min(start.x, x);
    const t = Math.min(start.y, y);
    Object.assign(rectEl.style, {
      left: `${l}px`,
      top: `${t}px`,
      width: `${Math.abs(x - start.x)}px`,
      height: `${Math.abs(y - start.y)}px`,
    });
    rectEl.hidden = false;
  };

  const onDown = (e) => {
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    start = { x: e.clientX, y: e.clientY };
    draw(e.clientX, e.clientY);
    box.setPointerCapture?.(e.pointerId);
  };

  const onMove = (e) => {
    if (!start) return;
    e.preventDefault();
    draw(e.clientX, e.clientY);
  };

  const onUp = (e) => {
    if (!start) return;
    e.preventDefault();
    e.stopPropagation();
    const r = clampToViewport(
      {
        left: Math.min(start.x, e.clientX),
        top: Math.min(start.y, e.clientY),
        right: Math.max(start.x, e.clientX),
        bottom: Math.max(start.y, e.clientY),
      },
      window.innerWidth,
      window.innerHeight
    );
    cancelSelection();
    if (r.width < MIN_DRAG_PX || r.height < MIN_DRAG_PX) {
      status("영역이 너무 작다. 다시 드래그하세요.", true);
      return;
    }
    // **덮개를 지운 뒤 두 프레임 기다린다.** 안 그러면 캡처에 덮개가 찍혀
    // 검출기가 반투명 회색 층 너머의 글자를 보게 된다. `hide-overlay` 왕복만으로
    // 충분할 때가 많지만 보장은 안 된다.
    requestAnimationFrame(() =>
      requestAnimationFrame(() =>
        send({
          type: "read-rect",
          // 보내는 것은 넓힌 창, 남길 것은 고른 범위.
          rect: expandForDetector(r, window.innerWidth, window.innerHeight),
          clip: r,
          dpr: dpr(),
          // 전체 읽기 박스와 id 가 부딪히면 번역이 엉뚱한 박스에 꽂힌다.
          prefix: `s${++partialSeq}-`,
        })
      )
    );
  };

  const onKey = (e) => {
    if (e.key === "Escape") {
      e.preventDefault();
      cancelSelection();
      status("영역 선택 취소");
    }
  };

  box._teardown = () => {
    box.removeEventListener("pointerdown", onDown);
    box.removeEventListener("pointermove", onMove);
    box.removeEventListener("pointerup", onUp);
    window.removeEventListener("keydown", onKey, true);
  };
  box.addEventListener("pointerdown", onDown);
  box.addEventListener("pointermove", onMove);
  box.addEventListener("pointerup", onUp);
  window.addEventListener("keydown", onKey, true);

  status("읽을 영역을 드래그하세요 · Esc 취소");
}

function cancelSelection() {
  const el = document.getElementById(SELECT_ID);
  if (!el) return;
  el._teardown?.();
  el.remove();
}
