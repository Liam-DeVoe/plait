from __future__ import annotations

# Permissions granted to daemon-spawned Claude sessions.
# Each entry is an --allowedTools pattern.
# Path-based tools use // for absolute paths; the worktree path is
# substituted at runtime via {worktree}.
DAEMON_ALLOWED_TOOLS = [
    "Bash(git *)",
    "Bash(just *)",
    "Bash(cargo *)",
    "Bash(npm *)",
    "Bash(go *)",
    "Bash(uv *)",
    "Bash(dune *)",
    "Read(//{worktree}/**)",
    "Edit(//{worktree}/**)",
    "Write(//{worktree}/**)",
    "Glob(//{worktree}/**)",
    "Grep(//{worktree}/**)",
]
