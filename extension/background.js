const DEFAULT_SERVER = "http://127.0.0.1:8765";

async function serverUrl() {
  const { server } = await chrome.storage.sync.get({ server: DEFAULT_SERVER });
  return server.replace(/\/$/, "");
}

async function call(path, { method = "GET", body } = {}) {
  const response = await fetch(`${await serverUrl()}${path}`, {
    method,
    headers: { "Content-Type": "application/json", "X-JobRadar": "1" },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `JobRadar returned ${response.status}`);
  return data;
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message.type !== "api") return false;
  call(message.path, message.options)
    .then((data) => sendResponse({ ok: true, data }))
    .catch((error) => sendResponse({ ok: false, error: error.message || "Could not reach JobRadar. Is it running?" }));
  return true;
});
