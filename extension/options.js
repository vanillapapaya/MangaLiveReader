// 설정 화면. background.js 의 DEFAULTS 와 키 이름이 같아야 한다.
const DEFAULTS = {
  serviceUrl: "http://127.0.0.1:8788/read",
  authToken: "",
};

const url = document.getElementById("url");
const token = document.getElementById("token");
const saved = document.getElementById("saved");

chrome.storage.sync.get(Object.keys(DEFAULTS)).then((got) => {
  const v = { ...DEFAULTS, ...got };
  url.value = v.serviceUrl;
  token.value = v.authToken;
});

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

  // **루프백이 아닌 주소는 권한을 따로 받아야 한다.** 사람마다 서비스 머신 주소가
  // 달라서 `manifest.json` 에 못 박을 수 없다 — `optional_host_permissions` 로
  // 두고 여기서 요청한다. 권한이 없으면 fetch 가 CORS 로 조용히 막힌다.
  //
  // **`request()` 앞에서 `await` 하면 안 된다.** 이 API 는 사용자 제스처가 살아
  // 있을 때만 먹는데, 앞에서 한 번이라도 await 하면 제스처 문맥이 끊겨 조용히
  // 실패한다. 예전에는 `permissions.contains()` 를 먼저 await 해서 **권한 요청
  // 팝업이 아예 안 떴다** — 맥북에서 못 붙던 원인이다.
  //
  // 이미 있는 권한을 다시 요청해도 무해하다 (바로 true 로 끝난다).
  const pattern = originPattern(value);
  if (pattern) {
    const granted = await chrome.permissions.request({ origins: [pattern] });
    if (!granted) {
      saved.hidden = false;
      saved.textContent = `${pattern} 권한이 없다 — 그 주소로는 못 붙는다`;
      saved.style.color = "#c33";
      return;
    }
  }

  await chrome.storage.sync.set({ serviceUrl: value, authToken: token.value.trim() });
  saved.hidden = false;
  saved.textContent = "저장했다";
  saved.style.color = "";
  setTimeout(() => (saved.hidden = true), 1500);
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
