"""METR study stripping and the /metr-repopulate opt-in skill.

A repo flagged `metr` has METR's ccmetr study tooling in its canonical
clone's .claude/. Worktrees plait creates for it must come up metr-free
(no study hooks, no gateway routing), with a /metr-repopulate skill to
opt back in per-worktree.
"""

import json
from pathlib import Path

from server import claude, config, git, metr

# --- is_metr_path ---


def test_is_metr_path_matches_study_content():
    assert metr.is_metr_path(".claude/ccmetr/bin/hook")
    assert metr.is_metr_path(".claude/ccmetr/.install-manifest.json")
    assert metr.is_metr_path(".claude/skills/metr-start/SKILL.md")
    assert metr.is_metr_path(".claude/settings.local.json.bak")


def test_is_metr_path_leaves_other_files_alone():
    assert not metr.is_metr_path(".claude/skills/porting/SKILL.md")
    assert not metr.is_metr_path(".claude/settings.json")
    assert not metr.is_metr_path(".claude/settings.local.json")
    assert not metr.is_metr_path(".claude/CLAUDE.md")
    assert not metr.is_metr_path(".claude")
    assert not metr.is_metr_path("src/ccmetr/thing.py")


# --- strip_settings ---


def _metr_settings_json() -> dict:
    """The settings.json METR's installer writes (study keys only)."""
    return {
        "hooks": {
            "UserPromptSubmit": [
                {
                    "matcher": "",
                    "hooks": [
                        {
                            "type": "command",
                            "command": "${CLAUDE_PROJECT_DIR}/.claude/ccmetr/bin/hook",
                        }
                    ],
                }
            ],
            "SessionStart": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": "${CLAUDE_PROJECT_DIR}/.claude/ccmetr/bin/session-start",
                        }
                    ]
                }
            ],
        },
        "statusLine": {
            "type": "command",
            "command": "${CLAUDE_PROJECT_DIR}/.claude/ccmetr/bin/statusline",
        },
        "permissions": {"allow": ["Bash(.claude/ccmetr/bin/ccmetr *)"]},
        "$schema": "https://json.schemastore.org/claude-code-settings.json",
    }


def test_strip_settings_pure_study_file_vanishes():
    assert metr.strip_settings(json.dumps(_metr_settings_json())) is None


def test_strip_settings_preserves_non_study_content():
    data = _metr_settings_json()
    data["hooks"]["UserPromptSubmit"][0]["hooks"].append(
        {"type": "command", "command": "echo mine"}
    )
    data["permissions"]["allow"].append("WebSearch")
    data["env"] = {
        "ANTHROPIC_BASE_URL": "https://study.example.com",
        "ANTHROPIC_AUTH_TOKEN": "secret",
        "MY_VAR": "keep",
    }

    out = json.loads(metr.strip_settings(json.dumps(data)))

    assert out["hooks"]["UserPromptSubmit"][0]["hooks"] == [
        {"type": "command", "command": "echo mine"}
    ]
    # SessionStart held only the study hook, so the whole event is gone.
    assert "SessionStart" not in out["hooks"]
    assert "statusLine" not in out
    assert out["permissions"]["allow"] == ["WebSearch"]
    assert out["env"] == {"MY_VAR": "keep"}


def test_strip_settings_unparseable_returned_unchanged():
    assert metr.strip_settings("{oops") == "{oops"
    assert metr.strip_settings("[1, 2]") == "[1, 2]"


# --- copy_local_files stripping ---


def _seed_study_clone(git_env):
    """Lay out a canonical clone the way METR's installer leaves it."""
    claude_dir = git_env.clone / ".claude"
    (claude_dir / "ccmetr" / "bin").mkdir(parents=True)
    (claude_dir / "ccmetr" / "bin" / "hook").write_text("#!/bin/sh\n")
    (claude_dir / "skills" / "metr-start").mkdir(parents=True)
    (claude_dir / "skills" / "metr-start" / "SKILL.md").write_text("start")
    (claude_dir / "skills" / "porting").mkdir(parents=True)
    (claude_dir / "skills" / "porting" / "SKILL.md").write_text("porting")
    (claude_dir / "settings.json").write_text(json.dumps(_metr_settings_json()))
    (claude_dir / "settings.local.json").write_text(
        json.dumps(
            {
                "permissions": {"allow": ["WebSearch"]},
                "env": {
                    "ANTHROPIC_BASE_URL": "https://study.example.com",
                    "ANTHROPIC_AUTH_TOKEN": "secret",
                },
            }
        )
    )
    (claude_dir / "settings.local.json.bak").write_text("{}")


async def test_copy_local_files_strips_study_content_for_metr_repo(
    git_env, tmp_path
):
    _seed_study_clone(git_env)
    config.get_repo(git_env.repo_id).metr = True

    dest = tmp_path / "dest"
    await git.copy_local_files(git_env.repo_id, dest)

    # Study tooling and skills are gone; other .claude content survives.
    assert not (dest / ".claude" / "ccmetr").exists()
    assert not (dest / ".claude" / "skills" / "metr-start").exists()
    assert not (dest / ".claude" / "settings.local.json.bak").exists()
    assert (dest / ".claude" / "skills" / "porting" / "SKILL.md").exists()
    # settings.json was pure study content → not written at all.
    assert not (dest / ".claude" / "settings.json").exists()
    # settings.local.json keeps the user's permissions, loses the gateway env.
    local = json.loads((dest / ".claude" / "settings.local.json").read_text())
    assert local == {"permissions": {"allow": ["WebSearch"]}}


async def test_copy_local_files_copies_study_content_for_non_metr_repo(
    git_env, tmp_path
):
    _seed_study_clone(git_env)

    dest = tmp_path / "dest"
    await git.copy_local_files(git_env.repo_id, dest)

    assert (dest / ".claude" / "ccmetr" / "bin" / "hook").exists()
    assert (dest / ".claude" / "skills" / "metr-start" / "SKILL.md").exists()
    settings = json.loads((dest / ".claude" / "settings.json").read_text())
    assert "statusLine" in settings


# --- /metr-repopulate skill install ---


async def test_metr_repo_worktop_gets_repopulate_skill(git_env):
    _seed_study_clone(git_env)
    config.get_repo(git_env.repo_id).metr = True

    wt = Path(await git.create_worktree(git_env.repo_id, "metr-branch", "wt-metr"))
    await claude.write_worktop_claude_md(str(wt), "wt-metr", git_env.repo_id)

    skill = wt / ".claude" / "skills" / "metr-repopulate" / "SKILL.md"
    text = skill.read_text()
    # Placeholders are rendered: the worktop id, plait's URL, and the
    # canonical clone path all appear literally.
    assert "wt-metr" in text
    assert f"http://localhost:{claude.PLAIT_PORT}" in text
    assert str(git_env.clone) in text
    assert "{worktop_id}" not in text
    assert "{base_url}" not in text
    assert "{repo_path}" not in text
    # The JSON braces in the curl example survive the substitution.
    assert '{"enabled": false}' in text


async def test_non_metr_repo_worktop_has_no_repopulate_skill(git_env):
    wt = Path(await git.create_worktree(git_env.repo_id, "plain-branch", "wt-plain"))
    await claude.write_worktop_claude_md(str(wt), "wt-plain", git_env.repo_id)

    assert not (wt / ".claude" / "skills" / "metr-repopulate").exists()
