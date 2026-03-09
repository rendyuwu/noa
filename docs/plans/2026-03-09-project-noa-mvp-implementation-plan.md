# Project NOA MVP Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Ship a monorepo with a ChatGPT-like UI (assistant-ui) backed by a FastAPI agent server that authenticates via LDAP, supports multi-user + admin RBAC, persists threads/messages, and enforces explicit approval for any CHANGE tool.

**Architecture:** Next.js frontend uses assistant-ui with (1) `unstable_useRemoteThreadListRuntime` for multi-thread persistence and (2) `useAssistantTransportRuntime` per-thread to stream state from a Python backend. The backend exposes auth/admin/thread endpoints + a `/assistant` Assistant Transport endpoint that streams state via `assistant-stream`, runs READ tools directly, and gates CHANGE tools behind a persisted two-phase approval.

**Tech Stack:** Next.js + TypeScript + assistant-ui; Python 3.11+ + FastAPI + assistant-stream; Postgres + SQLAlchemy async + Alembic; LDAP (python-ldap) + JWT.

---

## Conventions

- Backend package name: `noa_api`
- Backend ports (dev): api `:8000`, web `:3000`, postgres `:5432`
- Risk: `READ` vs `CHANGE`
- Approval gate: persisted `action_requests` + custom Assistant Transport commands (`approve-action`, `deny-action`).

## Task 1: Scaffold monorepo skeleton

**Files:**
- Create: `README.md`
- Create: `docker-compose.yml`
- Create: `apps/web/` (Next.js)
- Create: `apps/api/` (FastAPI)

**Step 1: Add root README**

Create `README.md`:

```md
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
```

**Step 2: Add docker-compose**

Create `docker-compose.yml`:

```yml
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_USER: noa
      POSTGRES_PASSWORD: noa
      POSTGRES_DB: noa
    ports:
      - "5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U noa -d noa"]
      interval: 5s
      timeout: 3s
      retries: 30
    volumes:
      - noa_pgdata:/var/lib/postgresql/data

volumes:
  noa_pgdata:
```

**Step 3: Commit**

Run:

```bash
git add README.md docker-compose.yml
git commit -m "chore: add monorepo readme and dev postgres"
```

## Task 2: Scaffold `apps/api` Python project

**Files:**
- Create: `apps/api/pyproject.toml`
- Create: `apps/api/src/noa_api/main.py`
- Create: `apps/api/src/noa_api/core/config.py`
- Create: `apps/api/src/noa_api/core/logging.py`
- Create: `apps/api/src/noa_api/api/router.py`
- Create: `apps/api/src/noa_api/api/routes/health.py`

**Step 1: Create `apps/api/pyproject.toml`**

```toml
[project]
name = "noa-api"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
  "fastapi>=0.115.0",
  "uvicorn[standard]>=0.30.0",
  "pydantic>=2.9.0",
  "pydantic-settings>=2.5.0",
  "structlog>=24.4.0",
  "orjson>=3.10.0",
]

[project.optional-dependencies]
dev = [
  "pytest>=8.0.0",
  "pytest-asyncio>=0.24.0",
  "httpx>=0.27.0",
  "ruff>=0.6.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/noa_api"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

**Step 2: Minimal FastAPI app + router**

Create `apps/api/src/noa_api/main.py`:

```py
from fastapi import FastAPI

from noa_api.api.router import api_router
from noa_api.core.logging import configure_logging


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title="Project NOA API")
    app.include_router(api_router)
    return app


app = create_app()
```

Create `apps/api/src/noa_api/api/router.py`:

```py
from fastapi import APIRouter

from noa_api.api.routes.health import router as health_router

api_router = APIRouter()
api_router.include_router(health_router, tags=["health"])
```

Create `apps/api/src/noa_api/api/routes/health.py`:

```py
from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
```

**Step 3: Logging + config skeleton**

Create `apps/api/src/noa_api/core/logging.py`:

```py
import logging

import structlog


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO)
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )
```

Create `apps/api/src/noa_api/core/config.py`:

```py
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Filled later (DB, LDAP, JWT, etc.)
    environment: str = "development"


settings = Settings()
```

**Step 4: Add a basic test**

Create `apps/api/tests/test_health.py`:

```py
from httpx import AsyncClient

from noa_api.main import create_app


async def test_health_ok() -> None:
    app = create_app()
    async with AsyncClient(app=app, base_url="http://test") as client:
        r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
