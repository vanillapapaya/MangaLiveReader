// 잠깐 숨기기 · 페이지 전환 감지 · 자동 켜기 · 정리
//
// `content.js` 에서 갈라 나온 파일이다. **모듈이 아니다** — MV3 콘텐츠
// 스크립트는 manifest 의 `js` 배열 순서대로 같은 전역에서 실행된다.
// 그래서 import 없이 서로의 함수를 그냥 부른다. 순서를 바꾸면 깨진다.


// ---------------------------------------------------------------------------
// 잠깐 숨기기
//
// 원문 그림을 확인하고 싶을 때가 있다 — 박스가 말풍선을 가리거나, 번역이 맞는지
// 원문 획을 보고 싶을 때.
//
// **누르고 있는 동안만** 사라진다. 토글이면 "다시 켜는 것" 을 기억해야 하는데,
// 잠깐 보는 용도에는 누르고 있는 쪽이 손이 덜 간다. 길게 볼 일이 있으면 패널의
// 「숨김」 으로 고정할 수 있다.
// ---------------------------------------------------------------------------

const PEEK_KEY = "Backquote"; // ` — 뷰어 단축키와 겹칠 일이 거의 없다

function setPeek(on) {
  const root = document.getElementById(OVERLAY_ID);
  if (root) root.classList.toggle("mlr-peek", on);
}

addEventListener("keydown", (e) => {
  // 입력란에 타이핑 중이면 안 된다.
  const t = e.target;
  if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
  if (e.code === PEEK_KEY && !e.repeat && !e.altKey && !e.ctrlKey && !e.metaKey) {
    e.preventDefault();
    setPeek(true);
  }
}, true);

addEventListener("keyup", (e) => {
  if (e.code === PEEK_KEY) setPeek(false);
}, true);

