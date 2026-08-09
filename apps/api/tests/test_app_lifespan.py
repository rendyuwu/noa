"""App startup guards (T8, T3).

T8 requires the long-lived services be constructed once, in the lifespan. The reason is
specific rather than stylistic: `JWTService.__init__` allowlists the JWT algorithm and
checks the RFC 7518 minimum key length, so a per-request build turns a configuration
error into a 500 on somebody's first login instead of a refusal to start. These tests
pin both halves — built once, and a bad config kills the boot.

`/auth/logout` is the probe throughout: it depends on `JWTService` and on nothing else,
so it exercises the state wiring without needing a database.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncEngine

from core.approvals.execution_host import AsyncioApprovedChangeExecutor
from core.approvals.expiry import PendingExpirySweeper
from core.approvals.reaper import StrandedRunReaper
from core.auth.errors import AuthConfigurationError
from core.auth.jwt_service import JWTService
from core.config import Settings
from noa_api import main
from noa_api.api.deps import STATE_JWT_SERVICE
from noa_api.api.routes.auth import router as auth_router
from support.auth import build_settings

# Port 1 is privileged and nothing listens there, so the DSN is well-formed and
# unreachable — which is exactly the shape needed to prove the engine is lazy.
UNREACHABLE_POSTGRES_URL = "postgresql+asyncpg://noa:noa@127.0.0.1:1/noa"


@pytest.fixture
def pinned_settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Make the lifespan read explicit settings, not the developer's `.env`."""
    settings = build_settings()
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    return settings


def spy_on_jwt_service(monkeypatch: pytest.MonkeyPatch) -> list[JWTService]:
    """Record every `JWTService` the app builds."""
    built: list[JWTService] = []
    real = main.JWTService

    def factory(settings: Settings) -> JWTService:
        service = real(settings)
        built.append(service)
        return service

    monkeypatch.setattr(main, "JWTService", factory)
    return built


# --- Built once (T8) ---


def test_jwt_service_built_once_at_startup(
    monkeypatch: pytest.MonkeyPatch, pinned_settings: Settings
) -> None:
    """One construction for the whole process, however many requests arrive."""
    built = spy_on_jwt_service(monkeypatch)
    app = main.create_app()

    with TestClient(app) as client:
        assert len(built) == 1

        client.post("/auth/logout")
        client.post("/auth/logout")

        assert len(built) == 1
        assert getattr(app.state, STATE_JWT_SERVICE) is built[0]


def test_bad_jwt_algorithm_fails_at_startup_not_on_first_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A config error must be a boot failure, ⊥ a 500 on an operator's first login.

    `AUTH_JWT_ALGORITHM=none` would otherwise mint unsigned tokens that verify, so the
    guard exists — the point here is *when* it fires.
    """
    monkeypatch.setattr(main, "get_settings", lambda: build_settings(auth_jwt_algorithm="none"))
    app = main.create_app()

    with pytest.raises(AuthConfigurationError):
        with TestClient(app):
            pass  # pragma: no cover - startup raises before the body runs


def test_short_secret_for_the_chosen_algorithm_fails_at_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HS512 needs 64 bytes; V53's 32-char floor only satisfies HS256."""
    monkeypatch.setattr(
        main,
        "get_settings",
        lambda: build_settings(auth_jwt_algorithm="HS512", auth_jwt_secret="k" * 32),
    )
    app = main.create_app()

    with pytest.raises(AuthConfigurationError):
        with TestClient(app):
            pass  # pragma: no cover - startup raises before the body runs


# --- Engine lifecycle ---


