# Project NOA

AI operations workspace: chat UI + controlled tools.

## Dev Quickstart

### 1) Start Postgres

```bash
docker compose up -d postgres
```

### 2) Start API

```bash
cd apps/api
uv sync
uv run uvicorn noa_api.main:app --reload --port 8000
```

### 3) Start Web

```bash
cd apps/web
npm install
npm run dev
```

Open: http://localhost:3000
