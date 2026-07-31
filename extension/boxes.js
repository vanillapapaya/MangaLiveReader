// 박스 크기 조정 · 손으로 한 작업 저장 · 뷰어 추적 · 라벨 배치
//
// `content.js` 에서 갈라 나온 파일이다. **모듈이 아니다** — MV3 콘텐츠
// 스크립트는 manifest 의 `js` 배열 순서대로 같은 전역에서 실행된다.
// 그래서 import 없이 서로의 함수를 그냥 부른다. 순서를 바꾸면 깨진다.


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
    // 손으로 옮겼으면 비율도 다시 잡아야 한다. 안 그러면 다음 스크롤에서
    // 옛 비율로 되돌아간다.
    const vr = viewerRect();
    if (changed && vr) box.dataset.n = JSON.stringify(toNorm(box, vr));
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

/** 지금 화면에서 뷰어 요소들이 차지하는 사각형.
 *
 * **`ctx.rect` 를 쓰면 안 된다.** 그 값은 "이번에 잘라 보낸 범위" 라, 부분 읽기
 * (영역 지정·다시 읽기) 때는 뷰어가 아니라 **작은 크롭 사각형**이다. 저장은 부분
 * 읽기 끝에 하고 복원은 전체 읽기 끝에 하므로, 그대로 쓰면 **기준이 서로 달라져**
 * 좌표가 통째로 어긋난다 — 손으로 더한 박스가 되살아나도 엉뚱한 곳에 놓이거나
 * 화면 밖으로 나가 사라진 것처럼 보인다.
 *
 * 뷰어 요소는 두 경우 모두 같으므로 여기서 직접 잰다.
 */
function viewerRect() {
  const live = viewerEls.filter((el) => el.isConnected);
  if (!live.length) return null;
  const u = unionRects(live.map((el) => el.getBoundingClientRect()));
  const w = u.right - u.left;
  const h = u.bottom - u.top;
  return w > 0 && h > 0 ? { x: u.left, y: u.top, width: w, height: h } : null;
}

/** 화면 좌표 → 뷰어 사각형 기준 비율. */
function toNorm(box, v) {
  const r = box.getBoundingClientRect();
  return {
    nx: (r.left - v.x) / v.width,
    ny: (r.top - v.y) / v.height,
    nw: r.width / v.width,
    nh: r.height / v.height,
  };
}

function fromNorm(a, v) {
  return {
    left: v.x + a.nx * v.width,
    top: v.y + a.ny * v.height,
    width: a.nw * v.width,
    height: a.nh * v.height,
  };
}

/** 이 박스가 있던 자리를 "지운 자리" 로 적어 둔다.
 *
 * **적어 두지 않으면 다음 전체 읽기에서 되살아난다.** 캐시는 페이지 해시로 잡히므로
 * 내가 박스를 고쳐도 서버는 처음 검출 결과를 그대로 돌려준다. 그 위에 손으로 고친
 * 박스까지 복원되니 둘이 겹쳐서 번역이 두 겹으로 나왔다.
 *
 * 크기를 바꾼 뒤 다시 읽는 경우가 있어 지금 자리(`n`)가 아니라 **처음 자리**(`n0`)로
 * 적는다. 캐시가 돌려주는 것이 그 자리다.
 */
function markRemoved(box) {
  let n;
  try {
    n = JSON.parse(box.dataset.n0 || box.dataset.n || "");
  } catch {
    return;
  }
  if (!n) return;
  const cx = n.nx + n.nw / 2;
  const cy = n.ny + n.nh / 2;
  // 같은 자리를 여러 번 다시 읽어도 기록은 하나면 된다
  if (removedMarks.some((m) => Math.abs(m.cx - cx) < 0.01 && Math.abs(m.cy - cy) < 0.01)) return;
  removedMarks.push({ cx, cy });
}

