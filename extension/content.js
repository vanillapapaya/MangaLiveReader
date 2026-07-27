// 콘텐츠 스크립트. 뷰어 요소를 찾아 알려주고, 결과를 페이지 위에 겹친다.
//
// 이 파일이 확장 경로의 우위를 보여주는 곳이다. OS 캡처 경로는 화면 좌표를
// 추측해야 하지만(DESIGN.md §6 의 ±4px 예산, Retina 배율, DPI 125%), 여기서는
// DOM 이 "만화가 이 사각형에 있다" 고 직접 알려준다.

const OVERLAY_ID = "mlr-overlay";
let ctx = null; // { rect, scale, dpr } — 좌표 환산에 필요한 값

/** 부분 읽기로 만든 박스 묶음에 붙일 번호. 전체 읽기 박스(mlr-box-0…)와 id 가
 *  부딪히지 않게 한다. 드래그 선택(`s1-`)과 다시 읽기(`r1-`)가 같이 쓴다.
 *
 *  **쓰는 곳보다 위에 둔다** — `let` 은 초기화 전에 못 읽는다(TDZ). 지금은 둘 다
 *  사용자 조작 시점에 불려 안 터지지만, 초기화 중에 부르는 순간 깨진다. */
let partialSeq = 0;

/** 지금 보고 있는 페이지의 신원(전체 읽기의 phash). `begin` 에서 받는다. */
let pageKey = null;

/** 지운 박스의 중심(뷰어 기준 비율). 복원할 때 이 근처 박스를 다시 지운다. */
let removedMarks = [];

/** 마지막 probe 에서 본 뷰어 요소들. 박스가 스크롤을 따라가는 데 쓴다. */
let viewerEls = [];

// ---------------------------------------------------------------------------
// 확장이 다시 로드되면 이 스크립트는 유령이 된다
//
// 개발 중 `chrome://extensions` 에서 새로고침하면 **페이지에 남아 있던 이 스크립트는
// 그대로 돈다.** 하지만 `chrome.runtime` 핸들이 죽어서 호출할 때마다
// `Extension context invalidated` 가 터진다. MutationObserver 가 계속 신호를 쏘므로
// 콘솔이 오류로 뒤덮인다.
//
// 죽은 것을 알아채고 **스스로 멈춘 뒤 새로고침하라고 알린다.** 조용히 망가지는 것보다
// 낫다 — 사용자는 왜 아무 반응이 없는지 알 길이 없다.
// ---------------------------------------------------------------------------

let stale = false;

function alive() {
  try {
    return Boolean(chrome.runtime?.id);
  } catch {
    return false; // 컨텍스트가 죽으면 접근 자체가 던진다
  }
}

function goStale() {
  if (stale) return;
  stale = true;
  try {
    observer?.disconnect();
  } catch {}
  stopSpeaking();
  const el = document.getElementById(OVERLAY_ID);
  if (el) {
    el.style.visibility = "visible";
    const st = el.querySelector("#mlr-status");
    if (st) {
      st.textContent = "확장이 다시 로드됐다 — 이 페이지를 새로고침하세요 (Cmd/Ctrl+R)";
      st.classList.add("mlr-error");
    }
  }
}

/** background 로 보낸다. 컨텍스트가 죽었으면 조용히 접고 안내한다. */
function send(msg) {
  if (stale || !alive()) return goStale();
  try {
    chrome.runtime.sendMessage(msg)?.catch?.(() => {});
  } catch {
    goStale();
  }
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  switch (msg.type) {
    case "probe":
      // 여기서 예외가 나면 background 는 undefined 를 받고 "요소를 못 찾았다" 로
      // 오해한다. 진짜 원인을 넘겨서 상태 표시줄에 그대로 뜨게 한다.
      try {
        sendResponse(probeViewer());
      } catch (err) {
        sendResponse({ error: String(err?.stack || err) });
      }
      return true;
    case "hide-overlay":
      overlay().style.visibility = "hidden";
      sendResponse({ ok: true });
      return true;
    case "show-overlay":
      // 자동 감지가 "안 바뀌었다" 로 끝났을 때 되돌린다. 이게 없으면 확인할 때마다
      // 오버레이가 깜빡인다.
      overlay().style.visibility = "visible";
      sendResponse({ ok: true });
      return true;
    case "select-region":
      startSelection();
      break;
    case "auto-state":
      autoOn = msg.on;
      syncPanel();
      status(autoOn ? "자동 감지 켜짐 — 페이지를 넘기면 바로 읽는다" : "자동 감지 꺼짐");
      break;
    case "begin":
      // `prefix` 는 한 페이지 안에서 여러 번 읽을 때 box id 가 부딪히지 않게 한다.
      // 전체 읽기는 "" 이고 기존 박스를 전부 지운다. 영역 하나만 다시 읽는
      // 경우(우클릭 → 다시 읽기)는 지우면 안 되므로 접두사로 갈라 둔다.
      ctx = {
        rect: msg.rect,
        scale: msg.scale,
        dpr: msg.dpr,
        prefix: msg.prefix || "",
        clip: msg.clip || null,
        partial: Boolean(msg.partial),
      };
      // **전체 읽기일 때만 기존 박스를 지운다.** 영역을 새로 지정한 것은 "여기도
      // 읽어줘" 지 "나머지는 버려" 가 아니다. 예전에는 `merge` 유무로 갈랐는데
      // 드래그 선택에는 merge 가 없어서 기존 번역이 통째로 사라졌다.
      if (msg.partial) {
        if (msg.merge) document.getElementById(`mlr-box-${msg.merge}`)?.remove();
      } else {
        reset();
        extraCount = 0;
        // 페이지가 바뀌었으면 신원도 바뀐다. 부분 읽기는 조각의 해시라 안 쓴다.
        pageKey = msg.pageKey || null;
      }
      status(`검출 중… (${msg.elapsed}ms)`);
      break;
    case "status":
      status(`${msg.message} (${msg.elapsed}ms)`);
      break;
    case "cached":
      status(msg.data.hit ? `캐시 적중 (${msg.elapsed}ms)` : `캐시 없음 (${msg.elapsed}ms)`);
      break;
    case "ocr":
      // 지금 뷰어가 어디 있는지 박아 둔다. 이후 스크롤·확대는 이 기준으로 따라간다.
      {
        const live = viewerEls.filter((el) => el.isConnected);
        if (live.length) {
          const u = unionRects(live.map((el) => el.getBoundingClientRect()));
          ctx.anchor = { left: u.left, top: u.top, width: u.right - u.left };
        }
        const b = overlay().querySelector("#mlr-boxes");
        if (b) b.style.transform = "";
      }
      drawBoxes(msg.data.regions);
      status(`원문 ${msg.data.regions.length}개 · ${msg.elapsed}ms`);
      break;
    case "translation":
      fillTranslation(msg.data);
      break;
    case "done":
      status(`완료 ${msg.elapsed}ms · 번역 ${msg.data.timings.translate}ms`);
      // 손으로 더한 박스를 되살린다. 전체 읽기가 끝난 뒤라야 지울 것/더할 것을
      // 제자리에 맞출 수 있다.
      if (!ctx?.partial) restoreEdits();
      else saveEdits();
      break;
    case "error":
      status(`오류: ${msg.data.message}`, true);
      break;
  }
  sendResponse({ ok: true });
  return true;
});

