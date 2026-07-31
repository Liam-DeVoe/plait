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

// Plait's trefoil-knot mark (a single-color rendering of icons/icon.svg),
// shown as the button's leading visual. Drawn in `currentColor` and tinted
// plait-blue via content.css, so the label text stays GitHub-native while
// the icon identifies the button as plait's.
const KNOT_SVG =
  '<svg class="plait-knot" width="16" height="16" viewBox="8.5 6 34.5 36" aria-hidden="true">' +
  '<polygon fill="none" stroke="currentColor" stroke-width="3.5" stroke-linejoin="round" points="' +
  "40.48,24.54 40.36,25.61 40.12,26.66 39.76,27.67 39.28,28.64 38.71,29.54 38.04,30.36 37.30,31.11 " +
  "36.48,31.76 35.60,32.32 34.69,32.77 33.74,33.13 32.79,33.38 31.83,33.54 30.88,33.60 29.95,33.57 " +
  "29.06,33.46 28.21,33.28 27.40,33.04 26.66,32.75 25.96,32.42 25.33,32.06 24.76,31.69 24.24,31.30 " +
  "23.77,30.92 23.35,30.55 22.98,30.20 22.63,29.87 22.31,29.57 22.00,29.30 21.71,29.06 21.40,28.86 " +
  "21.09,28.68 20.76,28.52 20.41,28.38 20.02,28.25 19.60,28.12 19.14,27.99 18.65,27.83 18.12,27.66 " +
  "17.56,27.44 16.96,27.19 16.35,26.88 15.72,26.51 15.09,26.08 14.47,25.57 13.86,25.00 13.28,24.35 " +
  "12.74,23.63 12.25,22.84 11.83,21.99 11.48,21.08 11.22,20.12 11.06,19.13 11.00,18.11 11.04,17.07 " +
  "11.20,16.04 11.47,15.02 11.85,14.03 12.34,13.08 12.94,12.19 13.64,11.37 14.42,10.64 15.29,9.99 " +
  "16.23,9.45 17.21,9.02 18.25,8.71 19.30,8.52 20.37,8.45 21.44,8.49 22.49,8.66 23.51,8.93 " +
  "24.48,9.31 25.40,9.79 26.25,10.36 27.03,11.00 27.73,11.70 28.35,12.45 28.87,13.25 29.31,14.06 " +
  "29.67,14.89 29.94,15.71 30.13,16.53 30.25,17.32 30.31,18.09 30.32,18.82 30.28,19.50 30.20,20.14 " +
  "30.11,20.74 30.00,21.28 29.88,21.79 29.77,22.25 29.67,22.68 29.59,23.08 29.53,23.46 29.50,23.82 " +
  "29.50,24.18 29.53,24.54 29.59,24.92 29.67,25.32 29.77,25.75 29.88,26.21 30.00,26.72 30.11,27.26 " +
  "30.20,27.86 30.28,28.50 30.32,29.18 30.31,29.91 30.25,30.68 30.13,31.47 29.94,32.29 29.67,33.11 " +
  "29.31,33.94 28.87,34.75 28.35,35.55 27.73,36.30 27.03,37.00 26.25,37.64 25.40,38.21 24.48,38.69 " +
  "23.51,39.07 22.49,39.34 21.44,39.51 20.37,39.55 19.30,39.48 18.25,39.29 17.21,38.98 16.23,38.55 " +
  "15.29,38.01 14.42,37.36 13.64,36.63 12.94,35.81 12.34,34.92 11.85,33.97 11.47,32.98 11.20,31.96 " +
  "11.04,30.93 11.00,29.89 11.06,28.87 11.22,27.88 11.48,26.92 11.83,26.01 12.25,25.16 12.74,24.37 " +
  "13.28,23.65 13.86,23.00 14.47,22.43 15.09,21.92 15.72,21.49 16.35,21.12 16.96,20.81 17.56,20.56 " +
  "18.12,20.34 18.65,20.17 19.14,20.01 19.60,19.88 20.02,19.75 20.41,19.62 20.76,19.48 21.09,19.32 " +
  "21.40,19.14 21.71,18.94 22.00,18.70 22.31,18.43 22.63,18.13 22.98,17.80 23.35,17.45 23.77,17.08 " +
  "24.24,16.70 24.76,16.31 25.33,15.94 25.96,15.58 26.66,15.25 27.40,14.96 28.21,14.72 29.06,14.54 " +
  "29.95,14.43 30.88,14.40 31.83,14.46 32.79,14.62 33.74,14.87 34.69,15.23 35.60,15.68 36.48,16.24 " +
  "37.30,16.89 38.04,17.64 38.71,18.46 39.28,19.36 39.76,20.33 40.12,21.34 40.36,22.39 40.48,23.46" +
  '"/></svg>';

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

// The first real button in the header actions cluster (New issue / Code /
// etc). Same data-component anchor on PR and issue pages. We insert our
// button directly before it and copy its classes, so we sit adjacent to it
// and look native. GitHub streams this cluster in via a Suspense boundary,
// so on early injection passes it may not exist yet — callers must treat
// null as "not yet" and let the MutationObserver retry, or we'd inject an
// unstyled orphan (and the duplicate guard would keep it forever).
function findRefButton() {
  const actions = document.querySelector('[data-component="PH_Actions"]');
  return actions?.querySelector('button[data-component="Button"]') || null;
}

// First class on `el` starting with `prefix`, or "" — lets us copy GitHub's
// current hashed class names (which change between deploys) at runtime.
function classFrom(el, prefix) {
  if (!el) return "";
  return [...el.classList].find((c) => c.startsWith(prefix)) || "";
}

function makeButton(ref, page) {
  // Reuse a real header button (`ref`) as a styling template.
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
    KNOT_SVG +
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

  const ref = findRefButton();
  if (!ref) return;

  ref.parentElement.insertBefore(makeButton(ref, page), ref);
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
