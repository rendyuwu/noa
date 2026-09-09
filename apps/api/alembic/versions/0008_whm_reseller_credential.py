"""whm servers: is_reseller_credential, additive

Revision ID: 0008_whm_reseller
Revises: 0007_result_tables
Create Date: 2026-09-09

T77 (V109, C12). A WHM row may hold a *reseller* token rather than the root one, and a
reseller token may only write the accounts it owns (measured on a live host, §R.33). Two
things read this column, and neither is an authorization check:

- `whm_list_servers` filters its output by it (V109(a)). 16 clusters times ~7 rows is a
  context problem, not a permission problem.
- the admin write refuses a `true` row whose `name` is not its `api_username` (V109(b)),
  because that equality is what makes `server_ref = owner` resolve at all —
  `resolve_whm_server_ref` matches id, `name` and hostname, and never `api_username`.

`NOT NULL DEFAULT false`, and the default **stays** on the column rather than being dropped
after the backfill: additive means an insert written before this migration still succeeds,
so every existing row and every existing caller keeps working at zero configuration change
(C12). There is no backfill — `false` is the truthful value for a root credential, and the 16
root rows are the majority.

**`downgrade` drops the marks, and a later `upgrade` does not bring them back** — every row
returns to `false` and each reseller row has to be marked again by hand. Nothing fails in the
meantime, which is the reason to write it down: the flag gates nothing (V109), so what an
operator gets is `whm_list_servers` quietly ceasing to filter and naming the credentials it used
to hide — a listing that grew rather than an error (V110).

One column, no index. Nothing filters or orders by it in SQL: the tool path reads the flag off
rows it already selected, and the admin list returns every row regardless.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Under 32 characters: `alembic_version.version_num` is `VARCHAR(32)`, and a longer id fails
# after the DDL, on the bookkeeping UPDATE (T37's recorded gotcha).
revision: str = "0008_whm_reseller"
down_revision: str | None = "0007_result_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "whm_servers",
        sa.Column(
            "is_reseller_credential",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("whm_servers", "is_reseller_credential")
