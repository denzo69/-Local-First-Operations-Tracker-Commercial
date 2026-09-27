"""add product identifiers and barcodes

Revision ID: d2e4f6a8b0c1
Revises: d6e8f0a1b2c3
Create Date: 2026-09-27 19:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d2e4f6a8b0c1"
down_revision: Union[str, None] = "d6e8f0a1b2c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("products") as batch_op:
        batch_op.add_column(sa.Column("sku", sa.String(length=100), nullable=True))

    op.execute("UPDATE products SET sku = printf('P-%06d', id) WHERE sku IS NULL OR sku = ''")

    with op.batch_alter_table("products", recreate="always") as batch_op:
        batch_op.alter_column("sku", existing_type=sa.String(length=100), nullable=False)
        batch_op.create_index("ix_products_sku", ["sku"], unique=True)

    op.create_table(
        "product_barcodes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=100), nullable=False),
        sa.Column("symbology", sa.String(length=50), server_default="code128", nullable=False),
        sa.Column("unit_multiplier", sa.Numeric(precision=18, scale=3), server_default="1", nullable=False),
        sa.Column("is_primary", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_product_barcodes_id", "product_barcodes", ["id"])
    op.create_index("ix_product_barcodes_product_id", "product_barcodes", ["product_id"])
    op.create_index("ix_product_barcodes_code", "product_barcodes", ["code"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_product_barcodes_code", table_name="product_barcodes")
    op.drop_index("ix_product_barcodes_product_id", table_name="product_barcodes")
    op.drop_index("ix_product_barcodes_id", table_name="product_barcodes")
    op.drop_table("product_barcodes")
    with op.batch_alter_table("products", recreate="always") as batch_op:
        batch_op.drop_index("ix_products_sku")
        batch_op.drop_column("sku")
