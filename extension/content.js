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

//: 페이지를 옮겨도 남는 토글. `저장소 키 → 오버레이 클래스`.
//: **쓰는 곳보다 위에 둔다** — `const` 는 초기화 전에 못 읽는다(TDZ).
const STICKY_TOGGLES = {
  showAll: "mlr-show-all",
  showExtra: "mlr-show-extra",
  hideStatus: "mlr-hide-status",
};

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
    case "probe-stable":
      // **자동 감지 전용.** 뷰어를 다시 찾지 않고 지난 읽기에서 본 요소를 그대로 쓴다.
      //
      // `probeViewer()` 는 매번 기하 휴리스틱을 새로 돌리는데, 캐러셀이 움직이거나
      // 타일이 늦게 뜨면 **고르는 요소가 달라진다.** 그러면 잘라 보내는 영역이 달라져
      // 해시가 흔들리고, 같은 페이지인데도 "바뀌었다" 로 판정해 통째로 다시 번역한다.
      // 실측 로그에서 같은 페이지가 70초 동안 8번 새로 번역됐다 (region 수가
      // 4·5·6·7·8·11 로 널뛴 것이 영역이 달라졌다는 증거다).
      try {
        const live = viewerEls.filter((el) => el.isConnected);
        if (live.length) {
          const u = unionRects(live.map((el) => el.getBoundingClientRect()));
          const rect = clampToViewport(u, window.innerWidth, window.innerHeight);
          const whole = Math.max(1, (u.right - u.left) * (u.bottom - u.top));
          const seen = Math.max(0, rect.width) * Math.max(0, rect.height);
          // **아직 화면에 충분히 남아 있을 때만 재사용한다.**
          //
          // 세로 스크롤 뷰어(yanmaga 등)는 페이지를 위아래로 쌓아 두고, 스크롤하면
          // 읽던 페이지가 화면 밖으로 밀려나면서 **DOM 에는 그대로 붙어 있다.**
          // `isConnected` 만 보면 이미 안 보이는 페이지를 계속 찍게 된다.
          //
          // 절반 넘게 보이면 "같은 페이지를 계속 보는 중" 이라 재사용하고, 그 아래로
          // 내려가면 새로 찾는다.
          if (seen / whole > 0.5 && rect.width > 50 && rect.height > 50) {
            // `full` 은 **뷰포트로 자르기 전** 뷰어 사각형이다. 캐시가 좌표를
            // 되돌릴 때 기준으로 쓴다 — 잘린 rect 로는 스크롤 위치가 섞인다.
            sendResponse({
              rect, full: { x: u.left, y: u.top, width: u.right - u.left, height: u.bottom - u.top },
              tag: "고정", dpr: dpr(), count: live.length,
            });
            return true;
          }
        }
        sendResponse(probeViewer()); // 사라졌거나 화면 밖으로 나갔으면 새로 찾는다
      } catch (err) {
        sendResponse({ error: String(err?.stack || err) });
      }
      return true;
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
      // **그려질 때까지 기다렸다가 응답한다.** 스타일만 바꾸고 바로 답하면 브라우저가
      // 아직 다시 그리기 전이라 `captureVisibleTab` 이 **오버레이가 있는 프레임**을
      // 찍는다 — 상태줄·박스 글자가 검출기에 들어가 번역된다.
      //
      // 탭이 뒤로 가면 `requestAnimationFrame` 이 아예 안 돈다. 그때 응답을 영영
      // 안 보내면 background 가 그 자리에서 멎으므로 시간 제한을 같이 건다.
      {
        let done = false;
        const reply = () => {
          if (done) return;
          done = true;
          sendResponse({ ok: true });
        };
        requestAnimationFrame(() => requestAnimationFrame(reply));
        setTimeout(reply, 120);
      }
      return true;
    case "show-overlay":
      showOverlay();
      sendResponse({ ok: true });
      return true;
    case "select-region":
      startSelection();
      break;
    case "speak-toggle":
      if (speaking) stopSpeaking();
      else speakAll();
      break;
    case "stale":
      // 페이지가 바뀐 것이 확인됐다. 새 박스가 오기 전까지 **옛 번역을 치운다** —
      // 새 그림 위에 이전 페이지의 번역이 얹혀 있는 것이 가장 거슬린다.
      markStale();
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
        viewerFull: msg.viewerFull || null,
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
        // **바로 지우지 않는다.** 다시 읽어서 아무것도 안 나오면(그 영역에 글자가
        // 없거나 검출이 실패하면) 원래 박스마저 사라져 손해만 본다. 결과가 도착한
        // 뒤에 지운다.
        if (msg.merge) {
          const old = document.getElementById(`mlr-box-${msg.merge}`);
          if (old) {
            old.dataset.replacing = "1";
            old.style.opacity = "0.35";
          }
        }
      } else {
        reset();
        overlay().querySelector("#mlr-boxes")?.classList.remove("mlr-stale");
        extraCount = 0;
        // 페이지가 바뀌었으면 신원도 바뀐다. 부분 읽기는 조각의 해시라 안 쓴다.
        pageKey = msg.pageKey || null;
      }
      showOverlay(); // 캡처가 끝났으니 되돌린다
      setBusy(true, "읽는 중");
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
      // 예전에는 여기서 변환 기준을 박아 뒀다. 이제 박스마다 비율을 들고 있으므로
      // 남은 변환만 지운다 (옛 세션이 남겨 놓았을 수 있다).
      {
        const b = overlay().querySelector("#mlr-boxes");
        if (b) b.style.transform = "";
      }
      // 캐시에서 온 좌표면 그 기준 사각형이 같이 온다. 없으면 이번 캡처 좌표다.
      ctx.cachedViewer = msg.data.cached_viewer || null;
      {
        const drawn = drawBoxes(msg.data.regions);
        const old = document.querySelector('.mlr-box[data-replacing="1"]');
        if (old) {
          if (drawn > 0) {
            // **치웠다는 사실을 남긴다.** 이게 없어서 다음 전체 읽기 때 캐시가
            // 원래 박스를 되돌려 놓고, 그 위에 고친 박스까지 복원돼 겹쳤다.
            markRemoved(old);
            old.remove();
          } else {
            delete old.dataset.replacing;
            old.style.opacity = "";
            status("이 영역에서 글자를 못 찾았다 — 원래 박스를 되돌린다", true);
            break;
          }
        }
        status(`원문 ${drawn}개 · ${msg.elapsed}ms`);
        setBusy(true, `번역 중 0/${drawn}`);
      }
      break;
    case "translation":
      fillTranslation(msg.data);
      {
        const root = document.getElementById(OVERLAY_ID);
        const done = root?.querySelectorAll(".mlr-box.mlr-translated").length ?? 0;
        const all = root?.querySelectorAll(".mlr-box").length ?? 0;
        setBusy(true, `번역 중 ${done}/${all}`);
      }
      break;
    case "done":
      flashBusy(msg.data.timings.translate ? "완료" : "캐시");
      status(`완료 ${msg.elapsed}ms · 번역 ${msg.data.timings.translate}ms`);
      // 손으로 더한 박스를 되살린다. 전체 읽기가 끝난 뒤라야 지울 것/더할 것을
      // 제자리에 맞출 수 있다.
      if (!ctx?.partial) restoreEdits();
      else saveEdits();
      break;
    case "error":
      showOverlay(); // 캡처 도중 실패했으면 숨은 채로 남아 있다
      flashBusy("실패", true);
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
    const whole = { x: 0, y: 0, width: vw, height: vh };
    return { rect: whole, full: whole, tag: "viewport(폴백)", dpr: dpr(), count: 0 };
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

  // **한 페이지가 가로 띠 여러 장으로 쪼개져 있으면 세로로도 이어 붙인다.**
  //
  // SpeedBinb(yanmaga 등)는 한 페이지를 띠로 잘라 세로로 쌓는다. 실측:
  //
  //   IMG 888x426 @0,42     ← 이 셋이 한 페이지다
  //   IMG 888x426 @0,465
  //   IMG 888x428 @0,888
  //   IMG 888x426 @-936,42  ← 이전 페이지 (화면 밖)
  //
  // "같은 행" 규칙만 쓰면 셋 중 하나만 잡혀 **페이지의 3분의 1만 보낸다.**
  // 스크롤할 때마다 씨앗이 다른 띠로 바뀌어 잘라 보내는 영역도 널뛴다 —
  // 실측 로그에서 같은 페이지의 region 수가 4·5·6·7·8·11 로 흔들린 원인이다.
  //
  // **위아래로 쌓인 다른 페이지와는 구별해야 한다.** 한 페이지의 띠는
  //   · 좌우 범위가 거의 같고 (같은 칼럼)
  //   · 세로로 맞닿아 있다 (틈이 거의 없다)
  // 별개 페이지는 틈이 벌어지거나 좌우가 어긋난다.
  extendVertically(group, cands);

  // 스크롤·확대를 따라가려면 **사각형이 아니라 요소**를 들고 있어야 한다.
  viewerEls = group.map((c) => c.el);
  const rect = clampToViewport(unionRects(group.map((c) => c.r)), vw, vh);
  const tag =
    seed.el.tagName.toLowerCase() + (group.length > 1 ? ` ×${group.length}` : "");
  return { rect, full: rect, tag, dpr: dpr(), count: group.length };
}