/**
 * 뷰포트에서 만화 뷰어로 보이는 요소를 찾는다.
 *
 * 씨앗 하나를 잡고 **그 옆에 나란히 놓인 비슷한 크기의 것들을 합친다.**
 * 펼침면이 이 경우다.
 *
 * 처음에는 "같은 부모의 형제" 로 묶었는데 실패했다 — 펼침면은 보통 각 페이지가
 * 별도 div 로 감싸여 있어 형제가 아니다. DOM 구조는 사이트마다 다르므로 기대면
 * 안 된다. 대신 **기하로만** 판단한다: 씨앗과 세로로 충분히 겹치고(같은 행) 면적이
 * 씨앗에 견줄 만하면 같은 펼침면으로 본다.
 *
 * 실측(sunday-webry): 페이지가 837×1316 canvas 로 **가로 캐러셀**에 늘어서 있다.
 * 보이는 것은 x=42 와 x=879 둘이고 나머지는 x 가 음수(왼쪽 화면 밖)이거나
 * x=1758(오른쪽 화면 밖)이다. 화면 밖 것을 걸러내고 보이는 둘을 합치면
 * x 42..1716, 폭 1674 — 펼침면 하나가 정확히 나온다.
 */
function probeViewer() {
  const vw = window.innerWidth;
  const vh = window.innerHeight;

  const cands = [];
  for (const el of document.querySelectorAll("img, canvas")) {
    const r = el.getBoundingClientRect();
    if (r.width < 200 || r.height < 200) continue;
    if (r.bottom <= 0 || r.top >= vh || r.right <= 0 || r.left >= vw) continue;
    // 뷰포트와 겹치는 면적으로 센다 — 화면 밖으로 나간 부분은 캡처되지 않는다
    const w = Math.min(r.right, vw) - Math.max(r.left, 0);
    const h = Math.min(r.bottom, vh) - Math.max(r.top, 0);
    if (w < 200 || h < 200) continue;
    cands.push({ el, r, area: w * h });
  }

  if (cands.length === 0) {
    return { rect: { x: 0, y: 0, width: vw, height: vh }, tag: "viewport(폴백)", dpr: dpr(), count: 0 };
  }

  // **씨앗은 뷰포트 중앙에 가장 가까운 것으로 고른다.** 면적으로 고르면 안 된다 —
  // 세로 스크롤 뷰어는 펼침면을 위아래로 쌓아 화면에 4페이지를 보여주기도 하는데
  // (sunday-webry 실측), 그때 면적 최대는 지금 읽고 있는 페이지가 아닐 수 있다.
  const cy = vh / 2;
  cands.sort((a, b) => {
    const d = (r) => Math.abs((Math.max(r.top, 0) + Math.min(r.bottom, vh)) / 2 - cy);
    return d(a.r) - d(b.r) || b.area - a.area;
  });
  const seed = cands[0];

  // 씨앗과 "같은 행" 이고 크기가 견줄 만한 것들 = 한 펼침면.
  // **행 하나만 잡는다.** 4페이지를 한 번에 보내면 order.py 의 펼침면 분할(2페이지
  // 가정)이 엉키고, 페이지당 1회 호출이라는 §8.4 전제도 무너진다.
  const group = cands.filter((c) => {
    if (c === seed) return true;
    if (c.area < seed.area * 0.25) return false;
    const overlap =
      Math.min(c.r.bottom, seed.r.bottom) - Math.max(c.r.top, seed.r.top);
    return overlap >= 0.6 * Math.min(c.r.height, seed.r.height);
  });

  // 스크롤·확대를 따라가려면 **사각형이 아니라 요소**를 들고 있어야 한다.
  viewerEls = group.map((c) => c.el);
  const rect = clampToViewport(unionRects(group.map((c) => c.r)), vw, vh);
  const tag =
    seed.el.tagName.toLowerCase() + (group.length > 1 ? ` ×${group.length}(펼침면)` : "");
  return { rect, tag, dpr: dpr(), count: group.length };
}

function dpr() {
  return window.devicePixelRatio || 1;
}

function unionRects(rects) {
  return {
    left: Math.min(...rects.map((r) => r.left)),
    top: Math.min(...rects.map((r) => r.top)),
    right: Math.max(...rects.map((r) => r.right)),
    bottom: Math.max(...rects.map((r) => r.bottom)),
  };
}

/** 검출기에 보낼 창을 넓힌다.
 *
 * 검출기는 만화 **페이지 전체**로 학습돼서, 글자가 화면을 크게 차지하면 분포 밖이라
 * 아무것도 못 찾는다. 실측(shonenjumpplus_002430, 221×243 말풍선):
 *
 *   넓힌 배율   선택이 차지하는 폭   검출
 *   1.0배       100%                0개
 *   1.5배        67%                0개
 *   2.0배        50%                0개
 *   2.5배        40%                2개
 *   3.0배        33%                3개  ← 고른 말풍선을 되찾음
 *
 * 그래서 **고른 것보다 3배 넓게 잘라 보내고, 결과는 고른 범위로 걸러낸다.**
 * 사용자가 고른 범위는 그대로 지켜지고 검출기만 문맥을 얻는다.
 */
const DETECT_CONTEXT_K = 3;

function expandForDetector(r, vw, vh) {
  const cx = r.x + r.width / 2;
  const cy = r.y + r.height / 2;
  const w = r.width * DETECT_CONTEXT_K;
  const h = r.height * DETECT_CONTEXT_K;
  return clampToViewport(
    { left: cx - w / 2, top: cy - h / 2, right: cx + w / 2, bottom: cy + h / 2 },
    vw,
    vh
  );
}

function clampToViewport(r, vw, vh) {
  const x = Math.max(0, r.left);
  const y = Math.max(0, r.top);
  return {
    x,
    y,
    width: Math.min(r.right, vw) - x,
    height: Math.min(r.bottom, vh) - y,
  };
}

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
  document.documentElement.appendChild(box);

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

// ---------------------------------------------------------------------------
// 오버레이
// ---------------------------------------------------------------------------

// **`overlay()` 보다 위에 둔다.** `const`/`let` 은 호이스팅돼도 초기화 전에는
// 못 읽는다(TDZ). 지금은 `overlay()` 가 늦게 불려 안 터지지만, 누가 초기화 중에
// 부르는 순간 바로 깨진다.
// 버튼 여덟 개를 늘 띄워 두면 화면을 가리고, 정작 쓰는 것은 두셋이다.
// **자주 쓰는 셋만 보이고 나머지는 「⋯」 안에 접는다.**
const PANEL_HTML = `
<div id="mlr-panel">
  <button data-act="read"   title="이 페이지를 캡처해 번역한다 (Alt+Shift+M)">번역</button>
  <button data-act="select" title="읽을 영역을 드래그로 고르기 (Alt+Shift+D)">영역</button>
  <button data-act="auto"   title="페이지가 넘어가면 자동으로 읽는다">자동</button>
  <button data-act="more"   title="나머지 기능" class="mlr-more">⋯</button>
  <div class="mlr-rest" hidden>
    <button data-act="fresh"  title="이 페이지의 캐시를 지우고 다시 번역한다">갱신</button>
    <button data-act="speak"  title="원문을 읽기 순서대로 소리내어 읽는다">음성</button>
    <button data-act="labels" title="라벨 전체 펼치기/접기 (Alt+Shift+L)">라벨</button>
    <button data-act="extra"  title="숨긴 효과음·잡문 보기 (Alt+Shift+S)">효과음</button>
    <button data-act="status" title="왼쪽 위 상태줄 켜기/끄기">상태</button>
  </div>
</div>`;

