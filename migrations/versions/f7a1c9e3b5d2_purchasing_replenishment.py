"""add purchase orders and replenishment settings

Revision ID: f7a1c9e3b5d2
Revises: e4f6a8b0c2d3
Create Date: 2026-09-28 09:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f7a1c9e3b5d2"
down_revision: Union[str, None] = "e4f6a8b0c2d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("products") as batch_op:
        batch_op.add_column(sa.Column("preferred_supplier_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("supplier_product_code", sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column("reorder_point", sa.Numeric(precision=18, scale=3), server_default="0", nullable=False))
        batch_op.add_column(sa.Column("target_stock_quantity", sa.Numeric(precision=18, scale=3), server_default="0", nullable=False))
        batch_op.create_foreign_key("fk_products_preferred_supplier_id_suppliers", "suppliers", ["preferred_supplier_id"], ["id"])
    op.create_index("ix_products_preferred_supplier_id", "products", ["preferred_supplier_id"])

    op.create_table(
        "purchase_orders",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("order_number", sa.String(length=40), nullable=False),
        sa.Column("supplier_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), server_default="draft", nullable=False),
        sa.Column("order_date", sa.Date(), nullable=False),
        sa.Column("expected_date", sa.Date(), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("ordered_at", sa.DateTime(), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["supplier_id"], ["suppliers.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_number"),
    )
    op.create_index("ix_purchase_orders_id", "purchase_orders", ["id"])
    op.create_index("ix_purchase_orders_order_number", "purchase_orders", ["order_number"])
    op.create_index("ix_purchase_orders_supplier_id", "purchase_orders", ["supplier_id"])
    op.create_index("ix_purchase_orders_status", "purchase_orders", ["status"])
    op.create_index("ix_purchase_orders_order_date", "purchase_orders", ["order_date"])

    op.create_table(
        "purchase_order_lines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("purchase_order_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("destination_location_id", sa.Integer(), nullable=False),
        sa.Column("supplier_product_code", sa.String(length=100), nullable=True),
        sa.Column("ordered_quantity", sa.Numeric(precision=18, scale=3), nullable=False),
        sa.Column("received_quantity", sa.Numeric(precision=18, scale=3), server_default="0", nullable=False),
        sa.Column("unit_cost_ex_vat", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("vat_rate", sa.Numeric(precision=5, scale=2), server_default="24", nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["destination_location_id"], ["warehouse_locations.id"]),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.ForeignKeyConstraint(["purchase_order_id"], ["purchase_orders.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("id", "purchase_order_id", "product_id"):
        op.create_index(f"ix_purchase_order_lines_{column}", "purchase_order_lines", [column])

    with op.batch_alter_table("goods_receipts") as batch_op:
        batch_op.add_column(sa.Column("purchase_order_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key("fk_goods_receipts_purchase_order_id_purchase_orders", "purchase_orders", ["purchase_order_id"], ["id"])
    op.create_index("ix_goods_receipts_purchase_order_id", "goods_receipts", ["purchase_order_id"])
    with op.batch_alter_table("goods_receipt_lines") as batch_op:
        batch_op.add_column(sa.Column("purchase_order_line_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key("fk_goods_receipt_lines_purchase_order_line_id_purchase_order_lines", "purchase_order_lines", ["purchase_order_line_id"], ["id"])
    op.create_index("ix_goods_receipt_lines_purchase_order_line_id", "goods_receipt_lines", ["purchase_order_line_id"])


def downgrade() -> None:
    op.drop_index("ix_goods_receipt_lines_purchase_order_line_id", table_name="goods_receipt_lines")
    with op.batch_alter_table("goods_receipt_lines") as batch_op:
        batch_op.drop_constraint("fk_goods_receipt_lines_purchase_order_line_id_purchase_order_lines", type_="foreignkey")
        batch_op.drop_column("purchase_order_line_id")
    op.drop_index("ix_goods_receipts_purchase_order_id", table_name="goods_receipts")
    with op.batch_alter_table("goods_receipts") as batch_op:
        batch_op.drop_constraint("fk_goods_receipts_purchase_order_id_purchase_orders", type_="foreignkey")
        batch_op.drop_column("purchase_order_id")
    for column in ("product_id", "purchase_order_id", "id"):
        op.drop_index(f"ix_purchase_order_lines_{column}", table_name="purchase_order_lines")
    op.drop_table("purchase_order_lines")
    for index in ("ix_purchase_orders_order_date", "ix_purchase_orders_status", "ix_purchase_orders_supplier_id", "ix_purchase_orders_order_number", "ix_purchase_orders_id"):
        op.drop_index(index, table_name="purchase_orders")
    op.drop_table("purchase_orders")
    op.drop_index("ix_products_preferred_supplier_id", table_name="products")
    with op.batch_alter_table("products") as batch_op:
        batch_op.drop_constraint("fk_products_preferred_supplier_id_suppliers", type_="foreignkey")
        batch_op.drop_column("target_stock_quantity")
        batch_op.drop_column("reorder_point")
        batch_op.drop_column("supplier_product_code")
        batch_op.drop_column("preferred_supplier_id")
