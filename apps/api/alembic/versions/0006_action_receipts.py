"""action receipts: what an approved CHANGE actually did

Revision ID: 0006_action_receipts
Revises: 0005_decided_reason_check
Create Date: 2026-08-09

T36 (V46). V46 names three artifacts for an approved change — the `tool_runs` row (what
ran), this receipt (what it did), and the audit log. The run says a change completed; the
receipt is the two-part story DECISIONS §6.5 requires an operator to read back:
before-state and after-state, each verified separately, never collapsed into one "done".

Nothing writes this table yet. T38's executor does; T42's card and T63's
`noa_get_action_result` read it beside the run.

Ported from `noa-old` `MCP:apps/api/alembic/versions/0006_action_receipts.py` (C13) with
three departures, each named because a port carries the code and not the defect:

- `receipt_data`, not `payload` — §T.36's name.
- No `terminal_phase`. It carried the terminal state of a multi-phase workflow and C16
  drops workflows; the terminal state lives on `tool_runs.status` and
  `action_requests.status`, and a third column saying it again is a third truth about one
  moment.
- No `schema_version`. `approval_context` and `tool_runs.args` are both unversioned JSONB;
  versioning the third would make their bareness look deliberate when it is not.

**One receipt per request, stated rather than inherited.** `noa-old` made
`action_request_id` the primary key, so uniqueness came with the table. §T.36 names a
separate `id`, so the unique constraint below is what keeps the property — and it is
load-bearing: T38's executor and its reaper can both reach a finished run, and a second
receipt turns "the receipt" into "some receipt". It is also the index an idempotent
`ON CONFLICT (action_request_id) DO NOTHING` writer needs in order to work at all.

One index, and deliberately fewer than the port's three. `noa-old` indexed `tool_run_id`,
`terminal_phase` and `created_at`; nothing filters or orders by them here, the same
discipline that left `risk` unindexed at 0003 and kept 0004's index set small. Every
reader arrives holding an `action_request_id`, which the unique constraint already
indexes.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# Kept under 32 characters: `alembic_version.version_num` is `VARCHAR(32)`, and a longer id
# fails at the very end of the upgrade — after the DDL, on the bookkeeping UPDATE.
revision: str = "0006_action_receipts"
down_revision: str | None = "0005_decided_reason_check"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "action_receipts",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        # CASCADE and NOT NULL, unlike every FK added since 0003. Those are SET NULL because
        # the row still describes something without its subject; a receipt without its
        # request does not — the tool name, the requester and the arguments all live on
        # `action_requests`, and 0004 already made that row outlive a user deletion.
        sa.Column("action_request_id", _UUID, nullable=False),
        # SET NULL, mirroring `action_requests.tool_run_id`: one edge, described the same way
        # at both ends.
        sa.Column("tool_run_id", _UUID, nullable=True),
        # No default, unlike `tool_runs.args` and exactly like `approval_context`: an empty
        # receipt is not a legitimate state, so an insert omitting it must fail rather than
        # record an outcome with nothing in it.
        sa.Column("receipt_data", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["action_request_id"],
            ["action_requests.id"],
            name="fk_action_receipts_action_request_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tool_run_id"],
            ["tool_runs.id"],
            name="fk_action_receipts_tool_run_id",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("action_request_id", name="uq_action_receipts_action_request_id"),
    )


def downgrade() -> None:
    op.drop_table("action_receipts")
