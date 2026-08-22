# NOA API image — FastAPI admin routes + the FastMCP server (§T.60, C12).
#
# Build context is the REPO ROOT, not this directory: `noa-api` is a uv workspace member and
# `noa-core` is the workspace root (`core/` + the root `pyproject.toml` + `uv.lock`), so an
# `apps/api`-scoped context cannot resolve its own dependency.
#
#   docker build -f apps/api/Dockerfile -t noa-api .
#
# Two stages exist for one reason: `python-ldap==3.4.7` publishes an sdist and no wheel
# (uv.lock records `sdist = ...` with no `wheels` list), so it is compiled here against
# OpenLDAP headers. Shipping those headers and a C compiler in the runtime image would be
# build tooling on a production host for no benefit.

# `-bookworm` is pinned rather than left as plain `-slim`: the apt package names below
# (`libldap-2.5-0`) are Debian-release-specific, so an unpinned base would move the runtime
# libraries out from under the wheel built in the first stage.
#
# 3.12 is the top of the supported range, not a preference. C1 holds `>=3.11,<3.13` because
# `pgpy==0.6.0` imports the stdlib `imghdr` module, removed in 3.13, at import time — which
# takes down the whole tool registry, not just the password-reset path.
# `apps/api/tests/test_deployment.py` asserts this tag against `requires-python`, so a bump
# past the bound fails a test rather than an operator's first CHANGE call.
FROM python:3.12-slim-bookworm AS builder

# uv is pinned like every other dependency (C2's discipline applied to tooling): the resolver
# that reads `uv.lock` is part of what makes `--frozen` reproducible.
COPY --from=ghcr.io/astral-sh/uv:0.10.9 /uv /usr/local/bin/uv

ENV UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        gcc \
        libldap2-dev \
        libsasl2-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependency layer first, so editing application source does not recompile python-ldap.
# `--no-install-workspace` resolves and installs the locked dependency set while skipping the
# workspace members themselves, which are not in the context yet.
COPY pyproject.toml uv.lock ./
COPY apps/api/pyproject.toml apps/api/pyproject.toml
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --no-install-workspace --package noa-api

COPY core/ core/
COPY apps/api/ apps/api/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --package noa-api


FROM python:3.12-slim-bookworm AS runtime

# Runtime halves of the two -dev packages above. `libsasl2-modules` carries the SASL mechanism
# plugins: without it a bind that negotiates anything other than simple auth fails at connect
# time with a mechanism error, which reads like an LDAP outage rather than a missing package.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libldap-2.5-0 \
        libsasl2-2 \
        libsasl2-modules \
    && rm -rf /var/lib/apt/lists/*

# A fixed high uid so a mounted volume's ownership is predictable across hosts.
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin noa

WORKDIR /app

ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# `--no-editable` above means `core` and `noa_api` are real installs in the venv, so the only
# source that has to travel is Alembic's. It keeps its original depth (`/app/apps/api/alembic`)
# because `alembic/env.py` resolves the repo root as `parents[3]` to put `core` on `sys.path`.
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/apps/api/alembic /app/apps/api/alembic
COPY --from=builder /app/apps/api/alembic.ini /app/apps/api/alembic.ini

USER noa

EXPOSE 8000

# `/health` is liveness (V51): it answers with Postgres down on purpose, so a database outage is
# never reported as this process being dead. Readiness is deliberately not probed here — see
# `docs/deployment.md`.
HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"]

# ONE process, deliberately. No `--workers`, and one replica per deployment:
#   * V79 pins session-JWT verification to zero clock leeway because mint and verify share a
#     clock. A second replica makes that two clocks, and the first symptom is a login whose
#     token is rejected the instant it is issued.
#   * T38's approved-change executor is an in-process asyncio task, T39's expiry sweep and
#     T38's reaper are in-process loops, and T66's notification register is a per-process
#     WeakSet. N replicas means N sweepers and a register that knows a fraction of the sessions.
# Scaling out is a deliberate change with V79's revisit trigger attached, not a replica count.
#
# TLS and forwarded headers belong to the ingress. `--proxy-headers` is NOT set here: it is
# only safe alongside an explicit `--forwarded-allow-ips`, and a default that trusts the wrong
# hop lets a client dictate its own source address.
CMD ["uvicorn", "noa_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
