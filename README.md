# Project NOA

AI operations workspace: chat UI + controlled tools.

## Prerequisites

- Docker + Docker Compose
- Python 3.11+ and `uv` (for API; added in later tasks)
- Node.js 20+ and npm (for web; added in later tasks)

## Dev Quickstart

### Current branch state (Task 1)

Only Postgres is available right now. The `apps/api` and `apps/web` projects are added in later tasks.

```bash
docker compose up -d postgres
```

### Intended workflow once API and web are scaffolded

Run API and web in separate terminals.

#### Terminal 1: API

```bash
cd apps/api
uv sync
uv run uvicorn noa_api.main:app --reload --port 8000
```

#### Terminal 2: Web

```bash
cd apps/web
npm install
npm run dev
```

Open: http://localhost:3000
