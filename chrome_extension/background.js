// Service worker: the only place allowed to talk to the local plait server.
//
// Content scripts run in the github.com origin (HTTPS), so a fetch from there
// to http://localhost would be blocked as mixed content. The service worker
// runs in the extension origin and, with `host_permissions` for localhost,
// can make the request without CORS preflight concerns.

const DEFAULT_BASE_URL = "http://localhost:57381";

async function plaitBaseUrl() {
  const { plaitBaseUrl } = await chrome.storage.sync.get("plaitBaseUrl");
  return (plaitBaseUrl || DEFAULT_BASE_URL).replace(/\/+$/, "");
}

async function reviewPr(prUrl) {
  const base = await plaitBaseUrl();
  const res = await fetch(`${base}/review-pr`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pr_url: prUrl }),
  });
  const text = await res.text();
  let data;
  try {
    data = JSON.parse(text);
  } catch {
    data = { detail: text };
  }
  if (!res.ok) {
    throw new Error(data.detail || `plait returned ${res.status}`);
  }
  return data;
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg?.type !== "review-pr") return false;
  reviewPr(msg.prUrl)
    .then((data) => sendResponse({ ok: true, data }))
    .catch((err) => sendResponse({ ok: false, error: String(err.message || err) }));
  // Return true to keep the message channel open for the async response.
  return true;
});
