// Injects plait buttons into GitHub pages:
//   - PR pages:    "Review locally"  -> ephemeral review worktree in VS Code
//   - Issue pages: "Open in plait"   -> full worktop, opened in a new tab
//
// GitHub's PR and issue pages are the same client-rendered Primer React app
// (PageHeader). It navigates without full reloads and swaps DOM nodes in
// place, so a one-shot injection on load is not enough. We re-run injection
// on a debounced MutationObserver and guard against duplicates by element id.
//
// The visible markup is content-hashed (prc-* / *-module__* class names churn
// on every GitHub deploy), so we anchor on the *stable* data-component
// attributes Primer emits (PH_Actions, Button, buttonContent, text) and copy
// the live button classes off an existing header button to stay native-looking.
//
// Buttons only appear on repos registered with plait. The service worker
// answers "repo-registered" messages from a cached GET /repos; results are
// memoized here per owner/repo. If plait is unreachable, nothing is injected
// (the buttons couldn't work anyway).

// Each page kind: how to recognize the URL, what the button says, and which
// service-worker message the click sends. `onSuccess` returns the transient
// label shown before reverting.
const PAGES = {
  pr: {
    buttonId: "plait-review-locally",
    label: "Review locally",
    pattern: /^\/([^/]+)\/([^/]+)\/pull\/(\d+)/,
    kindPath: "pull",
    message: "review-pr",
    urlKey: "prUrl",
    onSuccess: () => "Opened in VS Code",
  },
  issue: {
    buttonId: "plait-open-in-plait",
    label: "Open in plait",
    pattern: /^\/([^/]+)\/([^/]+)\/issues\/(\d+)/,
    kindPath: "issues",
    message: "open-issue",
    urlKey: "issueUrl",
    onSuccess: () => "Opened",
  },
};

// Matches the current location against the known page kinds.
function currentPage() {
  for (const page of Object.values(PAGES)) {
    const m = location.pathname.match(page.pattern);
    if (!m) continue;
    const [, owner, repo, number] = m;
    return {
      ...page,
      ownerRepo: `${owner}/${repo}`,
      url: `https://github.com/${owner}/${repo}/${page.kindPath}/${number}`,
    };
  }
  return null;
}

// owner/repo -> true | false | "pending". Memoizes the service worker's
// registration answers for the lifetime of this page.
const registered = new Map();

// The header actions cluster (View status / Code / New issue buttons) next to
// the title. Same data-component anchor on PR and issue pages.
function findActions() {
  return (
    document.querySelector('[data-component="PH_Actions"] .d-flex.gap-2') ||
    document.querySelector('[data-component="PH_Actions"]')
  );
}

// First class on `el` starting with `prefix`, or "" — lets us copy GitHub's
// current hashed class names (which change between deploys) at runtime.
function classFrom(el, prefix) {
  if (!el) return "";
  return [...el.classList].find((c) => c.startsWith(prefix)) || "";
}

function makeButton(actions, page) {
  // Reuse a real header button as a styling template.
  const ref = actions.querySelector('button[data-component="Button"]');
  const baseCls = classFrom(ref, "prc-Button-ButtonBase");
  const contentCls = classFrom(
    ref?.querySelector('[data-component="buttonContent"]'),
    "prc-Button-ButtonContent",
  );
  const labelCls = classFrom(
    ref?.querySelector('[data-component="text"]'),
    "prc-Button-Label",
  );

  const btn = document.createElement("button");
  btn.id = page.buttonId;
  btn.type = "button";
  btn.dataset.component = "Button";
  btn.dataset.size = "medium";
  btn.dataset.variant = "default";
  btn.className = `${baseCls} plait-review-btn`;
  btn.innerHTML =
    `<span data-component="buttonContent" data-align="center" class="${contentCls}">` +
    `<span data-component="text" class="${labelCls}">${page.label}</span></span>`;
  btn.addEventListener("click", (e) => onClick(e, page));
  return btn;
}

async function onClick(e, page) {
  const btn = e.currentTarget;
  const target = currentPage();
  if (!target) return;

  const label = btn.querySelector('[data-component="text"]') || btn;
  const original = label.textContent;
  btn.disabled = true;
  label.textContent = "Opening…";

  try {
    const resp = await chrome.runtime.sendMessage({
      type: page.message,
      [page.urlKey]: target.url,
    });
    if (!resp?.ok) throw new Error(resp?.error || "Unknown error");
    label.textContent = page.onSuccess(resp.data);
  } catch (err) {
    label.textContent = "Failed";
    console.error(`[plait] ${page.message} failed:`, err);
  } finally {
    setTimeout(() => {
      label.textContent = original;
      btn.disabled = false;
    }, 4000);
  }
}

function inject() {
  const page = currentPage();
  if (!page) return;
  if (document.getElementById(page.buttonId)) return;

  // Gate on plait registration. First sight of a repo kicks off an async
  // check; when it lands, re-run injection.
  const known = registered.get(page.ownerRepo);
  if (known === undefined) {
    registered.set(page.ownerRepo, "pending");
    chrome.runtime
      .sendMessage({ type: "repo-registered", ownerRepo: page.ownerRepo })
      .then((resp) => {
        registered.set(page.ownerRepo, resp?.ok && resp.data === true);
        inject();
      })
      .catch(() => registered.delete(page.ownerRepo));
    return;
  }
  if (known !== true) return;

  const actions = findActions();
  if (!actions) return;

  actions.prepend(makeButton(actions, page));
}

// Debounce: the React app mutates the DOM constantly, so coalesce bursts.
let scheduled = false;
const observer = new MutationObserver(() => {
  if (scheduled) return;
  scheduled = true;
  requestAnimationFrame(() => {
    scheduled = false;
    inject();
  });
});
observer.observe(document.body, { childList: true, subtree: true });

inject();
