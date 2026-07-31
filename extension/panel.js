// 오버레이 · 조작 패널 · 상태 표시 · 우클릭 메뉴
//
// `content.js` 에서 갈라 나온 파일이다. **모듈이 아니다** — MV3 콘텐츠
// 스크립트는 manifest 의 `js` 배열 순서대로 같은 전역에서 실행된다.
// 그래서 import 없이 서로의 함수를 그냥 부른다. 순서를 바꾸면 깨진다.


// ---------------------------------------------------------------------------
// 오버레이
// ---------------------------------------------------------------------------

// **`overlay()` 보다 위에 둔다.** `const`/`let` 은 호이스팅돼도 초기화 전에는
// 못 읽는다(TDZ). 지금은 `overlay()` 가 늦게 불려 안 터지지만, 누가 초기화 중에
// 부르는 순간 바로 깨진다.
// 버튼 여덟 개를 늘 띄워 두면 화면을 가리고, 정작 쓰는 것은 두셋이다.
// **자주 쓰는 셋만 보이고 나머지는 「⋯」 안에 접는다.**
//: 버튼 정의. **한 곳에서만 관리한다** — 예전에는 이름·설명·단축키가 HTML 문자열
//: 안에 흩어져 있어 단축키를 바꿀 때 놓치기 쉬웠다.
//:
//: 툴팁은 브라우저 기본 `title` 을 쓰지 않는다. 뜨는 데 1초 넘게 걸리고 위치도
//: 못 정해서, 마우스를 올린 순간 단축키를 알려 주는 용도로는 못 쓴다.
const PANEL_BUTTONS = [
  { act: "read", label: "번역", key: "Alt+Shift+M", desc: "이 페이지 번역", sub: "Shift+클릭 — 캐시 무시하고 다시" },
  { act: "select", label: "영역", key: "Alt+Shift+D", desc: "읽을 곳 직접 고르기", sub: "자동으로 못 찾을 때" },
  { act: "auto", label: "자동", key: null, desc: "넘기면 알아서 번역" },
];

const PANEL_MORE = [
  { act: "fresh", label: "갱신", key: "Alt+Shift+R", desc: "캐시 버리고 다시 번역", sub: "결과가 이상할 때" },
  { act: "speak", label: "음성", key: "Alt+Shift+P", desc: "원문 소리내어 읽기" },
  { act: "labels", label: "라벨", key: "Alt+Shift+L", desc: "번역 항상 보이기" },
  { act: "extra", label: "효과음", key: "Alt+Shift+S", desc: "효과음·잡문도 보이기" },
  { act: "status", label: "상태", key: null, desc: "왼쪽 위 진행 표시" },
  { act: "hide", label: "숨김", key: "`", desc: "잠깐 걷어내고 그림 보기", sub: "백틱은 누르는 동안만" },
  { act: "drop", label: "캐시삭제", key: null, desc: "이 페이지 캐시만 버리기", sub: "다시 읽지는 않는다" },
  { act: "signals", label: "진단", key: null, desc: "이 페이지가 어떤 뷰어인지 재본다", sub: "판정 경계를 정하려고 모으는 중" },
];

// ---------------------------------------------------------------------------
// 단축키 표기
//
// `manifest.json` 은 `Alt+Shift+M` 하나만 적어 두면 Chrome 이 플랫폼에 맞춰
// 매핑한다 — macOS 에서 `Alt` 는 `Option(⌥)` 이다. **동작은 알아서 맞는데 화면에
// 적힌 글자는 안 바뀐다.** 맥에서 「Alt+Shift+M」 을 보면 어느 키인지 한 번 더
// 생각해야 한다.
//
// 보이는 쪽만 그 플랫폼의 기호로 바꾼다.
// ---------------------------------------------------------------------------

const IS_MAC = /Mac|iPhone|iPad/i.test(
  navigator.userAgentData?.platform || navigator.platform || navigator.userAgent
);

const MAC_KEYS = { Alt: "⌥", Shift: "⇧", Ctrl: "⌃", Control: "⌃", Cmd: "⌘", Meta: "⌘" };