/** 자동 감지가 켜져 있는가. background 가 `auto-state` 로 알려 준다. */
let autoOn = false;

function overlay() {
  let el = document.getElementById(OVERLAY_ID);
  if (!el) {
    el = document.createElement("div");
    el.id = OVERLAY_ID;
    el.innerHTML =
      '<div id="mlr-status"></div><div id="mlr-boxes"></div>' + PANEL_HTML;
    document.documentElement.appendChild(el);
    bindPanel(el);
  }
  el.style.visibility = "visible";
  return el;
}

// ---------------------------------------------------------------------------
// 조작 패널
//
// 단축키만 있으면 외워야 쓴다. 화면에 버튼을 두면 처음 보는 사람도 쓸 수 있고,
// 자동 감지처럼 **상태가 있는 기능**은 지금 켜져 있는지 눈으로 보여야 한다.
//
// 패널은 `#mlr-overlay` 안에 둔다 — 캡처 직전 `hide-overlay` 로 같이 숨겨져야
// 스크린샷에 안 찍힌다. 대신 오버레이는 `pointer-events: none` 이라 패널만
// `auto` 로 되돌린다 (아래 CSS).
// ---------------------------------------------------------------------------

function bindPanel(root) {
  root.querySelector("#mlr-panel").addEventListener("click", (e) => {
    const act = e.target?.dataset?.act;
    if (!act) return;
    e.preventDefault();
    e.stopPropagation();
    switch (act) {
      case "read":
        send({ type: "do-read" });
        break;
      case "fresh":
        // 「번역」은 캐시를 쓴다(같은 페이지면 공짜·같은 문장). 결과가 이상할 때만
        // 이걸로 낡은 캐시를 버리고 다시 읽는다.
        send({ type: "do-read", refresh: true });
        break;
      case "select":
        startSelection();
        break;
      case "auto":
        send({ type: "set-auto", on: !autoOn });
        break;
      case "labels":
        root.classList.toggle("mlr-show-all");
        layoutLabels();
        syncPanel();
        break;
      case "extra":
        root.classList.toggle("mlr-show-extra");
        layoutLabels();
        syncPanel();
        break;
      case "status":
        root.classList.toggle("mlr-hide-status");
        syncPanel();
        break;
      case "speak":
        if (speaking) stopSpeaking();
        else speakAll();
        break;
      case "more": {
        const rest = root.querySelector("#mlr-panel .mlr-rest");
        rest.hidden = !rest.hidden;
        break;
      }
    }
  });
  syncPanel();
}

/** 켜짐/꺼짐이 있는 버튼에 표시를 맞춘다. */
function syncPanel() {
  const root = document.getElementById(OVERLAY_ID);
  if (!root) return;
  const on = (act, v) =>
    root.querySelector(`#mlr-panel [data-act="${act}"]`)?.classList.toggle("mlr-on", v);
  on("auto", autoOn);
  on("labels", root.classList.contains("mlr-show-all"));
  on("extra", root.classList.contains("mlr-show-extra"));
  // 상태줄은 기본이 켜짐이라 이 버튼만 처음부터 켜진 표시다.
  on("status", !root.classList.contains("mlr-hide-status"));
  on("speak", speaking);
}

function reset() {
  overlay().querySelector("#mlr-boxes").innerHTML = "";
}

function status(text, isError = false) {
  const el = overlay().querySelector("#mlr-status");
  el.textContent = text;
  el.classList.toggle("mlr-error", isError);
}

/** 서비스 bbox(전송 이미지 좌표) → 페이지 CSS 좌표.
 *
 * 전송 이미지 1px = 장치 픽셀 1/scale px = CSS 픽셀 1/(scale·dpr) px.
 * 여기에 뷰어 사각형의 원점을 더하면 끝이다. **추측이 하나도 없다** —
 * scale 과 dpr 을 우리가 직접 정했기 때문이다.
 */
function toCss([x, y, w, h]) {
  const k = 1 / (ctx.scale * ctx.dpr);
  return {
    left: ctx.rect.x + x * k,
    top: ctx.rect.y + y * k,
    width: w * k,
    height: h * k,
  };
}

function inClip(p, c) {
  const mx = p.left + p.width / 2;
  const my = p.top + p.height / 2;
  return mx >= c.x && mx <= c.x + c.width && my >= c.y && my <= c.y + c.height;
}

function drawBoxes(regions) {
  const boxes = overlay().querySelector("#mlr-boxes");
  // 전체 읽기는 `begin` 에서 이미 비웠다. 여기서 또 비우면 영역 하나만 다시
  // 읽을 때 나머지 박스가 통째로 사라진다.
  for (const r of regions) {
    const p = toCss(r.bbox);
    // 검출기에 문맥을 주려고 넓게 보냈으므로, 고른 범위 밖의 것은 버린다.
    // 중심으로 판정한다 — 경계에 걸친 박스를 통째로 버리면 고른 말풍선이 사라진다.
    if (ctx.clip && !inClip(p, ctx.clip)) continue;
    const div = document.createElement("div");
    div.className = "mlr-box" + (r.is_bubble ? "" : " mlr-sfx");
    div.id = `mlr-box-${ctx.prefix}${r.id}`;
    Object.assign(div.style, {
      left: `${p.left}px`,
      top: `${p.top}px`,
      width: `${p.width}px`,
      height: `${p.height}px`,
    });
    // 원문을 남겨 둔다. 번역이 도착하면 라벨을 덮어쓰므로, 보관하지 않으면
    // "원문 보기" 를 할 수가 없다.
    div.dataset.ja = r.text ?? "";
    // 부분 읽기로 생긴 박스만 저장 대상이다 (전체 읽기 결과는 캐시가 들고 있다).
    if (ctx.partial) div.dataset.manual = "1";
    div.innerHTML = `<span class="mlr-label">${escapeHtml(r.text)}</span>`;
    // 라벨이 아래 말풍선을 가리지 않게, 박스가 화면 아래쪽이면 위로 붙인다
    if (p.top + p.height + 28 > window.innerHeight) div.classList.add("mlr-label-above");
    boxes.appendChild(div);
  }
  layoutLabels();
}