```

**Step 5: Run tests**

Run:

```bash
cd apps/api
uv sync
uv run pytest -q
```

Expected: PASS.

**Step 6: Commit**

```bash
git add apps/api
git commit -m "feat(api): scaffold fastapi app with health endpoint"
```

## Task 3: Add Postgres + Alembic + core DB models (users/roles/audit + threads/messages)

**Files:**
- Modify: `apps/api/pyproject.toml`
- Create: `apps/api/alembic.ini`
- Create: `apps/api/alembic/env.py`
- Create: `apps/api/alembic/versions/0001_init.py`
- Create: `apps/api/src/noa_api/storage/postgres/base.py`
- Create: `apps/api/src/noa_api/storage/postgres/client.py`
- Create: `apps/api/src/noa_api/storage/postgres/models.py`

**Step 1: Add dependencies**

Update `apps/api/pyproject.toml` dependencies:

```toml
"sqlalchemy[asyncio]>=2.0.0",
"asyncpg>=0.29.0",
"alembic>=1.13.0",
```

**Step 2: Add DB settings**

Update `apps/api/src/noa_api/core/config.py`:

```py
from pydantic import PostgresDsn


class Settings(BaseSettings):
    ...
    postgres_url: PostgresDsn
```

**Step 3: SQLAlchemy base + client**

Create `apps/api/src/noa_api/storage/postgres/base.py`:

```py
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
```

Create `apps/api/src/noa_api/storage/postgres/client.py`:

```py
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from noa_api.core.config import settings


def create_engine() -> AsyncEngine:
    return create_async_engine(str(settings.postgres_url), pool_pre_ping=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
```

**Step 4: Core models**

Create `apps/api/src/noa_api/storage/postgres/models.py` (single-file for MVP; can split later):

```py
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from noa_api.storage.postgres.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    ldap_dn: Mapped[str | None] = mapped_column(Text, nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)


class UserRole(Base):
    __tablename__ = "user_roles"
    __table_args__ = (UniqueConstraint("user_id", "role_id", name="uq_user_roles_user_id_role_id"),)

    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)


class RoleToolPermission(Base):
    __tablename__ = "role_tool_permissions"
    __table_args__ = (UniqueConstraint("role_id", "tool_name", name="uq_role_tool_permissions_role_id_tool"),)

    role_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)
    tool_name: Mapped[str] = mapped_column(String(200), nullable=False, index=True, primary_key=True)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    event_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    user_email: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    tool_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    meta_data: Mapped[dict[str, object]] = mapped_column("metadata", JSONB, nullable=False, server_default="'{}'::jsonb")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)


class Thread(Base):
    __tablename__ = "threads"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    owner_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    thread_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("threads.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)


Index("idx_audit_log_event_type", AuditLog.event_type)
Index("idx_audit_log_created_at", AuditLog.created_at)
```

**Step 5: Alembic migration**

Create `apps/api/alembic.ini` and `apps/api/alembic/env.py` with async SQLAlchemy engine; generate `0001_init.py` to create the tables above.

**Step 6: Run migration**

Run:

```bash
cd apps/api
uv run alembic upgrade head
```

**Step 7: Commit**

```bash
git add apps/api
git commit -m "feat(api): add postgres models and initial migration"
```

## Task 4: LDAP auth + JWT + pending approval flow

**Files:**
- Modify: `apps/api/pyproject.toml`
- Modify: `apps/api/src/noa_api/core/config.py`
- Create: `apps/api/src/noa_api/core/auth/errors.py`
- Create: `apps/api/src/noa_api/core/auth/ldap_service.py`
- Create: `apps/api/src/noa_api/core/auth/jwt_service.py`
- Create: `apps/api/src/noa_api/core/auth/auth_service.py`
- Create: `apps/api/src/noa_api/core/auth/deps.py`
- Create: `apps/api/src/noa_api/api/routes/auth.py`
- Modify: `apps/api/src/noa_api/api/router.py`
- Test: `apps/api/tests/test_auth_login.py`

**Step 1: Add deps**

Update `apps/api/pyproject.toml`:

```toml
"python-ldap>=3.4.4",
"PyJWT>=2.9.0",
```

**Step 2: Settings**

Extend `apps/api/src/noa_api/core/config.py` with LDAP + JWT fields:

```py
from pydantic import SecretStr

class Settings(BaseSettings):
    ...
    ldap_host: str
    ldap_port: int = 389
    ldap_username: str
    ldap_password: SecretStr
    ldap_base_dn: str
    ldap_timeout: int = 5
    ldap_ssl: bool = False
    ldap_tls: bool = False
    ldap_logging: bool = False

    auth_jwt_secret: SecretStr
    auth_jwt_algorithm: str = "HS256"
    auth_jwt_access_token_ttl_seconds: int = 3600

    bootstrap_admin_emails: list[str] = []
```

**Step 3: Port LDAP service (from `noa-old`)**

Create `apps/api/src/noa_api/core/auth/ldap_service.py` by adapting:
- `noa-old/src/core/auth/ldap_service.py`

**Step 4: JWT service**

Create `apps/api/src/noa_api/core/auth/jwt_service.py`:

```py
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt

from noa_api.core.config import settings


class JWTService:
    def create_access_token(self, email: str, user_id: UUID) -> tuple[str, int]:
        ttl = int(settings.auth_jwt_access_token_ttl_seconds)
        now = datetime.now(UTC)
        payload = {
            "sub": email,
            "uid": str(user_id),
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(seconds=ttl)).timestamp()),
        }
        token = jwt.encode(payload, settings.auth_jwt_secret.get_secret_value(), algorithm=settings.auth_jwt_algorithm)
        return token, ttl

    def decode(self, token: str) -> dict[str, object]:
        return jwt.decode(token, settings.auth_jwt_secret.get_secret_value(), algorithms=[settings.auth_jwt_algorithm])
