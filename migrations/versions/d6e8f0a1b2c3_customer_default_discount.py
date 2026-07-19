"""customer default discount

Revision ID: d6e8f0a1b2c3
Revises: b9d2e4f6a8c0
Create Date: 2026-07-19 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d6e8f0a1b2c3"
down_revision: Union[str, None] = "b9d2e4f6a8c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("customers", recreate="always") as batch_op:
        batch_op.add_column(
            sa.Column(
                "default_discount_percent",
                sa.Numeric(precision=5, scale=2),
                nullable=False,
                server_default="0",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("customers", recreate="always") as batch_op:
        batch_op.drop_column("default_discount_percent")