// ---------------------------------------------------------------------------
// 박스 우클릭 메뉴
//
// 검출·OCR·번역 중 하나만 틀려도 박스 하나가 통째로 쓸모없어진다. 페이지를
// 통째로 다시 읽는 것 말고 **그 박스만** 손볼 수단이 필요하다.
//
// - 지우기   : 오검출(그림을 글자로 봄)을 치운다
// - 다시 읽기: 그 영역만 잘라 다시 보낸다. 잘린 조각은 §5.4 정규화에서 크게
//              확대되므로 (작은 말풍선이면 5-6배) 전체 페이지로 읽을 때보다
//              OCR 이 훨씬 잘 된다. 깨알 글씨가 헛것으로 읽힌 경우의 해법이다
// ---------------------------------------------------------------------------

const MENU_ID = "mlr-menu";

/** 다시 읽을 때 사각형을 이만큼 넓힌다 — bbox 가 글자에 딱 붙어 있어 획이 잘린다. */
const REREAD_PAD_PX = 10;

function closeMenu() {
  document.getElementById(MENU_ID)?.remove();
}

function openMenu(box, x, y) {
  closeMenu();
  const menu = document.createElement("div");
  menu.id = MENU_ID;
  const showing = overlay().classList.contains("mlr-show-extra");
  const onJa = box.dataset.showing === "ja";
  const hasKo = Boolean(box.dataset.ko);
  menu.innerHTML =
    '<button data-act="say">읽어주기</button>' +
    `<button data-act="orig"${hasKo ? "" : " disabled"}>${onJa ? "번역 보기" : "원문 보기"}</button>` +
    '<button data-act="reread">다시 읽기</button>' +
    '<button data-act="resize">크기 조정</button>' +
    '<button data-act="remove">지우기</button>' +
    `<button data-act="extra">${showing ? "걸러진 것 숨기기" : "걸러진 것 보기"}</button>`;
  overlay().appendChild(menu);

  // 화면 밖으로 나가지 않게. 메뉴를 붙인 뒤라야 크기를 안다.
  const m = menu.getBoundingClientRect();
  menu.style.left = `${Math.min(x, window.innerWidth - m.width - 4)}px`;
  menu.style.top = `${Math.min(y, window.innerHeight - m.height - 4)}px`;

  menu.addEventListener("click", (e) => {
    const act = e.target?.dataset?.act;
    if (!act) return;
    e.preventDefault();
    e.stopPropagation();
    closeMenu();
    if (act === "remove") {
      // 되돌아왔을 때 다시 지우려면 어디였는지 남겨야 한다.
      if (ctx) {
        const n = toNorm(box);
        removedMarks.push({ cx: n.nx + n.nw / 2, cy: n.ny + n.nh / 2 });
      }
      box.remove();
      saveEdits();
      status("영역 지움");
      return;
    }
    if (act === "extra") {
      const on = overlay().classList.toggle("mlr-show-extra");
      layoutLabels();
      syncPanel();
      status(on ? "효과음·잡문 표시" : `효과음·잡문 ${extraCount}개 숨김`);
      return;
    }
    if (act === "say") {
      stopSpeaking();
      speakOne(box);
      return;
    }
    if (act === "orig") {
      const toJa = box.dataset.showing !== "ja";
      box.dataset.showing = toJa ? "ja" : "ko";
      box.classList.toggle("mlr-showing-ja", toJa);
      const l = box.querySelector(".mlr-label");
      if (l) l.textContent = toJa ? box.dataset.ja || "" : box.dataset.ko || "";
      layoutLabels();
      return;
    }
    if (act === "resize") startResize(box);
    else if (act === "reread") reread(box);
  });
}

/** 박스 하나만 다시 읽는다. */
function reread(box) {
  // 오버레이가 `position: fixed; inset: 0` 이라 박스의 화면 좌표가 곧 뷰포트
  // CSS 좌표다 — 드래그 선택과 같은 좌표계라 그대로 넘길 수 있다.
  const b = box.getBoundingClientRect();
  const r = clampToViewport(
    {
      left: b.left - REREAD_PAD_PX,
      top: b.top - REREAD_PAD_PX,
      right: b.right + REREAD_PAD_PX,
      bottom: b.bottom + REREAD_PAD_PX,
    },
    window.innerWidth,
    window.innerHeight
  );
  if (r.width < 8 || r.height < 8) {
    status("영역이 화면 밖이다", true);
    return;
  }
  status("이 영역만 다시 읽는다…");
  // 캡처에 우리 박스가 찍히면 안 된다. `hide-overlay` 가 뒤따라 오지만 두 프레임
  // 기다려 확실히 지워진 뒤 요청한다 (드래그 선택과 같은 이유).
  requestAnimationFrame(() =>
    requestAnimationFrame(() =>
      send({
        type: "read-rect",
        rect: expandForDetector(r, window.innerWidth, window.innerHeight),
        clip: r,
        dpr: dpr(),
        merge: box.id.replace(/^mlr-box-/, ""),
        prefix: `r${++partialSeq}-`,
      })
    )
  );
}

// ---------------------------------------------------------------------------
// 음성 읽기 (Web Speech API)
//
// **브라우저 내장을 쓴다.** 서버 TTS 는 모델·GPU·지연이 붙는데, 개인용 읽기 도구에
// 그만한 값을 치를 이유가 없다. `speechSynthesis` 는 비용 0, 서버 변경 0 이고
// 맥에는 한국어(Yuna)·일본어 음성이 이미 깔려 있다.
//
// **읽는 것은 원문(일본어)이다.** 번역은 눈으로 보면 되고, 귀로 듣고 싶은 것은
// 원문이다 — 말투·억양은 번역문이 아니라 원문에 있다. 눈은 한국어, 귀는 일본어.
// ---------------------------------------------------------------------------

/** 전체 읽기가 도는 중인가. 패널 「음성」 버튼 상태와 같다. */
let speaking = false;

//: 음성 목록이 채워지기를 **한 번만** 기다린다. 결과는 캐시하지 않는다.
let voicesWait = null;

/** 지금 쓸 수 있는 음성 목록. 비어 있으면 한 번 기다렸다가 다시 읽는다.
 *
 * **목록을 캐시하면 안 된다.** 예전에는 `loadVoices()` 가 결과 배열을 통째로
 * 캐시했는데, 첫 호출이 목록이 채워지기 전이면 **빈 목록(또는 일부만 든 목록)이
 * 영구히 굳었다.** 윈도우에 일본어 음성을 깔았는데도 계속 한국어로 읽히던 원인이다.
 * `getVoices()` 는 싸므로 매번 새로 읽는다.
 */
async function loadVoices() {
  let v = speechSynthesis.getVoices();
  if (v.length) return v;
  if (!voicesWait) {
    voicesWait = new Promise((resolve) => {
      speechSynthesis.addEventListener("voiceschanged", () => resolve(), { once: true });
      // 이벤트가 영영 안 올 수도 있다 (플랫폼차).
      setTimeout(resolve, 1500);
    });
  }
  await voicesWait;
  return speechSynthesis.getVoices();
}

