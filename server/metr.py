"""METR study awareness.

A repo flagged `metr` has METR's ccmetr study tooling installed in its
canonical clone's .claude/ (untracked): hook scripts under `ccmetr/`,
`/metr-*` skills, and settings entries that route every Claude session
through the study gateway (ANTHROPIC_BASE_URL override, prompt hooks,
statusline). Left alone, plait's `.claude/` auto-copy would carry all of
that into every worktree, making plait's automated sessions study
participants.

This module knows what the study content looks like so worktrees start
metr-free:

- `is_metr_path()` names the files to skip outright (the ccmetr tooling
  and the /metr-* skills).
- `strip_settings()` removes the study's *keys* from the two settings
  files the METR installer merged into, preserving everything else in
  them (e.g. the user's own permissions).

Opting a worktree back into the study is done by the /metr-reinstall
skill (see claude.install_metr_claude_files), which copies the content
back from the canonical clone and disables auto-tends for the worktop.

The markers here are hardcoded to how METR's installer lays things out;
if the study tooling changes shape, update them in lockstep.
"""

from __future__ import annotations

import json
from pathlib import PurePosixPath

# Substring identifying study-owned hook / statusline commands, e.g.
# "${CLAUDE_PROJECT_DIR}/.claude/ccmetr/bin/hook".
_CCMETR_COMMAND_MARKER = "/ccmetr/"
# Env vars the installer writes to route sessions through the gateway.
_CCMETR_ENV_KEYS = ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN")

# The two files the METR installer *merges into* rather than owns. These
# are filtered key-by-key (strip_settings) instead of skipped, because
# they can hold non-study content that must follow the repo.
SETTINGS_FILES = (".claude/settings.json", ".claude/settings.local.json")


def is_metr_path(rel: str) -> bool:
    """True if a repo-relative path is study content to skip entirely."""
    parts = PurePosixPath(rel).parts
    if len(parts) < 2 or parts[0] != ".claude":
        return False
    if parts[1] == "ccmetr":
        return True
    if len(parts) >= 3 and parts[1] == "skills" and parts[2].startswith("metr-"):
        return True
    # Backup the installer leaves next to settings.local.json.
    return parts[1].startswith("settings.local.json.")


def _is_ccmetr_command(entry: object) -> bool:
    return isinstance(entry, dict) and _CCMETR_COMMAND_MARKER in str(
        entry.get("command", "")
    )


def strip_settings(text: str) -> str | None:
    """Strip the study's keys from a settings JSON document.

    Removes hooks and the statusLine whose command points into ccmetr/,
    the ccmetr permission entry, and the gateway env vars. Containers
    left empty by the removal are dropped too. Returns the filtered JSON,
    or None if nothing meaningful remains (callers skip the file then).

    Unparseable input is returned unchanged — a hand-edited file with a
    stray comma shouldn't vanish from worktrees.
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return text
    if not isinstance(data, dict):
        return text

    hooks = data.get("hooks")
    if isinstance(hooks, dict):
        for event, matchers in list(hooks.items()):
            if not isinstance(matchers, list):
                continue
            for matcher in matchers:
                if isinstance(matcher, dict) and isinstance(
                    matcher.get("hooks"), list
                ):
                    matcher["hooks"] = [
                        h for h in matcher["hooks"] if not _is_ccmetr_command(h)
                    ]
            matchers = [
                m for m in matchers if not isinstance(m, dict) or m.get("hooks")
            ]
            if matchers:
                hooks[event] = matchers
            else:
                del hooks[event]
        if not hooks:
            del data["hooks"]

    if _is_ccmetr_command(data.get("statusLine")):
        del data["statusLine"]

    permissions = data.get("permissions")
    if isinstance(permissions, dict):
        for key in ("allow", "deny", "ask"):
            entries = permissions.get(key)
            if isinstance(entries, list):
                kept = [e for e in entries if _CCMETR_COMMAND_MARKER not in str(e)]
                if kept:
                    permissions[key] = kept
                elif key in permissions:
                    del permissions[key]
        if not permissions:
            del data["permissions"]

    env = data.get("env")
    if isinstance(env, dict):
        for key in _CCMETR_ENV_KEYS:
            env.pop(key, None)
        if not env:
            del data["env"]

    if not data or set(data) == {"$schema"}:
        return None
    return json.dumps(data, indent=2) + "\n"
