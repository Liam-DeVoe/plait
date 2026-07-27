---
name: metr-reinstall
description: Opt this worktree into the METR study by copying the study tooling back from the canonical clone.
disable-model-invocation: true
---

Plait created this worktree with the METR study content stripped out, so
sessions here don't run through the study gateway. This skill opts the
worktree back in. Do the following:

1. Copy the study tooling from the canonical clone:
   ```
   cp -R "{repo_path}/.claude/ccmetr" .claude/ccmetr
   for d in "{repo_path}"/.claude/skills/metr-*; do cp -R "$d" .claude/skills/; done
   ```

2. Merge the study keys from `{repo_path}/.claude/settings.json` into this
   worktree's `.claude/settings.json`, creating the file if it doesn't exist
   and preserving any keys already in it. The study keys are:
   - every `hooks` entry whose command contains `/ccmetr/`
   - the `statusLine` (its command also points into ccmetr)
   - the `Bash(.claude/ccmetr/bin/ccmetr *)` entry in `permissions.allow`

3. Do NOT copy `settings.local.json` or any study key/credential. Auth
   self-configures: on the first prompt after restart, the ccmetr client
   writes the gateway URL and study key into this worktree's
   `settings.local.json` (from the METR_AUTH_TOKEN stash in the user's
   `~/.claude/settings.json`).

4. Tell the user to restart Claude Code in this worktree to load the study
   settings. Also note: this session and any earlier sessions in this
   worktree never went through the study gateway, so they won't appear in
   the study's transcripts.

<!-- worktop-only -->
5. Disable plait's automatic tend sessions for this worktop, so plait's
   background automation doesn't run through the study gateway:
   ```
   curl -s -X POST {base_url}/worktops/{worktop_id}/tends-enabled \
     -H 'Content-Type: application/json' -d '{"enabled": false}'
   ```
   (Manual tends triggered from the plait UI still work.)
