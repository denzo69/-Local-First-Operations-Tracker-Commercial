"""optional cashier shifts

Revision ID: d4f7a2c9b8e1
Revises: 9e4c3b2a1f08
Create Date: 2026-07-13 09:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4f7a2c9b8e1"
down_revision: Union[str, None] = "9e4c3b2a1f08"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("sales", recreate="always") as batch_op:
        batch_op.alter_column("seller_id", existing_type=sa.Integer(), nullable=True)
        batch_op.alter_column("shift_id", existing_type=sa.Integer(), nullable=True)

    with op.batch_alter_table("payments", recreate="always") as batch_op:
        batch_op.alter_column("shift_id", existing_type=sa.Integer(), nullable=True)
        batch_op.alter_column("seller_id", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    connection = op.get_bind()
    nullable_rows = connection.execute(
        sa.text(
            """
            SELECT
                (SELECT COUNT(*) FROM sales WHERE seller_id IS NULL OR shift_id IS NULL)
                +
                (SELECT COUNT(*) FROM payments WHERE seller_id IS NULL OR shift_id IS NULL)
            """
        )
    ).scalar()
    if nullable_rows:
        raise RuntimeError(
            "Cannot downgrade optional cashier shifts while shiftless or sellerless sales/payments exist."
        )

    with op.batch_alter_table("payments", recreate="always") as batch_op:
        batch_op.alter_column("seller_id", existing_type=sa.Integer(), nullable=False)
        batch_op.alter_column("shift_id", existing_type=sa.Integer(), nullable=False)

    with op.batch_alter_table("sales", recreate="always") as batch_op:
        batch_op.alter_column("shift_id", existing_type=sa.Integer(), nullable=False)
        batch_op.alter_column("seller_id", existing_type=sa.Integer(), nullable=False)
