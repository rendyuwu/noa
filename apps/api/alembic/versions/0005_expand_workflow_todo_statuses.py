"""expand workflow todo statuses

Revision ID: 0005_expand_workflow_todo_statuses
Revises: 0004_workflow_todos
Create Date: 2026-03-16 00:00:01.000000
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005_expand_workflow_todo_statuses"
down_revision: Union[str, None] = "0004_workflow_todos"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("ck_workflow_todos_status", "workflow_todos", type_="check")
    op.create_check_constraint(
        "ck_workflow_todos_status",
        "workflow_todos",
        "status IN ('pending', 'in_progress', 'waiting_on_user', "
        "'waiting_on_approval', 'completed', 'cancelled')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_workflow_todos_status", "workflow_todos", type_="check")
    op.create_check_constraint(
        "ck_workflow_todos_status",
        "workflow_todos",
        "status IN ('pending', 'in_progress', 'completed', 'cancelled')",
    )