async function saveEdits() {
  const v = viewerRect();
  if (!pageKey || !v) return;
  const root = document.getElementById(OVERLAY_ID);
  if (!root) return;
  const added = [...root.querySelectorAll('.mlr-box[data-manual="1"]')].map((b) => ({
    ...toNorm(b, v),
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
  const v = viewerRect();
  if (!pageKey || !v) return;
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
      const n = toNorm(b, v);
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
    const p = fromNorm(a, v);
    const div = document.createElement("div");
    div.className = "mlr-box mlr-translated";
    div.id = `mlr-box-keep${++i}`;
    div.dataset.manual = "1";
    div.dataset.ja = a.ja;
    div.dataset.ko = a.ko;
    div.dataset.kind = a.kind;
    div.dataset.n = JSON.stringify({ nx: a.nx, ny: a.ny, nw: a.nw, nh: a.nh });
    div.dataset.n0 = div.dataset.n;
    if (a.kind === "sfx" || a.kind === "extra") div.classList.add(`mlr-kind-${a.kind}`);
    Object.assign(div.style, {
      left: `${p.left}px`, top: `${p.top}px`,
      width: `${p.width}px`, height: `${p.height}px`,
    });
    div.innerHTML = `<span class="mlr-label">${escapeHtml(a.ko || a.ja)}</span>`;
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
    relayoutBoxes();
  });
}

/** 박스를 지금 뷰어 사각형에 맞춰 다시 놓는다.
 *
 * **`transform` 하나로 처리하지 않는다.** 예전에는 `#mlr-boxes` 에 이동+배율 변환을
 * 걸었는데, 전체화면처럼 배율이 크게 바뀌면 **라벨 글자까지 같이 확대·축소돼** 읽을
 * 수 없게 된다. 뷰어가 다시 그려져 기준 요소가 사라지면 통째로 어긋나기도 한다.
 *
 * 박스마다 뷰어 기준 비율(`dataset.n`)을 들고 있다가 매번 절대 좌표를 다시 낸다.
 * 스크롤·확대·전체화면·창 크기 변경이 전부 같은 경로로 처리되고, 라벨은 배율의
 * 영향을 받지 않아 항상 같은 크기로 읽힌다.
 */
function relayoutBoxes() {
  const root = document.getElementById(OVERLAY_ID);
  const boxes = root?.querySelector("#mlr-boxes");
  if (!boxes || !boxes.children.length) return;

  // 뷰어가 다시 그려져 기억한 요소가 사라졌으면 새로 찾는다 (전체화면 전환 때
  // SpeedBinb 는 타일을 새로 만든다).
  if (!viewerEls.some((el) => el.isConnected)) {
    try {
      probeViewer();
    } catch {
      return;
    }
  }
  const v = viewerRect();
  if (!v) return;

  for (const el of boxes.children) {
    if (!el.dataset.n) continue;
    const p = fromNorm(JSON.parse(el.dataset.n), v);
    el.style.left = `${p.left}px`;
    el.style.top = `${p.top}px`;
    el.style.width = `${p.width}px`;
    el.style.height = `${p.height}px`;

  }
  layoutLabels();
}

addEventListener("scroll", followViewer, { passive: true, capture: true });
addEventListener("resize", followViewer, { passive: true });
// 전체화면 전환은 `resize` 가 늦게 오거나 안 올 수 있다. 직접 듣는다.
// ---------------------------------------------------------------------------
// 전체화면에서는 오버레이를 그 안으로 옮긴다
//
// **전체화면일 때 브라우저는 `:fullscreen` 요소와 그 자손만 그린다.** 우리 오버레이는
// `documentElement` 에 붙어 있어 그 바깥이라 **통째로 안 보인다** — `position: fixed`
// 도 `z-index: 최대` 도 소용없다. 확장이 "안 되는" 것처럼 보이지만 실제로는 읽기도
// 번역도 다 돌고 있고 화면에만 안 나온다 (comic-fuz 등).
//
// 전체화면이 풀리면 되돌린다. 안 그러면 사라진 요소 안에 갇힌다.
// ---------------------------------------------------------------------------

function fsElement() {
  return document.fullscreenElement || document.webkitFullscreenElement || null;
}

function reparentOverlay() {
  const el = document.getElementById(OVERLAY_ID);
  if (!el) return;
  const host = fsElement() ?? document.documentElement;
  // `contains` 로 본다 — 전체화면 요소의 **자손**이면 그대로 둬도 그려진다.
  if (host !== el.parentNode && !host.contains(el)) host.appendChild(el);
  // 선택 덮개도 같이 옮긴다 (드래그 중에 전체화면을 켜는 경우).
  const sel = document.getElementById(SELECT_ID);
  if (sel && host !== sel.parentNode && !host.contains(sel)) host.appendChild(sel);
}

function onFullscreenChange() {
  reparentOverlay();
  followViewer();
}

addEventListener("fullscreenchange", onFullscreenChange);
addEventListener("webkitfullscreenchange", onFullscreenChange);

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

//: 라벨이 화면 가장자리에서 남길 여백.
const LABEL_EDGE_PAD = 8;

// ---------------------------------------------------------------------------
// 라벨을 손으로 옮기기
//
// 자동 배치(`fitLabel` + `layoutLabels`)는 대부분 맞지만 늘 맞지는 않는다.
// 그림의 중요한 곳을 가리거나, 좁은 칸에서 자리를 못 찾는 경우가 남는다.
//
// **라벨을 끌면 옮겨진다.** 옮긴 자리는 그 자리에 고정되고 자동 배치가 건드리지
// 않는다. 더블클릭하면 자동으로 되돌린다.
//
// 박스가 아니라 **라벨**을 끈다 — 박스를 끄는 것은 「크기 조정」이 이미 쓴다.
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// 라벨 ↔ 박스 연결선
//
// 라벨을 멀리 옮기면 **어느 말풍선의 번역인지 알 수 없다.** 끄는 동안, 그리고
// 옮겨 둔 라벨에 마우스를 올렸을 때 선으로 잇는다.
//
// SVG 하나를 오버레이에 두고 선 하나만 옮겨 쓴다. 라벨마다 만들면 박스가 20개인
// 페이지에서 DOM 이 그만큼 는다.
// ---------------------------------------------------------------------------

function leaderLine() {
  const root = overlay();
  let svg = root.querySelector("#mlr-leader");
  if (!svg) {
    svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.id = "mlr-leader";
    svg.innerHTML = '<line x1="0" y1="0" x2="0" y2="0" /><circle cx="0" cy="0" r="3" />';
    root.appendChild(svg);
  }
  return svg;
}

/** 라벨과 그 박스를 잇는다. 라벨에서 가장 가까운 박스 변의 한 점을 잡는다 —
 *  박스 중심으로 그으면 선이 말풍선 글자를 가로지른다. */
function showLeader(label) {
  const box = label.closest(".mlr-box");
  if (!box) return;
  const b = box.getBoundingClientRect();
  const l = label.getBoundingClientRect();
  if (!l.width || !b.width) return;

  const lx = l.left + l.width / 2;
  const ly = l.top + l.height / 2;
  // 박스 안쪽으로 물리는 점 (라벨 쪽 변에서 잡는다)
  const bx = Math.max(b.left + 4, Math.min(lx, b.right - 4));
  const by = Math.max(b.top + 4, Math.min(ly, b.bottom - 4));

  const svg = leaderLine();
  const line = svg.querySelector("line");
  const dot = svg.querySelector("circle");
  line.setAttribute("x1", bx); line.setAttribute("y1", by);
  line.setAttribute("x2", lx); line.setAttribute("y2", ly);
  dot.setAttribute("cx", bx); dot.setAttribute("cy", by);
  svg.classList.add("mlr-on");
}

function hideLeader() {
  document.getElementById(OVERLAY_ID)?.querySelector("#mlr-leader")?.classList.remove("mlr-on");
}

// 옮겨 둔 라벨에 마우스를 올리면 어디 것인지 보여 준다. 안 옮긴 라벨은 박스에
// 붙어 있으니 선이 필요 없다.
document.addEventListener("pointerover", (e) => {
  const label = e.target?.closest?.(".mlr-label");
  if (label?.dataset.pinned) showLeader(label);
}, true);
document.addEventListener("pointerout", (e) => {
  if (e.target?.closest?.(".mlr-label") && !dragLabel) hideLeader();
}, true);

let dragLabel = null;

//: 이만큼은 움직여야 "옮겼다" 로 본다. 그냥 누른 것까지 고정하면, 라벨을 클릭할
//: 때마다 자동 배치에서 빠져 나중에 자리가 엉킨다.
const DRAG_THRESHOLD_PX = 3;

document.addEventListener(
  "pointerdown",
  (e) => {
    const label = e.target?.closest?.(".mlr-label");
    // 왼쪽 버튼 + 라벨 위에서만. 크기 조정 손잡이와 겹치지 않게 그 상태는 뺀다.
    //
    // **`Alt` 를 누르고 있으면 잡지 않는다.** 여기서 preventDefault 를 하는 통에
    // 라벨의 글자를 긁을 수가 없다. 단어 하나를 사전에 넣어 보고 싶을 때가 있어서
    // 빠져나갈 구멍을 하나 둔다 — 새 UI 를 더하는 대신 브라우저 기본 동작을
    // 되살리는 쪽이다. 긁고 나면 복사든 검색이든 브라우저 우클릭이 알아서 한다.
    if (!label || e.button !== 0 || e.altKey || label.closest(".mlr-resizing")) return;
    e.preventDefault();
    e.stopPropagation();
    const cur = readShift(label);
    dragLabel = { el: label, x: e.clientX, y: e.clientY, dx: cur.dx, dy: cur.dy, moved: false };
    label.setPointerCapture?.(e.pointerId);
    label.classList.add("mlr-label-dragging");
  },
  true
);

document.addEventListener(
  "pointermove",
  (e) => {
    if (!dragLabel) return;
    e.preventDefault();
    const mx = e.clientX - dragLabel.x;
    const my = e.clientY - dragLabel.y;
    if (!dragLabel.moved && Math.hypot(mx, my) < DRAG_THRESHOLD_PX) return;
    dragLabel.moved = true;
    applyShift(dragLabel.el, Math.round(dragLabel.dx + mx), Math.round(dragLabel.dy + my));
    showLeader(dragLabel.el);
  },
  true
);

document.addEventListener(
  "pointerup",
  (e) => {
    if (!dragLabel) return;
    e.preventDefault();
    e.stopPropagation();
    const { el, moved } = dragLabel;
    el.classList.remove("mlr-label-dragging");
    hideLeader();
    if (moved) {
      // 옮긴 자리를 기억하고 **자동 배치에서 빼 둔다.** 안 빼면 다음 번역이 도착할
      // 때 원래 자리로 되돌아간다.
      const t = /translate\(([-\d.]+)px,\s*([-\d.]+)px\)/.exec(el.style.transform || "");
      el.dataset.dx = String(Math.round(Number(t?.[1] ?? 0)));
      el.dataset.dy = String(Math.round(Number(t?.[2] ?? 0)));
      el.dataset.pinned = "1";
    }
    dragLabel = null;
  },
  true
);

// 더블클릭하면 자동 배치로 되돌린다 — 잘못 옮겼을 때 되돌릴 길이 있어야 한다.
document.addEventListener(
  "dblclick",
  (e) => {
    const label = e.target?.closest?.(".mlr-label");
    if (!label) return;
    e.preventDefault();
    e.stopPropagation();
    hideLeader();
    delete label.dataset.pinned;
    delete label.dataset.dy;
    label.dataset.dx = "0";
    layoutLabels();
    status("라벨 자리를 되돌렸다");
  },
  true
);

/** 지금 걸린 이동량을 읽는다. */
function readShift(label) {
  return { dx: Number(label.dataset.dx || 0), dy: Number(label.dataset.dy || 0) };
}

/** 라벨의 가로·세로 이동을 한 `transform` 으로 합쳐 넣는다.
 *
 * 화면 안으로 밀어 넣는 것(`fitLabel`)과 겹침을 피해 내리는 것(`layoutLabels`)이
 * 둘 다 `transform` 을 쓴다. 각자 대입하면 나중 것이 앞의 것을 지운다.
 */
function applyShift(label, dx, dy) {
  label.style.transform = dx || dy ? `translate(${dx}px, ${dy}px)` : "";
}

//: 라벨 폭의 하한·상한. 하한보다 좁아지면 글자가 세로로 한 자씩 떨어져 못 읽는다.
const LABEL_MIN_WIDTH = 120;
const LABEL_MAX_WIDTH = 340;

/** 라벨이 화면 밖으로 나가지 않게 방향과 최대 폭을 정한다.
 *
 * 예전에는 `left: 0` 에 고정이고, 위로 붙일지는 **28px 어림값**으로 판단했다.
 * 그래서 오른쪽 끝 말풍선의 번역은 오른쪽으로 넘쳐 못 읽었고, 여러 줄짜리 긴
 * 번역은 아래로 넘쳐 못 읽었다 — 어림값이 한 줄 기준이었기 때문이다.
 *
 * **실제 크기를 재서 정한다.**
 */
function fitLabel(box, label, panel) {
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const b = box.getBoundingClientRect();

  // **패널이 가린 폭도 화면 밖과 똑같이 취급한다.** 화면 안이라도 패널 아래로
  // 들어가면 못 읽는다. 세로로 겹치는 구간에서만 따진다 (패널은 위쪽에만 있다).
  let rightEdge = vw;
  if (panel && b.top < panel.bottom && b.bottom > panel.top) {
    rightEdge = Math.min(rightEdge, panel.left);
  }

  // -- 좌우 -------------------------------------------------------------------
  //
  // **폭 어림값(140px)으로 정하면 안 된다.** 예전에는 "오른쪽에 140px 미만이면
  // 뒤집는다" 였는데, 라벨이 실제로 얼마나 넓은지와 무관한 숫자다. 짧은 번역은
  // 140px 이하로도 들어가고, 긴 번역은 200px 이 남아도 넘친다 — 끝이 조금씩 잘렸다.
  //
  // 아래쪽처럼 **실제로 재서** 정한다: 한 번 놓아 보고, 넘치면 뒤집고, 그래도
  // 넘치면 넘치는 만큼 밀어 넣는다.
  const roomRight = Math.max(0, rightEdge - b.left - LABEL_EDGE_PAD);
  const roomLeft = Math.max(0, b.right - LABEL_EDGE_PAD);

  // 폭이 넓은 쪽으로 먼저 놓는다. 최대 폭은 그쪽으로 실제 쓸 수 있는 만큼.
  const toLeft = roomLeft > roomRight;
  box.classList.toggle("mlr-label-left", toLeft);
  label.dataset.dx = "0";
  applyShift(label, 0, 0);
  label.style.setProperty(
    "--mlr-label-max",
    `${Math.max(LABEL_MIN_WIDTH, Math.min(LABEL_MAX_WIDTH, toLeft ? roomLeft : roomRight))}px`
  );

  // 재 보고 그래도 밖으로 나가면 그만큼 밀어 넣는다. 최대 폭을 줬어도 긴 단어
  // 하나가 안 접히면(`overflow-wrap: anywhere` 로 웬만하면 접히지만) 넘칠 수 있고,
  // 양쪽 다 좁으면 애초에 들어갈 자리가 없다.
  const r = label.getBoundingClientRect();
  let dx = 0;
  if (r.right > rightEdge - LABEL_EDGE_PAD) dx = rightEdge - LABEL_EDGE_PAD - r.right;
  if (r.left + dx < LABEL_EDGE_PAD) dx = LABEL_EDGE_PAD - r.left;
  // **`transform` 을 직접 쓰지 않는다.** 겹침 배치(`layoutLabels`)도 같은 속성을
  // 세로 이동에 쓰는데, 나중에 쓰는 쪽이 앞의 값을 지워 버린다. 두 값을 따로
  // 들고 있다가 합쳐서 넣는다.
  label.dataset.dx = String(Math.round(dx));
  applyShift(label, Math.round(dx), 0);

  // -- 위아래 -----------------------------------------------------------------
  //
  // 실제 높이를 재고 정한다. **위쪽 자리도 본다** — 아래가 모자라다고 무턱대고
  // 위로 붙이면 화면 위로 넘치는 경우가 있다 (화면 아래쪽의 긴 번역).
  label.style.removeProperty("display");
  const h = label.getBoundingClientRect().height || 0;
  const fitsBelow = b.bottom + 2 + h <= vh - LABEL_EDGE_PAD;
  const fitsAbove = b.top - 2 - h >= LABEL_EDGE_PAD;
  box.classList.toggle("mlr-label-above", !fitsBelow && fitsAbove);
}

function doLayoutLabels() {
  const root = document.getElementById(OVERLAY_ID);
  if (!root) return;

  const labels = [...root.querySelectorAll(".mlr-box")]
    .filter(isShown) // 걸러진 박스는 뺀다
    .map((b) => b.querySelector(".mlr-label"))
    .filter(Boolean);

  // 접어 둔 상태에서는 마우스 올린 하나만 보이므로 겹칠 일이 없다. 되돌려 둔다.
  if (!root.classList.contains("mlr-show-all")) {
    for (const l of labels) {
      applyShift(l, Number(l.dataset.dx || 0), l.dataset.pinned ? Number(l.dataset.dy || 0) : 0);
    }
    return;
  }

  // 패널 사각형은 한 번만 잰다 — 라벨마다 재면 강제 레이아웃이 그만큼 는다.
  const panelEl = root.querySelector("#mlr-panel");
  const panelRect =
    panelEl && getComputedStyle(panelEl).display !== "none"
      ? panelEl.getBoundingClientRect()
      : null;

  const hit = (a, b) =>
    a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
  const shift = (r, dy) => ({
    left: r.left, right: r.right, top: r.top + dy, bottom: r.bottom + dy,
  });

  const placed = [];
  const items = labels
    .map((l) => {
      // **손으로 옮긴 것은 건드리지 않는다.** `fitLabel` 은 `dataset.dx` 를 다시
      // 쓰고 `applyShift(l, dx, 0)` 로 세로 이동을 지운다 — 옮겨 둔 자리가 그대로
      // 사라진다. 저장해 둔 값을 그대로 다시 걸어 주고 배치 계산에서 뺀다.
      //
      // 이게 빠져 있어서 "옮긴 라벨이 원문 보기 하면 제자리로 돌아간다" 가 났다.
      // 아래 루프에 `pinned` 를 보는 분기는 있었는데 여기서 넣어 주지 않아
      // 언제나 undefined 였다 — 죽은 코드였다.
      const pinned = Boolean(l.dataset.pinned);
      if (pinned) {
        applyShift(l, Number(l.dataset.dx || 0), Number(l.dataset.dy || 0));
        return { l, r: l.getBoundingClientRect(), pinned };
      }
      // **겹침을 풀기 전에 화면 안으로 들어오게 한다.** 순서가 바뀌면 방향이
      // 바뀌면서 사각형이 달라져 겹침 계산이 어긋난다.
      const box = l.closest(".mlr-box");
      if (box) fitLabel(box, l, panelRect);
      return { l, r: l.getBoundingClientRect(), pinned };
    })
    .sort((a, b) => a.r.top - b.r.top || a.r.left - b.r.left);

  for (const { l, r, pinned } of items) {
    if (r.width === 0) continue;
    // 손으로 옮긴 것은 자리만 차지하고 밀리지 않는다.
    if (pinned) {
      placed.push(r);
      continue;
    }
    let dy = 0;
    while (dy <= LABEL_MAX_SHIFT_PX && placed.some((p) => hit(p, shift(r, dy)))) {
      dy += LABEL_STEP_PX;
    }
    // 밀어낸 결과가 화면 밖이면 밀지 않는다 — 안 겹치게 하려다 아예 못 읽게 된다.
    if (dy > LABEL_MAX_SHIFT_PX || r.bottom + dy > window.innerHeight - LABEL_EDGE_PAD) dy = 0;
    applyShift(l, Number(l.dataset.dx || 0), dy);
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
