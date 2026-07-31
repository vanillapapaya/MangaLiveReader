// 서비스 워커. 캡처 → 정규화 → mtl-service 왕복 → 콘텐츠 스크립트로 중계.
//
// 여기서 fetch 를 하는 이유: host_permissions 가 있으면 확장 출처로 요청이 나가므로
// 서비스에 CORS 헤더를 붙일 필요가 없다. 콘텐츠 스크립트에서 부르면 페이지 출처가
// 되어 서버 쪽 수정이 필요해진다.
//
// MV3 서비스 워커는 30초 유휴 시 종료되지만 진행 중인 fetch 는 살아 있는 것으로
// 친다. 번역이 9초라 문제되지 않는다.

// 서비스 주소는 저장소에서 읽는다 (옵션 화면에서 설정). 하드코딩하면 맥북·윈도우
// 양쪽에서 쓸 때마다 파일을 고쳐야 한다.
//
// 기본값은 루프백이다 — 서비스와 브라우저가 같은 머신에 있는 흔한 경우.
//
// **다른 기기에서 쓸 때는 옵션 화면에서 서비스 주소를 넣는다.** 서비스가 Tailscale
// 인터페이스에만 바인딩하므로(`service.toml` 의 `bind_tailscale_only`) 그 머신의
// Tailscale 주소를 넣으면 된다. 그 주소는 `manifest.json` 에 못 박아 둘 수 없으니
// (사람마다 다르다) `optional_host_permissions` 로 두고 옵션 화면이 저장할 때
// 권한을 요청한다.
const DEFAULTS = {
  serviceUrl: "http://127.0.0.1:8788/read",
  authToken: "", // 서비스의 MTL_AUTH_TOKEN 과 같은 값. 기본 설정에서는 비워 둔다
  //: 번역 키. **비워 두는 것이 기본이다** — 서비스 쪽 키 파일을 쓴다.
  //: 넣으면 고른 모델에 맞는 것 하나만 `X-Api-Key` 로 나간다.
  keyAnthropic: "",
  keyGemini: "",
  keyOpenai: "",
  // 음성 합성 서버 (GPT-SoVITS). 비우면 브라우저 내장 음성을 쓴다.
  ttsUrl: "",
  ttsVoice: "",
  //: 번역 모델. 빈 값이면 서버의 `service.toml` 을 따른다.
  model: "",
  //: 사이트 자동 켜기를 아예 끄는 스위치. 목록은 그대로 두고 내릴 수 있다 —
  //: 다시 켤 때 사이트를 다시 적지 않아도 된다.
  autoSitesOn: true,
  //: **주소에 이 낱말이 들어가야 켠다.** 쉼표로 구분.
  //:
  //: 호스트만 보면 홈페이지에서도 켜졌다. 뷰어 판정(`probeViewer`)은 200px 넘는
  //: 그림 하나면 통과하는데, 홈페이지 배너가 그대로 걸리기 때문이다.
  //:
  //: 주소 칸(`/episode/123`)을 `/` 로 자른 **한 칸과 통째로 같아야** 맞는 것으로
  //: 본다. 일부만 겹치는 것은 안 친다 — `read` 가 `readme` 에 걸리면 곤란하다.
  //: 다만 `episode-123` 처럼 뒤에 구분자가 붙은 것은 맞는 것으로 본다.
  //:
  //: 비우면 이 규칙을 안 쓴다 (호스트만 보던 예전 동작).
  autoPaths: "viewer, episode, episodes, chapter, chapters, story, read, book, volume",
  //: 여기 적은 호스트에서는 「자동」이 알아서 켜진다. 한 줄에 하나.
  //: 뒤에서부터 맞춘다 — `yanmaga.jp` 는 `www.yanmaga.jp` 에도 걸린다.
  //:
  //: **기본은 비어 있다.** 사이트 이름을 배포물에 박아 두면 "이 사이트들에서 쓰라"
  //: 는 안내가 된다. 자기가 보는 곳을 옵션 화면에서 적는다. 비어 있으면 자동은
  //: 사용자가 버튼으로 켠 탭에서만 돈다.
  autoSites: "",
};

// ---------------------------------------------------------------------------
// 음성 합성 중계
//
// **콘텐츠 스크립트가 직접 부르지 않는다.** 그러면 페이지 출처로 요청이 나가
// TTS 서버에 CORS 헤더를 붙여야 한다. 여기서 부르면 확장 출처라 그럴 필요가 없다
// (`/read` 와 같은 이유).
//
// 오디오는 **base64 바이트**로 돌려준다.
//
//   Blob URL — 이 워커의 수명에 묶이는데 MV3 워커는 30초 유휴로 죽는다.
//   data URL — `new Audio(url)` 은 **페이지의 CSP(media-src)** 를 탄다. 만화
//     사이트는 CSP 가 빡빡해서 `data:` 오디오가 막히고, `onerror` 로 "오디오를 못
//     읽었다" 만 남는다. 실제로 그 증상이 났다.
//
// 콘텐츠 스크립트가 Web Audio 로 디코드해 재생한다 — 리소스 로드가 아니라 스크립트라
// CSP 를 타지 않는다.
// ---------------------------------------------------------------------------

async function ttsVoices() {
  const { ttsUrl } = await settings();
  if (!ttsUrl) return null;
  const resp = await fetch(new URL("/voices", ttsUrl).href, { signal: AbortSignal.timeout(8000) });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

async function ttsSpeak(text, lang) {
  const { ttsUrl, ttsVoice } = await settings();
  if (!ttsUrl || !text) return null;
  const resp = await fetch(new URL("/tts", ttsUrl).href, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      text,
      language: lang === "ko-KR" ? "Korean" : "Japanese",
      ...(ttsVoice ? { voice: ttsVoice } : {}),
    }),
    // 40자 대사가 2초, 넉넉히 잡아도 이 안에 끝난다. 넘으면 내장 음성으로 떨어진다.
    signal: AbortSignal.timeout(30000),
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const buf = await resp.arrayBuffer();
  let bin = "";
  const b = new Uint8Array(buf);
  // btoa 는 문자열을 받는다. 한 번에 넘기면 인자 개수 제한에 걸리므로 나눠 붙인다.
  for (let i = 0; i < b.length; i += 0x8000) {
    bin += String.fromCharCode.apply(null, b.subarray(i, i + 0x8000));
  }
  return btoa(bin);
}