/** `Alt+Shift+M` → 맥이면 `⌥⇧M`, 아니면 그대로. */
function keyLabel(k) {
  if (!k || !IS_MAC) return k;
  // 「` (누르는 동안)」 처럼 조합키가 아닌 것은 건드리지 않는다.
  if (!k.includes("+")) return k;
  return k
    .split("+")
    .map((part) => MAC_KEYS[part.trim()] ?? part.trim())
    .join("");
}

const btnHtml = (b) =>
  `<button data-act="${b.act}" data-desc="${escapeHtml(b.desc)}"` +
  (b.sub ? ` data-sub="${escapeHtml(b.sub)}"` : "") +
  (b.key ? ` data-key="${escapeHtml(keyLabel(b.key))}"` : "") +
  `>${b.label}</button>`;

const PANEL_HTML = `
<div id="mlr-panel">
  ${PANEL_BUTTONS.map(btnHtml).join("\n  ")}
  <button data-act="more" class="mlr-more" data-desc="나머지 기능">⋯</button>
  <div class="mlr-rest" hidden>
    ${PANEL_MORE.map(btnHtml).join("\n    ")}
  </div>
  <div id="mlr-tip" hidden></div>
</div>
<div id="mlr-busy" hidden><span class="mlr-dot"></span><span class="mlr-dot"></span><span class="mlr-dot"></span><b></b></div>`;

/** 자동 감지가 켜져 있는가. background 가 `auto-state` 로 알려 준다. */
let autoOn = false;

function overlay() {
  let el = document.getElementById(OVERLAY_ID);
  if (!el) {
    el = document.createElement("div");
    el.id = OVERLAY_ID;
    el.innerHTML =
      '<div id="mlr-status"></div><div id="mlr-boxes"></div>' + PANEL_HTML;
    (fsElement() ?? document.documentElement).appendChild(el);
    bindPanel(el);
    applyLabelSize();
    applyToggles();
  } else {
    // 이미 있으면 지금 전체화면 상태에 맞는 자리인지 확인한다.
    reparentOverlay();
  }
  return el;
}

/** 오버레이를 보이게 한다. **`overlay()` 는 이걸 하지 않는다.**
 *
 * 예전에는 `overlay()` 가 부를 때마다 `visibility = "visible"` 로 되돌렸다. 그런데
 * `status()` 도 `overlay()` 를 부른다 — 숨겨 놓은 사이에 상태 메시지가 한 번이라도
 * 나가면 **오버레이가 도로 켜지고, 그 상태로 캡처돼 상태줄 글자가 번역된다.**
 * 켜는 것은 켜려는 곳에서만 명시적으로 한다.
 */
//: 라벨 글씨 크기의 허용 범위. 밖의 값이 오면 기본으로 떨어진다 — 저장소가
//: 오염돼도 화면이 못 쓰게 되면 안 된다.
const LABEL_SIZE_MIN = 9;
const LABEL_SIZE_MAX = 20;
const LABEL_SIZE_DEFAULT = 12;

// ---------------------------------------------------------------------------
// 눌러 놓은 토글은 페이지를 옮겨도 남는다
//
// 「라벨」「효과음」「상태」「패널 고정」은 화면에만 있어서 페이지를 넘기거나 다른
// 작품으로 옮기면 초기값으로 돌아갔다. 매번 다시 누르는 것이 번거롭다.
//
// 「자동」은 예외다 — 그건 background 가 탭별로 들고 있고, 사이트 목록에 따라
// 알아서 켜지므로 여기서 건드리지 않는다.
//
// `storage.sync` 라 기기 사이에도 따라간다 (맥북에서 켜면 윈도우에서도 켜져 있다).
// ---------------------------------------------------------------------------

