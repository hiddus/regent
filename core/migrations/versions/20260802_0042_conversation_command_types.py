"""Expand conversation_commands.command_type check for guidance + fork.

Revision ID: 20260802_0042
Revises: 20260731_0041
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260802_0042"
down_revision: str | None = "20260731_0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW = (
    "command_type IN ("
    "'QUERY','MODIFY','CONTINUE','PAUSE','RESUME',"
    "'CORRECT','APPROVE','REJECT','SELECT_OPTION'"
    ")"
)
_OLD = "command_type IN ('QUERY','MODIFY','CONTINUE')"


def upgrade() -> None:
    op.drop_constraint(
        "ck_conversation_commands_type",
        "conversation_commands",
        type_="check",
    )
    op.create_check_constraint(
        "ck_conversation_commands_type",
        "conversation_commands",
        _NEW,
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_conversation_commands_type",
        "conversation_commands",
        type_="check",
    )
    op.create_check_constraint(
        "ck_conversation_commands_type",
        "conversation_commands",
        _OLD,
    )