/** 고른 모델에 맞는 키. 모델을 안 골랐으면(서버 설정) 보내지 않는다 —
 *  어느 프로바이더인지 확장이 알 수 없어서 엉뚱한 키를 보내게 된다. */
function keyFor(model, s) {
  if (!model) return "";
  if (model.startsWith("claude-")) return s.keyAnthropic || "";
  if (model.startsWith("gemini-")) return s.keyGemini || "";
  if (model.startsWith("gpt-")) return s.keyOpenai || "";
  return ""; // 로컬 모델 등 — 키가 필요 없다
}

async function settings() {
  return { ...DEFAULTS, ...(await chrome.storage.sync.get(Object.keys(DEFAULTS))) };
}

/** 기본값을 저장소에 심는다.
 *
 * 콘텐츠 스크립트는 `chrome.storage` 를 직접 읽는데, 저장된 적이 없으면 background 의
 * `DEFAULTS` 를 알 길이 없다 — 사이트 목록이 비어 보여 자동이 영영 안 켜진다.
 * **이미 있는 값은 건드리지 않는다.**
 */
async function seedDefaults() {
  try {
    const got = await chrome.storage.sync.get(Object.keys(DEFAULTS));
    const missing = {};
    for (const [k, v] of Object.entries(DEFAULTS)) {
      if (got[k] === undefined) missing[k] = v;
    }
    if (Object.keys(missing).length) await chrome.storage.sync.set(missing);
  } catch {
    /* 저장소가 막혀도 나머지는 돈다 */
  }
}

chrome.runtime.onInstalled.addListener(seedDefaults);
chrome.runtime.onStartup.addListener(seedDefaults);
seedDefaults(); // 워커가 살아날 때마다 — 이미 있으면 아무것도 안 한다

// DESIGN.md §5.4 — 서비스는 이 규격으로 정규화된 이미지만 본다.
const MIN_SHORT_SIDE = 1200;
const MAX_SHORT_SIDE = 1600;
//: 긴 변 상한. 드래그로 가늘고 긴 영역을 골랐을 때 확대가 터지는 것을 막는다.
const MAX_LONG_SIDE = 2600;
const JPEG_QUALITY = 0.88;

/** 이 탭의 호스트 권한을 확보한다. **사용자 제스처가 살아 있을 때만 부를 것.**
 *
 * `chrome.tabs.captureVisibleTab` 은 `activeTab` 이나 그 사이트의 host permission
 * 이 필요하다. `activeTab` 은 **사용자가 확장을 직접 실행한 그 순간**에만 주어져서,
 * 자동 감지처럼 나중에 도는 코드에는 없다 — 그래서 매번 손으로 한 번 눌러야 했다.
 *
 * 아이콘 클릭·단축키는 제스처 문맥이므로 **여기서 바로 요청한다.** 한 번 허용하면
 * 그 뒤로는 자동도 된다.
 *
 * **`await` 를 앞에 두지 않는다** — `permissions.contains()` 로 미리 확인하고
 * 싶어지지만, 그 await 가 제스처를 끊어 팝업이 안 뜬다. 이미 있는 권한을 다시
 * 요청하면 팝업 없이 곧바로 true 다.
 */
function ensureHost(tab) {
  let origin;
  try {
    const u = new URL(tab.url);
    if (u.protocol !== "http:" && u.protocol !== "https:") return Promise.resolve(true);
    origin = `${u.protocol}//${u.hostname}/*`;
  } catch {
    return Promise.resolve(true); // 주소를 못 읽으면 그냥 진행한다
  }
  return chrome.permissions.request({ origins: [origin] }).catch(() => false);
}

chrome.action.onClicked.addListener((tab) => {
  ensureHost(tab).then(() => run(tab));
});
chrome.commands.onCommand.addListener((cmd, tab) => {
  if (cmd === "read-page") ensureHost(tab).then(() => run(tab));
  // 드래그 UI 는 콘텐츠 스크립트가 띄운다. 다 고르면 "read-rect" 로 되돌아온다.
  // **여기서 응답을 기다리지 않는다** — 드래그는 몇 초가 걸릴 수 있고 MV3
  // 서비스 워커는 응답 대기 중에도 30초 유휴로 죽을 수 있다.
  if (cmd === "select-region") {
    ensureHost(tab).then(() =>
      chrome.tabs.sendMessage(tab.id, { type: "select-region" }).catch(() => {})
    );
  }
  // 「갱신」 — 이 페이지의 낡은 캐시를 지우고 다시 읽는다. 결과가 이상할 때 쓴다.
  if (cmd === "refresh-page") ensureHost(tab).then(() => run(tab, null, null, { refresh: true }));
  // 음성은 캡처가 필요 없다 — 이미 화면에 있는 번역을 읽는다. 권한도 필요 없다.
  if (cmd === "speak-page") chrome.tabs.sendMessage(tab.id, { type: "speak-toggle" }).catch(() => {});
});

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  const tab = sender.tab;
  if (!tab) return;
  if (msg?.type === "read-rect") {
    // `merge`/`prefix` 는 그대로 `begin` 으로 흘려보낸다. 박스 하나만 다시 읽을 때
    // 나머지 박스를 지우지 않기 위한 표시다 (content.js 의 우클릭 메뉴).
    run(tab, {
      rect: msg.rect,
      dpr: msg.dpr,
      tag: msg.merge ? "다시 읽기" : "드래그 선택",
      merge: msg.merge || null,
      prefix: msg.prefix || "",
      // 검출기 문맥용으로 넓혀 보낸 창 안에서, 실제로 남길 범위.
      clip: msg.clip || null,
    });
  }
  else if (msg?.type === "do-read") run(tab, null, null, { refresh: Boolean(msg.refresh) });
  else if (msg?.type === "set-auto") setAuto(tab, msg.on, msg.silent);
  else if (msg?.type === "purge-all") purgeCache(tab, { all: true });
  else if (msg?.type === "signals") {
    // 뷰어 신호를 서비스 로그로 보낸다. 실패해도 조용히 넘어간다 — 진단일 뿐이다.
    settings().then(({ serviceUrl, authToken }) =>
      fetch(serviceUrl.replace(/\/read\/?$/, "/signals"), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(authToken ? { "X-Auth-Token": authToken } : {}),
        },
        body: JSON.stringify({ ...msg.data, url: tab.url, title: tab.title }),
      }).catch(() => {})
    );
  }
  else if (msg?.type === "purge-page") purgePage(tab);
  else if (msg?.type === "tts") {
    // 콘텐츠 스크립트가 기다리므로 반드시 답해야 한다 (실패해도 null 로).
    ttsSpeak(msg.text, msg.lang)
      .then((b64) => sendResponse({ b64 }))
      .catch((err) => sendResponse({ error: String(err.message || err) }));
    return true; // 비동기 응답
  }
  else if (msg?.type === "tts-voices") {
    ttsVoices()
      .then((v) => sendResponse({ voices: v }))
      .catch((err) => sendResponse({ error: String(err.message || err) }));
    return true;
  }
  else if (msg?.type === "page-maybe-changed") {
    // 워커가 죽었다 살아나도 `storage.session` 에서 상태를 되찾는다 (loadState).
    schedule(tab, msg.fast);
  }
});

