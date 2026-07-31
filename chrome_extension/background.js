// Service worker: the only place allowed to talk to the local plait server.
//
// Content scripts run in the github.com origin (HTTPS), so a fetch from there
// to http://localhost would be blocked as mixed content. The service worker
// runs in the extension origin and, with `host_permissions` for localhost,
// can make the request without CORS preflight concerns.

const DEFAULT_BASE_URL = "http://localhost:57381";

// How long a fetched repo list stays fresh. Registering a new repo in plait
// shows up in the extension within this window at worst.
const REPOS_TTL_MS = 5 * 60 * 1000;

async function plaitBaseUrl() {
  const { plaitBaseUrl } = await chrome.storage.sync.get("plaitBaseUrl");
  return (plaitBaseUrl || DEFAULT_BASE_URL).replace(/\/+$/, "");
}

async function plaitFetch(path, init) {
  const base = await plaitBaseUrl();
  const res = await fetch(`${base}${path}`, init);
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

function reviewPr(prUrl) {
  return plaitFetch("/review-pr", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pr_url: prUrl }),
  });
}

async function openIssue(issueUrl) {
  const data = await plaitFetch("/open-issue", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ issue_url: issueUrl }),
  });
  // The whole point: land the user on the worktop page.
  await chrome.tabs.create({ url: data.url });
  return data;
}

// The GitHub upstreams (owner/repo, lowercased) of plait's registered repos.
// Cached in storage.session (survives service-worker teardown, cleared when
// the browser exits) so PR/issue page loads don't hammer the local server.
async function registeredUpstreams() {
  const { reposCache } = await chrome.storage.session.get("reposCache");
  // Timestamps, not Date.now() math on the server side: this is purely a
  // local staleness check.
  if (reposCache && Date.now() - reposCache.fetchedAt < REPOS_TTL_MS) {
    return reposCache.upstreams;
  }
  const repos = await plaitFetch("/repos");
  const upstreams = repos
    .map((r) => r.upstream)
    .filter(Boolean)
    .map((u) => u.toLowerCase());
  await chrome.storage.session.set({
    reposCache: { upstreams, fetchedAt: Date.now() },
  });
  return upstreams;
}

async function repoRegistered(ownerRepo) {
  try {
    const upstreams = await registeredUpstreams();
    return upstreams.includes(ownerRepo.toLowerCase());
  } catch {
    // Plait unreachable: report unregistered so no buttons appear — they
    // couldn't work anyway.
    return false;
  }
}

const HANDLERS = {
  "review-pr": (msg) => reviewPr(msg.prUrl),
  "open-issue": (msg) => openIssue(msg.issueUrl),
  "repo-registered": (msg) => repoRegistered(msg.ownerRepo),
};

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  const handler = HANDLERS[msg?.type];
  if (!handler) return false;
  handler(msg)
    .then((data) => sendResponse({ ok: true, data }))
    .catch((err) => sendResponse({ ok: false, error: String(err.message || err) }));
  // Return true to keep the message channel open for the async response.
  return true;
});
