// 설정 화면. background.js 의 DEFAULTS 와 키 이름이 같아야 한다.
const DEFAULTS = {
  serviceUrl: "http://100.66.125.121:8788/read",
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

document.getElementById("save").addEventListener("click", async () => {
  await chrome.storage.sync.set({
    serviceUrl: url.value.trim() || DEFAULTS.serviceUrl,
    authToken: token.value.trim(),
  });
  saved.hidden = false;
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