// ---------------------------------------------------------------------------
// 페이지 전환 자동 감지 (DESIGN.md §5.1)
//
// 콘텐츠 스크립트는 "뭔가 일어났을 수 있다" 만 올려 준다. 진짜 바뀌었는지는
// 여기서 **캡처해서 해시로** 판정한다 — canvas 뷰어는 다시 그려도 DOM 이 안
// 바뀌므로 DOM 만 봐서는 알 수 없다.
//
// 상태를 탭별로 둔다. 탭마다 다른 만화를 볼 수 있고, 한 탭에서 켠 것이 다른
// 탭에서 캡처를 일으키면 안 된다.
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// 직전 페이지 (번역 문맥)
//
// 만화 대사는 페이지를 넘어 이어진다. 주어를 생략한 문장의 화자, 존댓말/반말,
// 말꼬리가 앞 페이지에 달려 있다 (`DESIGN.md` §8.4). 서버는 `prev_page_phash` 를
// 받으면 그 페이지 원문을 캐시에서 꺼내 번역기에 문맥으로 준다.
//
// **탭별로 둔다.** 탭마다 다른 작품을 볼 수 있다.
// **전체 읽기만 기록한다.** 부분 읽기(영역·다시 읽기)는 페이지가 아니라 조각이라
// 다음 페이지의 문맥이 될 수 없다.
// ---------------------------------------------------------------------------

/** tabId → 직전 전체 읽기의 phash. `storage.session` 에도 남긴다 —
 *  MV3 워커는 30초 유휴로 죽고, 메모리만 쓰면 페이지 두어 장마다 문맥이 끊긴다. */
const lastPage = new Map();
const PREV_KEY = (tabId) => `mlr-prev-${tabId}`;

async function getPrevPage(tabId) {
  if (lastPage.has(tabId)) return lastPage.get(tabId);
  try {
    const got = await chrome.storage.session.get(PREV_KEY(tabId));
    const v = got[PREV_KEY(tabId)] ?? null;
    if (v) lastPage.set(tabId, v);
    return v;
  } catch {
    return null;
  }
}

function setPrevPage(tabId, phash) {
  lastPage.set(tabId, phash);
  chrome.storage.session.set({ [PREV_KEY(tabId)]: phash }).catch(() => {});
}

/** tabId → { lastHash, lastAt, timer, busy } — 메모리 사본 */
const watching = new Map();

// **상태를 `chrome.storage.session` 에도 둔다.**
//
// MV3 서비스 워커는 30초 유휴로 죽고 되살아난다. 메모리에만 두면 되살아날 때마다
// `lastHash` 가 null 이라 무조건 "바뀌었다" 로 판정해 **클릭할 때마다 페이지를 통째로
// 다시 읽는다.** 실제로 그 증상이 났다 ("클릭할 때마다 번역이 바뀐다").
//
// `storage.session` 은 디스크에 안 남고 브라우저를 닫으면 사라진다 — 캐시성
// 상태에 맞다.
const KEY = (tabId) => `mlr-watch-${tabId}`;

//: 같은 탭을 동시에 읽어 오는 것을 묶는다. 아래 참조.
const loadingState = new Map();

async function loadState(tabId) {
  let st = watching.get(tabId);
  if (st) return st;
  // **동시에 부르면 상태 객체가 둘이 된다.**
  //
  // 워커가 막 살아난 직후에는 `watching` 이 비어 있어 둘 다 저장소를 읽으러 간다.
  // 그러면 각자 자기 객체를 만들어 `watching` 에 넣는데, `schedule()` 의
  // `clearTimeout(st.timer)` 가 **남의 타이머를 못 지운다** — 확인이 두 번 돌고
  // 같은 페이지를 같은 초에 두 번 읽는다 (실측 로그에 그 쌍이 있다).
  //
  // 진행 중인 읽기를 하나로 묶어 같은 객체를 돌려준다.
  let pending = loadingState.get(tabId);
  if (!pending) {
    pending = (async () => {
      const got = await chrome.storage.session.get(KEY(tabId));
      const saved = got[KEY(tabId)];
      if (!saved) return null;
      const made = watching.get(tabId) ?? { ...saved, timer: null, busy: false };
      watching.set(tabId, made);
      return made;
    })();
    loadingState.set(tabId, pending);
  }
  try {
    return await pending;
  } finally {
    loadingState.delete(tabId);
  }
}

function saveState(tabId, st) {
  chrome.storage.session
    .set({
      [KEY(tabId)]: {
        on: true,
        lastHash: st.lastHash,
        lastAt: st.lastAt,
      },
    })
    .catch(() => {});
}

//: 신호가 온 뒤 이만큼 조용해야 확인한다. 페이지 넘김 애니메이션·타일 로딩이
//: 끝나기를 기다리는 시간이다 (DESIGN.md §5.2 의 stable_frames 에 해당).
const SETTLE_MS = 700;

//: 신호가 이어져도 이만큼 지나면 더 미루지 않고 확인한다. 뷰어에 광고·애니메이션이
//: 있으면 DOM 변화가 끊이지 않아 `SETTLE_MS` 디바운스가 영영 안 끝난다 —
//: 페이지를 넘겨도 자동 번역이 안 걸린다.
const SETTLE_MAX_MS = 2200;

//: 사람이 직접 넘긴 신호(클릭·방향키)는 더 빨리 본다. DOM 변화와 달리 **넘김이
//: 거의 확실**하고, 넘김 애니메이션은 보통 300ms 안에 끝난다. 기다리는 시간이 곧
//: 새 그림 위에 옛 번역이 얹혀 있는 시간이다.
const SETTLE_FAST_MS = 380;

//: 자동으로는 이 간격보다 자주 읽지 않는다. 판정이 틀려도 API 를 쏟아붓지 않게
//: 막는 마지막 방어선이다. 사람이 페이지를 이보다 빨리 넘기지도 않는다.
const AUTO_MIN_INTERVAL_MS = 4000;