```

**Step 5: Auth service (pending approval + bootstrap admin)**

Implement `apps/api/src/noa_api/core/auth/auth_service.py` similar to:
- `noa-old/src/core/auth/auth_service.py`

**Step 6: FastAPI auth routes**

Create `apps/api/src/noa_api/api/routes/auth.py`:
- `POST /auth/login` (LDAP bind; create user if missing; set active if bootstrap; return JWT)
- `GET /auth/me` (return user + effective permissions)

**Step 7: Tests**

Write `apps/api/tests/test_auth_login.py` with monkeypatched LDAPService to:
- return a fake LDAP user
- validate pending approval behavior (inactive users rejected)
- validate bootstrap admin is active

**Step 8: Commit**

```bash
git add apps/api
git commit -m "feat(api): ldap login with jwt and pending approval"
```

## Task 5: RBAC enforcement + admin endpoints (enable/disable users + tool allowlists)

**Files:**
- Create: `apps/api/src/noa_api/core/auth/authorization.py`
- Create: `apps/api/src/noa_api/api/routes/admin.py`
- Modify: `apps/api/src/noa_api/api/router.py`
- Test: `apps/api/tests/test_rbac.py`

**Step 1: Implement AuthorizationService**

Port/adapt logic from:
- `noa-old/src/core/auth/authorization.py`

Rules:
- Admin role bypasses tool checks.
- Disabled users have zero permissions.

**Step 2: Admin routes**

Create `apps/api/src/noa_api/api/routes/admin.py`:
- `GET /admin/users`
- `PATCH /admin/users/{id}`: `{ is_active: bool }`
- `GET /admin/tools`: return known tools from tool registry
- `PUT /admin/users/{id}/tools`: `{ tools: string[] }` (implemented via per-user role)

**Step 3: Tests**

`apps/api/tests/test_rbac.py`:
- non-admin cannot call admin routes
- admin can enable/disable
- tool allowlist changes apply immediately

**Step 4: Commit**

```bash
git add apps/api
git commit -m "feat(api): rbac and admin user/tool management"
```

## Task 6: Threads API for assistant-ui Remote Thread List

**Files:**
- Create: `apps/api/src/noa_api/api/routes/threads.py`
- Modify: `apps/api/src/noa_api/api/router.py`
- Test: `apps/api/tests/test_threads.py`

**Step 1: Implement thread CRUD endpoints**

Endpoints (owner-scoped):
- `GET /threads`
- `POST /threads`
- `PATCH /threads/{id}`
- `POST /threads/{id}/archive`
- `POST /threads/{id}/unarchive`
- `DELETE /threads/{id}`
- `GET /threads/{id}` (for adapter `fetch`)

**Step 2: Tests**

`apps/api/tests/test_threads.py` should validate:
- users only see own threads
- archive/delete work

**Step 3: Commit**

```bash
git add apps/api
git commit -m "feat(api): thread list endpoints for assistant-ui"
```

## Task 7: Assistant Transport `/assistant` endpoint (streaming state)

**Files:**
- Modify: `apps/api/pyproject.toml`
- Create: `apps/api/src/noa_api/api/routes/assistant.py`
- Modify: `apps/api/src/noa_api/api/router.py`

**Step 1: Add dependency**

Add python package:
- `assistant-stream` (imported as `assistant_stream`)

**Step 2: Add endpoint skeleton**

Create `apps/api/src/noa_api/api/routes/assistant.py` implementing the contract described in assistant-ui docs `runtimes/assistant-transport`:
- Accept: `{ state, commands, system?, tools?, threadId }`
- Return: streaming response via `assistant_stream.serialization.DataStreamResponse`

The callback should:
- Load canonical thread messages from DB (ignore client state for authority)
- Apply commands:
  - `add-message`
  - custom: `approve-action`, `deny-action`
- Update `controller.state` to include:
  - `messages`: array of messages
  - `isRunning`: bool

**Step 3: Commit**

```bash
git add apps/api
git commit -m "feat(api): assistant transport endpoint skeleton"
```

## Task 8: Tool registry + demo tools + approval-gated change tool

**Files:**
- Create: `apps/api/src/noa_api/core/tools/registry.py`
- Create: `apps/api/src/noa_api/core/tools/demo_tools.py`
- Create: `apps/api/src/noa_api/storage/postgres/action_models.py` (or extend models)
- Create: `apps/api/src/noa_api/storage/postgres/tool_run_models.py` (or extend models)

**Step 1: Add models**

Add `action_requests` + `tool_runs` tables (new migration `0002_actions_tools.py`).

**Step 2: Registry**

`apps/api/src/noa_api/core/tools/registry.py`:
- defines `ToolRisk = Literal["READ", "CHANGE"]`
- tool definitions include name/description/risk/execute
- registry can list all tools for admin UI

**Step 3: Demo tools**

`apps/api/src/noa_api/core/tools/demo_tools.py`:
- READ: `get_current_time`, `get_current_date`
- CHANGE: `set_demo_flag` (writes `{ key, value }` into DB)

**Step 4: Commit**

```bash
git add apps/api
git commit -m "feat(api): tool registry with read/change demo tools"
```

## Task 9: Agent loop (LLM + tool calling + approval gating)

**Files:**
- Modify: `apps/api/pyproject.toml` (OpenAI-compatible client)
- Create: `apps/api/src/noa_api/core/agent/runner.py`
- Modify: `apps/api/src/noa_api/api/routes/assistant.py`

**Step 1: Add LLM dependency**

Add one of:
- `openai` Python SDK (configure base_url + api_key)
- or `litellm` (multi-provider)

**Step 2: Implement runner**

`apps/api/src/noa_api/core/agent/runner.py`:
- Input: thread messages + available tools (filtered by RBAC)
- Output: appended messages + tool call records
- Rules:
  - READ tools execute immediately; results appended.
  - CHANGE tools create `action_request` and append an approval card message; do not execute.

**Step 3: Wire into `/assistant`**

In `apps/api/src/noa_api/api/routes/assistant.py`:
- After applying `add-message`, call runner.
- Stream assistant text via `append-text` updates.

**Step 4: Commit**

```bash
git add apps/api
git commit -m "feat(api): llm runner with tool calling and approval gating"
```

## Task 10: `apps/web` scaffold with assistant-ui + remote thread list + auth

**Files:**
- Create: `apps/web` (Next.js app)
- Create: `apps/web/app/login/page.tsx`
- Create: `apps/web/app/(app)/assistant/page.tsx`
- Create: `apps/web/app/(admin)/admin/page.tsx`

**Step 1: Scaffold Next.js + assistant-ui components**

Use assistant-ui CLI as a starting point, then adapt:
- `with-assistant-transport` (Assistant Transport runtime)
- `custom-thread-list` docs (Remote Thread List adapter)

**Step 2: Auth client**

Implement a minimal auth store:
- store JWT in `localStorage`
- `fetchWithAuth()` helper adds `Authorization: Bearer ...`

**Step 3: Remote thread list runtime**

Implement adapter methods calling backend endpoints:
- list/initialize/rename/archive/unarchive/delete/fetch/generateTitle

Per-thread runtime hook:
- `useAssistantTransportRuntime({ api: NEXT_PUBLIC_API_URL + "/assistant", headers: () => ({Authorization}) })`

**Step 4: Chat page**

Use assistant-ui components:
- `ThreadListSidebar` + `Thread`
- `ToolGroup` + `ToolFallback`

**Step 5: Approval UI**

Register a tool UI for the approval pseudo-tool (e.g. `request_approval`) that renders Approve/Deny buttons and sends custom commands to backend.

**Step 6: Admin UI**

Basic admin page:
- list users
- toggle active
- edit allowed tools

**Step 7: Commit**

```bash
git add apps/web
git commit -m "feat(web): assistant-ui app with auth, threads, approvals, admin"
```

## Task 11: End-to-end verification

**Step 1: Run DB**

`docker compose up -d postgres`

**Step 2: Run migrations**

`cd apps/api && uv run alembic upgrade head`

**Step 3: Run API + Web**

- API: `uv run uvicorn noa_api.main:app --reload --port 8000`
- Web: `npm run dev`

**Step 4: Manual smoke**

- Login as bootstrap admin (via `BOOTSTRAP_ADMIN_EMAILS`)
- Create thread, ask “what time is it” -> READ tool
- Ask “set demo flag x=y” -> approval request appears
- Click Approve -> tool executes and result appears
- Disable user in admin -> subsequent tool calls denied

---

Plan complete.