//: 여성 음성 우선 목록. **Web Speech API 에는 성별 필드가 없다** — 이름으로 고르는
//: 수밖에 없다. 앞에 있는 것부터 찾고, 하나도 없으면 그 언어의 아무 음성이나 쓴다.
//:
//: macOS 기본: 일본어 Kyoko(여)/Otoya(남), 한국어 Yuna(여).
//: Chrome 이 얹는 Google 음성도 대개 여성이다.
const FEMALE_VOICES = {
  ja: ["kyoko", "o-ren", "google 日本語", "haruka", "nanami", "ayumi", "sayaka"],
  ko: ["yuna", "google 한국의", "heami", "sun-hi", "ji-min"],
};

async function pickVoice(lang) {
  const voices = await loadVoices();
  const pre = lang.split("-")[0];
  const norm = (v) => v.lang.replace("_", "-");
  const sameLang = voices.filter((v) => norm(v).startsWith(pre));

  for (const want of FEMALE_VOICES[pre] ?? []) {
    const hit = sameLang.find((v) => v.name.toLowerCase().includes(want));
    if (hit) return hit;
  }
  // 여성 음성을 못 찾으면 그 언어의 아무거나. 남성이라도 안 읽는 것보다 낫다.
  return sameLang.find((v) => norm(v) === lang) || sameLang[0] || null;
}

/** 소리내어 읽을 글·언어·음성을 정한다.
 *
 * **원문(일본어)을 우선한다.** 번역은 눈으로 보면 되고, 귀로 듣고 싶은 것은 원문이다 —
 * 말투·억양은 번역문이 아니라 원문에 있다. 눈은 한국어, 귀는 일본어.
 *
 * **다만 일본어 음성이 없으면 번역문을 읽는다.** 윈도우는 일본어 TTS 가 기본으로
 * 안 깔려 있다 (실측: Heami/ko, Zira·David/en 뿐). 그대로 두면 윈도우에서 음성이
 * 통째로 무음이 된다 — 안 읽는 것보다 번역문이라도 읽는 게 낫다.
 */
async function resolveSpeech(box) {
  const ja = (box.dataset.ja || "").trim();
  const ko = (box.dataset.ko || "").trim();

  if (ja) {
    const v = await pickVoice("ja-JP");
    if (v) return { text: ja, lang: "ja-JP", voice: v };
    if (ko) {
      return { text: ko, lang: "ko-KR", voice: await pickVoice("ko-KR"), fellBack: true };
    }
    return { text: ja, lang: "ja-JP", voice: null }; // 기본 음성에 맡긴다
  }
  return { text: ko, lang: "ko-KR", voice: await pickVoice("ko-KR") };
}

/** 읽을 글이 있는가만 볼 때 쓴다 (음성 조회 없이 빠르게). */
function hasSpeech(box) {
  return Boolean((box.dataset.ja || box.dataset.ko || "").trim());
}

//: **말하는 중인 utterance 를 전역에 붙들어 둔다.**
//:
//: Chrome 의 오래된 버그: 지역 변수로만 들고 있으면 말하는 도중에 GC 가 물어가
//: 소리가 끊기거나 아예 안 난다. `genai.Client()` 를 변수에 안 담아 터졌던 것과
//: 같은 부류다 (DEVLOG.md).
let currentUtterance = null;

/** 한 마디 말한다. 끝나면(또는 실패하면) resolve. 실패 이유는 상태줄에 남긴다. */
function say(text, lang, voice) {
  return new Promise((resolve) => {
    const u = new SpeechSynthesisUtterance(text);
    currentUtterance = u; // GC 방지
    u.lang = lang;
    if (voice) u.voice = voice;
    u.onend = () => {
      currentUtterance = null;
      resolve();
    };
    u.onerror = (e) => {
      currentUtterance = null;
      // "interrupted"/"canceled" 는 우리가 멈춘 것이라 오류가 아니다.
      if (e.error && !["interrupted", "canceled"].includes(e.error)) {
        status(`음성 실패: ${e.error} (${lang})`, true);
      }
      resolve();
    };
    speechSynthesis.speak(u);
    // Chrome 이 이따금 pause 상태로 시작한다. 깨워 준다.
    speechSynthesis.resume();
  });
}

/** 일본어로 못 읽는 이유를 짚어 준다. "안 나온다" 만으로는 어디를 손볼지 모른다. */
async function noJapaneseReason() {
  const v = await loadVoices();
  if (!v.length) return "브라우저가 음성을 하나도 못 봤다 — 브라우저를 완전히 껐다 켤 것";
  const langs = [...new Set(v.map((x) => x.lang.split(/[-_]/)[0]))].sort().join(" ");
  return `일본어 음성이 없어 번역문을 읽는다 (있는 언어: ${langs} · 브라우저 재시작 필요할 수 있음)`;
}

async function speakOne(box) {
  if (speechSynthesis.speaking || speechSynthesis.pending) speechSynthesis.cancel();
  const { text, lang, voice, fellBack } = await resolveSpeech(box);
  if (!text) return;
  if (fellBack) status(await noJapaneseReason());
  else if (!voice) {
    const n = (await loadVoices()).length;
    status(`${lang} 음성이 없다 (설치된 음성 ${n}개). 시스템 설정에서 추가할 것`, true);
  }
  await say(text, lang, voice);
}

/** 페이지 전체를 읽기 순서대로. id 순서가 곧 읽기 순서다 (order.py 가 정한 것). */
async function speakAll() {
  const root = document.getElementById(OVERLAY_ID);
  if (!root) return;
  const boxes = [...root.querySelectorAll(".mlr-box")].filter(
    (b) => getComputedStyle(b).opacity !== "0" && hasSpeech(b)
  );
  if (!boxes.length) {
    status("읽을 것이 없다", true);
    return;
  }

  speaking = true;
  syncPanel();
  if (speechSynthesis.speaking || speechSynthesis.pending) speechSynthesis.cancel();

  const first = await resolveSpeech(boxes[0]);
  status(
    first.fellBack
      ? await noJapaneseReason()
      : first.voice
        ? `음성: ${first.voice.name}`
        : "맞는 음성이 없어 기본 음성으로 읽는다"
  );

  for (let i = 0; i < boxes.length && speaking; i++) {
    const box = boxes[i];
    const { text, lang, voice } = await resolveSpeech(box);
    if (!text) continue;
    box.classList.add("mlr-speaking");
    status(`읽는 중 ${i + 1}/${boxes.length}`);
    try {
      await say(text, lang, voice);
    } finally {
      box.classList.remove("mlr-speaking");
    }
  }

  speaking = false;
  syncPanel();
  status("읽기 끝");
}

function stopSpeaking() {
  speaking = false;
  currentUtterance = null;
  speechSynthesis.cancel();
  document
    .querySelectorAll(".mlr-box.mlr-speaking")
    .forEach((b) => b.classList.remove("mlr-speaking"));
  syncPanel();
}

// ---------------------------------------------------------------------------
// 박스 크기 조정
//
// 검출 bbox 가 조금 어긋나면(글자를 반만 덮거나 옆 칸을 물면) 그 박스는 통째로
// 쓸모없어진다. 손으로 맞춰 다시 읽을 수 있어야 한다.
//
// **손잡이는 고른 박스 하나에만 붙인다.** 모든 박스에 늘 달아 두면 화면이 손잡이로
// 뒤덮이고, 라벨을 보려고 마우스를 올릴 때마다 걸린다.
// ---------------------------------------------------------------------------

