// Injects a "Review locally" button into GitHub PR pages.
//
// GitHub's PR page is a client-rendered Primer React app (PageHeader). It
// navigates without full reloads and swaps DOM nodes in place, so a one-shot
// injection on load is not enough. We re-run injection on a debounced
// MutationObserver and guard against duplicates by element id.
//
// The visible markup is content-hashed (prc-* / *-module__* class names churn
// on every GitHub deploy), so we anchor on the *stable* data-component
// attributes Primer emits (PH_Actions, Button, buttonContent, text) and copy
// the live button classes off an existing header button to stay native-looking.

const BUTTON_ID = "plait-review-locally";

// Matches https://github.com/<owner>/<repo>/pull/<number>(/...)
function currentPrUrl() {
  const m = location.pathname.match(/^\/([^/]+)\/([^/]+)\/pull\/(\d+)/);
  if (!m) return null;
  const [, owner, repo, number] = m;
  return `https://github.com/${owner}/${repo}/pull/${number}`;
}

// The header actions cluster (View status / Code buttons) next to the title.
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

function makeButton(actions) {
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
  btn.id = BUTTON_ID;
  btn.type = "button";
  btn.dataset.component = "Button";
  btn.dataset.size = "medium";
  btn.dataset.variant = "default";
  btn.className = `${baseCls} plait-review-btn`;
  btn.innerHTML =
    `<span data-component="buttonContent" data-align="center" class="${contentCls}">` +
    `<span data-component="text" class="${labelCls}">Review locally</span></span>`;
  btn.addEventListener("click", onClick);
  return btn;
}

async function onClick(e) {
  const btn = e.currentTarget;
  const prUrl = currentPrUrl();
  if (!prUrl) return;

  const label = btn.querySelector('[data-component="text"]') || btn;
  const original = label.textContent;
  btn.disabled = true;
  label.textContent = "Opening…";

  try {
    const resp = await chrome.runtime.sendMessage({ type: "review-pr", prUrl });
    if (!resp?.ok) throw new Error(resp?.error || "Unknown error");
    label.textContent = "Opened in VS Code";
  } catch (err) {
    label.textContent = "Failed";
    console.error("[plait] review-pr failed:", err);
  } finally {
    setTimeout(() => {
      label.textContent = original;
      btn.disabled = false;
    }, 4000);
  }
}

function inject() {
  if (!currentPrUrl()) return;
  if (document.getElementById(BUTTON_ID)) return;

  const actions = findActions();
  if (!actions) return;

  actions.prepend(makeButton(actions));
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
