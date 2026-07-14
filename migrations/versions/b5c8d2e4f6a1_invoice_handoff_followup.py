"""invoice handoff followup

Revision ID: b5c8d2e4f6a1
Revises: a4d7b9c2e1f3
Create Date: 2026-07-12 11:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b5c8d2e4f6a1"
down_revision: Union[str, None] = "a4d7b9c2e1f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("sales", recreate="always") as batch_op:
        batch_op.add_column(sa.Column("transferred_to_invoicing_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("external_invoice_service", sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column("external_invoice_number", sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column("invoice_date", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("due_date", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("external_invoice_reference", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("invoice_handoff_notes", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("payment_status_checked_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("paid_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("next_follow_up_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("reminder_count", sa.Integer(), nullable=True, server_default="0"))
        batch_op.add_column(sa.Column("last_reminder_sent_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("follow_up_notes", sa.Text(), nullable=True))

    op.create_index("ix_sales_external_invoice_number", "sales", ["external_invoice_number"])
    op.create_index("ix_sales_due_date", "sales", ["due_date"])
    op.create_index("ix_sales_next_follow_up_at", "sales", ["next_follow_up_at"])


def downgrade() -> None:
    op.drop_index("ix_sales_next_follow_up_at", table_name="sales")
    op.drop_index("ix_sales_due_date", table_name="sales")
    op.drop_index("ix_sales_external_invoice_number", table_name="sales")
    with op.batch_alter_table("sales", recreate="always") as batch_op:
        batch_op.drop_column("follow_up_notes")
        batch_op.drop_column("last_reminder_sent_at")
        batch_op.drop_column("reminder_count")
        batch_op.drop_column("next_follow_up_at")
        batch_op.drop_column("paid_at")
        batch_op.drop_column("payment_status_checked_at")
        batch_op.drop_column("invoice_handoff_notes")
        batch_op.drop_column("external_invoice_reference")
        batch_op.drop_column("due_date")
        batch_op.drop_column("invoice_date")
        batch_op.drop_column("external_invoice_number")
        batch_op.drop_column("external_invoice_service")
        batch_op.drop_column("transferred_to_invoicing_at")
