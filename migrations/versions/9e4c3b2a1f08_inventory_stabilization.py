"""inventory stabilization

Revision ID: 9e4c3b2a1f08
Revises: 7c2a91f4d8e3
Create Date: 2026-07-12 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "9e4c3b2a1f08"
down_revision: Union[str, None] = "7c2a91f4d8e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("can_receive_sales_credit", sa.Boolean(), nullable=True))
    op.execute("UPDATE users SET can_receive_sales_credit = 0 WHERE can_receive_sales_credit IS NULL")
    op.execute(
        """
        UPDATE users
        SET can_receive_sales_credit = 1
        WHERE role_id IN (SELECT id FROM roles WHERE code = 'seller')
        """
    )

    with op.batch_alter_table("sales", recreate="always") as batch_op:
        batch_op.add_column(sa.Column("sold_by_user_id", sa.Integer(), sa.ForeignKey("users.id", name="fk_sales_sold_by_user_id_users"), nullable=True))
        batch_op.add_column(sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id", name="fk_sales_created_by_user_id_users"), nullable=True))
        batch_op.add_column(sa.Column("cash_register_id", sa.Integer(), sa.ForeignKey("cash_registers.id", name="fk_sales_cash_register_id_cash_registers"), nullable=True))
        batch_op.add_column(sa.Column("created_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("seller_override_reason", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("seller_overridden_by_user_id", sa.Integer(), sa.ForeignKey("users.id", name="fk_sales_seller_overridden_by_user_id_users"), nullable=True))
        batch_op.add_column(sa.Column("seller_overridden_at", sa.DateTime(), nullable=True))

    with op.batch_alter_table("payments", recreate="always") as batch_op:
        batch_op.add_column(sa.Column("received_by_user_id", sa.Integer(), sa.ForeignKey("users.id", name="fk_payments_received_by_user_id_users"), nullable=True))

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
    op.execute(
        """
        UPDATE payments
        SET received_by_user_id = (
            SELECT sales.created_by_user_id
            FROM sales
            WHERE sales.id = payments.sale_id
        )
        WHERE received_by_user_id IS NULL
        """
    )
    op.execute("UPDATE payments SET received_by_user_id = seller_id WHERE received_by_user_id IS NULL")

    op.create_index("ix_sales_sold_by_user_id", "sales", ["sold_by_user_id"])
    op.create_index("ix_sales_created_by_user_id", "sales", ["created_by_user_id"])
    op.create_index("ix_sales_cash_register_id", "sales", ["cash_register_id"])
    op.create_index("ix_payments_received_by_user_id", "payments", ["received_by_user_id"])

    op.add_column("goods_receipts", sa.Column("freight_vat_rate", sa.Numeric(5, 2), nullable=True))
    op.add_column("goods_receipts", sa.Column("freight_vat_amount", sa.Numeric(12, 2), nullable=True))
    op.add_column("goods_receipts", sa.Column("freight_total_inc_vat", sa.Numeric(12, 2), nullable=True))
    op.add_column("goods_receipts", sa.Column("other_costs_vat_rate", sa.Numeric(5, 2), nullable=True))
    op.add_column("goods_receipts", sa.Column("other_costs_vat_amount", sa.Numeric(12, 2), nullable=True))
    op.add_column("goods_receipts", sa.Column("other_costs_total_inc_vat", sa.Numeric(12, 2), nullable=True))
    op.execute("UPDATE goods_receipts SET freight_vat_rate = 0 WHERE freight_vat_rate IS NULL")
    op.execute("UPDATE goods_receipts SET freight_vat_amount = 0 WHERE freight_vat_amount IS NULL")
    op.execute("UPDATE goods_receipts SET freight_total_inc_vat = COALESCE(freight_total_ex_vat, 0) WHERE freight_total_inc_vat IS NULL")
    op.execute("UPDATE goods_receipts SET other_costs_vat_rate = 0 WHERE other_costs_vat_rate IS NULL")
    op.execute("UPDATE goods_receipts SET other_costs_vat_amount = 0 WHERE other_costs_vat_amount IS NULL")
    op.execute("UPDATE goods_receipts SET other_costs_total_inc_vat = COALESCE(other_costs_total_ex_vat, 0) WHERE other_costs_total_inc_vat IS NULL")

    op.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_inventory_transactions_no_update
        BEFORE UPDATE ON inventory_transactions
        BEGIN
            SELECT RAISE(ABORT, 'inventory_transactions are immutable');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_inventory_transactions_no_delete
        BEFORE DELETE ON inventory_transactions
        BEGIN
            SELECT RAISE(ABORT, 'inventory_transactions are immutable');
        END
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_inventory_transactions_no_delete")
    op.execute("DROP TRIGGER IF EXISTS trg_inventory_transactions_no_update")
    op.drop_column("goods_receipts", "other_costs_total_inc_vat")
    op.drop_column("goods_receipts", "other_costs_vat_amount")
    op.drop_column("goods_receipts", "other_costs_vat_rate")
    op.drop_column("goods_receipts", "freight_total_inc_vat")
    op.drop_column("goods_receipts", "freight_vat_amount")
    op.drop_column("goods_receipts", "freight_vat_rate")
    op.drop_index("ix_payments_received_by_user_id", table_name="payments")
    op.drop_index("ix_sales_cash_register_id", table_name="sales")
    op.drop_index("ix_sales_created_by_user_id", table_name="sales")
    op.drop_index("ix_sales_sold_by_user_id", table_name="sales")
    with op.batch_alter_table("payments", recreate="always") as batch_op:
        batch_op.drop_column("received_by_user_id")
    with op.batch_alter_table("sales", recreate="always") as batch_op:
        batch_op.drop_column("seller_overridden_at")
        batch_op.drop_column("seller_overridden_by_user_id")
        batch_op.drop_column("seller_override_reason")
        batch_op.drop_column("created_at")
        batch_op.drop_column("cash_register_id")
        batch_op.drop_column("created_by_user_id")
        batch_op.drop_column("sold_by_user_id")
    op.drop_column("users", "can_receive_sales_credit")