//: 띠끼리 이 정도까지 벌어져도 한 페이지로 본다. 실측에서는 3px 겹쳐 있었다.
const STRIP_GAP_MAX = 24;
//: 좌우 범위가 이 비율 넘게 어긋나면 다른 것으로 본다.
const STRIP_ALIGN_TOL = 0.1;

/** `group` 에 세로로 맞닿은 같은 폭의 조각들을 이어 붙인다 (제자리 수정). */
function extendVertically(group, cands) {
  const w0 = group[0].r.width;
  const l0 = group[0].r.left;
  const aligned = (r) =>
    Math.abs(r.width - w0) <= w0 * STRIP_ALIGN_TOL &&
    Math.abs(r.left - l0) <= w0 * STRIP_ALIGN_TOL;

  let grew = true;
  while (grew) {
    grew = false;
    const u = unionRects(group.map((c) => c.r));
    for (const c of cands) {
      if (group.includes(c) || !aligned(c.r)) continue;
      // 위나 아래로 맞닿아 있나 (음수면 겹친 것 — 그것도 맞닿은 것이다)
      const gap = Math.max(c.r.top - u.bottom, u.top - c.r.bottom);
      if (gap <= STRIP_GAP_MAX) {
        group.push(c);
        grew = true;
      }
    }
  }
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
// 공용
//
// **여러 파일이 쓰는 것은 여기 둔다.** 콘텐츠 스크립트는 manifest 순서대로 실행되고,
// 앞 파일이 뒤 파일의 함수를 **로드 시점에** 부르면 ReferenceError 로 죽는다.
// 그러면 그 파일의 나머지도 실행되지 않아 다음 파일까지 연쇄로 깨진다.
// (실제로 그렇게 났다 — panel.js 가 escapeHtml 을 로드 중에 쓰는데 그게 auto.js 에
// 있어서, panel.js 가 중단되고 거기 선언된 MENU_ID 까지 사라졌다.)
// ---------------------------------------------------------------------------

function escapeHtml(s) {
  const d = document.createElement("div");
  d.textContent = s ?? "";
  return d.innerHTML;
}
