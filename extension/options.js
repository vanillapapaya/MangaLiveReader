// 설정 화면. background.js 의 DEFAULTS 와 키 이름이 같아야 한다.
const DEFAULTS = {
  serviceUrl: "http://127.0.0.1:8788/read",
  authToken: "",
  ttsUrl: "",
  ttsVoice: "",
  autoSites: "",
};

const url = document.getElementById("url");
const token = document.getElementById("token");
const saved = document.getElementById("saved");
const ttsurl = document.getElementById("ttsurl");
const ttsvoice = document.getElementById("ttsvoice");
const voicemsg = document.getElementById("voicemsg");
const autosites = document.getElementById("autosites");

chrome.storage.sync.get(Object.keys(DEFAULTS)).then((got) => {
  const v = { ...DEFAULTS, ...got };
  url.value = v.serviceUrl;
  token.value = v.authToken;
  ttsurl.value = v.ttsUrl;
  autosites.value = v.autoSites;
  if (v.ttsVoice) {
    // 목록을 아직 안 받았어도 저장된 값은 보여 준다.
    ttsvoice.add(new Option(v.ttsVoice, v.ttsVoice, true, true));
  }
});

// 음성 서버에서 목소리 목록을 받아 채운다.
document.getElementById("loadvoices").addEventListener("click", async () => {
  const base = ttsurl.value.trim();
  if (!base) {
    voicemsg.textContent = "주소를 먼저 넣을 것";
    return;
  }
  // **권한을 먼저 받는다.** `/read` 와 같은 이유 — 없으면 fetch 가 CORS 로 막힌다.
  // `await` 앞에 두면 사용자 제스처가 끊기므로 request 를 바로 부른다.
  const pattern = originPattern(base);
  if (pattern && !(await chrome.permissions.request({ origins: [pattern] }))) {
    voicemsg.textContent = `${pattern} 권한이 없다`;
    return;
  }
  voicemsg.textContent = "불러오는 중…";
  try {
    const resp = await fetch(new URL("/voices", base).href);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    const cur = ttsvoice.value;
    ttsvoice.innerHTML = '<option value="">(서버 기본값)</option>';
    for (const name of data.voices ?? []) {
      const desc = data.info?.[name]?.description;
      ttsvoice.add(new Option(desc ? `${name} — ${desc}` : name, name, false, name === cur));
    }
    voicemsg.textContent = `${(data.voices ?? []).length}개`;
  } catch (err) {
    voicemsg.textContent = `실패: ${err.message}`;
  }
});

/** 호스트 목록 → 권한 패턴.
 *
 * **자동 실행에는 사이트 권한이 반드시 필요하다.** `chrome.tabs.captureVisibleTab`
 * 은 `activeTab` 이나 그 사이트의 host permission 이 있어야 하는데, `activeTab` 은
 * **사용자가 확장을 직접 실행할 때만** 주어진다 (아이콘 클릭·단축키). 자동으로
 * 켜지면 그게 없어 캡처가 막히고, 그때마다 손으로 한 번 눌러야 한다.
 */
function sitePatterns(text) {
  return [
    ...new Set(
      text
        .split(/[\n,]/)
        // `https://yanmaga.jp/x` 처럼 붙여 넣어도 호스트만 뽑는다.
        .map((x) =>
          x
            .trim()
            .toLowerCase()
            .replace(/^[a-z]+:\/\//, "")
            .replace(/^\*?\.?/, "")
            .replace(/[/:?#].*$/, "")
        )
        .filter((h) => /^[a-z0-9.-]+\.[a-z]{2,}$/.test(h))
        .flatMap((h) => [`*://${h}/*`, `*://*.${h}/*`])
    ),
  ];
}

/** 주소의 출처(origin) 만 뽑는다. 권한은 출처 단위로 준다. */
function originPattern(u) {
  try {
    return `${new URL(u).origin}/*`;
  } catch {
    return null;
  }
}

document.getElementById("save").addEventListener("click", async () => {
  const value = url.value.trim() || DEFAULTS.serviceUrl;
  const tts = ttsurl.value.trim();

  // ---------------------------------------------------------------------------
  // 필요한 권한을 **한 번에, 맨 먼저** 요청한다.
  //
  // `chrome.permissions.request()` 는 사용자 제스처가 살아 있을 때만 먹는다.
  // **`await` 를 한 번이라도 거치면 제스처가 끊겨 팝업이 아예 안 뜬다.**
  //
  // 이 함정에 세 번 걸렸다: 처음엔 `permissions.contains()` 를 먼저 await 해서,
  // 그 다음엔 요청을 세 번으로 나눠서(둘째·셋째가 await 뒤라 조용히 실패). 그래서
  // 사이트를 추가해도 팝업이 안 뜨고 자동이 안 켜졌다.
  //
  // 나눌 이유도 없다 — 한 팝업에 다 넣으면 사용자도 한 번만 답한다.
  // ---------------------------------------------------------------------------
  const wanted = [
    ...(originPattern(value) ? [originPattern(value)] : []),
    ...(tts && originPattern(tts) ? [originPattern(tts)] : []),
    ...sitePatterns(autosites.value),
  ];

  let granted = true;
  if (wanted.length) {
    try {
      granted = await chrome.permissions.request({ origins: [...new Set(wanted)] });
    } catch (err) {
      granted = false;
      saved.hidden = false;
      saved.textContent = `권한 요청 실패: ${err.message}`;
      saved.style.color = "#c33";
    }
  }

  await chrome.storage.sync.set({
    serviceUrl: value,
    authToken: token.value.trim(),
    ttsUrl: tts,
    ttsVoice: ttsvoice.value,
    autoSites: autosites.value,
  });
  saved.hidden = false;
  if (granted) {
    saved.textContent = "저장했다";
    saved.style.color = "";
    setTimeout(() => (saved.hidden = true), 1500);
  } else {
    // 설정은 저장했다. 권한만 없는 것이니 다시 누르면 된다.
    saved.textContent = "저장했지만 권한이 없다 — 자동 실행·원격 접속이 안 된다. 다시 「저장」을 눌러 허용할 것";
    saved.style.color = "#c33";
  }
});

// 캐시 전부 지우기. **되돌릴 수 없으므로 한 번 묻는다.**
document.getElementById("purge").addEventListener("click", async () => {
  if (!confirm("캐시를 전부 지운다. 지운 페이지는 다시 읽을 때 번역 비용이 새로 든다.\n계속할까?")) {
    return;
  }
  const out = document.getElementById("purged");
  out.hidden = false;
  out.textContent = "지우는 중…";
  try {
    const { serviceUrl, authToken } = {
      ...DEFAULTS,
      ...(await chrome.storage.sync.get(Object.keys(DEFAULTS))),
    };
    const url = serviceUrl.replace(/\/read\/?$/, "/cache/purge");
    const resp = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(authToken ? { "X-Auth-Token": authToken } : {}),
      },
      body: JSON.stringify({ all: true }),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const { deleted } = await resp.json();
    out.textContent = `${deleted}개 지웠다`;
  } catch (err) {
    out.textContent = `실패: ${err.message}`;
  }
});
