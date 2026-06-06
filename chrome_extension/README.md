# Plait — "Review locally" Chrome extension

Adds a **Review locally** button to GitHub pull-request pages. Clicking it asks
your local plait server to:

1. fetch the PR head (`refs/pull/<n>/head`) from the configured upstream remote,
2. check it out in an ephemeral worktree, on a local branch wired to push back
   to the PR's head repo (so you can commit and `git push` fixes),
3. open that worktree in VS Code, and
4. start a Claude session primed to read the diff and answer review questions.

A review is a throwaway, not a worktop: no PR ownership and no daemon
involvement — just a worktree on disk and a Claude session in VS Code. It leaves
no DB record; stale review worktrees are pruned by age. Push works for any PR you
have rights to — same-repo, your own fork, or a contributor's fork with
maintainer-can-modify — and simply fails if you don't.

## How it fits together

The extension is a thin UI. It can't spawn `git`/`code`/`claude` itself —
browsers are sandboxed — so all the real work happens in plait, which already
knows how to make worktrees and drive VS Code. The only thing the extension
sends is the PR URL; plait resolves `owner/repo` → local clone via the
**upstream** configured on the Repos page.

```
content.js  (github.com)  --sendMessage-->  background.js  (extension origin)
                                                   |
                                                   v  POST /review-pr {pr_url}
                                          plait server (localhost:57381)
                                                   |
                              git.create_review_worktree + _open_vscode_terminal
```

The `fetch` lives in the **service worker** on purpose: a call from the
content script (an `https://github.com` page) to `http://localhost` is blocked
as mixed content. The service worker runs in the extension origin and, with
`http://localhost/*` in `host_permissions`, reaches plait without CORS issues.

## Backend contract

`POST /review-pr` with `{"pr_url": "https://github.com/owner/repo/pull/123"}`
returns `{"status": "opened", "worktree_path": "..."}`. Requires the upstream
`owner/repo` to be configured as a repo in plait (Repos page) and the local
clone to have a remote whose URL matches that upstream.

## Install (unpacked)

1. `chrome://extensions` → enable **Developer mode** → **Load unpacked** →
   select this `chrome_extension/` directory.
2. Click the **Plait toolbar icon** (or ⋮ → **Options**) if your plait server
   isn't on the default `http://localhost:57381`.
3. Visit any PR on a configured upstream repo — the **Review locally** button
   appears next to the PR title actions.

## Limitations / notes

- macOS-only handoff: opening the VS Code terminal and typing the prompt uses
  AppleScript (`osascript` + System Events), and needs Accessibility
  permission for whatever process runs the plait server.
- The button is injected into GitHub's Primer React PR header by anchoring on
  the stable `data-component="PH_Actions"` attribute (the hashed `prc-*` class
  names are copied off a live button at runtime, so deploys that re-hash them
  don't break styling). If GitHub renames the `data-component` attributes,
  update the selectors in `content.js`.
- Review worktrees are ephemeral and untracked. They live at
  `worktrees/review-<repo>-<n>/`, re-reviewing a PR refreshes that worktree to
  the latest head, and plait's daemon prunes any older than a few days on its
  periodic sweep (and once on startup). The daemon never otherwise touches them.