const HANDLES = ["nw", "n", "ne", "e", "se", "s", "sw", "w"];

/** 조정 중인 박스와 원래 크기. Esc 로 되돌릴 때 쓴다. */
let resizing = null;

function cancelResize(restore = false) {
  if (!resizing) return;
  const { box, orig, teardown } = resizing;
  teardown();
  box.classList.remove("mlr-resizing");
  box.querySelectorAll(".mlr-handle").forEach((h) => h.remove());
  if (restore) Object.assign(box.style, orig);
  resizing = null;
}

function startResize(box) {
  cancelResize();
  box.classList.add("mlr-resizing");
  const orig = {
    left: box.style.left, top: box.style.top,
    width: box.style.width, height: box.style.height,
  };

  for (const dir of HANDLES) {
    const h = document.createElement("div");
    h.className = `mlr-handle mlr-h-${dir}`;
    h.dataset.dir = dir;
    box.appendChild(h);
  }

  let drag = null;

  const onDown = (e) => {
    const dir = e.target?.dataset?.dir;
    if (!dir) return;
    e.preventDefault();
    e.stopPropagation();
    const r = box.getBoundingClientRect();
    drag = { dir, x: e.clientX, y: e.clientY, l: r.left, t: r.top, w: r.width, h: r.height };
    e.target.setPointerCapture?.(e.pointerId);
  };

  const onMove = (e) => {
    if (!drag) return;
    e.preventDefault();
    const dx = e.clientX - drag.x;
    const dy = e.clientY - drag.y;
    let { l, t, w, h } = drag;
    if (drag.dir.includes("w")) { l += dx; w -= dx; }
    if (drag.dir.includes("e")) { w += dx; }
    if (drag.dir.includes("n")) { t += dy; h -= dy; }
    if (drag.dir.includes("s")) { h += dy; }
    if (w < 12 || h < 12) return;   // 뒤집히거나 사라지지 않게
    Object.assign(box.style, {
      left: `${l}px`, top: `${t}px`, width: `${w}px`, height: `${h}px`,
    });
  };

  const onUp = (e) => {
    if (!drag) return;
    e.preventDefault();
    e.stopPropagation();
    drag = null;
    // **크기를 바꿨으면 바로 다시 읽는다.** 조정만 하고 끝내면 박스만 커지고
    // 원문은 그대로라 아무 소용이 없다.
    const changed =
      box.style.left !== orig.left || box.style.top !== orig.top ||
      box.style.width !== orig.width || box.style.height !== orig.height;
    cancelResize();
    if (changed) reread(box);
  };

  const onKey = (e) => {
    if (e.key === "Escape") {
      e.preventDefault();
      cancelResize(true);
      status("크기 조정 취소");
    }
  };

  box.addEventListener("pointerdown", onDown);
  box.addEventListener("pointermove", onMove);
  box.addEventListener("pointerup", onUp);
  window.addEventListener("keydown", onKey, true);

  resizing = {
    box,
    orig,
    teardown: () => {
      box.removeEventListener("pointerdown", onDown);
      box.removeEventListener("pointermove", onMove);
      box.removeEventListener("pointerup", onUp);
      window.removeEventListener("keydown", onKey, true);
    },
  };
  status("모서리를 끌어 크기를 맞추세요 · 놓으면 다시 읽는다 · Esc 취소");
}

document.addEventListener(
  "contextmenu",
  (e) => {
    const box = e.target?.closest?.(".mlr-box");
    if (!box) return;
    e.preventDefault();
    e.stopPropagation();
    openMenu(box, e.clientX, e.clientY);
  },
  true
);

// 메뉴 밖을 누르거나 Esc 면 닫는다.
document.addEventListener("pointerdown", (e) => {
  if (!e.target?.closest?.(`#${MENU_ID}`)) closeMenu();
}, true);
window.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    closeMenu();
    if (speaking) stopSpeaking();
  }
}, true);

// ---------------------------------------------------------------------------
// 손으로 한 작업을 페이지별로 남긴다
//
// 「영역」 으로 더한 박스와 「지우기」 로 없앤 박스는 화면에만 있어서, 다른 페이지에
// 갔다 오면 사라진다. 돌아왔을 때 전체 읽기는 캐시로 되살아나는데 손으로 한 것만
// 없으니 "처음 것으로 되돌아간다" 로 보인다.
//
// **좌표는 뷰어 사각형 기준 비율로 저장한다.** 화면 픽셀로 저장하면 창 크기나 확대
// 배율이 바뀌는 순간 전부 어긋난다. 비율이면 뷰어가 커지든 작아지든 따라간다.
//
// 키는 전체 읽기의 phash — 지각 해시라 같은 페이지면 캡처가 조금 달라도 같은 키다.
// ---------------------------------------------------------------------------

const EDITS_KEY = (k) => `mlr-edits-${k}`;

//: 저장해 둘 페이지 수. storage.local 은 기본 5MB 라 무한정 쌓으면 언젠가 막힌다.
const EDITS_KEEP = 200;

/** 화면 좌표 → 뷰어 사각형 기준 비율. */
function toNorm(box) {
  const r = box.getBoundingClientRect();
  const v = ctx.rect;
  return {
    nx: (r.left - v.x) / v.width,
    ny: (r.top - v.y) / v.height,
    nw: r.width / v.width,
    nh: r.height / v.height,
  };
}

function fromNorm(a) {
  const v = ctx.rect;
  return {
    left: v.x + a.nx * v.width,
    top: v.y + a.ny * v.height,
    width: a.nw * v.width,
    height: a.nh * v.height,
  };
}

async function saveEdits() {
  if (!pageKey || !ctx) return;
  const root = document.getElementById(OVERLAY_ID);
  if (!root) return;
  const added = [...root.querySelectorAll('.mlr-box[data-manual="1"]')].map((b) => ({
    ...toNorm(b),
    ja: b.dataset.ja || "",
    ko: b.dataset.ko || "",
    kind: b.dataset.kind || "dialogue",
  }));
  try {
    await chrome.storage.local.set({
      [EDITS_KEY(pageKey)]: { added, removed: removedMarks, at: Date.now() },
    });
    pruneEdits();
  } catch {
    /* 저장이 안 돼도 화면은 그대로 쓴다 */
  }
}

