"""sale seller attribution

Revision ID: 8d4b2f3a91c7
Revises: 3f0d1c9a8b22
Create Date: 2026-07-11 17:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "8d4b2f3a91c7"
down_revision: Union[str, None] = "3f0d1c9a8b22"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("sales", sa.Column("sold_by_user_id", sa.Integer(), nullable=True))
    op.add_column("sales", sa.Column("created_by_user_id", sa.Integer(), nullable=True))
    op.add_column("sales", sa.Column("cash_register_id", sa.Integer(), nullable=True))
    op.add_column("sales", sa.Column("created_at", sa.DateTime(), nullable=True))
    op.add_column("sales", sa.Column("seller_override_reason", sa.Text(), nullable=True))
    op.add_column("sales", sa.Column("seller_overridden_by_user_id", sa.Integer(), nullable=True))
    op.add_column("sales", sa.Column("seller_overridden_at", sa.DateTime(), nullable=True))

    op.execute("UPDATE sales SET sold_by_user_id = seller_id WHERE sold_by_user_id IS NULL")
    op.execute("UPDATE sales SET created_by_user_id = seller_id WHERE created_by_user_id IS NULL")
    op.execute(
        """
        UPDATE sales
        SET cash_register_id = (
            SELECT shifts.cash_register_id
            FROM shifts
            WHERE shifts.id = sales.shift_id
        )
        WHERE cash_register_id IS NULL
        """
    )
    op.execute("UPDATE sales SET created_at = sold_at WHERE created_at IS NULL")

    op.create_index("ix_sales_sold_by_user_id", "sales", ["sold_by_user_id"])
    op.create_index("ix_sales_created_by_user_id", "sales", ["created_by_user_id"])
    op.create_index("ix_sales_cash_register_id", "sales", ["cash_register_id"])


def downgrade() -> None:
    op.drop_index("ix_sales_cash_register_id", table_name="sales")
    op.drop_index("ix_sales_created_by_user_id", table_name="sales")
    op.drop_index("ix_sales_sold_by_user_id", table_name="sales")
    op.drop_column("sales", "seller_overridden_at")
    op.drop_column("sales", "seller_overridden_by_user_id")
    op.drop_column("sales", "seller_override_reason")
    op.drop_column("sales", "created_at")
    op.drop_column("sales", "cash_register_id")
    op.drop_column("sales", "created_by_user_id")
    op.drop_column("sales", "sold_by_user_id")
