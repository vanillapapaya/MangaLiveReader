// 음성 읽기 (브라우저 내장 · GPT-SoVITS 서버)
//
// `content.js` 에서 갈라 나온 파일이다. **모듈이 아니다** — MV3 콘텐츠
// 스크립트는 manifest 의 `js` 배열 순서대로 같은 전역에서 실행된다.
// 그래서 import 없이 서로의 함수를 그냥 부른다. 순서를 바꾸면 깨진다.


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
 * **다만 브라우저에 일본어 음성이 없고 서버도 없으면** 번역문을 읽는다. 윈도우는
 * 일본어 TTS 가 기본으로 안 깔려 있다 (실측: Heami/ko, Zira·David/en 뿐). 그대로 두면
 * 윈도우에서 통째로 무음이 된다 — 안 읽는 것보다 번역문이라도 읽는 게 낫다.
 *
 * **순서를 틀리면 안 된다.** 예전에는 브라우저 음성만 보고 곧바로 번역문으로 내렸다.
 * 그러면 음성 서버는 일본어를 읽을 수 있는데도 **물어보지도 않고** 한국어를 받는다 —
 * 윈도우에서 번역문이 읽히던 이유다. 서버가 설정돼 있으면 원문을 그대로 들고 가고,
 * 서버까지 실패한 뒤에야 `fallbackSpeech()` 로 내린다.
 */
async function resolveSpeech(box) {
  const ja = (box.dataset.ja || "").trim();
  const ko = (box.dataset.ko || "").trim();

  if (ja) {
    const v = await pickVoice("ja-JP");
    if (v) return { text: ja, lang: "ja-JP", voice: v };
    // 서버가 읽어 줄 수 있다. 여기서 내리면 서버에 기회가 안 간다.
    if (await hasTtsServer()) return { text: ja, lang: "ja-JP", voice: null, needsServer: true };
    if (ko) {
      return { text: ko, lang: "ko-KR", voice: await pickVoice("ko-KR"), fellBack: true };
    }
    return { text: ja, lang: "ja-JP", voice: null }; // 기본 음성에 맡긴다
  }
  return { text: ko, lang: "ko-KR", voice: await pickVoice("ko-KR") };
}

/** 음성 서버가 설정돼 있는가. 매번 저장소를 읽지 않게 한 번만 본다
 *  (옵션에서 주소를 바꾸면 `storage.onChanged` 가 지운다). */
let serverTts = null;
async function hasTtsServer() {
  if (serverTts !== null) return serverTts;
  try {
    serverTts = Boolean((await chrome.storage.sync.get("ttsUrl")).ttsUrl?.trim());
  } catch {
    serverTts = false;
  }
  return serverTts;
}