//: 지각 해시(16×16 평균 해시, 256비트)의 허용 거리. 이보다 가까우면 같은 화면으로
//: 본다. 정확 일치(sha256)로 비교하면 광고 애니메이션·JPEG 인코딩 흔들림 한 픽셀에도
//: "바뀌었다" 가 되어 같은 페이지를 계속 다시 읽는다 — 실제로 그 증상이 났다.
const AHASH_SAME_MAX = 12;

function hamming(a, b) {
  if (!a || !b || a.length !== b.length) return Infinity;
  let d = 0;
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) d++;
  return d;
}

async function setAuto(tab, on, silent = false) {
  if (on) {
    // **캡처 권한이 없으면 켜 봐야 헛돈다.** `activeTab` 은 사용자가 직접 실행할
    // 때만 주어지므로, 자동으로 켜려면 그 사이트의 host permission 이 있어야 한다.
    try {
      const origin = new URL(tab.url).origin + "/*";
      if (!(await chrome.permissions.contains({ origins: [origin] }))) {
        if (!silent) {
          tell(tab, "이 사이트 권한이 없어 자동을 못 켠다 — 확장 옵션에서 「저장」 을 눌러 허용할 것", true);
        }
        return;
      }
    } catch {
      return;
    }
    const st = (await loadState(tab.id)) ?? { lastHash: null, lastAt: 0, timer: null, busy: false };
    watching.set(tab.id, st);
    saveState(tab.id, st);
    // **켜자마자 한 번 본다.** 켜기만 하면 신호(클릭·키·DOM 변화)를 기다리는데,
    // 페이지가 이미 다 그려진 뒤라면 그런 신호가 영영 안 온다 — 사이트에 들어가도
    // 자동 번역이 안 걸리고, 손으로 한 번 눌러야 그때부터 돈다.
    schedule(tab, true);
  } else {
    const st = watching.get(tab.id);
    if (st) clearTimeout(st.timer);
    watching.delete(tab.id);
    chrome.storage.session.remove(KEY(tab.id)).catch(() => {});
  }
  chrome.tabs.sendMessage(tab.id, { type: "auto-state", on }).catch(() => {});
}

async function schedule(tab, fast = false) {
  const st = await loadState(tab.id);
  if (!st) return;
  const now = Date.now();
  if (!st.firstSignalAt) st.firstSignalAt = now;
  clearTimeout(st.timer);
  // 신호가 끊임없이 오면 `clearTimeout` 때문에 확인이 영영 밀린다. 첫 신호로부터
  // `SETTLE_MAX_MS` 가 지나면 더 미루지 않는다.
  if (now - st.firstSignalAt >= SETTLE_MAX_MS) {
    st.firstSignalAt = 0;
    check(tab, fast);
    return;
  }
  st.timer = setTimeout(() => {
    st.firstSignalAt = 0;
    check(tab, fast);
  }, fast ? SETTLE_FAST_MS : SETTLE_MS);
}

/** 캡처해서 해시가 달라졌을 때만 읽는다. */
async function check(tab, fast = false) {
  const st = await loadState(tab.id);
  if (!st) return;
  // **읽는 중에 온 신호를 버리면 안 된다.** `busy` 는 검출·OCR 뿐 아니라 **번역
  // 5-10초 내내** 잡혀 있다. 그 사이 페이지를 넘기면(빨리 두 장 넘기면 늘 그렇다)
  // 그 넘김이 통째로 사라져, 읽기가 끝난 뒤에도 옛 페이지 번역이 남는다.
  // 표시만 해 두고 끝나면 다시 본다.
  if (st.busy) {
    st.pending = true;
    return;
  }
  // **간격 제한에 걸렸다고 그냥 버리면 안 된다.** 페이지를 빨리 넘기면 그 넘김이
  // 통째로 사라져 자동 번역이 안 걸린다. 남은 시간만큼 미뤘다가 다시 본다.
  const wait = AUTO_MIN_INTERVAL_MS - (Date.now() - st.lastAt);
  if (wait > 0) {
    clearTimeout(st.timer);
    st.timer = setTimeout(() => check(tab, fast), wait + 50);
    return;
  }
  st.busy = true;
  try {
    // 자동 확인은 **지난 읽기와 같은 영역**을 잘라야 한다. 매번 뷰어를 다시 찾으면
    // 영역이 흔들려 해시가 달라지고, 같은 페이지를 계속 다시 번역한다.
    //
    // **오버레이를 숨길지는 신호의 성격에 따라 다르다.**
    //
    //   **확인은 언제나 오버레이를 켠 채로 한다.**
    //
    //   예전에는 클릭·방향키(`fast`)일 때 숨기고 찍었다. "넘김이 거의 확실하니
    //   깜빡임은 동작에 묻힌다" 고 봤는데 **틀렸다** — 클릭 대부분은 넘김이 아니다
    //   (메뉴바를 부르거나 그냥 화면을 누르는 것). 안 바뀌었으면 되살리느라
    //   깜빡이고, 그게 잦다.
    //
    //   비교하는 두 캡처에 같은 오버레이가 똑같이 찍히므로 판정에는 지장이 없다.
    const prepared = await prepare(tab, null, true, true);
    // 바뀌었든 아니든 확인한 시각을 남긴다. 안 그러면 "안 바뀜" 이 이어질 때
    // 확인이 몰려 캡처가 잦아진다.
    st.lastAt = Date.now();

    // 같은 화면이면 **아무것도 건드리지 않고** 끝낸다 — 숨긴 적이 없으니 되살릴
    // 것도 없다. 여기가 대부분의 경우다.
    if (hamming(prepared.ahash, st.lastHash) <= AHASH_SAME_MAX) return;

    st.lastHash = prepared.ahash;
    saveState(tab.id, st);
    // 바뀐 것이 확정됐다. 이제부터 화면이 한 번 깜빡이는 것은 **읽기 때문**이고,
    // 그건 새 페이지가 뜨는 순간이라 눈에 덜 띈다.
    //
    // 이 캡처는 오버레이가 찍혀 있어 OCR 에 못 쓴다. 새로 찍는 동안 옛 번역이
    // 새 그림 위에 얹혀 있으므로 먼저 흐리게 해 둔다.
    chrome.tabs.sendMessage(tab.id, { type: "stale" }).catch(() => {});
    await run(tab);
  } catch {
    // 탭이 닫혔거나 캡처가 막혔다. 다음 신호에서 다시 해 본다.
    await chrome.tabs.sendMessage(tab.id, { type: "show-overlay" }).catch(() => {});
  } finally {
    st.busy = false;
    // 읽는 동안 페이지가 또 넘어갔으면 지금 화면을 다시 본다.
    if (st.pending) {
      st.pending = false;
      schedule(tab, true);
    }
  }
}

