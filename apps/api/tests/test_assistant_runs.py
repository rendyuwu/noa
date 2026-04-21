from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from noa_api.core.config import settings
from noa_api.storage.postgres.lifecycle import AssistantRunStatus
from noa_api.storage.postgres.models import AssistantRun


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


@pytest.mark.asyncio
async def test_assistant_run_migration_enforces_one_active_run_per_thread() -> None:
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