/** 서버까지 안 됐을 때 마지막으로 번역문으로 내린다. 없으면 null. */
async function fallbackSpeech(box) {
  const ko = (box.dataset.ko || "").trim();
  if (!ko) return null;
  return { text: ko, lang: "ko-KR", voice: await pickVoice("ko-KR"), fellBack: true };
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

// ---------------------------------------------------------------------------
// 서버 음성 (GPT-SoVITS)
//
// 브라우저 내장 음성은 어디서나 되지만 기계음이다. 학습된 목소리로 원문을 들으면
// 일본어 듣기용으로 값어치가 다르다.
//
// **실측 RTF 0.25** — 40자 대사가 1.9초 합성에 7.6초 재생이다. 재생이 합성보다
// 4배 느리므로, 지금 재생하는 동안 다음 것을 미리 합성해 두면 끊기지 않는다.
//
// 서버가 없거나 실패하면 조용히 내장 음성으로 떨어진다 — 소리가 아예 안 나는 것이
// 제일 나쁘다.
// ---------------------------------------------------------------------------

//: 말풍선 사이 기본 간격. 사람이 다음 칸으로 눈을 옮기는 정도.
const SPEAK_GAP_MS = 420;

//: 문장이 끝났으면 조금 더 쉰다 — 이어지는 대사와 끝난 대사는 다르게 들려야 한다.
const SPEAK_GAP_END_MS = 700;

/** 이 대사 뒤에 얼마나 쉴까. 원문의 끝맺음을 보고 정한다. */
function gapFor(cur) {
  const t = (cur.text || "").trim();
  if (!t) return SPEAK_GAP_MS;
  // 「…」 로 끝나면 말이 이어지는 중이다. 오래 쉬면 흐름이 끊긴다.
  // **전각 `．`(U+FF0E)를 빠뜨리면 안 된다** — manga-ocr 은 말줄임을 그걸로 낸다
  // (「なんか．．．」). 반각 `.` 이나 `。` 만 보면 이어지는 말을 끝난 것으로 읽는다.
  if (/[…‥。．.]{2,}$/.test(t) || /[、,]$/.test(t)) return SPEAK_GAP_MS;
  // 문장이 닫혔으면(。！？ 등) 조금 더.
  if (/[。．.!！?？♡♪]$/.test(t)) return SPEAK_GAP_END_MS;
  return SPEAK_GAP_MS;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** 지금 재생 중인 소리. 멈출 때 필요하다. */
let audioSrc = null;
let audioCtx = null;

/** 서버에 합성을 시켜 data URL 을 받는다. 실패하면 null (내장 음성으로 떨어진다).
 *
 * **실패 이유를 삼키지 않는다.** 예전에는 전부 조용히 null 로 만들어서, 서버가
 * 주소를 안 받았는지·권한이 없는지·죽었는지 구분할 수 없었다. 실제로 background 의
 * `ReferenceError` 한 줄이 여기 묻혀 "왜 내장 음성이 나오지" 로만 보였다.
 */
let ttsLastError = null;

async function ttsFetch(text, lang) {
  if (stale || !alive()) return null;
  try {
    const r = await chrome.runtime.sendMessage({ type: "tts", text, lang });
    if (r?.b64) {
      ttsLastError = null;
      return r.b64;
    }
    ttsLastError = r?.error ?? "음성 서버 주소가 비어 있다 (확장 옵션에서 설정)";
  } catch (err) {
    ttsLastError = String(err?.message || err);
  }
  return null;
}

/** base64 오디오를 재생한다. **재생됐으면 true, 못 했으면 false.**
 *
 * **`new Audio(dataUrl)` 을 쓰지 않는다.** 그건 리소스 로드라 **페이지의 CSP
 * (media-src)** 를 타는데, 만화 사이트는 CSP 가 빡빡해서 `data:` 오디오가 막힌다 —
 * `onerror` 만 나고 "오디오를 못 읽었다" 로 끝났다.
 *
 * Web Audio 는 스크립트로 디코드·재생하므로 CSP 를 타지 않는다.
 */
async function playAudio(b64) {
  try {
    if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    // 사용자 제스처 없이 만들어졌으면 멈춰 있다. 버튼을 누른 흐름이라 풀린다.
    if (audioCtx.state === "suspended") await audioCtx.resume();

    const bin = atob(b64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    const buf = await audioCtx.decodeAudioData(bytes.buffer);

    return await new Promise((resolve) => {
      const src = audioCtx.createBufferSource();
      audioSrc = src;
      src.buffer = buf;
      src.connect(audioCtx.destination);
      src.onended = () => {
        if (audioSrc === src) audioSrc = null;
        resolve(true);
      };
      src.start();
    });
  } catch (err) {
    ttsLastError = `재생 실패: ${String(err?.name || err?.message || err)}`;
    return false;
  }
}

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
  const how = navigator.userAgent.includes("Windows")
    ? "설정 → 시간 및 언어 → 언어 → 일본어 추가(음성 포함) 후 브라우저 재시작"
    : "시스템 설정에서 일본어 음성을 추가할 것";
  return `일본어 음성이 없어 번역문을 읽는다 (있는 언어: ${langs}) — ${how}`;
}

async function speakOne(box) {
  if (speechSynthesis.speaking || speechSynthesis.pending) speechSynthesis.cancel();
  let s = await resolveSpeech(box);
  if (!s.text) return;
  const url = await ttsFetch(s.text, s.lang);
  if (url && (await playAudio(url))) return;
  if (ttsLastError) status(`음성 서버 실패: ${ttsLastError}`, true);
  // 서버를 믿고 원문을 골랐는데 서버가 안 됐다. **이제야** 번역문으로 내린다.
  if (s.needsServer) s = (await fallbackSpeech(box)) || s;
  if (s.fellBack) status(await noJapaneseReason());
  else if (!s.voice) {
    const n = (await loadVoices()).length;
    status(`${s.lang} 음성이 없다 (설치된 음성 ${n}개). 시스템 설정에서 추가할 것`, true);
  }
  await say(s.text, s.lang, s.voice);
}

/** 페이지 전체를 읽기 순서대로. id 순서가 곧 읽기 순서다 (order.py 가 정한 것). */
async function speakAll() {
  const root = document.getElementById(OVERLAY_ID);
  if (!root) return;
  const boxes = [...root.querySelectorAll(".mlr-box")].filter(
    (b) => isShown(b) && hasSpeech(b)
  );
  if (!boxes.length) {
    status("읽을 것이 없다", true);
    return;
  }

  speaking = true;
  syncPanel();
  if (speechSynthesis.speaking || speechSynthesis.pending) speechSynthesis.cancel();

  // 서버 음성을 쓸 수 있는지 첫 문장으로 알아본다.
  const first = await resolveSpeech(boxes[0]);
  let firstUrl = await ttsFetch(first.text, first.lang);
  const useServer = firstUrl !== null;
  if (!useServer && ttsLastError) status(`음성 서버 실패: ${ttsLastError}`, true);
  status(
    useServer
      ? "음성: 서버 (GPT-SoVITS)"
      : first.fellBack || first.needsServer
        ? await noJapaneseReason()
        : first.voice
          ? `음성: ${first.voice.name}`
          : "맞는 음성이 없어 기본 음성으로 읽는다"
  );

  // **다음 것을 미리 합성해 둔다.** 재생이 합성보다 4배 느리므로 한 칸만 앞서면
  // 충분하다. 이게 없으면 말풍선마다 1-2초씩 끊긴다.
  // `ready` 는 **지금 재생할** 오디오다. 재생을 시작하기 전에 다음 것의 합성을
  // 걸어 두고, 재생이 끝나면 그것을 받아 다음 차례로 넘긴다. 한 칸만 앞서면
  // 충분하다 — 재생이 합성보다 4배 느리다.
  let ready = firstUrl;
  for (let i = 0; i < boxes.length && speaking; i++) {
    const box = boxes[i];
    const cur = await resolveSpeech(box);

    const ahead =
      useServer && i + 1 < boxes.length
        ? resolveSpeech(boxes[i + 1]).then((nx) => (nx.text ? ttsFetch(nx.text, nx.lang) : null))
        : Promise.resolve(null);

    box.classList.add("mlr-speaking");
    status(`읽는 중 ${i + 1}/${boxes.length}`);
    try {
      // 서버 오디오가 재생되지 않으면(자동재생 차단 등) 내장 음성으로 떨어진다.
      const played = ready ? await playAudio(ready) : false;
      if (!played && cur.text) {
        if (ready && ttsLastError) status(`${ttsLastError} — 내장 음성으로 읽는다`, true);
        // 서버를 믿고 원문을 골랐는데 서버가 안 됐다 — 여기서 번역문으로 내린다.
        const f = cur.needsServer ? (await fallbackSpeech(box)) || cur : cur;
        await say(f.text, f.lang, f.voice);
      }
    } finally {
      box.classList.remove("mlr-speaking");
    }
    // **미리 받은 것과 다음 박스가 어긋나면 안 된다.** 여기서 한 칸씩 같이 민다.
    ready = await ahead;

    // 말풍선 사이에 숨을 둔다. 붙여 놓으면 한 사람이 몰아치듯 들려서 어디서
    // 끊기는지 알 수 없다. **재생이 합성보다 4배 빠르므로 이 틈은 공짜다** —
    // 기다리는 동안 다음 것은 이미 합성돼 있다.
    if (i + 1 < boxes.length && speaking) await sleep(gapFor(cur));
  }

  speaking = false;
  syncPanel();
  status("읽기 끝");
}

function stopSpeaking() {
  speaking = false;
  currentUtterance = null;
  if (audioSrc) {
    try {
      audioSrc.onended = null;
      audioSrc.stop();
    } catch {
      /* 이미 끝났으면 던진다 */
    }
    audioSrc = null;
  }
  speechSynthesis.cancel();
  document
    .querySelectorAll(".mlr-box.mlr-speaking")
    .forEach((b) => b.classList.remove("mlr-speaking"));
  syncPanel();
}