chrome.tabs.onRemoved.addListener((tabId) => {
  const st = watching.get(tabId);
  if (st) clearTimeout(st.timer);
  watching.delete(tabId);
  lastPage.delete(tabId);
  chrome.storage.session.remove(PREV_KEY(tabId)).catch(() => {});
  chrome.storage.session.remove(KEY(tabId)).catch(() => {});
});

/** 읽기가 끝난 뒤 자동 감지의 기준 해시를 새로 찍는다.
 *
 * 오버레이(박스·라벨)가 바뀌었으므로 읽기 전 해시로는 다음 비교를 할 수 없다.
 * 박스가 다 그려질 틈을 조금 준 뒤 한 번 찍는다. 오버레이를 숨기지 않으므로
 * 화면은 깜빡이지 않는다.
 */
function refreshBaseline(tab, tries = 5) {
  setTimeout(async () => {
    const st = watching.get(tab.id);
    if (!st) return;
    // **읽는 중이면 포기하지 말고 다시 온다.**
    //
    // 예전에는 여기서 그냥 돌아갔다. 그런데 읽는 동안 쌓인 신호(`pending`)는
    // 읽기가 끝나자마자 확인을 다시 돌리므로 `busy` 가 곧바로 다시 켜진다 —
    // 그 틈에 깨어난 이 함수가 포기하면 **기준이 읽기 전 화면 그대로** 남는다.
    // 그러면 방금 번역한 페이지가 "바뀐 것" 으로 판정돼 통째로 다시 읽히고,
    // 그 사이 화면의 번역이 사라진다. 실제로 그 증상이 났다.
    if (st.busy) {
      if (tries > 0) refreshBaseline(tab, tries - 1);
      return;
    }
    try {
      const { ahash } = await prepare(tab, null, true, true);
      st.lastHash = ahash;
      st.lastAt = Date.now();
      saveState(tab.id, st);
    } catch {
      // 못 찍었으면 다음 확인에서 "바뀌었다" 로 보고 한 번 더 읽을 뿐이다.
    }
  }, 400);
}

function tell(tab, m, err) {
  return chrome.tabs
    .sendMessage(tab.id, { type: err ? "error" : "status", message: m, data: { message: m }, elapsed: 0 })
    .catch(() => {});
}

/** 캐시를 지운다. `{all:true}` 또는 `{phash, profile}`. */
async function purgeCache(tab, body) {
  try {
    const { serviceUrl, authToken } = await settings();
    // /read 엔드포인트에서 경로만 바꾼다 — 주소를 두 곳에 두면 한쪽만 고치게 된다.
    const url = serviceUrl.replace(/\/read\/?$/, "/cache/purge");
    const resp = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...(authToken ? { "X-Auth-Token": authToken } : {}) },
      body: JSON.stringify(body),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const { deleted } = await resp.json();
    return deleted;
  } catch (err) {
    tell(tab, `캐시 지우기 실패: ${err.message}`, true);
    return null;
  }
}

/** 지금 보고 있는 페이지의 캐시만 지운다. **다시 읽지는 않는다.**
 *
 * 「갱신」(지우고 바로 다시 읽기)과 나눠 둔 이유: 결과가 이상한데 지금 당장 다시
 * 읽고 싶지는 않을 때가 있다 — 번역 비용이 드니까. 지워만 두면 다음에 그 페이지를
 * 열 때 새로 읽는다.
 *
 * 캐시 키는 화면 픽셀의 해시라 **지금 화면을 한 번 찍어야** 무엇을 지울지 안다.
 *
 * **여기서는 오버레이를 숨긴다** (자동 감지의 확인과 다르다). 캐시 키는 읽을 때
 * 오버레이 없이 찍은 해시라, 켠 채로 찍으면 키가 달라져 정작 그 페이지를 못 지운다.
 * 손으로 누른 동작이라 한 번 깜빡이는 것은 괜찮다.
 */
async function purgePage(tab) {
  try {
    const { phash } = await prepare(tab, null, true);
    await chrome.tabs.sendMessage(tab.id, { type: "show-overlay" }).catch(() => {});
    const deleted = await purgeCache(tab, { phash, profile: hostToProfile(tab.url) });
    if (deleted !== null) {
      tell(tab, deleted ? `이 페이지 캐시를 지웠다 (${deleted}개)` : "이 페이지는 캐시에 없다");
      // 기준 해시도 버린다. 안 그러면 자동 감지가 "안 바뀌었다" 로 보고 안 읽는다.
      const st = watching.get(tab.id);
      if (st) {
        st.lastHash = null;
        saveState(tab.id, st);
      }
    }
  } catch (err) {
    tell(tab, `캐시 지우기 실패: ${err.message}`, true);
  }
}

/** 캡처까지만 한다. 자동 감지가 "바뀌었나" 를 판정하려면 이 결과가 필요하고,
 *  바뀌었으면 **같은 캡처를 그대로** 읽기에 넘겨 두 번 찍지 않는다.
 *
 *  @param override 뷰어 자동 탐지 대신 쓸 사각형. 없으면 probe 한다.
 */
