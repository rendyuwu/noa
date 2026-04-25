# Contributing to Project NOA

Thank you for your interest in contributing to Project NOA. This guide covers everything you need to get started.

## Prerequisites

- Docker + Docker Compose
- Python 3.11+ and [uv](https://docs.astral.sh/uv/)
- Node.js 20+ and npm
- Git

OS packages for `python-ldap` (Ubuntu):

```bash
sudo apt-get install -y libldap2-dev libsasl2-dev libssl-dev
```

## Development Setup

### 1. Fork and clone

```bash
git clone https://github.com/<your-username>/noa.git
cd noa
```

### 2. Start Postgres

```bash
docker compose up -d postgres
```

### 3. Configure environment

```bash
cp .env.example .env
```

Edit `.env` with your local settings. See `README.md` for required variables.

### 4. Run the API

```bash
cd apps/api
uv sync
uv run alembic upgrade head
uv run uvicorn noa_api.main:app --reload --port 8000
```

### 5. Run the web app

```bash
cd apps/web
npm install
npm run dev
```

Open http://localhost:3000.

## Branch Naming

Use type-prefixed branches:

| Prefix | Purpose |
|--------|---------|
| `feat/` | New feature |
| `fix/` | Bug fix |
| `docs/` | Documentation only |
| `refactor/` | Code refactoring (no behavior change) |
| `test/` | Adding or updating tests |
| `chore/` | Build, CI, tooling changes |

Examples: `feat/proxmox-snapshot-tool`, `fix/thread-archive-404`, `docs/update-whm-integration`

## Commit Messages

We use [Conventional Commits](https://www.conventionalcommits.org/).

```
<type>(<scope>): <subject>
```

### Types

`feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`, `perf`, `ci`, `revert`

### Scopes

`ui`, `auth`, `api`, `db`, `agent`, `whm`, `proxmox`, `admin`, `ci`

### Rules

- Imperative mood: "add feature" not "added feature"
- Lowercase first letter
- No period at end
- Subject line max 50 characters

### Examples

```
feat(whm): add firewall batch unblock tool
fix(auth): handle expired JWT in cookie refresh
docs(api): update assistant transport endpoint spec
refactor(agent): extract tool execution into separate module
test(proxmox): add VM NIC toggle integration tests
chore(ci): add web lint and typecheck to CI
```

## Pull Request Process

1. Create a feature branch from `master`
2. Make your changes
3. Ensure all checks pass locally:

**API:**
```bash
cd apps/api
uv run ruff check
uv run pytest -q
```

**Web:**
```bash
cd apps/web
npm run lint
npm run typecheck
npm run test
```

4. Push your branch and open a PR
5. Fill out the PR template completely
6. At least one approving review is required
7. Squash and merge after approval
8. Delete the branch after merge

## Code Style

### Python (apps/api)

- Linter: [ruff](https://docs.astral.sh/ruff/)
- Type hints required for function signatures
- Async-first (SQLAlchemy async, asyncio)
- Parameterized queries only (no raw SQL)

### TypeScript (apps/web)

- Linter: ESLint (flat config in `eslint.config.mjs`)
- TypeScript strict mode
- 2-space indentation, single quotes, semicolons
- `@/*` path aliases for imports
- Named exports preferred
- Server Components by default (Next.js App Router)

### Design System

UI changes must follow `DESIGN.md`:

- Grayscale palette with warm undertones (no chromatic colors except blue focus ring)
- Border radius: 12px or 9999px (pill)
- Zero shadows (ring-based elevation)
- Font weights: 400 or 500 only

## Testing

### API

- Framework: pytest + pytest-asyncio
- Location: `apps/api/tests/`
- Run: `uv run pytest -q`
- New features need happy-path + edge-case tests
- Bug fixes need regression tests

### Web

- Framework: Vitest + Testing Library
- Location: `apps/web/src/**/__tests__/`
- Run: `npm run test`
- New features need happy-path + edge-case tests
- Bug fixes need regression tests

## Issue Guidelines

### Bug Reports

Use the [bug report template](.github/ISSUE_TEMPLATE/bug_report.md). Include:
- Steps to reproduce
- Expected vs actual behavior
- Environment details

### Feature Requests

Use the [feature request template](.github/ISSUE_TEMPLATE/feature_request.md). Include:
- Problem statement
- Proposed solution
- Design considerations (if UI)

### Labels

| Label | Purpose |
|-------|---------|
| `bug` | Something isn't working |
| `feature` | New feature request |
| `docs` | Documentation improvement |
| `good first issue` | Good for newcomers |
| `help wanted` | Extra attention needed |
| `duplicate` | Duplicate issue |
| `wontfix` | Will not be addressed |
| `breaking` | Breaking change |
| `security` | Security-related |

## Security

Do not open public issues for security vulnerabilities. See [SECURITY.md](.github/SECURITY.md) for reporting instructions.

Key security rules:
- Never commit secrets or credentials
- Auth middleware changes require extra review
- Input sanitization must not be weakened
- Use parameterized queries only (SQLAlchemy / Prisma)
- SSRF protection must not be bypassed
- CHANGE tools must always require `reason` parameter
