"""backfill sale document numbers

Revision ID: c9d1e7a4b2f6
Revises: b5c8d2e4f6a1
Create Date: 2026-07-12 12:30:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = "c9d1e7a4b2f6"
down_revision: Union[str, None] = "b5c8d2e4f6a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE sales
        SET document_number = 'SALE-' || strftime('%Y', COALESCE(sold_at, created_at, CURRENT_TIMESTAMP)) || '-' || printf('%06d', id)
        WHERE document_number IS NULL OR document_number = ''
        """
    )
    op.execute(
        """
        INSERT OR IGNORE INTO settings (key, value, updated_at)
        VALUES ('sale_document_prefix', 'SALE-', CURRENT_TIMESTAMP)
        """
    )
    op.execute(
        """
        INSERT OR IGNORE INTO settings (key, value, updated_at)
        VALUES ('sale_document_padding', '6', CURRENT_TIMESTAMP)
        """
    )
    op.execute(
        """
        INSERT OR IGNORE INTO settings (key, value, updated_at)
        VALUES ('sale_document_annual_reset', 'false', CURRENT_TIMESTAMP)
        """
    )
    op.execute(
        """
        INSERT OR IGNORE INTO settings (key, value, updated_at)
        SELECT 'next_sale_document_sequence', CAST(COALESCE(MAX(id), 0) + 1 AS TEXT), CURRENT_TIMESTAMP
        FROM sales
        """
    )
    op.execute(
        """
        INSERT OR IGNORE INTO settings (key, value, updated_at)
        VALUES ('sale_document_sequence_year', strftime('%Y', CURRENT_TIMESTAMP), CURRENT_TIMESTAMP)
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM settings WHERE key IN ('sale_document_prefix', 'sale_document_padding', 'sale_document_annual_reset', 'next_sale_document_sequence', 'sale_document_sequence_year')")
