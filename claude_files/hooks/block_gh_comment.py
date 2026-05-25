#!/usr/bin/env python3
"""PreToolUse Bash hook: block Claude from posting GitHub comments.

PreToolUse hooks fire even when Claude Code runs with
`--dangerously-skip-permissions`, so this is a hard guardrail regardless
of permission mode.

Reads the PreToolUse JSON payload on stdin, inspects
`tool_input.command`, and prints a deny JSON if the command matches a
comment-posting `gh` invocation. Silent (exit 0, no output) otherwise.
"""

from __future__ import annotations

import json
import re
import sys

PATTERNS: list[tuple[str, str]] = [
    (
        r"\bgh\s+pr\s+comment\b",
        "Plait blocks `gh pr comment` to keep Claude from posting on the user's behalf.",
    ),
    (
        r"\bgh\s+issue\s+comment\b",
        "Plait blocks `gh issue comment` to keep Claude from posting on the user's behalf.",
    ),
    (
        r"\bgh\s+pr\s+review\b",
        "Plait blocks `gh pr review` to keep Claude from posting on the user's behalf.",
    ),
]


def _deny(reason: str) -> None:
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        },
        sys.stdout,
    )
    sys.exit(0)


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return
    cmd = (payload.get("tool_input") or {}).get("command")
    if not isinstance(cmd, str):
        return
    for pattern, reason in PATTERNS:
        if re.search(pattern, cmd):
            _deny(reason)
    if (
        re.search(r"\bgh\s+api\b", cmd)
        and re.search(r"(-X\s+POST|--method\s+POST)", cmd, re.IGNORECASE)
        and re.search(r"/(comments|reviews)\b", cmd)
    ):
        _deny("Plait blocks raw `gh api` POSTs to comments/reviews endpoints.")


if __name__ == "__main__":
    main()
