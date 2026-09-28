"""add inventory reservations and line-level refunds

Revision ID: e4f6a8b0c2d3
Revises: d2e4f6a8b0c1
Create Date: 2026-09-28 07:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e4f6a8b0c2d3"
down_revision: Union[str, None] = "d2e4f6a8b0c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "inventory_reservations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("job_item_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("warehouse_location_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=18, scale=3), nullable=False),
        sa.Column("status", sa.String(length=30), server_default="active", nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("consumed_at", sa.DateTime(), nullable=True),
        sa.Column("released_at", sa.DateTime(), nullable=True),
        sa.Column("release_reason", sa.Text(), nullable=True),
        sa.Column("inventory_transaction_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["inventory_transaction_id"], ["inventory_transactions.id"]),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.ForeignKeyConstraint(["job_item_id"], ["job_items.id"]),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.ForeignKeyConstraint(["warehouse_location_id"], ["warehouse_locations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "job_item_id",
            "warehouse_location_id",
            name="ux_inventory_reservation_item_location",
        ),
    )
    op.create_index("ix_inventory_reservations_id", "inventory_reservations", ["id"])
    op.create_index("ix_inventory_reservations_job_id", "inventory_reservations", ["job_id"])
    op.create_index("ix_inventory_reservations_job_item_id", "inventory_reservations", ["job_item_id"])
    op.create_index("ix_inventory_reservations_product_id", "inventory_reservations", ["product_id"])
    op.create_index(
        "ix_inventory_reservations_warehouse_location_id",
        "inventory_reservations",
        ["warehouse_location_id"],
    )
    op.create_index("ix_inventory_reservations_status", "inventory_reservations", ["status"])

    op.create_table(
        "refund_lines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("refund_id", sa.Integer(), nullable=False),
        sa.Column("sale_line_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column("gross_amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("net_amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("vat_amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("restocked", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("restock_location_id", sa.Integer(), nullable=True),
        sa.Column(
            "inventory_cost_ex_vat",
            sa.Numeric(precision=12, scale=2),
            server_default="0",
            nullable=False,
        ),
        sa.Column("inventory_transaction_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["inventory_transaction_id"], ["inventory_transactions.id"]),
        sa.ForeignKeyConstraint(["refund_id"], ["refunds.id"]),
        sa.ForeignKeyConstraint(["restock_location_id"], ["warehouse_locations.id"]),
        sa.ForeignKeyConstraint(["sale_line_id"], ["sale_lines.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_refund_lines_id", "refund_lines", ["id"])
    op.create_index("ix_refund_lines_refund_id", "refund_lines", ["refund_id"])
    op.create_index("ix_refund_lines_sale_line_id", "refund_lines", ["sale_line_id"])


def downgrade() -> None:
    op.drop_index("ix_refund_lines_sale_line_id", table_name="refund_lines")
    op.drop_index("ix_refund_lines_refund_id", table_name="refund_lines")
    op.drop_index("ix_refund_lines_id", table_name="refund_lines")
    op.drop_table("refund_lines")

    op.drop_index("ix_inventory_reservations_status", table_name="inventory_reservations")
    op.drop_index(
        "ix_inventory_reservations_warehouse_location_id",
        table_name="inventory_reservations",
    )
    op.drop_index("ix_inventory_reservations_product_id", table_name="inventory_reservations")
    op.drop_index("ix_inventory_reservations_job_item_id", table_name="inventory_reservations")
    op.drop_index("ix_inventory_reservations_job_id", table_name="inventory_reservations")
    op.drop_index("ix_inventory_reservations_id", table_name="inventory_reservations")
    op.drop_table("inventory_reservations")
