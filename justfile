server *args:
    .venv/bin/uvicorn server.api:app {{ args }} --port 8000

web:
    cd web && npm run dev

serve:
    trap 'kill 0' EXIT; just server & just web & wait

dev:
    trap 'kill 0' EXIT; just server --reload --reload-dir server --reload-include prompts.toml & just web & wait

test *args:
    uv run pytest tests/ -n auto {{ args }}

format:
    uv run shed

install:
    uv sync
    cd web && npm install
