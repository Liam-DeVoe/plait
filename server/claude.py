from __future__ import annotations

from pathlib import Path

import tomllib

from server import config

ORRERY_PORT = 8000
PROMPTS_PATH = Path(__file__).parent.parent / "prompts.toml"


def _load_prompts() -> dict:
    return tomllib.loads(PROMPTS_PATH.read_text())


def orrery_system_prompt(cell_id: str) -> str:
    """Generate the system prompt that tells Claude about Orrery hooks."""
    base_url = f"http://localhost:{ORRERY_PORT}"
    prompts = _load_prompts()
    return (
        prompts["cell_system"]["template"]
        .strip()
        .format(cell_id=cell_id, base_url=base_url)
    )


def sortie_system_prompt(
    sortie_id: str,
    exploration_dir: str,
    repo_paths: dict[str, str],
) -> str:
    """Generate the system prompt for a sortie orchestrator session."""
    base_url = f"http://localhost:{ORRERY_PORT}"
    repo_list = "\n".join(f"  - {rid}: {path}" for rid, path in repo_paths.items())
    prompts = _load_prompts()
    return (
        prompts["sortie_system"]["template"]
        .strip()
        .format(
            sortie_id=sortie_id,
            base_url=base_url,
            exploration_dir=exploration_dir,
            repo_list=repo_list,
        )
    )


def tend_prompt(branch: str) -> str:
    """Build a prompt for the tend session."""
    prompts = _load_prompts()
    author = config.get_author()
    return prompts["tend"]["template"].strip().format(branch=branch, author=author)