async function applyToggles() {
  const root = document.getElementById(OVERLAY_ID);
  if (!root) return;
  let got;
  try {
    got = await chrome.storage.sync.get([...Object.keys(STICKY_TOGGLES), "panelPinned"]);
  } catch {
    return;
  }
  for (const [key, cls] of Object.entries(STICKY_TOGGLES)) {
    root.classList.toggle(cls, Boolean(got[key]));
  }
  root.querySelector("#mlr-panel")?.classList.toggle("mlr-pinned", Boolean(got.panelPinned));
  // 「⋯」 안쪽도 고정 상태에 맞춰 열어 둔다.
  const rest = root.querySelector("#mlr-panel .mlr-rest");
  if (rest) rest.hidden = !got.panelPinned;
  syncPanel();
  layoutLabels();
}

/** 지금 상태를 저장한다. 토글을 누를 때마다 부른다. */
function saveToggles() {
  const root = document.getElementById(OVERLAY_ID);
  if (!root) return;
  const out = { panelPinned: Boolean(root.querySelector("#mlr-panel")?.classList.contains("mlr-pinned")) };
  for (const [key, cls] of Object.entries(STICKY_TOGGLES)) {
    out[key] = root.classList.contains(cls);
  }
  chrome.storage.sync.set(out).catch(() => {});
}

/** 저장된 글씨 크기를 오버레이에 꽂는다. CSS 변수 하나면 기본·전체펼침이 같이 따라온다. */
async function applyLabelSize() {
  const el = document.getElementById(OVERLAY_ID);
  if (!el) return;
  let px = LABEL_SIZE_DEFAULT;
  try {
    const got = await chrome.storage.sync.get("labelSize");
    const v = Number(got.labelSize);
    if (Number.isFinite(v) && v >= LABEL_SIZE_MIN && v <= LABEL_SIZE_MAX) px = v;
  } catch {
    /* 못 읽으면 기본값 */
  }
  el.style.setProperty("--mlr-label-size", `${px}px`);
}

// 옵션 화면에서 바꾸면 **열려 있는 페이지에도 바로 반영한다.** 새로고침해야만
// 보이면 어느 크기가 맞는지 고르기가 번거롭다.
try {
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== "sync") return;
    if (changes.labelSize) applyLabelSize();
    if (changes.ttsUrl) serverTts = null; // 폴백 판단을 다시 하게 한다
    // 다른 탭에서 토글을 누르면 여기도 따라간다 — 탭마다 다르면 헷갈린다.
    if (Object.keys(STICKY_TOGGLES).some((k) => changes[k]) || changes.panelPinned) {
      applyToggles();
    }
  });
} catch {
  /* 저장소가 막힌 환경 */
}

