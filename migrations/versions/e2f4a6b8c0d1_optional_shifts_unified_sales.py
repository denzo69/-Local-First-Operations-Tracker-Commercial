"""optional shifts for unified sales

Revision ID: e2f4a6b8c0d1
Revises: c9d1e7a4b2f6
Create Date: 2026-07-13 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e2f4a6b8c0d1"
down_revision: Union[str, None] = "c9d1e7a4b2f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("sales", recreate="always") as batch_op:
        batch_op.add_column(sa.Column("business_date", sa.Date(), nullable=True))
        batch_op.alter_column("seller_id", existing_type=sa.Integer(), nullable=True)
        batch_op.alter_column("shift_id", existing_type=sa.Integer(), nullable=True)

    op.execute(
        """
        UPDATE sales
        SET business_date = (
            SELECT shifts.business_date
            FROM shifts
            WHERE shifts.id = sales.shift_id
        )
        WHERE shift_id IS NOT NULL AND business_date IS NULL
        """
    )
    op.execute(
        """
        UPDATE sales
        SET business_date = date(COALESCE(sold_at, finalized_at, created_at, CURRENT_TIMESTAMP))
        WHERE business_date IS NULL
        """
    )
    op.create_index("ix_sales_business_date", "sales", ["business_date"])

    with op.batch_alter_table("payments", recreate="always") as batch_op:
        batch_op.alter_column("shift_id", existing_type=sa.Integer(), nullable=True)
        batch_op.alter_column("seller_id", existing_type=sa.Integer(), nullable=True)

    op.execute(
        """
        INSERT OR IGNORE INTO settings (key, value, updated_at)
        VALUES ('require_cashier_shift', 'false', CURRENT_TIMESTAMP)
        """
    )


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

    op.execute("DELETE FROM settings WHERE key = 'require_cashier_shift'")
    with op.batch_alter_table("payments", recreate="always") as batch_op:
        batch_op.alter_column("seller_id", existing_type=sa.Integer(), nullable=False)
        batch_op.alter_column("shift_id", existing_type=sa.Integer(), nullable=False)

    op.drop_index("ix_sales_business_date", table_name="sales")
    with op.batch_alter_table("sales", recreate="always") as batch_op:
        batch_op.alter_column("shift_id", existing_type=sa.Integer(), nullable=False)
        batch_op.alter_column("seller_id", existing_type=sa.Integer(), nullable=False)
        batch_op.drop_column("business_date")