async function prepare(tab, override = null, stable = false, keepOverlay = false) {
  // 1. 뷰어 요소의 CSS 사각형을 콘텐츠 스크립트에서 받는다.
  //    **OS 캡처 경로에는 없는 정보다.** 화면 좌표를 추측하는 대신 DOM 이
  //    "만화가 여기 있다" 고 알려준다.
  //    자동 탐지는 기하 휴리스틱이라 반드시 틀리는 사이트가 나온다. 그때
  //    Alt+Shift+D 로 손수 집은 사각형이 override 로 들어온다.
  const probe =
    override ?? (await chrome.tabs.sendMessage(tab.id, { type: stable ? "probe-stable" : "probe" }));
  if (probe?.error) throw new Error("probe 실패: " + probe.error);
  if (!probe?.rect) throw new Error("뷰어로 볼 만한 요소를 못 찾았다");

  // 2. 오버레이를 지우고 캡처한다 (§5.2 캡처 피드백 루프).
  //    OS 캡처와 달리 우리가 직접 숨기므로 결정론적이다.
  //
  //    **자동 감지의 "바뀌었나" 확인은 숨기지 않는다** (`keepOverlay`).
  //    숨겼다 되살리는 것이 곧 **깜빡임**인데, 확인은 4초에 한 번까지 일어나므로
  //    화면이 쉼 없이 깜빡인다. 비교하는 두 캡처에 **같은 오버레이가 똑같이**
  //    찍히므로 판정에는 지장이 없다 — 아래 만화가 바뀌어야 해시가 달라진다.
  //    진짜로 읽을 때는 당연히 숨긴다 (박스가 찍히면 검출기가 글자로 읽는다).
  if (!keepOverlay) await chrome.tabs.sendMessage(tab.id, { type: "hide-overlay" });
  let dataUrl;
  try {
    dataUrl = await chrome.tabs.captureVisibleTab(tab.windowId, { format: "png" });
  } catch (err) {
    // 십중팔구 권한 문제다. 짐작하지 말고 그대로 보여 준다.
    const m = String(err?.message || err);
    throw new Error(
      /permission|access/i.test(m)
        ? `화면을 못 찍었다 — 이 사이트 권한이 없다. Alt+Shift+M 을 눌러 허용할 것 (${m})`
        : `화면을 못 찍었다: ${m}`
    );
  }

  // 3. 뷰어 영역만 잘라 §5.4 규격으로 정규화한다.
  const { blob, scale, ahash } = await cropAndNormalize(dataUrl, probe.rect, probe.dpr);
  // **캐시 키로 sha256 을 보내면 안 된다.** 서버는 phash 의 해밍 거리로 캐시를
  // 맞추는데(`service.toml` 의 `fuzzy_hamming`), 암호학적 해시는 1픽셀만 달라도
  // 비트의 절반이 바뀐다 — 거리가 늘 ~128 이라 퍼지 매칭이 **원리적으로 절대 안
  // 걸린다.** 같은 페이지를 다시 읽을 때마다 번역 API 를 새로 치게 된다.
  //
  // 지각 해시를 보내면 조금 다른 캡처도 같은 페이지로 맞아 캐시가 실제로 먹는다.
  return { probe, blob, scale, phash: bitsToHex(ahash), ahash };
}

