server *args:
    .venv/bin/uvicorn server.api:app {{ args }} --port 8000

web:
    cd web && npm run dev

serve:
    trap 'kill 0' EXIT; just server & p1=$!; just web & p2=$!; while kill -0 $p1 2>/dev/null && kill -0 $p2 2>/dev/null; do sleep 10; done; kill 0

dev:
    trap 'kill 0' EXIT; just server --reload --reload-dir server --reload-include prompts.toml & just web & wait

test *args:
    uv run pytest tests/ -n auto {{ args }}

format:
    uv run shed

install:
    uv sync
    cd web && npm install
