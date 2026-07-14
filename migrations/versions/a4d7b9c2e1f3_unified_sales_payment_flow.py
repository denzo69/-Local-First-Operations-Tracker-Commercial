"""unified sales payment flow

Revision ID: a4d7b9c2e1f3
Revises: 9e4c3b2a1f08
Create Date: 2026-07-12 10:15:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a4d7b9c2e1f3"
down_revision: Union[str, None] = "9e4c3b2a1f08"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("sales", recreate="always") as batch_op:
        batch_op.add_column(sa.Column("source_type", sa.String(length=50), nullable=True))
        batch_op.add_column(sa.Column("idempotency_key", sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column("finalized_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("settlement_status", sa.String(length=50), nullable=True))
        batch_op.add_column(sa.Column("invoice_customer_snapshot_json", sa.Text(), nullable=True))

    op.execute("UPDATE sales SET source_type = CASE WHEN work_order_id IS NULL THEN 'pos' ELSE 'work_order' END WHERE source_type IS NULL")
    op.execute("UPDATE sales SET settlement_status = CASE WHEN status = 'refunded' THEN 'refunded' ELSE 'paid' END WHERE settlement_status IS NULL")
    op.execute("UPDATE sales SET finalized_at = sold_at WHERE finalized_at IS NULL")
    op.create_index("ix_sales_source_type", "sales", ["source_type"])
    op.create_index("ix_sales_settlement_status", "sales", ["settlement_status"])
    op.create_index("ix_sales_idempotency_key", "sales", ["idempotency_key"], unique=True)
    op.create_index(
        "ux_sales_active_work_order",
        "sales",
        ["work_order_id"],
        unique=True,
        sqlite_where=sa.text("work_order_id IS NOT NULL AND status != 'cancelled'"),
    )


def downgrade() -> None:
    op.drop_index("ux_sales_active_work_order", table_name="sales")
    op.drop_index("ix_sales_idempotency_key", table_name="sales")
    op.drop_index("ix_sales_settlement_status", table_name="sales")
    op.drop_index("ix_sales_source_type", table_name="sales")
    with op.batch_alter_table("sales", recreate="always") as batch_op:
        batch_op.drop_column("invoice_customer_snapshot_json")
        batch_op.drop_column("settlement_status")
        batch_op.drop_column("finalized_at")
        batch_op.drop_column("idempotency_key")
        batch_op.drop_column("source_type")