function showOverlay() {
  overlay().style.visibility = "visible";
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
  const panel = root.querySelector("#mlr-panel");
  const tip = root.querySelector("#mlr-tip");

  // 마우스를 올린 버튼의 설명과 단축키를 패널 왼쪽에 띄운다.
  panel.addEventListener("pointerover", (e) => {
    const b = e.target?.closest?.("button[data-desc]");
    if (!b) return;
    // 한 줄에 다 넣으면 길어져 만화를 가린다. 짧은 설명 + 단축키를 한 줄에 두고,
    // 부연은 아래 작은 글씨로 내린다.
    tip.innerHTML =
      `<span class="mlr-tip-main">${escapeHtml(b.dataset.desc)}` +
      (b.dataset.key ? `<kbd>${escapeHtml(b.dataset.key)}</kbd>` : "") +
      `</span>` +
      (b.dataset.sub ? `<span class="mlr-tip-sub">${escapeHtml(b.dataset.sub)}</span>` : "");
    tip.hidden = false;
    // 그 버튼 높이에 맞춰 놓는다 — 어느 버튼 설명인지 눈으로 이어져야 한다.
    tip.style.top = `${b.offsetTop}px`;
  });
  panel.addEventListener("pointerleave", () => {
    tip.hidden = true;
  });

  panel.addEventListener("click", (e) => {
    const act = e.target?.dataset?.act;
    if (!act) return;
    e.preventDefault();
    e.stopPropagation();
    switch (act) {
      case "read":
        // Shift 를 누른 채 「번역」 = 「갱신」. 갱신은 「⋯」 안에 접혀 있어서
        // 손이 잘 안 가는데, 결과가 이상할 때 바로 필요한 것이 그거다.
        send({ type: "do-read", refresh: e.shiftKey });
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
        saveToggles();
        break;
      case "extra":
        root.classList.toggle("mlr-show-extra");
        layoutLabels();
        syncPanel();
        saveToggles();
        break;
      case "status":
        root.classList.toggle("mlr-hide-status");
        syncPanel();
        saveToggles();
        break;
      case "speak":
        if (speaking) stopSpeaking();
        else speakAll();
        break;
      case "hide":
        root.classList.toggle("mlr-hidden");
        syncPanel();
        break;
      case "drop":
        send({ type: "purge-page" });
        break;
      case "signals": {
        // **상태줄이 꺼져 있어도 보이게 한다.** 진단인데 안 보이면 소용이 없다.
        root.classList.remove("mlr-hide-status");
        const s = reportSignals();
        // 서비스 로그에도 남긴다 — 사이트를 옮겨 다니며 눌러도 알아서 쌓인다.
        send({ type: "signals", data: s });
        break;
      }
      case "more": {
        // 접힌 상태에서 누르면 **펼친 채로 고정**한다 (마우스를 떼도 안 접힌다).
        // 나머지 기능을 쓰려면 손이 패널을 벗어나야 하는 경우가 있다.
        const panel = root.querySelector("#mlr-panel");
        const rest = panel.querySelector(".mlr-rest");
        if (!panel.classList.contains("mlr-pinned")) {
          panel.classList.add("mlr-pinned");
          rest.hidden = false;
        } else if (rest.hidden) {
          rest.hidden = false;
        } else {
          rest.hidden = true;
          panel.classList.remove("mlr-pinned");
        }
        syncPanel();
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
  on("more", !root.querySelector("#mlr-panel .mlr-rest")?.hidden);
  // 접혀 있을 때도 "뭔가 켜져 있다" 는 것은 보여야 한다 (특히 「자동」).
  const anyOn = autoOn || root.classList.contains("mlr-show-all")
    || root.classList.contains("mlr-show-extra") || speaking;
  root.querySelector('#mlr-panel [data-act="more"]')?.classList.toggle("mlr-has-on", anyOn);
  on("hide", root.classList.contains("mlr-hidden"));
}

function reset() {
  overlay().querySelector("#mlr-boxes").innerHTML = "";
}

// ---------------------------------------------------------------------------
// 지금 뭐 하는 중인지 (오른쪽 아래)
//
// 상태줄을 끄고 보는 경우가 많다 — 만화를 가리기 때문이다. 그러면 **번역 중인지,
// 서버가 끊긴 건지, 그냥 멈춘 건지** 알 방법이 없다. 박스가 안 사라지는 이유를
// 가늠할 수 없다는 제보.
//
// 상태줄과 **따로** 둔다. 오른쪽 아래 구석이라 만화를 거의 안 가리고, 점 세 개가
// 움직이는 동안에는 "돌고 있다" 는 것이 한눈에 보인다.
// ---------------------------------------------------------------------------

let busyTimer = null;

function setBusy(on, label = "") {
  const el = document.getElementById(OVERLAY_ID)?.querySelector("#mlr-busy");
  if (!el) return;
  clearTimeout(busyTimer);
  el.querySelector("b").textContent = label;
  el.classList.toggle("mlr-busy-error", false);
  if (on) {
    el.hidden = false;
    // **영영 도는 것처럼 보이면 안 된다.** 서버가 조용히 끊기면 done 이 안 오는데,
    // 그때 계속 돌고 있으면 "멈춘 건지 도는 건지" 를 또 알 수 없다.
    busyTimer = setTimeout(() => {
      el.classList.add("mlr-busy-error");
      el.querySelector("b").textContent = "응답 없음";
      busyTimer = setTimeout(() => (el.hidden = true), 6000);
    }, 45000);
  } else {
    el.hidden = true;
  }
}

/** 끝났다는 표시를 잠깐 보여 준다. */
function flashBusy(label, isError = false) {
  const el = document.getElementById(OVERLAY_ID)?.querySelector("#mlr-busy");
  if (!el) return;
  clearTimeout(busyTimer);
  el.hidden = false;
  el.classList.toggle("mlr-busy-error", isError);
  el.querySelector("b").textContent = label;
  busyTimer = setTimeout(() => (el.hidden = true), isError ? 5000 : 1200);
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
/** 캐시에서 온 좌표를 지금 화면에 맞게 옮긴다.
 *
 * 캐시는 phash 로 찾는데 퍼지 매칭이라 **크기가 다른 캡처도 같은 행에 붙는다.**
 * 저장된 bbox 는 그때 캡처의 이미지 좌표계라 `toCss` 로 바로 풀면 어긋난다.
 * 서버가 그 좌표의 기준(그때 뷰어 사각형)을 같이 주므로, 뷰어 대비 비율을 내
 * 지금 사각형에 다시 곱한다. 근사가 아니라 같은 기준으로 되돌리는 것이다.
 *
 * **화면 밖으로 나가면 환산을 믿지 않는다.** 기준이 어긋나면 박스가 뷰포트 밖으로
 * 날아가 번역이 통째로 사라진 것처럼 보인다. 그럴 바에는 이번 캡처 좌표로 그리는
 * 편이 낫다 — 조금 어긋나도 보이기는 한다.
 */
function fromCachedFrame([x, y, w, h], base, now) {
  if (!base || !now || !base[2] || !base[3]) return toCss([x, y, w, h]);
  const [bx, by, bw, bh] = base;
  const p = {
    left: now.x + ((x - bx) / bw) * now.width,
    top: now.y + ((y - by) / bh) * now.height,
    width: (w / bw) * now.width,
    height: (h / bh) * now.height,
  };
  const sane =
    Number.isFinite(p.left) && Number.isFinite(p.top) && p.width > 0 && p.height > 0 &&
    p.left < innerWidth && p.top < innerHeight && p.left + p.width > 0 && p.top + p.height > 0;
  return sane ? p : toCss([x, y, w, h]);
}

function toCss([x, y, w, h]) {
  const k = 1 / (ctx.scale * ctx.dpr);
  return {
    left: ctx.rect.x + x * k,
    top: ctx.rect.y + y * k,
    width: w * k,
    height: h * k,
  };
}

/** 옛 결과를 흐리게 하고 상호작용을 끊는다. `begin` 의 `reset()` 이 실제로 지운다.
 *
 * 지우지 않고 흐리게만 하는 이유: 곧바로 비우면 화면이 한 번 텅 비었다가 다시
 * 차서 오히려 덜컹인다. 남겨 두면 "여기 뭔가 있었다" 는 자리 감각이 이어진다.
 */
function markStale() {
  const boxes = document.getElementById(OVERLAY_ID)?.querySelector("#mlr-boxes");
  if (boxes) boxes.classList.add("mlr-stale");
}

/** 지금 화면에 보이는 박스인가.
 *
 * **`opacity` 로 판정하지 않는다.** 걸러진 효과음을 보이게 할 때 opacity 를 쓰면
 * 쌓임 맥락이 생겨 라벨이 다른 박스 위로 못 올라간다. 그래서 CSS 를 색으로 바꿨는데,
 * opacity 로 판정하던 코드가 조용히 어긋났다. 클래스로 직접 본다.
 */
function isShown(b) {
  const hidden = b.classList.contains("mlr-kind-sfx") || b.classList.contains("mlr-kind-extra");
  return !hidden || overlay().classList.contains("mlr-show-extra");
}

function inClip(p, c) {
  const mx = p.left + p.width / 2;
  const my = p.top + p.height / 2;
  return mx >= c.x && mx <= c.x + c.width && my >= c.y && my <= c.y + c.height;
}

/** 지금 화면에 있는 번역을 원문 기준으로 챙겨 둔다.
 *
 * 다시 읽으면 박스를 새로 그리는데, 그 읽기가 중간에 끊기면(같은 페이지를 연달아
 * 읽을 때 앞의 것이 취소된다) **원문만 남고 번역이 사라진다.** 갱신을 누르기 전에는
 * 돌아오지 않는다 — 실제로 그 증상이 났다.
 *
 * 원문이 같으면 같은 말풍선이다. 그 번역을 그대로 물려준다. 새 번역이 도착하면
 * `fillTranslation` 이 덮어쓰므로 낡은 것이 남지도 않는다.
 */
function carryOverTranslations() {
  const got = new Map();
  for (const box of document.querySelectorAll(".mlr-box.mlr-translated")) {
    const ja = box.dataset.ja || "";
    if (ja && box.dataset.ko) got.set(ja, { ko: box.dataset.ko, kind: box.dataset.kind });
  }
  return got;
}

function drawBoxes(regions) {
  const carried = carryOverTranslations();
  const boxes = overlay().querySelector("#mlr-boxes");
  // 전체 읽기는 `begin` 에서 이미 비웠다. 여기서 또 비우면 영역 하나만 다시
  // 읽을 때 나머지 박스가 통째로 사라진다.
  const vrect = viewerRect();
  let drawn = 0;
  for (const r of regions) {
    // 캐시에서 온 좌표는 그때 기준으로 환산한다 (content.js `fromCachedFrame`).
    // 기준은 **이번 캡처가 쓴 사각형**이다. 여기서 다시 재면 뒤로 돌아갔을 때
    // 뷰어 요소가 갈려 엉뚱한 값이 나온다 (박스가 화면 밖으로 날아갔다).
    const p = ctx.cachedViewer
      ? fromCachedFrame(r.bbox, ctx.cachedViewer, ctx.viewerFull || vrect)
      : toCss(r.bbox);
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
    // **뷰어 기준 비율을 박아 둔다.** 스크롤·확대·전체화면에서 이 값으로 다시 놓는다.
    if (vrect) {
      div.dataset.n = JSON.stringify({
        nx: (p.left - vrect.x) / vrect.width,
        ny: (p.top - vrect.y) / vrect.height,
        nw: p.width / vrect.width,
        nh: p.height / vrect.height,
      });
      // **처음 자리는 따로 박아 둔다.** `n` 은 손으로 크기를 바꾸면 갱신되는데,
      // 캐시가 다음에 돌려주는 것은 언제나 처음 자리다. 지웠다고 적을 때 이 값을
      // 써야 캐시가 돌려준 원래 박스와 맞출 수 있다 (markRemoved 참고).
      div.dataset.n0 = div.dataset.n;
    }
    // 원문을 남겨 둔다. 번역이 도착하면 라벨을 덮어쓰므로, 보관하지 않으면
    // "원문 보기" 를 할 수가 없다.
    div.dataset.ja = r.text ?? "";
    // 부분 읽기로 생긴 박스만 저장 대상이다 (전체 읽기 결과는 캐시가 들고 있다).
    if (ctx.partial) div.dataset.manual = "1";
    div.innerHTML = `<span class="mlr-label">${escapeHtml(r.text)}</span>`;
    // 같은 원문이 방금 전 화면에 번역돼 있었으면 그것을 물려받는다 (위 주석 참조).
    const before = carried.get(r.text ?? "");
    if (before) {
      div.dataset.ko = before.ko;
      div.dataset.kind = before.kind || "dialogue";
      div.classList.add("mlr-translated");
      div.querySelector(".mlr-label").textContent = before.ko;
    }
    // 라벨이 아래 말풍선을 가리지 않게, 박스가 화면 아래쪽이면 위로 붙인다
    boxes.appendChild(div);
    drawn += 1;
  }
  layoutLabels();
  return drawn;
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
      markRemoved(box);
      box.remove();
      saveEdits();
      status("영역 지움");
      return;
    }
    if (act === "extra") {
      const on = overlay().classList.toggle("mlr-show-extra");
      layoutLabels();
      syncPanel();
      saveToggles();
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