def test_health_answers_with_an_unreachable_database(monkeypatch: pytest.MonkeyPatch) -> None:
    """V51: a liveness probe that needs Postgres cannot report "API up, DB down".

    `create_async_engine` opens nothing until first use, and `/health` declares no
    dependencies, so both halves have to hold for this to pass.
    """
    monkeypatch.setattr(
        main, "get_settings", lambda: build_settings(postgres_url=UNREACHABLE_POSTGRES_URL)
    )

    with TestClient(main.create_app()) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_lifespan_disposes_the_engine(
    monkeypatch: pytest.MonkeyPatch, pinned_settings: Settings
) -> None:
    """Connections ⊥ outlive the app; the lifespan that made the engine ends it.

    `dispose` is patched on the class, not the instance — `AsyncEngine` makes the
    attribute read-only. The engine is captured separately so the assertion is about
    *this* app's engine rather than any disposal happening somewhere.
    """
    engines: list[AsyncEngine] = []
    disposed: list[AsyncEngine] = []
    real_create_engine = main.create_engine
    real_dispose = AsyncEngine.dispose

    def factory(settings: Settings) -> AsyncEngine:
        engine = real_create_engine(settings)
        engines.append(engine)
        return engine

    async def tracked_dispose(self: AsyncEngine, **kwargs: Any) -> None:
        disposed.append(self)
        await real_dispose(self, **kwargs)

    monkeypatch.setattr(main, "create_engine", factory)
    monkeypatch.setattr(AsyncEngine, "dispose", tracked_dispose)

    with TestClient(main.create_app()):
        assert disposed == []

    assert engines and disposed == engines


# --- T39's expiry sweeper ---


def test_the_sweeper_starts_with_the_app_and_stops_before_the_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V32's background half runs for exactly the life of the app, and no longer.

    The *ordering* is the claim worth pinning: `stop()` has to complete before
    `engine.dispose()`, because a sweep still in flight would otherwise be running against a
    disposed pool — a shutdown-time error in the one component whose whole job is to keep
    working when things go wrong.

    The real `PendingExpirySweeper` is subclassed rather than replaced, so what starts and
    stops here is the production task, not a stand-in for it.
    """
    journal: list[str] = []
    settings = build_settings(approval_expiry_sweep_interval_seconds=3600)
    monkeypatch.setattr(main, "get_settings", lambda: settings)

    class RecordingSweeper(PendingExpirySweeper):
        async def start(self) -> None:
            await super().start()
            journal.append("sweeper:started")

        async def stop(self) -> None:
            await super().stop()
            journal.append("sweeper:stopped")

    built: list[dict[str, Any]] = []

    def factory(**kwargs: Any) -> RecordingSweeper:
        built.append(kwargs)
        return RecordingSweeper(**kwargs)

    async def tracked_dispose(self: AsyncEngine, **kwargs: Any) -> None:
        journal.append("engine:disposed")

    monkeypatch.setattr(main, "PendingExpirySweeper", factory)
    monkeypatch.setattr(AsyncEngine, "dispose", tracked_dispose)

    with TestClient(main.create_app()):
        assert journal == ["sweeper:started"]

    assert journal == ["sweeper:started", "sweeper:stopped", "engine:disposed"]
    # Built once, from settings — not from a number written twice.
    assert len(built) == 1
    assert built[0]["interval_seconds"] == 3600
    assert built[0]["session_factory"] is not None


# --- T38's reaper and executor ---


def test_the_reaper_starts_with_the_app_and_stops_before_the_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V30's background half runs for exactly the life of the app, and no longer.

    Same claim and same reason as the sweeper above, one component over: a pass still in flight
    when `dispose()` runs is a pass against a disposed pool. The real `StrandedRunReaper` is
    subclassed rather than replaced, so what starts and stops is the production task.

    All three settings are asserted, and they are three settings on purpose — how often it looks
    is a resolution, how long a run may sit `STARTED` is a lifetime, and how much one pass may
    resolve is a bound (V92). A batch size that never left `core.config` would be an unbounded
    pass with a documented limit.
    """
    journal: list[str] = []
    settings = build_settings(
        approval_stranded_run_reap_interval_seconds=3600,
        approval_stranded_run_reap_after_seconds=1800,
        approval_stranded_run_reap_batch_size=25,
    )
    monkeypatch.setattr(main, "get_settings", lambda: settings)

    class RecordingReaper(StrandedRunReaper):
        async def start(self) -> None:
            await super().start()
            journal.append("reaper:started")

        async def stop(self) -> None:
            await super().stop()
            journal.append("reaper:stopped")

    built: list[dict[str, Any]] = []

    def factory(**kwargs: Any) -> RecordingReaper:
        built.append(kwargs)
        return RecordingReaper(**kwargs)

    async def tracked_dispose(self: AsyncEngine, **kwargs: Any) -> None:
        journal.append("engine:disposed")

    monkeypatch.setattr(main, "StrandedRunReaper", factory)
    monkeypatch.setattr(AsyncEngine, "dispose", tracked_dispose)

    with TestClient(main.create_app()):
        assert journal == ["reaper:started"]

    assert journal == ["reaper:started", "reaper:stopped", "engine:disposed"]
    assert len(built) == 1
    assert built[0]["interval_seconds"] == 3600
    assert built[0]["reap_after_seconds"] == 1800
    assert built[0]["batch_size"] == 25


