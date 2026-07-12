"""weighted average inventory cost

Revision ID: 7c2a91f4d8e3
Revises: 3f0d1c9a8b22
Create Date: 2026-07-11 19:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7c2a91f4d8e3"
down_revision: Union[str, None] = "3f0d1c9a8b22"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("products", sa.Column("current_weighted_average_cost_ex_vat", sa.Numeric(18, 6), nullable=True))
    op.add_column("products", sa.Column("current_inventory_quantity", sa.Numeric(18, 3), nullable=True))
    op.add_column("products", sa.Column("current_inventory_value_ex_vat", sa.Numeric(18, 2), nullable=True))
    op.add_column("products", sa.Column("current_purchase_price_ex_vat", sa.Numeric(12, 2), nullable=True))
    op.add_column("products", sa.Column("current_purchase_price_inc_vat", sa.Numeric(12, 2), nullable=True))
    op.execute("UPDATE products SET current_inventory_quantity = 0 WHERE current_inventory_quantity IS NULL")
    op.execute("UPDATE products SET current_inventory_value_ex_vat = 0 WHERE current_inventory_value_ex_vat IS NULL")
    op.add_column("sales", sa.Column("cost_of_goods_sold_ex_vat", sa.Numeric(12, 2), nullable=True))
    op.add_column("sales", sa.Column("gross_profit_ex_vat", sa.Numeric(12, 2), nullable=True))
    op.add_column("sales", sa.Column("gross_margin_percent", sa.Numeric(7, 3), nullable=True))
    op.add_column("sale_lines", sa.Column("cost_of_goods_sold_ex_vat", sa.Numeric(12, 2), nullable=True))
    op.add_column("sale_lines", sa.Column("gross_profit_ex_vat", sa.Numeric(12, 2), nullable=True))
    op.add_column("sale_lines", sa.Column("gross_margin_percent", sa.Numeric(7, 3), nullable=True))
    op.execute("UPDATE sales SET cost_of_goods_sold_ex_vat = 0 WHERE cost_of_goods_sold_ex_vat IS NULL")
    op.execute("UPDATE sales SET gross_profit_ex_vat = 0 WHERE gross_profit_ex_vat IS NULL")
    op.execute("UPDATE sale_lines SET cost_of_goods_sold_ex_vat = 0 WHERE cost_of_goods_sold_ex_vat IS NULL")
    op.execute("UPDATE sale_lines SET gross_profit_ex_vat = 0 WHERE gross_profit_ex_vat IS NULL")

    op.create_table(
        "suppliers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("business_id", sa.String(100), nullable=True),
        sa.Column("contact_name", sa.String(255), nullable=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("phone", sa.String(100), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_suppliers_id", "suppliers", ["id"])
    op.create_index("ix_suppliers_name", "suppliers", ["name"])

    op.create_table(
        "warehouses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("code", sa.String(50), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("is_external", sa.Boolean(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("code"),
    )
    op.create_index("ix_warehouses_id", "warehouses", ["id"])
    op.create_index("ix_warehouses_code", "warehouses", ["code"])
    op.create_index("ix_warehouses_name", "warehouses", ["name"])

    op.create_table(
        "warehouse_locations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("warehouse_id", sa.Integer(), sa.ForeignKey("warehouses.id"), nullable=False),
        sa.Column("parent_id", sa.Integer(), sa.ForeignKey("warehouse_locations.id"), nullable=True),
        sa.Column("code", sa.String(50), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("location_type", sa.String(50), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_warehouse_locations_id", "warehouse_locations", ["id"])
    op.create_index("ix_warehouse_locations_code", "warehouse_locations", ["code"])

    op.create_table(
        "inventory_balances",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("warehouse_location_id", sa.Integer(), sa.ForeignKey("warehouse_locations.id"), nullable=False),
        sa.Column("quantity_on_hand", sa.Numeric(18, 3), nullable=True),
        sa.Column("quantity_reserved", sa.Numeric(18, 3), nullable=True),
        sa.Column("quantity_available", sa.Numeric(18, 3), nullable=True),
        sa.Column("weighted_average_cost_ex_vat", sa.Numeric(18, 6), nullable=True),
        sa.Column("inventory_value_ex_vat", sa.Numeric(18, 2), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("product_id", "warehouse_location_id", name="ux_inventory_balance_product_location"),
    )
    op.create_index("ix_inventory_balances_id", "inventory_balances", ["id"])

    op.create_table(
        "goods_receipts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("supplier_id", sa.Integer(), sa.ForeignKey("suppliers.id"), nullable=False),
        sa.Column("receipt_date", sa.Date(), nullable=False),
        sa.Column("delivery_number", sa.String(100), nullable=True),
        sa.Column("invoice_number", sa.String(100), nullable=True),
        sa.Column("freight_total_ex_vat", sa.Numeric(12, 2), nullable=True),
        sa.Column("other_costs_total_ex_vat", sa.Numeric(12, 2), nullable=True),
        sa.Column("allocation_method", sa.String(50), nullable=True),
        sa.Column("status", sa.String(50), nullable=True),
        sa.Column("received_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("posted_at", sa.DateTime(), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(), nullable=True),
        sa.Column("cancellation_reason", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_goods_receipts_id", "goods_receipts", ["id"])
    op.create_index("ix_goods_receipts_receipt_date", "goods_receipts", ["receipt_date"])
    op.create_index("ix_goods_receipts_delivery_number", "goods_receipts", ["delivery_number"])
    op.create_index("ix_goods_receipts_invoice_number", "goods_receipts", ["invoice_number"])
    op.create_index("ix_goods_receipts_status", "goods_receipts", ["status"])

    op.create_table(
        "goods_receipt_lines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("goods_receipt_id", sa.Integer(), sa.ForeignKey("goods_receipts.id"), nullable=False),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("destination_location_id", sa.Integer(), sa.ForeignKey("warehouse_locations.id"), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 3), nullable=False),
        sa.Column("purchase_unit_price_ex_vat", sa.Numeric(12, 2), nullable=False),
        sa.Column("vat_rate", sa.Numeric(5, 2), nullable=True),
        sa.Column("purchase_unit_price_inc_vat", sa.Numeric(12, 2), nullable=False),
        sa.Column("allocated_freight_ex_vat", sa.Numeric(12, 2), nullable=True),
        sa.Column("allocated_other_costs_ex_vat", sa.Numeric(12, 2), nullable=True),
        sa.Column("landed_unit_cost_ex_vat", sa.Numeric(18, 6), nullable=True),
        sa.Column("line_total_ex_vat", sa.Numeric(12, 2), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_goods_receipt_lines_id", "goods_receipt_lines", ["id"])

    op.create_table(
        "inventory_transactions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("warehouse_id", sa.Integer(), sa.ForeignKey("warehouses.id"), nullable=False),
        sa.Column("shelf_location_id", sa.Integer(), sa.ForeignKey("warehouse_locations.id"), nullable=True),
        sa.Column("transaction_type", sa.String(50), nullable=False),
        sa.Column("quantity_change", sa.Numeric(18, 3), nullable=False),
        sa.Column("unit_cost_ex_vat", sa.Numeric(18, 6), nullable=False),
        sa.Column("allocated_freight_cost", sa.Numeric(12, 2), nullable=True),
        sa.Column("allocated_other_cost", sa.Numeric(12, 2), nullable=True),
        sa.Column("total_inventory_cost", sa.Numeric(18, 2), nullable=False),
        sa.Column("inventory_value_before", sa.Numeric(18, 2), nullable=False),
        sa.Column("inventory_value_after", sa.Numeric(18, 2), nullable=False),
        sa.Column("stock_before", sa.Numeric(18, 3), nullable=False),
        sa.Column("stock_after", sa.Numeric(18, 3), nullable=False),
        sa.Column("weighted_average_cost_before", sa.Numeric(18, 6), nullable=True),
        sa.Column("weighted_average_cost_after", sa.Numeric(18, 6), nullable=True),
        sa.Column("supplier_id", sa.Integer(), sa.ForeignKey("suppliers.id"), nullable=True),
        sa.Column("purchase_invoice_number", sa.String(100), nullable=True),
        sa.Column("delivery_note_number", sa.String(100), nullable=True),
        sa.Column("goods_receipt_id", sa.Integer(), sa.ForeignKey("goods_receipts.id"), nullable=True),
        sa.Column("work_order_id", sa.Integer(), sa.ForeignKey("jobs.id"), nullable=True),
        sa.Column("sale_id", sa.Integer(), sa.ForeignKey("sales.id"), nullable=True),
        sa.Column("adjustment_reason", sa.Text(), nullable=True),
        sa.Column("reference", sa.String(255), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("reversal_of_transaction_id", sa.Integer(), sa.ForeignKey("inventory_transactions.id"), nullable=True),
    )
    op.create_index("ix_inventory_transactions_id", "inventory_transactions", ["id"])
    op.create_index("ix_inventory_transactions_warehouse_id", "inventory_transactions", ["warehouse_id"])
    op.create_index("ix_inventory_transactions_shelf_location_id", "inventory_transactions", ["shelf_location_id"])
    op.create_index("ix_inventory_transactions_transaction_type", "inventory_transactions", ["transaction_type"])
    op.create_index("ix_inventory_transactions_supplier_id", "inventory_transactions", ["supplier_id"])
    op.create_index("ix_inventory_transactions_purchase_invoice_number", "inventory_transactions", ["purchase_invoice_number"])
    op.create_index("ix_inventory_transactions_delivery_note_number", "inventory_transactions", ["delivery_note_number"])
    op.create_index("ix_inventory_transactions_created_at", "inventory_transactions", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_inventory_transactions_created_at", table_name="inventory_transactions")
    op.drop_index("ix_inventory_transactions_delivery_note_number", table_name="inventory_transactions")
    op.drop_index("ix_inventory_transactions_purchase_invoice_number", table_name="inventory_transactions")
    op.drop_index("ix_inventory_transactions_supplier_id", table_name="inventory_transactions")
    op.drop_index("ix_inventory_transactions_transaction_type", table_name="inventory_transactions")
    op.drop_index("ix_inventory_transactions_shelf_location_id", table_name="inventory_transactions")
    op.drop_index("ix_inventory_transactions_warehouse_id", table_name="inventory_transactions")
    op.drop_index("ix_inventory_transactions_id", table_name="inventory_transactions")
    op.drop_table("inventory_transactions")
    op.drop_index("ix_goods_receipt_lines_id", table_name="goods_receipt_lines")
    op.drop_table("goods_receipt_lines")
    op.drop_index("ix_goods_receipts_status", table_name="goods_receipts")
    op.drop_index("ix_goods_receipts_invoice_number", table_name="goods_receipts")
    op.drop_index("ix_goods_receipts_delivery_number", table_name="goods_receipts")
    op.drop_index("ix_goods_receipts_receipt_date", table_name="goods_receipts")
    op.drop_index("ix_goods_receipts_id", table_name="goods_receipts")
    op.drop_table("goods_receipts")
    op.drop_index("ix_inventory_balances_id", table_name="inventory_balances")
    op.drop_table("inventory_balances")
    op.drop_index("ix_warehouse_locations_code", table_name="warehouse_locations")
    op.drop_index("ix_warehouse_locations_id", table_name="warehouse_locations")
    op.drop_table("warehouse_locations")
    op.drop_index("ix_warehouses_name", table_name="warehouses")
    op.drop_index("ix_warehouses_code", table_name="warehouses")
    op.drop_index("ix_warehouses_id", table_name="warehouses")
    op.drop_table("warehouses")
    op.drop_index("ix_suppliers_name", table_name="suppliers")
    op.drop_index("ix_suppliers_id", table_name="suppliers")
    op.drop_table("suppliers")
    op.drop_column("sale_lines", "gross_margin_percent")
    op.drop_column("sale_lines", "gross_profit_ex_vat")
    op.drop_column("sale_lines", "cost_of_goods_sold_ex_vat")
    op.drop_column("sales", "gross_margin_percent")
    op.drop_column("sales", "gross_profit_ex_vat")
    op.drop_column("sales", "cost_of_goods_sold_ex_vat")
    op.drop_column("products", "current_purchase_price_inc_vat")
    op.drop_column("products", "current_purchase_price_ex_vat")
    op.drop_column("products", "current_inventory_value_ex_vat")
    op.drop_column("products", "current_inventory_quantity")
    op.drop_column("products", "current_weighted_average_cost_ex_vat")
