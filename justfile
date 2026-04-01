server:
    cd server && uv run uvicorn server.api:app --reload --port 8000

web:
    cd web && npm run dev

dev:
    @echo "Run 'just server' and 'just web' in separate terminals"

test *args:
    uv run pytest tests/ {{ args }}

format:
    uv run shed

install:
    uv sync
    cd web && npm install
