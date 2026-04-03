from __future__ import annotations

# Permissions granted to daemon-spawned Claude sessions.
# Each entry is an --allowedTools pattern.
# Path-based tools use // for absolute paths. Since {worktree} expands
# to an absolute path (starting with /), we use a single / prefix here.
DAEMON_ALLOWED_TOOLS = [
    "Bash",
    "Read(/{worktree}/**)",
    "Edit(/{worktree}/**)",
    "Write(/{worktree}/**)",
    "Glob(/{worktree}/**)",
    "Grep(/{worktree}/**)",
    "WebFetch(domain:hegel.dev)",
]

# Sortie sessions need access to the entire worktrees/ directory
# (both read-only exploration worktrees and cell worktrees created on-demand).
SORTIE_ALLOWED_TOOLS = [
    "Bash",
    "Read(/{worktree_root}/**)",
    "Edit(/{worktree_root}/**)",
    "Write(/{worktree_root}/**)",
    "Glob(/{worktree_root}/**)",
    "Grep(/{worktree_root}/**)",
]