async function run(tab, override = null, prepared = null, opts = {}) {
  if (!tab?.id) return;
  const t0 = performance.now();
  const say = (type, payload) =>
    chrome.tabs.sendMessage(tab.id, { type, ...payload, elapsed: Math.round(performance.now() - t0) })
      .catch(() => {}); // 콘텐츠 스크립트가 없는 페이지면 조용히 무시

  try {
    const { probe, blob, scale, phash } = prepared ?? (await prepare(tab, override));
    // 한 줄로 합친다 — 캡처가 이미 끝난 상태로 들어오는 경로(자동 감지)에서는
    // 두 줄을 따로 보내면 첫 줄이 보이기도 전에 덮인다.
    say("status", {
      message:
        `뷰어 ${Math.round(probe.rect.width)}×${Math.round(probe.rect.height)} (${probe.tag}) · ` +
        `${Math.round(blob.size / 1024)}KB scale ${scale.toFixed(2)}`,
    });

    // 4. 서비스로 보내고 SSE 를 흘려받는다.
    //    직전 페이지를 문맥으로 넘긴다. 방금 읽은 그 페이지를 자기 문맥으로 주면
    //    같은 원문을 두 번 넣는 꼴이므로 부분 읽기에서는 보내지 않는다.
    const prevPage = override ? null : await getPrevPage(tab.id);
    // 한 번만 읽는다 — 아래 fetch 에서도 같은 값을 쓴다.
    const s = await settings();
    const { serviceUrl, authToken, model } = s;
    const apiKey = keyFor(model, s);
    const meta = {
      phash,
      profile: hostToProfile(tab.url),
      mode: "natural",
      // 방금 읽은 그 페이지를 자기 문맥으로 주면 안 된다 — 같은 원문을 두 번 넣는 꼴이다.
      prev_page_phash: prevPage,
      // **켜 둔다.** 끄면 `is_bubble = false` 인 region 이 서버에서 걸러지는데,
      // 실측(sunday-webry)에서 그 판정이 틀리는 경우가 있었다 — 스크린톤 배경 위
      // 말풍선이 "그림 위" 로 오판돼 5개가 통째로 사라졌다(17개 → 22개).
      // 효과음이 섞여 들어오는 것보다 대사가 빠지는 쪽이 훨씬 나쁘다.
      include_sfx: true,
      // 서버가 아는 목록만 받아 준다 (`app.ALLOWED_MODELS`). 빈 값이면 서버 설정.
      model: model || null,
      // 손으로 고른 읽기(영역 지정·다시 읽기)는 캐시를 건너뛴다. 사용자가 굳이
      // 다시 시킨 것은 **지난 결과가 마음에 안 들어서**다. 캐시를 주면 예전에
      // 잘못 읽은 것을 그대로 돌려준다.
      no_cache: Boolean(override),
      // 「갱신」 — 이 페이지의 낡은 캐시를 지우고 다시 읽는다. 새 결과는 저장한다.
      refresh: Boolean(opts.refresh),
      // **번역할 범위를 전송 이미지 좌표로 넘긴다.** 「영역」·「다시 읽기」는 검출기에
      // 문맥을 주려고 3배 넓게 보내는데, 그 안을 전부 번역하면 클라이언트가 버릴
      // 것에까지 돈을 낸다. 뷰포트 CSS → 전송 이미지: (좌표 - 잘라낸 원점) × 배율.
      clip:
        probe.clip && probe.rect
          ? [
              Math.round((probe.clip.x - probe.rect.x) * probe.dpr * scale),
              Math.round((probe.clip.y - probe.rect.y) * probe.dpr * scale),
              Math.round(probe.clip.width * probe.dpr * scale),
              Math.round(probe.clip.height * probe.dpr * scale),
            ]
          : null,
      // **좌표를 되돌릴 기준.** 뷰어 사각형(자르기 전)을 전송 이미지 좌표로 넘긴다.
      //
      // 캐시는 phash 로 찾는데 그건 퍼지 매칭이라 **크기가 다른 캡처도 같은 행에
      // 붙는다.** 저장된 bbox 는 그때 캡처의 좌표계라 그대로 얹으면 어긋난다.
      // 이 사각형을 같이 저장해 두면, 나중 캡처에서 비율로 환산해 제자리에 놓을 수
      // 있다 — 다시 번역하지 않아도 된다 (content.js 의 `fromCachedFrame`).
      viewer:
        probe.full && probe.rect
          ? [
              Math.round((probe.full.x - probe.rect.x) * probe.dpr * scale),
              Math.round((probe.full.y - probe.rect.y) * probe.dpr * scale),
              Math.round(probe.full.width * probe.dpr * scale),
              Math.round(probe.full.height * probe.dpr * scale),
            ]
          : null,
    };
    const form = new FormData();
    form.append("image", blob, "page.jpg");
    form.append("meta", JSON.stringify(meta));

    let resp;
    try {
      resp = await postWithRetry(serviceUrl, form, authToken, say, apiKey);
    } catch (err) {
      // fetch 가 통째로 실패하면 브라우저가 이유를 안 알려 준다 (서비스가 죽었는지,
      // 방화벽인지, host_permissions 가 없는지 구분 불가). 짚을 곳을 알려 준다.
      // fetch 가 통째로 실패한 이유가 **권한 없음**인 경우가 가장 흔하다.
      // 그건 여기서 바로 확인할 수 있으니 먼저 짚어 준다.
      let hint = "";
      try {
        const origin = `${new URL(serviceUrl).origin}/*`;
        if (!(await chrome.permissions.contains({ origins: [origin] }))) {
          hint = ` — ${origin} 권한이 없다. 확장 옵션 화면에서 「저장」을 눌러 허용할 것`;
        }
      } catch {}
      throw new Error(
        `${serviceUrl} 에 못 붙었다${hint}. 서비스가 떠 있는지, 방화벽이 8788 을 ` +
          `막는지도 확인 (${err.message})`
      );
    }
    if (!resp.ok) throw new Error(`HTTP ${resp.status} ${await resp.text()}`);

    // 다음 페이지의 문맥이 될 수 있게 기억한다. 부분 읽기는 제외한다 (조각이라
    // 문맥으로 못 쓴다). 캐시 적중이든 아니든 "방금 본 페이지" 인 것은 같다.
    if (!override) setPrevPage(tab.id, phash);

    // 수동으로 읽었어도 자동 감지의 기준 해시를 맞춰 둔다. 안 그러면 바로
    // 다음 신호에서 "바뀌었다" 로 보고 같은 페이지를 또 읽는다.
    //
    // **전체 읽기일 때만.** 영역 하나만 읽은 해시를 기준으로 삼으면, 다음 확인에서
    // 전체 화면 해시와 당연히 달라 자동 감지가 곧바로 전체를 다시 읽고 방금 고친
    // 박스를 지워 버린다.
    // 읽고 나면 오버레이가 새 박스로 바뀐다. 지금 캡처의 해시로는 다음 비교를 할 수
    // 없으므로 **읽기가 끝난 뒤 기준을 새로 찍는다** (아래 `refreshBaseline`).
    //
    // 예전에는 "다음 확인 때 기준만 잡고 넘어가기" 로 처리했는데, 그 한 번의 확인
    // 동안 페이지를 넘기면 **그 넘김이 통째로 사라졌다** — "페이지 넘어갈 때 자동
    // 번역이 안 되는 일이 허다하다" 의 원인이다.

    // 좌표 환산에 필요한 값을 먼저 보낸다. 서비스 bbox 는 전송 이미지 좌표계다.
    say("begin", {
      rect: probe.rect,
      // **이번 캡처가 기준으로 삼은 뷰어 사각형(자르기 전).** 캐시 좌표를 되돌릴 때
      // 쓴다. 콘텐츠 스크립트가 `viewerRect()` 로 다시 재면 안 된다 — 뒤로 돌아가면
      // 뷰어 요소가 갈려 엉뚱한 값이 나오고, 박스가 화면 밖으로 날아간다.
      viewerFull: probe.full ?? null,
      scale,
      dpr: probe.dpr,
      merge: probe.merge ?? null,
      prefix: probe.prefix ?? "",
      clip: probe.clip ?? null,
      // 부분 읽기(영역 지정·다시 읽기)면 기존 박스를 지우면 안 된다.
      // `merge` 유무로 판단하면 안 된다 — 드래그 선택은 merge 가 없다.
      partial: Boolean(override),
      // 페이지 신원. 손으로 더한 박스를 이 키로 저장했다가 되돌아왔을 때 되살린다.
      // **전체 읽기의 phash 만 쓴다** — 부분 읽기의 phash 는 그 조각의 것이라
      // 페이지를 가리키지 않는다.
      pageKey: override ? null : phash,
    });

    for await (const [event, data] of readSSE(resp)) {
      say(event, { data });
    }
    if (!override) refreshBaseline(tab);
  } catch (err) {
    say("error", { data: { message: String(err?.message || err) } });
  }
}

/** 캡처 PNG 에서 뷰어 사각형만 잘라 §5.4 규격 JPEG 로 만든다. */
async function cropAndNormalize(dataUrl, rect, dpr) {
  const bitmap = await createImageBitmap(await (await fetch(dataUrl)).blob());

  // captureVisibleTab 은 뷰포트를 **장치 픽셀**로 준다. rect 는 CSS 픽셀이다.
  const sx = Math.max(0, Math.round(rect.x * dpr));
  const sy = Math.max(0, Math.round(rect.y * dpr));
  const sw = Math.min(bitmap.width - sx, Math.round(rect.width * dpr));
  const sh = Math.min(bitmap.height - sy, Math.round(rect.height * dpr));
  if (sw <= 0 || sh <= 0) throw new Error("뷰어 사각형이 화면 밖이다");

  const short = Math.min(sw, sh);
  let scale = 1;
  if (short > MAX_SHORT_SIDE) scale = MAX_SHORT_SIDE / short;
  else if (short < MIN_SHORT_SIDE) scale = MIN_SHORT_SIDE / short;

  // 짧은 변만 보고 키우면 가늘고 긴 선택에서 긴 변이 터진다. 말풍선 한 줄을
  // 드래그하면(예: 1600×160) 짧은 변 기준 배율이 7.5 라 12000×1200 이 된다 —
  // 업로드도 크고 검출기는 어차피 1024 로 줄여 본다. 자동 탐지 경로에서는 뷰어가
  // 이미 1200 이상이라 안 걸리지만, 드래그 선택에서는 늘 걸린다.
  const long = Math.max(sw, sh) * scale;
  if (long > MAX_LONG_SIDE) scale *= MAX_LONG_SIDE / long;

  const dw = Math.round(sw * scale);
  const dh = Math.round(sh * scale);
  const canvas = new OffscreenCanvas(dw, dh);
  const ctx = canvas.getContext("2d");
  ctx.drawImage(bitmap, sx, sy, sw, sh, 0, 0, dw, dh);
  bitmap.close();

  const blob = await canvas.convertToBlob({ type: "image/jpeg", quality: JPEG_QUALITY });
  // 전송 이미지 1px = 장치 픽셀 (1/scale)px = CSS 픽셀 (1/(scale*dpr))px
  return { blob, scale, ahash: averageHash(canvas) };
}

