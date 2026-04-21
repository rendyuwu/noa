from __future__ import annotations

import importlib.util
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from noa_api.api.assistant.assistant_repository import SQLAssistantRepository
from noa_api.core.config import settings
from noa_api.storage.postgres.lifecycle import (
    ActionRequestStatus,
    AssistantRunStatus,
    ToolRisk,
)
from noa_api.storage.postgres.models import ActionRequest, AssistantRun, Thread, User


def _load_assistant_runs_migration():
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "20260421_assistant_runs.py"
    )
    spec = importlib.util.spec_from_file_location(
        f"assistant_runs_migration_{uuid4().hex}", migration_path
    )
    assert spec is not None
    assert spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _create_repository_schema(sync_conn: sa.Connection, *, schema_name: str) -> None:
    migration = _load_assistant_runs_migration()
    sync_conn.exec_driver_sql(f'CREATE SCHEMA "{schema_name}"')
    sync_conn.exec_driver_sql(f'SET search_path TO "{schema_name}"')
    sync_conn.exec_driver_sql(
        """
        CREATE TABLE users (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            email varchar(255) NOT NULL UNIQUE,
            ldap_dn text,
            display_name varchar(255),
            is_active boolean NOT NULL DEFAULT false,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            last_login_at timestamptz
        )
        """
    )
    sync_conn.exec_driver_sql(
        """
        CREATE TABLE threads (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            owner_user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            external_id varchar(255),
            title varchar(255),
            is_archived boolean NOT NULL DEFAULT false,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_threads_owner_external_id UNIQUE (owner_user_id, external_id)
        )
        """
    )
    sync_conn.exec_driver_sql(
        """
        CREATE TABLE action_requests (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            thread_id uuid NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
            tool_name varchar(200) NOT NULL,
            args jsonb NOT NULL DEFAULT '{}'::jsonb,
            risk varchar(64) NOT NULL,
            status varchar(64) NOT NULL DEFAULT 'PENDING',
            requested_by_user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            decided_by_user_id uuid,
            decided_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )

    context = MigrationContext.configure(sync_conn)
    with Operations.context(context):
        migration.upgrade()


def test_assistant_run_status_values_are_stable() -> None:
    assert AssistantRunStatus.STARTING.value == "STARTING"
    assert AssistantRunStatus.RUNNING.value == "RUNNING"
    assert AssistantRunStatus.WAITING_APPROVAL.value == "WAITING_APPROVAL"
    assert AssistantRunStatus.COMPLETED.value == "COMPLETED"
    assert AssistantRunStatus.FAILED.value == "FAILED"


def test_assistant_run_model_carries_required_runtime_fields() -> None:
    run = AssistantRun(
        id=uuid4(),
        thread_id=uuid4(),
        owner_user_id=uuid4(),
        status=AssistantRunStatus.STARTING,
        owner_instance_id="api-1",
        sequence=0,
        live_snapshot={"message": ""},
        blocking_action_request_id=None,
        last_error_reason=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    assert run.status == AssistantRunStatus.STARTING
    assert run.owner_instance_id == "api-1"
    assert run.sequence == 0
    assert run.live_snapshot == {"message": ""}


@asynccontextmanager
async def _temporary_repository_database() -> AsyncIterator[tuple[AsyncEngine, str]]:
    engine = create_async_engine(str(settings.postgres_url), pool_pre_ping=True)
    schema_name = f"assistant_repository_test_{uuid4().hex}"

    try:
        async with engine.begin() as conn:
            await conn.run_sync(_create_repository_schema, schema_name=schema_name)

        yield engine, schema_name
    finally:
        async with engine.begin() as conn:
            await conn.execute(
                sa.text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
            )
        await engine.dispose()


async def _open_repository_session(
    *, engine: AsyncEngine, schema_name: str
) -> AsyncSession:
    session = AsyncSession(engine, expire_on_commit=False)
    await session.execute(sa.text(f'SET search_path TO "{schema_name}"'))
    return session


@asynccontextmanager
async def _temporary_repository_session() -> AsyncIterator[AsyncSession]:
    async with _temporary_repository_database() as (engine, schema_name):
        session = await _open_repository_session(engine=engine, schema_name=schema_name)
        try:
            yield session
        finally:
            await session.rollback()
            await session.close()


async def _create_thread(session: AsyncSession) -> Thread:
    owner = User(email=f"assistant-run-{uuid4().hex}@example.com")
    session.add(owner)
    await session.flush()

    thread = Thread(owner_user_id=owner.id)
    session.add(thread)
    await session.flush()
    return thread


async def _create_action_request(
    session: AsyncSession, *, thread: Thread
) -> ActionRequest:
    action_request = ActionRequest(
        thread_id=thread.id,
        tool_name="fake_change_tool",
        args={"key": "feature_x", "value": True},
        risk=ToolRisk.CHANGE,
        status=ActionRequestStatus.PENDING,
        requested_by_user_id=thread.owner_user_id,
    )
    session.add(action_request)
    await session.flush()
    return action_request


@pytest.mark.asyncio
async def test_repository_creates_active_run_for_thread() -> None:
    async with _temporary_repository_session() as session:
        repository = SQLAssistantRepository(session)
        thread = await _create_thread(session)

        run = await repository.create_assistant_run(
            thread_id=thread.id,
            owner_user_id=thread.owner_user_id,
            owner_instance_id="api-1",
        )

        assert run.thread_id == thread.id
        assert run.owner_user_id == thread.owner_user_id
        assert run.status == AssistantRunStatus.STARTING
        assert run.owner_instance_id == "api-1"
        assert run.sequence == 0
        assert run.live_snapshot == {}


@pytest.mark.asyncio
async def test_repository_returns_active_run_for_thread() -> None:
    async with _temporary_repository_session() as session:
        repository = SQLAssistantRepository(session)
        thread = await _create_thread(session)
        created = await repository.create_assistant_run(
            thread_id=thread.id,
            owner_user_id=thread.owner_user_id,
            owner_instance_id="api-1",
        )

        loaded = await repository.get_active_run(thread_id=thread.id)

        assert loaded is not None
        assert loaded.id == created.id


@pytest.mark.asyncio
async def test_repository_owner_missing_run_failed_when_owner_matches() -> None:
    async with _temporary_repository_session() as session:
        repository = SQLAssistantRepository(session)
        thread = await _create_thread(session)
        action_request = await _create_action_request(session, thread=thread)
        run = await repository.create_assistant_run(
            thread_id=thread.id,
            owner_user_id=thread.owner_user_id,
            owner_instance_id="api-1",
        )
        waiting = await repository.mark_run_waiting_approval(
            run_id=run.id,
            action_request_id=action_request.id,
        )

        assert waiting is not None
        assert waiting.status == AssistantRunStatus.WAITING_APPROVAL
        assert waiting.blocking_action_request_id == action_request.id

        updated = await repository.fail_run_if_owner_matches(
            run_id=run.id,
            owner_instance_id="api-1",
            reason="server_interrupted",
        )

        assert updated is not None
        assert updated.status == AssistantRunStatus.FAILED
        assert updated.last_error_reason == "server_interrupted"
        assert updated.blocking_action_request_id is None


@pytest.mark.asyncio
async def test_repository_fail_run_if_owner_matches_ignores_mismatched_owner() -> None:
    async with _temporary_repository_session() as session:
        repository = SQLAssistantRepository(session)
        thread = await _create_thread(session)
        action_request = await _create_action_request(session, thread=thread)
        run = await repository.create_assistant_run(
            thread_id=thread.id,
            owner_user_id=thread.owner_user_id,
            owner_instance_id="api-1",
        )
        waiting = await repository.mark_run_waiting_approval(
            run_id=run.id,
            action_request_id=action_request.id,
        )

        updated = await repository.fail_run_if_owner_matches(
            run_id=run.id,
            owner_instance_id="api-2",
            reason="server_interrupted",
        )

        assert updated is None
        assert waiting is not None
        assert waiting.status == AssistantRunStatus.WAITING_APPROVAL
        assert waiting.blocking_action_request_id == action_request.id
        assert waiting.last_error_reason is None


@pytest.mark.asyncio
async def test_repository_fail_run_if_owner_matches_ignores_terminal_runs() -> None:
    async with _temporary_repository_session() as session:
        repository = SQLAssistantRepository(session)
        thread = await _create_thread(session)

        completed_run = await repository.create_assistant_run(
            thread_id=thread.id,
            owner_user_id=thread.owner_user_id,
            owner_instance_id="api-1",
        )
        completed = await repository.mark_run_completed(run_id=completed_run.id)

        completed_updated = await repository.fail_run_if_owner_matches(
            run_id=completed_run.id,
            owner_instance_id="api-1",
            reason="server_interrupted",
        )

        assert completed_updated is None
        assert completed is not None
        assert completed.status == AssistantRunStatus.COMPLETED
        assert completed.last_error_reason is None
        assert completed.blocking_action_request_id is None

        failed_run = await repository.create_assistant_run(
            thread_id=thread.id,
            owner_user_id=thread.owner_user_id,
            owner_instance_id="api-1",
        )
        failed = await repository.mark_run_failed(
            run_id=failed_run.id,
            reason="original_failure",
        )

        failed_updated = await repository.fail_run_if_owner_matches(
            run_id=failed_run.id,
            owner_instance_id="api-1",
            reason="server_interrupted",
        )

        assert failed_updated is None
        assert failed is not None
        assert failed.status == AssistantRunStatus.FAILED
        assert failed.last_error_reason == "original_failure"
        assert failed.blocking_action_request_id is None


@pytest.mark.asyncio
async def test_repository_fail_run_if_owner_matches_does_not_rewrite_stale_run() -> (
    None
):
    async with _temporary_repository_database() as (engine, schema_name):
        seed_session = await _open_repository_session(
            engine=engine,
            schema_name=schema_name,
        )
        try:
            seed_repository = SQLAssistantRepository(seed_session)
            thread = await _create_thread(seed_session)
            run = await seed_repository.create_assistant_run(
                thread_id=thread.id,
                owner_user_id=thread.owner_user_id,
                owner_instance_id="api-1",
            )
            run_id = run.id
            await seed_session.commit()
        finally:
            await seed_session.close()

        stale_session = await _open_repository_session(
            engine=engine,
            schema_name=schema_name,
        )
        terminal_session = await _open_repository_session(
            engine=engine,
            schema_name=schema_name,
        )
        verify_session = await _open_repository_session(
            engine=engine,
            schema_name=schema_name,
        )
        try:
            stale_repository = SQLAssistantRepository(stale_session)
            terminal_repository = SQLAssistantRepository(terminal_session)
            verify_repository = SQLAssistantRepository(verify_session)

            stale_loaded = await stale_repository.get_assistant_run(run_id=run_id)
            assert stale_loaded is not None
            assert stale_loaded.status == AssistantRunStatus.STARTING

            completed = await terminal_repository.mark_run_completed(run_id=run_id)
            assert completed is not None
            assert completed.status == AssistantRunStatus.COMPLETED
            await terminal_session.commit()

            updated = await stale_repository.fail_run_if_owner_matches(
                run_id=run_id,
                owner_instance_id="api-1",
                reason="server_interrupted",
            )

            assert updated is None

            persisted = await verify_repository.get_assistant_run(run_id=run_id)
            assert persisted is not None
            assert persisted.status == AssistantRunStatus.COMPLETED
            assert persisted.last_error_reason is None
            assert persisted.blocking_action_request_id is None
        finally:
            await stale_session.rollback()
            await stale_session.close()
            await terminal_session.rollback()
            await terminal_session.close()
            await verify_session.rollback()
            await verify_session.close()


@pytest.mark.asyncio
async def test_repository_competing_snapshot_updates_increment_sequence() -> None:
    async with _temporary_repository_database() as (engine, schema_name):
        seed_session = await _open_repository_session(
            engine=engine,
            schema_name=schema_name,
        )
        try:
            seed_repository = SQLAssistantRepository(seed_session)
            thread = await _create_thread(seed_session)
            run = await seed_repository.create_assistant_run(
                thread_id=thread.id,
                owner_user_id=thread.owner_user_id,
                owner_instance_id="api-1",
            )
            run_id = run.id
            await seed_session.commit()
        finally:
            await seed_session.close()

        first_session = await _open_repository_session(
            engine=engine,
            schema_name=schema_name,
        )
        second_session = await _open_repository_session(
            engine=engine,
            schema_name=schema_name,
        )
        verify_session = await _open_repository_session(
            engine=engine,
            schema_name=schema_name,
        )
        try:
            first_repository = SQLAssistantRepository(first_session)
            second_repository = SQLAssistantRepository(second_session)
            verify_repository = SQLAssistantRepository(verify_session)

            first_loaded = await first_repository.get_assistant_run(run_id=run_id)
            second_loaded = await second_repository.get_assistant_run(run_id=run_id)
            assert first_loaded is not None
            assert second_loaded is not None
            assert first_loaded.sequence == 0
            assert second_loaded.sequence == 0

            first_snapshot = await first_repository.append_run_snapshot(
                run_id=run_id,
                snapshot={"message": "first"},
            )
            assert first_snapshot is not None
            assert first_snapshot.sequence == 1
            await first_session.commit()

            second_snapshot = await second_repository.append_run_snapshot(
                run_id=run_id,
                snapshot={"message": "second"},
            )
            assert second_snapshot is not None
            await second_session.commit()

            persisted = await verify_repository.get_assistant_run(run_id=run_id)
            assert persisted is not None
            assert persisted.sequence == 2
            assert persisted.live_snapshot == {"message": "second"}
        finally:
            await first_session.rollback()
            await first_session.close()
            await second_session.rollback()
            await second_session.close()
            await verify_session.rollback()
            await verify_session.close()


@pytest.mark.asyncio
async def test_repository_terminal_runs_cannot_be_reopened_by_lifecycle_helpers() -> (
    None
):
    async with _temporary_repository_session() as session:
        repository = SQLAssistantRepository(session)
        thread = await _create_thread(session)

        completed_run = await repository.create_assistant_run(
            thread_id=thread.id,
            owner_user_id=thread.owner_user_id,
            owner_instance_id="api-1",
        )
        completed = await repository.mark_run_completed(run_id=completed_run.id)

        reopened_completed = await repository.mark_run_running(run_id=completed_run.id)
        completed_snapshot = await repository.append_run_snapshot(
            run_id=completed_run.id,
            snapshot={"message": "should_not_write"},
        )

        assert reopened_completed is None
        assert completed_snapshot is None
        assert completed is not None
        assert completed.status == AssistantRunStatus.COMPLETED
        assert completed.sequence == 0
        assert completed.live_snapshot == {}

        failed_run = await repository.create_assistant_run(
            thread_id=thread.id,
            owner_user_id=thread.owner_user_id,
            owner_instance_id="api-2",
        )
        failed = await repository.mark_run_failed(
            run_id=failed_run.id,
            reason="original_failure",
        )

        reopened_failed = await repository.mark_run_running(run_id=failed_run.id)

        assert reopened_failed is None
        assert failed is not None
        assert failed.status == AssistantRunStatus.FAILED
        assert failed.last_error_reason == "original_failure"


@pytest.mark.asyncio
async def test_repository_stale_session_lifecycle_writes_do_not_rewrite_terminal_runs() -> (
    None
):
    async with _temporary_repository_database() as (engine, schema_name):
        seed_session = await _open_repository_session(
            engine=engine,
            schema_name=schema_name,
        )
        try:
            seed_repository = SQLAssistantRepository(seed_session)
            completed_thread = await _create_thread(seed_session)
            failed_thread = await _create_thread(seed_session)
            completed_run = await seed_repository.create_assistant_run(
                thread_id=completed_thread.id,
                owner_user_id=completed_thread.owner_user_id,
                owner_instance_id="api-1",
            )
            failed_run = await seed_repository.create_assistant_run(
                thread_id=failed_thread.id,
                owner_user_id=failed_thread.owner_user_id,
                owner_instance_id="api-2",
            )
            completed_run_id = completed_run.id
            failed_run_id = failed_run.id
            await seed_session.commit()
        finally:
            await seed_session.close()

        stale_session = await _open_repository_session(
            engine=engine,
            schema_name=schema_name,
        )
        terminal_session = await _open_repository_session(
            engine=engine,
            schema_name=schema_name,
        )
        verify_session = await _open_repository_session(
            engine=engine,
            schema_name=schema_name,
        )
        try:
            stale_repository = SQLAssistantRepository(stale_session)
            terminal_repository = SQLAssistantRepository(terminal_session)
            verify_repository = SQLAssistantRepository(verify_session)

            stale_completed = await stale_repository.get_assistant_run(
                run_id=completed_run_id
            )
            stale_failed = await stale_repository.get_assistant_run(
                run_id=failed_run_id
            )
            assert stale_completed is not None
            assert stale_failed is not None
            assert stale_completed.status == AssistantRunStatus.STARTING
            assert stale_failed.status == AssistantRunStatus.STARTING

            completed = await terminal_repository.mark_run_completed(
                run_id=completed_run_id
            )
            failed = await terminal_repository.mark_run_failed(
                run_id=failed_run_id,
                reason="original_failure",
            )
            assert completed is not None
            assert failed is not None
            await terminal_session.commit()

            stale_running_completed = await stale_repository.mark_run_running(
                run_id=completed_run_id
            )
            stale_snapshot_completed = await stale_repository.append_run_snapshot(
                run_id=completed_run_id,
                snapshot={"message": "stale"},
            )
            stale_running_failed = await stale_repository.mark_run_running(
                run_id=failed_run_id
            )

            assert stale_running_completed is None
            assert stale_snapshot_completed is None
            assert stale_running_failed is None

            persisted_completed = await verify_repository.get_assistant_run(
                run_id=completed_run_id
            )
            persisted_failed = await verify_repository.get_assistant_run(
                run_id=failed_run_id
            )
            assert persisted_completed is not None
            assert persisted_failed is not None
            assert persisted_completed.status == AssistantRunStatus.COMPLETED
            assert persisted_completed.sequence == 0
            assert persisted_completed.live_snapshot == {}
            assert persisted_failed.status == AssistantRunStatus.FAILED
            assert persisted_failed.last_error_reason == "original_failure"
        finally:
            await stale_session.rollback()
            await stale_session.close()
            await terminal_session.rollback()
            await terminal_session.close()
            await verify_session.rollback()
            await verify_session.close()


@pytest.mark.asyncio
async def test_repository_creates_active_run_support_helpers_update_lifecycle() -> None:
    async with _temporary_repository_session() as session:
        repository = SQLAssistantRepository(session)
        thread = await _create_thread(session)
        action_request = await _create_action_request(session, thread=thread)

        created = await repository.create_assistant_run(
            thread_id=thread.id,
            owner_user_id=thread.owner_user_id,
            owner_instance_id="api-1",
        )
        loaded = await repository.get_assistant_run(run_id=created.id)
        assert loaded is not None
        assert loaded.id == created.id

        running = await repository.mark_run_running(run_id=created.id)
        assert running is not None
        assert running.status == AssistantRunStatus.RUNNING
        assert running.blocking_action_request_id is None

        waiting = await repository.mark_run_waiting_approval(
            run_id=created.id,
            action_request_id=action_request.id,
        )
        assert waiting is not None
        assert waiting.status == AssistantRunStatus.WAITING_APPROVAL
        assert waiting.blocking_action_request_id == action_request.id

        await session.execute(
            sa.update(AssistantRun)
            .where(AssistantRun.id == created.id)
            .values(last_error_reason="stale_error")
        )
        await session.flush()

        resumed = await repository.mark_run_running(run_id=created.id)
        assert resumed is not None
        assert resumed.status == AssistantRunStatus.RUNNING
        assert resumed.blocking_action_request_id is None
        assert resumed.last_error_reason is None

        snapshot = {"message": "hello"}
        updated_snapshot = await repository.append_run_snapshot(
            run_id=created.id,
            snapshot=snapshot,
        )
        assert updated_snapshot is not None
        assert updated_snapshot.sequence == 1
        assert updated_snapshot.live_snapshot == snapshot

        completed = await repository.mark_run_completed(run_id=created.id)
        assert completed is not None
        assert completed.status == AssistantRunStatus.COMPLETED
        assert completed.blocking_action_request_id is None

        assert await repository.get_active_run(thread_id=thread.id) is None

        failed_run = await repository.create_assistant_run(
            thread_id=thread.id,
            owner_user_id=thread.owner_user_id,
            owner_instance_id="api-2",
        )
        failed = await repository.mark_run_failed(
            run_id=failed_run.id,
            reason="server_interrupted",
        )
        assert failed is not None
        assert failed.status == AssistantRunStatus.FAILED
        assert failed.last_error_reason == "server_interrupted"
        assert failed.blocking_action_request_id is None


@pytest.mark.asyncio
async def test_assistant_run_migration_enforces_one_active_run_per_thread() -> None:
    migration = _load_assistant_runs_migration()

    engine = create_async_engine(str(settings.postgres_url), pool_pre_ping=True)
    schema_name = f"assistant_runs_test_{uuid4().hex}"
    active_statuses = [
        AssistantRunStatus.STARTING.value,
        AssistantRunStatus.RUNNING.value,
        AssistantRunStatus.WAITING_APPROVAL.value,
    ]

    def exercise_migration(sync_conn: sa.Connection) -> None:
        sync_conn.exec_driver_sql(f'CREATE SCHEMA "{schema_name}"')
        sync_conn.exec_driver_sql(f'SET search_path TO "{schema_name}"')
        sync_conn.exec_driver_sql("CREATE TABLE users (id uuid PRIMARY KEY)")
        sync_conn.exec_driver_sql(
            """
            CREATE TABLE threads (
                id uuid PRIMARY KEY,
                owner_user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE
            )
            """
        )
        sync_conn.exec_driver_sql("CREATE TABLE action_requests (id uuid PRIMARY KEY)")

        context = MigrationContext.configure(sync_conn)
        with Operations.context(context):
            migration.upgrade()

        indexes = sa.inspect(sync_conn).get_indexes(
            "assistant_runs", schema=schema_name
        )
        assert any(
            index["name"] == "ix_assistant_runs_owner_user_id" for index in indexes
        )

        owner_user_id = uuid4()
        thread_id = uuid4()
        insert_sql = sa.text(
            """
            INSERT INTO assistant_runs (
                id,
                thread_id,
                owner_user_id,
                status,
                owner_instance_id,
                sequence,
                live_snapshot
            ) VALUES (
                :id,
                :thread_id,
                :owner_user_id,
                :status,
                :owner_instance_id,
                0,
                '{}'::jsonb
            )
            """
        )

        sync_conn.execute(
            sa.text("INSERT INTO users (id) VALUES (:id)"),
            {"id": owner_user_id},
        )
        sync_conn.execute(
            sa.text(
                "INSERT INTO threads (id, owner_user_id) VALUES (:id, :owner_user_id)"
            ),
            {"id": thread_id, "owner_user_id": owner_user_id},
        )

        for status in active_statuses:
            sync_conn.exec_driver_sql("DELETE FROM assistant_runs")
            sync_conn.execute(
                insert_sql,
                {
                    "id": uuid4(),
                    "thread_id": thread_id,
                    "owner_user_id": owner_user_id,
                    "status": status,
                    "owner_instance_id": "api-1",
                },
            )

            with pytest.raises(IntegrityError):
                with sync_conn.begin_nested():
                    sync_conn.execute(
                        insert_sql,
                        {
                            "id": uuid4(),
                            "thread_id": thread_id,
                            "owner_user_id": owner_user_id,
                            "status": AssistantRunStatus.RUNNING.value,
                            "owner_instance_id": "api-2",
                        },
                    )

        sync_conn.exec_driver_sql("DELETE FROM assistant_runs")
        sync_conn.execute(
            insert_sql,
            {
                "id": uuid4(),
                "thread_id": thread_id,
                "owner_user_id": owner_user_id,
                "status": AssistantRunStatus.COMPLETED.value,
                "owner_instance_id": "api-1",
            },
        )
        sync_conn.execute(
            insert_sql,
            {
                "id": uuid4(),
                "thread_id": thread_id,
                "owner_user_id": owner_user_id,
                "status": AssistantRunStatus.RUNNING.value,
                "owner_instance_id": "api-2",
            },
        )

    try:
        async with engine.begin() as conn:
            await conn.run_sync(exercise_migration)
    finally:
        async with engine.begin() as conn:
            await conn.execute(
                sa.text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
            )
        await engine.dispose()
