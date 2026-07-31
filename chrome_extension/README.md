# Plait Chrome extension

Adds plait buttons to GitHub pages, for repos registered with plait:

- **Review locally** on pull-request pages
- **Open in plait** on issue pages

Both buttons only appear on repos whose upstream is configured in plait
(Repos page). The service worker checks `GET /repos` and caches the answer
for 5 minutes, so a freshly registered repo may take a few minutes (or an
extension reload) to grow buttons.

## Review locally (PRs)

Clicking it asks your local plait server to:

1. fetch the PR head (`refs/pull/<n>/head`) from the configured upstream remote,
2. check it out in an ephemeral worktree, on a local branch wired to push back
   to the PR's head repo (so you can commit and `git push` fixes),
3. open that worktree in VS Code, and
4. start a Claude session primed to read the diff and answer review questions.

A review is lightweight, not a worktop: no PR ownership and no daemon
involvement — just a worktree on disk and a Claude session in VS Code. It leaves
no DB record and persists on disk until removed by hand. Push works for any PR
you have rights to — same-repo, your own fork, or a contributor's fork with
maintainer-can-modify — and simply fails if you don't.

## Open in plait (issues)

Clicking it opens a new tab on the plait worktop for that issue. If no open
worktop tracks the issue yet, plait creates one — a real worktop, with a DB
record and daemon tends — and seeds a Claude session with
`investigate <issue url>`, so investigation is already underway when the tab
loads. Re-clicking while the worktop is open goes back to the same worktop;
once it's archived (fix merged), a new click starts a fresh one.

## How it fits together

The extension is a thin UI. It can't spawn `git`/`code`/`claude` itself —
browsers are sandboxed — so all the real work happens in plait, which already
knows how to make worktrees and drive VS Code. The only thing the extension
sends is the PR/issue URL; plait resolves `owner/repo` → local clone via the
**upstream** configured on the Repos page.

```
content.js  (github.com)  --sendMessage-->  background.js  (extension origin)
                                                   |
                                                   v  POST /review-pr {pr_url}
                                                      POST /open-issue {issue_url}
                                          plait server (localhost:57381)
```

The `fetch` lives in the **service worker** on purpose: a call from the
content script (an `https://github.com` page) to `http://localhost` is blocked
as mixed content. The service worker runs in the extension origin and, with
`http://localhost/*` in `host_permissions`, reaches plait without CORS issues.

## Backend contract

- `POST /review-pr` with `{"pr_url": "https://github.com/owner/repo/pull/123"}`
  returns `{"status": "opened", "worktree_path": "..."}`.
- `POST /open-issue` with `{"issue_url": "https://github.com/owner/repo/issues/123"}`
  returns `{"worktop_id": "...", "url": "...", "created": true|false}`. The
  extension opens `url` in a new tab.
- `GET /repos` lists configured repos; the extension matches page URLs
  against each repo's `upstream` to decide whether to show buttons.

Both POST endpoints 400 if the URL's `owner/repo` isn't a configured upstream.

## Install (unpacked)

1. `chrome://extensions` → enable **Developer mode** → **Load unpacked** →
   select this `chrome_extension/` directory.
2. Click the **Plait toolbar icon** (or ⋮ → **Options**) if your plait server
   isn't on the default `http://localhost:57381`.
3. Visit any PR or issue on a configured upstream repo — the button appears
   next to the title actions.

## Limitations / notes

- macOS-only handoff for reviews: opening the VS Code terminal and typing the
  prompt uses AppleScript (`osascript` + System Events), and needs
  Accessibility permission for whatever process runs the plait server. The
  issue button has no such dependency — it just opens a browser tab.
- Buttons are injected into GitHub's Primer React page header by anchoring on
  the stable `data-component="PH_Actions"` attribute (the hashed `prc-*` class
  names are copied off a live button at runtime, so deploys that re-hash them
  don't break styling). If GitHub renames the `data-component` attributes,
  update the selectors in `content.js`.
- Review worktrees are persistent and untracked. They live at
  `worktrees/review-<repo>-<n>/`, keyed to the PR so re-reviewing reuses the
  existing worktree. The daemon never touches them.
- If the plait server is unreachable, no buttons are injected.