//: 해시를 계산할 때 볼 범위(가운데 비율).
//:
//: **뷰어 메뉴바를 무시하기 위한 것이다.** 만화 뷰어는 클릭하면 위에 제목 줄,
//: 아래에 페이지 이동 줄이 뜬다. 그림은 그대로인데 픽셀이 바뀌어서, 전체를 보고
//: 해시를 내면 **다른 페이지로 넘어간 것과 구별이 안 된다.**
//:
//: 실측(픽스처 8장, 위 9%·아래 11% 를 어둡게 덮어 메뉴바를 흉내):
//:
//:   범위          메뉴바로 인한 차이   다른 페이지 최소 거리
//:   1.00 × 1.00   최대 98             100      ← 사실상 구별 불가
//:   0.92 × 0.74   최대  0             100      ← 완전히 무시
//:
//: 위아래를 13% 씩 덜어내면 메뉴바가 해시를 아예 안 건드리고, 페이지 구별력은
//: 그대로다. 좌우 4% 는 옆에 붙는 UI 몫이다.
const HASH_CROP_X = 0.92;
const HASH_CROP_Y = 0.74;

/** 16×16 평균 해시. 자동 감지의 "같은 화면인가" 판정과 서버 캐시 키를 겸한다.
 *
 * 만화는 대부분 흰 바탕이라 8×8 로는 다른 페이지가 같게 나온다. 16×16(256비트)까지
 * 올려야 칸 배치 차이가 잡힌다.
 */
function averageHash(canvas) {
  const N = 16;
  const sw = canvas.width * HASH_CROP_X;
  const sh = canvas.height * HASH_CROP_Y;
  const sx = (canvas.width - sw) / 2;
  const sy = (canvas.height - sh) / 2;
  const small = new OffscreenCanvas(N, N);
  const g = small.getContext("2d");
  g.drawImage(canvas, sx, sy, sw, sh, 0, 0, N, N);
  const d = g.getImageData(0, 0, N, N).data;
  const lum = new Float64Array(N * N);
  let sum = 0;
  for (let i = 0; i < N * N; i++) {
    lum[i] = 0.299 * d[i * 4] + 0.587 * d[i * 4 + 1] + 0.114 * d[i * 4 + 2];
    sum += lum[i];
  }
  const avg = sum / (N * N);
  let bits = "";
  for (let i = 0; i < N * N; i++) bits += lum[i] > avg ? "1" : "0";
  return bits;
}

//: 재시도 간격. 늘려 가며 세 번까지 해 본다 — API 가 잠깐 흔들리는 것과
//: 진짜로 안 되는 것을 가르기에 충분하고, 오래 매달리지도 않는다.
const RETRY_DELAYS_MS = [800, 2500];

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** 서비스에 POST 한다. 일시적 실패는 다시 해 본다.
 *
 * 예전에는 한 번 실패하면 그 페이지는 그냥 끝이었다 — 네트워크가 한 번 끊기거나
 * 서버가 재시작 중이면 사용자가 직접 다시 눌러야 했다.
 *
 * **재시도하면 안 되는 것은 구별한다.** 400(잘못된 요청)·401(인증)은 다시 해도
 * 같은 답이 온다. 5xx 와 네트워크 오류만 다시 한다.
 */
async function postWithRetry(url, form, authToken, say, apiKey) {
  let lastErr;
  for (let i = 0; i <= RETRY_DELAYS_MS.length; i++) {
    if (i > 0) {
      say("status", { message: `다시 시도 ${i}/${RETRY_DELAYS_MS.length}…` });
      await sleep(RETRY_DELAYS_MS[i - 1]);
    }
    try {
      const resp = await fetch(url, {
        method: "POST",
        body: form,
        headers: {
          ...(authToken ? { "X-Auth-Token": authToken } : {}),
          // 고른 모델에 맞는 키. 없으면 안 보낸다 — 서비스가 자기 키를 쓴다.
          ...(apiKey ? { "X-Api-Key": apiKey } : {}),
        },
      });
      // 4xx 는 우리가 잘못 보낸 것이다. 다시 해도 같으니 바로 올린다.
      if (resp.ok || (resp.status >= 400 && resp.status < 500)) return resp;
      lastErr = new Error(`HTTP ${resp.status}`);
    } catch (err) {
      lastErr = err;
    }
  }
  throw lastErr;
}

/** SSE 스트림을 [이벤트, 데이터] 로 흘린다. */
async function* readSSE(resp) {
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    // 프레임 구분은 빈 줄이다.
    let idx;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const frame = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      let event = null;
      let data = null;
      for (const line of frame.split("\n")) {
        if (line.startsWith("event: ")) event = line.slice(7);
        else if (line.startsWith("data: ")) data = JSON.parse(line.slice(6));
      }
      if (event) yield [event, data];
    }
  }
}

/** "0101..." 비트 문자열 → 16진수. 서버의 `hamming()` 이 16진 문자열을 받는다. */
function bitsToHex(bits) {
  let hex = "";
  for (let i = 0; i < bits.length; i += 4) {
    hex += parseInt(bits.slice(i, i + 4), 2).toString(16);
  }
  return hex;
}

/** service.toml 의 [profiles.*] 이름으로 맞춘다. 없으면 호스트명 그대로.
 *
 * **프로필은 캐시 키의 일부다.** 같은 사이트가 `www` 있고 없고로 갈리면 캐시도
 * 갈려서 같은 페이지를 두 번 번역한다. `www.` 는 벗겨서 맞춘다. */
function hostToProfile(url) {
  try {
    const host = new URL(url).hostname.replace(/^www\./, "");
    return (
      {
        "comic-walker.com": "comicwalker",
        "yanmaga.jp": "yanmaga",
        "shonenjumpplus.com": "jumpplus",
      }[host] || host
    );
  } catch {
    return "unknown";
  }
}
