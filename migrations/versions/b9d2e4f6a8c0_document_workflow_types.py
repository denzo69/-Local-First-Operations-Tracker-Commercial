"""document workflow types

Revision ID: b9d2e4f6a8c0
Revises: a8c1e3f5b7d9
Create Date: 2026-07-14 22:25:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b9d2e4f6a8c0"
down_revision: Union[str, None] = "a8c1e3f5b7d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("jobs", recreate="always") as batch_op:
        batch_op.add_column(sa.Column("document_type", sa.String(length=50), nullable=True))
        batch_op.add_column(sa.Column("source_job_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("converted_at", sa.DateTime(), nullable=True))
        batch_op.create_foreign_key("fk_jobs_source_job_id_jobs", "jobs", ["source_job_id"], ["id"])

    op.execute("UPDATE jobs SET document_type = 'work_order' WHERE document_type IS NULL OR document_type = ''")
    op.create_index("ix_jobs_document_type", "jobs", ["document_type"])
    op.create_index("ix_jobs_source_job_id", "jobs", ["source_job_id"])


def downgrade() -> None:
    op.drop_index("ix_jobs_source_job_id", table_name="jobs")
    op.drop_index("ix_jobs_document_type", table_name="jobs")
    with op.batch_alter_table("jobs", recreate="always") as batch_op:
        batch_op.drop_constraint("fk_jobs_source_job_id_jobs", type_="foreignkey")
        batch_op.drop_column("converted_at")
        batch_op.drop_column("source_job_id")
        batch_op.drop_column("document_type")