def test_the_executor_is_stopped_before_the_engine_and_before_the_loops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shutdown order, and every part of it is load-bearing (V30).

    A change in flight holds a session, so the executor has to finish before `dispose()` like
    everything else. It is stopped *first* because cancelling it is what produces the leftover
    `STARTED` rows — nothing may still be starting work once the loops that resolve them are
    gone, and the reaper picks those up on the next boot.
    """
    journal: list[str] = []
    monkeypatch.setattr(main, "get_settings", lambda: build_settings())

    class RecordingExecutor(AsyncioApprovedChangeExecutor):
        async def stop(self) -> None:
            await super().stop()
            journal.append("executor:stopped")

    class RecordingReaper(StrandedRunReaper):
        async def stop(self) -> None:
            await super().stop()
            journal.append("reaper:stopped")

    class RecordingSweeper(PendingExpirySweeper):
        async def stop(self) -> None:
            await super().stop()
            journal.append("sweeper:stopped")

    async def tracked_dispose(self: AsyncEngine, **kwargs: Any) -> None:
        journal.append("engine:disposed")

    monkeypatch.setattr(main, "AsyncioApprovedChangeExecutor", RecordingExecutor)
    monkeypatch.setattr(main, "StrandedRunReaper", RecordingReaper)
    monkeypatch.setattr(main, "PendingExpirySweeper", RecordingSweeper)
    monkeypatch.setattr(AsyncEngine, "dispose", tracked_dispose)

    with TestClient(main.create_app()):
        assert journal == []

    assert journal == [
        "executor:stopped",
        "reaper:stopped",
        "sweeper:stopped",
        "engine:disposed",
    ]


def test_building_the_app_starts_no_execution_and_opens_no_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V51, one component further: the executor owns tasks only once an approval hands it a run.

    An executor that touched the database at construction would make every boot — and every
    `/health` probe on a broken deployment — depend on Postgres being up.
    """
    monkeypatch.setattr(
        main, "get_settings", lambda: build_settings(postgres_url=UNREACHABLE_POSTGRES_URL)
    )
    runtime = main.build_runtime(main.get_settings())

    assert runtime.approved_change_executor.outstanding == 0
    assert not runtime.stranded_run_reaper.running
    assert not runtime.expiry_sweeper.running


# --- State wiring ---


def test_missing_state_reports_the_lifespan_rather_than_an_attribute_error() -> None:
    """A `TestClient` never entered as a context manager runs no lifespan.

    Without the explicit message this surfaces as `AttributeError: jwt_service` from
    inside a dependency, which says nothing about the cause.
    """
    app = FastAPI()
    app.include_router(auth_router)

    with pytest.raises(RuntimeError, match=r"app\.state\.jwt_service is missing"):
        TestClient(app).post("/auth/logout")


def test_auth_routes_are_mounted_on_the_real_app(pinned_settings: Settings) -> None:
    """The router is wired in `create_app`, not only in the test harness.

    Read off the OpenAPI schema rather than `app.routes`: FastAPI keeps an included
    router as a single opaque entry there, so walking that list finds `/health` and
    misses every `/auth` path. The schema is also the surface I.admin-api describes.
    """
    paths = set(main.create_app().openapi()["paths"])

    assert {"/auth/login", "/auth/logout", "/auth/me", "/health"} <= paths
