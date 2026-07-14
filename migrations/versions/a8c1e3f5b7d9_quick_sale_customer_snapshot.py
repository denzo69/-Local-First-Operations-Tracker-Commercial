"""quick sale customer snapshot

Revision ID: a8c1e3f5b7d9
Revises: f3a9b7c1d2e4
Create Date: 2026-07-14 21:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a8c1e3f5b7d9"
down_revision: Union[str, None] = "f3a9b7c1d2e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("sales", recreate="always") as batch_op:
        batch_op.add_column(sa.Column("customer_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("customer_name_snapshot", sa.String(length=255), nullable=True))
        batch_op.create_foreign_key("fk_sales_customer_id_customers", "customers", ["customer_id"], ["id"])

    op.create_index("ix_sales_customer_id", "sales", ["customer_id"])


def downgrade() -> None:
    op.drop_index("ix_sales_customer_id", table_name="sales")
    with op.batch_alter_table("sales", recreate="always") as batch_op:
        batch_op.drop_constraint("fk_sales_customer_id_customers", type_="foreignkey")
        batch_op.drop_column("customer_name_snapshot")
        batch_op.drop_column("customer_id")