async function restoreEdits() {
  if (!pageKey || !ctx) return;
  removedMarks = [];
  let saved;
  try {
    saved = (await chrome.storage.local.get(EDITS_KEY(pageKey)))[EDITS_KEY(pageKey)];
  } catch {
    return;
  }
  if (!saved) return;
  const root = document.getElementById(OVERLAY_ID);
  if (!root) return;

  // 1) 지웠던 것 다시 지운다. 검출 결과가 조금 달라질 수 있으니 중심 거리로 맞춘다.
  removedMarks = saved.removed || [];
  for (const m of removedMarks) {
    for (const b of root.querySelectorAll(".mlr-box")) {
      const n = toNorm(b);
      if (Math.abs(n.nx + n.nw / 2 - m.cx) < 0.02 && Math.abs(n.ny + n.nh / 2 - m.cy) < 0.02) {
        b.remove();
        break;
      }
    }
  }

  // 2) 더했던 것 되살린다. 이미 번역까지 들고 있으므로 서버를 다시 부르지 않는다.
  const boxes = root.querySelector("#mlr-boxes");
  let i = 0;
  for (const a of saved.added || []) {
    const p = fromNorm(a);
    const div = document.createElement("div");
    div.className = "mlr-box mlr-translated";
    div.id = `mlr-box-keep${++i}`;
    div.dataset.manual = "1";
    div.dataset.ja = a.ja;
    div.dataset.ko = a.ko;
    div.dataset.kind = a.kind;
    if (a.kind === "sfx" || a.kind === "extra") div.classList.add(`mlr-kind-${a.kind}`);
    Object.assign(div.style, {
      left: `${p.left}px`, top: `${p.top}px`,
      width: `${p.width}px`, height: `${p.height}px`,
    });
    div.innerHTML = `<span class="mlr-label">${escapeHtml(a.ko || a.ja)}</span>`;
    if (p.top + p.height + 28 > window.innerHeight) div.classList.add("mlr-label-above");
    boxes.appendChild(div);
  }
  if (i) status(`손으로 더한 ${i}개 복원`);
  layoutLabels();
}

/** 오래된 페이지 기록을 덜어낸다. */
async function pruneEdits() {
  try {
    const all = await chrome.storage.local.get(null);
    const keys = Object.keys(all).filter((k) => k.startsWith("mlr-edits-"));
    if (keys.length <= EDITS_KEEP) return;
    keys.sort((a, b) => (all[a]?.at || 0) - (all[b]?.at || 0));
    await chrome.storage.local.remove(keys.slice(0, keys.length - EDITS_KEEP));
  } catch {
    /* 정리 실패는 치명적이지 않다 */
  }
}

// ---------------------------------------------------------------------------
// 박스가 뷰어를 따라가게 한다
//
// 오버레이는 `position: fixed` 라 **뷰포트에 붙어 있다.** 스크롤하면 만화는 움직이는데
// 박스는 그대로여서 엉뚱한 그림 위에 덕지덕지 남는다. 확대·창 크기 변경도 마찬가지다.
//
// 박스를 하나씩 다시 계산하지 않는다. `#mlr-boxes` 에 **변환 하나**만 걸면 된다 —
// 읽을 때의 뷰어 사각형을 지금 사각형으로 보내는 변환이다. 스크롤(이동)과
// 확대(배율)를 한 번에 처리하고 비용이 거의 없다.
// ---------------------------------------------------------------------------

let followPending = false;

function followViewer() {
  if (followPending) return;
  followPending = true;
  requestAnimationFrame(() => {
    followPending = false;
    doFollowViewer();
  });
}

function doFollowViewer() {
  const boxes = document.getElementById(OVERLAY_ID)?.querySelector("#mlr-boxes");
  if (!boxes || !ctx?.rect) return;

  const live = viewerEls.filter((el) => el.isConnected);
  if (!live.length) return;
  const now = unionRects(live.map((el) => el.getBoundingClientRect()));
  const w = now.right - now.left;
  if (w <= 0) return;

  // 읽을 때 쓴 사각형은 뷰포트로 잘린 것(clampToViewport)이라, 지금 사각형과 바로
  // 비교하면 안 된다. 요소 전체 사각형끼리 비교해야 한다.
  if (!ctx.anchor) {
    ctx.anchor = { left: now.left, top: now.top, width: w };
    return;
  }
  const s = w / ctx.anchor.width;
  const tx = now.left - ctx.anchor.left * s;
  const ty = now.top - ctx.anchor.top * s;
  boxes.style.transformOrigin = "0 0";
  boxes.style.transform =
    Math.abs(s - 1) < 0.001 && Math.abs(tx) < 0.5 && Math.abs(ty) < 0.5
      ? ""
      : `translate(${tx}px, ${ty}px) scale(${s})`;
}

addEventListener("scroll", followViewer, { passive: true, capture: true });
addEventListener("resize", followViewer, { passive: true });

// ---------------------------------------------------------------------------
// 라벨 겹침 풀기
//
// 라벨은 박스 아래(또는 위)에 붙는다. 말풍선이 촘촘한 칸에서는 라벨끼리 겹쳐
// **번역이 다른 번역에 가려진다.** 「라벨」 로 전부 펼쳤을 때 특히 심하다.
//
// 위에서 아래로 훑으며, 이미 놓인 라벨과 겹치면 아래로 밀어 빈자리를 찾는다.
// 완벽한 배치는 아니지만(겹침 최소화는 NP-hard 다) 실제로 읽을 수 있게는 된다.
// ---------------------------------------------------------------------------

//: 한 번에 미는 양과 최대 이동. 너무 많이 밀면 어느 말풍선의 번역인지 알 수 없다.
const LABEL_STEP_PX = 3;
const LABEL_MAX_SHIFT_PX = 160;

/** 여러 번 불려도 다음 프레임에 한 번만 돈다.
 *
 * 번역은 스트리밍이라 region 마다 따로 도착한다. 그때마다 바로 배치하면
 * `getBoundingClientRect()` 가 매번 레이아웃을 강제해 (박스 20개 × 번역 20개 =
 * 400회) 눈에 띄게 버벅인다.
 */
let layoutPending = false;

function layoutLabels() {
  if (layoutPending) return;
  layoutPending = true;
  requestAnimationFrame(() => {
    layoutPending = false;
    doLayoutLabels();
  });
}

function doLayoutLabels() {
  const root = document.getElementById(OVERLAY_ID);
  if (!root) return;

  const labels = [...root.querySelectorAll(".mlr-box")]
    .filter((b) => getComputedStyle(b).opacity !== "0") // 걸러진 박스는 뺀다
    .map((b) => b.querySelector(".mlr-label"))
    .filter(Boolean);

  // 접어 둔 상태에서는 마우스 올린 하나만 보이므로 겹칠 일이 없다. 되돌려 둔다.
  if (!root.classList.contains("mlr-show-all")) {
    for (const l of labels) l.style.transform = "";
    return;
  }

  const hit = (a, b) =>
    a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
  const shift = (r, dy) => ({
    left: r.left, right: r.right, top: r.top + dy, bottom: r.bottom + dy,
  });

  const placed = [];
  const items = labels
    .map((l) => {
      l.style.transform = "";
      return { l, r: l.getBoundingClientRect() };
    })
    .sort((a, b) => a.r.top - b.r.top || a.r.left - b.r.left);

  for (const { l, r } of items) {
    if (r.width === 0) continue;
    let dy = 0;
    while (dy <= LABEL_MAX_SHIFT_PX && placed.some((p) => hit(p, shift(r, dy)))) {
      dy += LABEL_STEP_PX;
    }
    if (dy > LABEL_MAX_SHIFT_PX) dy = 0; // 자리를 못 찾으면 원위치 — 멀리 보내는 게 더 나쁘다
    if (dy) l.style.transform = `translateY(${dy}px)`;
    placed.push(shift(r, dy));
  }
}

