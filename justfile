server:
    .venv/bin/uvicorn server.api:app --reload --port 8000

web:
    cd web && npm run dev

serve:
    trap 'kill 0' EXIT; just server & just web & wait

test *args:
    uv run pytest tests/ -n auto {{ args }}

format:
    uv run shed

install:
    uv sync
    cd web && npm install