// 키를 누른 채 탭을 옮기면 keyup 을 못 받는다. 돌아왔을 때 계속 숨어 있으면
// "사라졌다" 로 보이므로 되돌린다.
addEventListener("blur", () => setPeek(false));

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
    saveToggles();
    status(on ? "효과음·잡문 표시" : `효과음·잡문 ${extraCount}개 숨김`);
    return;
  }
  if (e.altKey && e.shiftKey && e.code === "KeyL") {
    const el = document.getElementById(OVERLAY_ID);
    if (el) {
      el.classList.toggle("mlr-show-all");
      layoutLabels();
      syncPanel();
      saveToggles();
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
//: `fast` = 사람이 직접 넘긴 신호(클릭·방향키). DOM 변화와 달리 넘김이 거의
//: 확실하므로 background 가 더 빨리, 그리고 오버레이를 숨긴 채로 확인한다.
const notifyMaybeChanged = throttle((opts) => {
  // DOM 이 움직였으면 주소가 바뀌었을 수도 있다 (SPA 는 pushState 로 옮겨 다니는데
  // 그건 이벤트를 안 낸다). 자동 감지가 꺼져 있어도 이건 봐야 한다.
  onNavigated();
  if (!autoOn) return;
  send({ type: "page-maybe-changed", fast: Boolean(opts?.fast) });
}, 250);

function throttle(fn, ms) {
  let last = 0;
  let timer = null;
  let pending = null;
  return (opts) => {
    // 빠른 신호가 한 번이라도 섞였으면 빠른 쪽으로 올린다.
    pending = { fast: Boolean(pending?.fast || opts?.fast) };
    const now = Date.now();
    const wait = Math.max(0, ms - (now - last));
    if (timer) return;
    timer = setTimeout(() => {
      timer = null;
      last = Date.now();
      const o = pending;
      pending = null;
      fn(o);
    }, wait);
  };
}

// ---------------------------------------------------------------------------
// 만화 사이트면 「자동」을 알아서 켠다
//
// 매번 손으로 켜는 것이 번거롭다. 다만 **아무 데서나 켜면 안 된다** — 자동은
// 화면을 캡처해 서버로 보내는 동작이라, 목차나 다른 페이지에서 돌면 헛돈다.
//
// 두 조건을 다 만족해야 켠다:
//   1. 호스트가 목록에 있다 (옵션 화면에서 고칠 수 있다)
//   2. **뷰어로 볼 만한 요소가 실제로 있다** — 목차 페이지에는 없다.
//      `probeViewer()` 가 폴백(뷰포트 전체)으로 떨어지면 뷰어가 아니라고 본다.
// ---------------------------------------------------------------------------

/** 주소가 뷰어 페이지처럼 생겼는가.
 *
 * **호스트만 보면 홈페이지에서도 켜진다.** `probeViewer()` 는 200px 넘는 그림 하나면
 * 통과하는데 홈페이지 배너가 그대로 걸린다 — 메인만 들어가도 다 번역해 버렸다.
 *
 * 주소 칸을 `/` 로 잘라 **한 칸과 통째로 같은지** 본다. 일부만 겹치는 것은 안 친다
 * (`read` 가 `readme` 에 걸리면 곤란하다). 다만 `episode-123`·`viewer.html` 처럼
 * 뒤에 구분자가 붙은 것은 같은 것으로 본다.
 */
function pathMatches(pathname, words) {
  const list = (words || "")
    .split(/[\n,]/)
    .map((x) => x.trim().toLowerCase().replace(/^\/+|\/+$/g, ""))
    .filter(Boolean);
  if (!list.length) return true; // 비워 두면 이 규칙을 안 쓴다
  const segs = pathname.toLowerCase().split("/").filter(Boolean);
  return segs.some((s) => list.some((w) => s === w || s.startsWith(w + "-") ||
                                            s.startsWith(w + "_") || s.startsWith(w + ".")));
}

function hostMatches(host, list) {
  const h = host.toLowerCase();
  return list
    .split(/[\n,]/)
    .map((x) => x.trim().toLowerCase().replace(/^\*?\.?/, ""))
    .filter(Boolean)
    .some((p) => h === p || h.endsWith("." + p));
}

async function maybeAutoEnable() {
  if (autoOn || !alive()) return;
  let cfg;
  try {
    cfg = await chrome.storage.sync.get(["autoSites", "autoSitesOn", "autoPaths"]);
  } catch {
    return;
  }
  // **스위치가 내려가 있으면 목록을 보지 않는다.** 목록은 남겨 두고 끌 수 있어야
  // 다시 켤 때 다시 적지 않는다.
  if (!cfg.autoSitesOn) return;
  const sites = cfg.autoSites;
  if (sites === undefined) return; // 아직 저장 전이면 background 기본값을 모른다
  if (!hostMatches(location.hostname, sites)) return;
  if (!pathMatches(location.pathname, cfg.autoPaths)) return;

  // 뷰어가 실제로 있는지 본다. 없으면 목차 같은 페이지다.
  let probe;
  try {
    probe = probeViewer();
  } catch {
    return;
  }
  if (!probe?.count) return;

  // **화면을 채우고 있어야 뷰어다.** 주소 규칙만으로는 부족하다 — 뷰어 주소 안의
  // 목차·표지 화면도 같은 주소를 쓴다. 만화를 읽는 중이면 그림이 화면 대부분을
  // 차지한다. 홈페이지 배너는 폭만 넓고 낮아서 여기서 걸린다.
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const r = probe.rect;
  if (r.height < vh * 0.6) return;
  if (r.width * r.height < vw * vh * 0.25) return;

  // `silent` — 권한이 없어도 떠들지 않는다. 자동은 거들 뿐이고, 손으로 켜거나
  // Alt+Shift+M 을 누르면 그때 안내가 나간다.
  send({ type: "set-auto", on: true, silent: true });
}

// 뷰어는 대개 늦게 그려진다. 몇 번 나눠 본다 — 첫 시도에 없다고 포기하면
// 지연 로딩하는 사이트에서 영영 안 켜진다.
for (const ms of [800, 2000, 4500]) setTimeout(maybeAutoEnable, ms);

// 음성 목록을 미리 받아 둔다. `speak()` 직전에 await 가 길면 사용자 제스처 문맥이
// 끊겨 Chrome 이 재생을 막는 경우가 있다.
try {
  loadVoices();
} catch {}

// ---------------------------------------------------------------------------
// 다른 화면으로 가면 오버레이를 치운다
//
// SPA 뷰어(목차 → 화 넘기기 등)는 페이지를 다시 불러오지 않아 콘텐츠 스크립트가
// 그대로 산다. 그러면 **옛 박스가 새 화면 위에 그대로 떠 있다** — 엉뚱한 그림에
// 번역이 얹힌 것처럼 보인다.
//
// 주소가 바뀌면 그건 확실히 다른 화면이다. 박스를 지우고 페이지 신원도 버린다.
// ---------------------------------------------------------------------------

let lastUrl = location.href;

function onNavigated() {
  if (location.href === lastUrl) return;
  lastUrl = location.href;
  const root = document.getElementById(OVERLAY_ID);
  if (root) {
    reset();
    extraCount = 0;
    status("다른 화면으로 옮겼다 — 다시 읽어야 한다");
  }
  // 손으로 한 작업은 페이지별로 저장돼 있으므로 여기서 버려도 안전하다.
  pageKey = null;
  removedMarks = [];
  stopSpeaking();
  cancelSelection();
  closeMenu();
}

addEventListener("popstate", onNavigated);
addEventListener("hashchange", onNavigated);
// pushState/replaceState 는 이벤트를 안 낸다. MutationObserver 신호에 얹어 확인한다.

// **우리 오버레이 위 클릭은 신호가 아니다.** 박스나 패널을 누른 것은 페이지를
// 넘긴 것이 아닌데, 그걸 신호로 잡으면 0.7초 뒤 확인 캡처가 돌아 화면이 깜빡인다
// ("박스를 클릭하면 깜빡이고 뭔가 바뀌는 것 같다" 는 제보의 정체다).
document.addEventListener(
  "click",
  (e) => {
    if (isOurs(e.target)) return;
    notifyMaybeChanged({ fast: true });
  },
  true
);
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
      notifyMaybeChanged({ fast: true });
    }
  },
  true
);

/** 우리 오버레이가 만든 것인가. DOM 변화와 클릭 신호를 거를 때 같이 쓴다. */
function isOurs(node) {
  for (let n = node; n; n = n.parentNode) {
    if (n.id === OVERLAY_ID || n.id === SELECT_ID || n.id === MENU_ID) return true;
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