/** 이번 페이지에서 대사가 아닌 것으로 분류된 개수. 상태줄에 알려 준다. */
let extraCount = 0;

function fillTranslation(tr) {
  const box = document.getElementById(`mlr-box-${ctx.prefix}${tr.id}`);
  if (!box) return;
  box.classList.add("mlr-translated");
  // `kind` 는 번역 모델이 **원문을 보고** 정한 분류다 (dialogue/sfx/caption).
  // 서버의 `is_bubble` 은 쓰지 않는다 — 스크린톤 배경 위 말풍선을 효과음으로
  // 오판하고, 배경 통계로는 고칠 수 없다는 것을 측정으로 확인했다 (DEVLOG.md).
  // 원문 텍스트를 보는 쪽이 훨씬 정확하고 추가 비용도 거의 없다.
  // 숨기는 것은 `sfx` 와 `extra` 뿐이다. `narration` 은 이야기의 일부라
  // 숨기면 내용이 끊긴다 (나레이션 상자·화 제목·간판).
  if (tr.kind === "sfx" || tr.kind === "extra") {
    box.classList.add(`mlr-kind-${tr.kind}`);
    extraCount += 1;
    status(`효과음·잡문 ${extraCount}개 숨김 · Alt+Shift+S 로 보기`);
  }
  const label = box.querySelector(".mlr-label");
  box.dataset.ko = tr.ko ?? "";
  box.dataset.kind = tr.kind ?? "dialogue";
  // 원문을 보는 중이면 덮어쓰지 않는다 — 번역이 스트리밍으로 늦게 와서 사용자가
  // 이미 원문으로 돌려놓은 상태일 수 있다.
  if (box.dataset.showing !== "ja") label.textContent = tr.ko;
  if (tr.note) label.title = tr.note;
  // 번역이 오면 글자가 길어져 겹침이 달라진다. 스트리밍이라 하나씩 도착하므로
  // 그때마다 다시 배치한다 (박스 20개 규모라 비용이 무시할 만하다).
  layoutLabels();
}

/** `Alt+Shift+L` 로 라벨 전체 펼치기/접기.
 *
 * 기본은 접어 두고 마우스 오버로 하나씩 본다 — 전부 펼치면 아래쪽 말풍선을 가려서
 * 원문을 못 읽는다. 다만 번역만 훑고 싶을 때가 있어 토글을 남긴다.
 */
document.addEventListener("keydown", (e) => {
  // `Alt+Shift+S` — 효과음·캡션 보이기/숨기기.
  // 숨기는 쪽이 기본이지만 분류가 틀릴 수 있으니 되돌릴 수단을 남긴다.
  if (e.altKey && e.shiftKey && e.code === "KeyS") {
    e.preventDefault();
    const on = overlay().classList.toggle("mlr-show-extra");
    syncPanel();
    status(on ? "효과음·잡문 표시" : `효과음·잡문 ${extraCount}개 숨김`);
    return;
  }
  if (e.altKey && e.shiftKey && e.code === "KeyL") {
    const el = document.getElementById(OVERLAY_ID);
    if (el) {
      el.classList.toggle("mlr-show-all");
      layoutLabels();
      syncPanel();
    }
  }
});

// ---------------------------------------------------------------------------
// 페이지 전환 감지 (DESIGN.md §5.1)
//
// **주기적으로 캡처해서 비교하지 않는다.** captureVisibleTab 은 비싸고 호출 제한이
// 있으며, 확인할 때마다 오버레이를 숨겼다 되살려야 해서 화면이 깜빡인다.
//
// 대신 "뭔가 일어났을 수 있다" 는 신호만 올려보내고 실제 판정은 background 가
// 한다 (캡처 → 해시 → 이전과 다르면 읽기). 신호는 넷이다:
//
//   · 클릭·키·휠  — 사람이 페이지를 넘기는 거의 모든 방법
//   · DOM 변화     — 뷰어가 스스로 바꾸는 경우 (자동 넘김, 지연 로딩 타일)
//
// canvas 뷰어는 다시 그려도 DOM 이 안 바뀌므로 입력 신호가 필수고, 반대로
// SPA 뷰어는 입력 없이 DOM 만 바뀌므로 둘 다 필요하다.
// ---------------------------------------------------------------------------

/** 신호를 몰아서 한 번만 올린다. background 가 다시 debounce 하므로 여기서는 짧게. */
const notifyMaybeChanged = throttle(() => {
  if (!autoOn) return;
  send({ type: "page-maybe-changed" });
}, 250);

function throttle(fn, ms) {
  let last = 0;
  let timer = null;
  return () => {
    const now = Date.now();
    const wait = Math.max(0, ms - (now - last));
    if (timer) return;
    timer = setTimeout(() => {
      timer = null;
      last = Date.now();
      fn();
    }, wait);
  };
}

// 음성 목록을 미리 받아 둔다. `speak()` 직전에 await 가 길면 사용자 제스처 문맥이
// 끊겨 Chrome 이 재생을 막는 경우가 있다.
try {
  loadVoices();
} catch {}

document.addEventListener("click", notifyMaybeChanged, true);
// **휠은 신호에서 뺐다.** 스크롤은 페이지 넘김이 아닌 경우가 대부분인데, 확인할
// 때마다 캡처하느라 오버레이가 숨었다 돌아와 **깜빡인다.** 게다가 이제 박스가
// 뷰어를 따라가므로(followViewer) 스크롤해도 결과가 어긋나지 않아 다시 읽을 이유가
// 없다. 스크롤로 새 페이지가 나타나는 뷰어는 DOM 변화가 같이 오므로 그쪽에 잡힌다.
document.addEventListener(
  "keyup",
  (e) => {
    // 페이지 넘김에 쓰이는 키만. 아무 키나 잡으면 검색창 타이핑에도 반응한다.
    if (
      ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "PageUp", "PageDown", "Space", "Enter"].includes(
        e.code === "Space" ? "Space" : e.key
      ) ||
      e.code === "Space"
    ) {
      notifyMaybeChanged();
    }
  },
  true
);

/** 우리 오버레이가 만든 변화는 무시한다 — 안 그러면 무한 루프다. */
function isOurs(node) {
  for (let n = node; n; n = n.parentNode) {
    if (n.id === OVERLAY_ID || n.id === SELECT_ID) return true;
  }
  return false;
}

const observer = new MutationObserver((records) => {
  if (stale) return;
  for (const r of records) {
    if (!isOurs(r.target)) {
      notifyMaybeChanged();
      return;
    }
  }
});

observer.observe(document.documentElement, {
  subtree: true,
  childList: true,
  attributes: true,
  // 뷰어가 페이지를 바꿀 때 건드리는 것들. 전체 속성을 보면 너무 시끄럽다.
  attributeFilter: ["src", "style", "class", "transform", "data-page"],
});

function escapeHtml(s) {
  const d = document.createElement("div");
  d.textContent = s ?? "";
  return d.innerHTML;
}
