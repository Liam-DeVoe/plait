# Run the backend server
server:
    cd server && uv run uvicorn server.api:app --reload --port 8000

# Run the frontend dev server
web:
    cd web && npm run dev

# Run both (in separate terminals)
dev:
    @echo "Run 'just server' and 'just web' in separate terminals"

# Install all dependencies
install:
    uv sync
    cd web && npm install
