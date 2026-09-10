"""action requests: a decided row must carry the operator's reason

Revision ID: 0005_decided_reason_check
Revises: 0004_action_requests
Create Date: 2026-08-08

T37, closing the item T34 flagged open by name. T34 argued that "a decided row
carries a non-blank reason" is machine-readable, so V84c says bind it at the mechanism rather
than trust prose — but left the constraint's exact shape to T37 and T39, because V15 names
the approve *endpoint* as the gate and because an expired row is terminal with nobody to have
typed anything. This is that shape, now that both callers exist.

"Contains a non-whitespace character" rather than `reason IS NOT NULL`: whitespace is not an
answer to "why is this change being made", and the field that authorises a change is the last
one to accept a placeholder. The endpoint refuses a blank reason with 409
`change_reason_required`; this is the same rule at the mechanism, so a *second*
writer — T38's executor, T39's sweep, an admin script — cannot record a decision nobody
justified.

`reason ~ '[^[:space:]]'` and **not** `btrim(reason) <> ''`: bare `btrim` strips spaces only,
so a reason of one tab satisfies it while the endpoint's Python `.strip()` rejects the same
string. Two spellings of "blank" is one too many, and the one in the database is the one a
writer that is not the endpoint would be measured against.

The explicit `reason IS NOT NULL` is load-bearing and not redundant: `NULL ~ '…'` evaluates
to NULL, and a CHECK that evaluates to NULL is *satisfied*. Without it the constraint would
refuse every blank string and wave through the NULL — the one case that matters most.

**EXPIRED is deliberately outside the constraint.** An expiry is the absence of an answer,
not one (`core.db.lifecycle.ActionRequestStatus`), so T39's background sweep must stay able
to write a terminal row with `reason IS NULL`. PENDING likewise: a request has no reason
until it is decided.

No data migration. `action_requests` gains its first decided row here — T33 writes only
PENDING, and T22-T29 are unbuilt, so no CHANGE tool exists to open one yet.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# Kept under 32 characters: `alembic_version.version_num` is `VARCHAR(32)`, and a longer id
# fails at the very end of the upgrade — after the DDL, on the bookkeeping UPDATE.
revision: str = "0005_decided_reason_check"
down_revision: str | None = "0004_action_requests"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT_NAME = "ck_action_requests_decided_reason"

# Spelled out rather than built from `ActionRequestStatus`, matching 0004's `_ACTION_REQUEST_
# STATUS`: a migration describes the schema as of this revision, and must keep doing so after
# the enum gains members.
CONDITION = (
    "status NOT IN ('APPROVED', 'DENIED') OR (reason IS NOT NULL AND reason ~ '[^[:space:]]')"
)


def upgrade() -> None:
    op.create_check_constraint(CONSTRAINT_NAME, "action_requests", CONDITION)


def downgrade() -> None:
    op.drop_constraint(CONSTRAINT_NAME, "action_requests", type_="check")
