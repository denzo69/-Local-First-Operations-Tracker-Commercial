"""optional shiftless refunds

Revision ID: f3a9b7c1d2e4
Revises: e2f4a6b8c0d1
Create Date: 2026-07-14 19:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f3a9b7c1d2e4"
down_revision: Union[str, None] = "e2f4a6b8c0d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("refunds", recreate="always") as batch_op:
        batch_op.add_column(sa.Column("business_date", sa.Date(), nullable=True))
        batch_op.alter_column("shift_id", existing_type=sa.Integer(), nullable=True)

    op.execute(
        """
        UPDATE refunds
        SET business_date = (
            SELECT shifts.business_date
            FROM shifts
            WHERE shifts.id = refunds.shift_id
        )
        WHERE shift_id IS NOT NULL AND business_date IS NULL
        """
    )
    op.execute(
        """
        UPDATE refunds
        SET business_date = date(COALESCE(refunded_at, CURRENT_TIMESTAMP))
        WHERE business_date IS NULL
        """
    )
    op.create_index("ix_refunds_business_date", "refunds", ["business_date"])


def downgrade() -> None:
    connection = op.get_bind()
    shiftless_refunds = connection.execute(
        sa.text("SELECT COUNT(*) FROM refunds WHERE shift_id IS NULL")
    ).scalar()
    if shiftless_refunds:
        raise RuntimeError("Cannot downgrade while shiftless refunds exist.")

    op.drop_index("ix_refunds_business_date", table_name="refunds")
    with op.batch_alter_table("refunds", recreate="always") as batch_op:
        batch_op.alter_column("shift_id", existing_type=sa.Integer(), nullable=False)
        batch_op.drop_column("business_date")
