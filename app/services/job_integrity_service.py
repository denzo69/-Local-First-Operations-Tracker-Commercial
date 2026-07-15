from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.models import InventoryTransaction, Job, Receipt, Sale


class JobIntegrityError(RuntimeError):
    """Raised when deleting a document would break operational or financial history."""


def _has_row(connection, statement) -> bool:
    return connection.execute(statement.limit(1)).first() is not None


def assert_job_can_be_deleted(db: Session, document_id: int) -> None:
    """Validate references before SQLAlchemy can detach related rows during flush."""
    if db.query(Job.id).filter(Job.source_job_id == document_id).first() is not None:
        raise JobIntegrityError(
            "This document cannot be deleted because another document was created from it."
        )

    if db.query(Sale.id).filter(Sale.work_order_id == document_id).first() is not None:
        raise JobIntegrityError(
            "This document cannot be deleted because it is linked to a finalized sale or invoice handoff."
        )

    if (
        db.query(InventoryTransaction.id)
        .filter(InventoryTransaction.work_order_id == document_id)
        .first()
        is not None
    ):
        raise JobIntegrityError(
            "This document cannot be deleted because it is linked to inventory history."
        )

    if db.query(Receipt.id).filter(Receipt.job_id == document_id).first() is not None:
        raise JobIntegrityError(
            "This document cannot be deleted because a printable document snapshot has already been created."
        )


@event.listens_for(Job, "before_delete")
def prevent_referenced_job_delete(mapper, connection, target: Job) -> None:
    """Keep document history safe for deletes outside the HTTP route layer too."""
    document_id = target.id

    if _has_row(
        connection,
        select(Job.id).where(Job.source_job_id == document_id),
    ):
        raise JobIntegrityError(
            "This document cannot be deleted because another document was created from it."
        )

    if _has_row(
        connection,
        select(Sale.id).where(Sale.work_order_id == document_id),
    ):
        raise JobIntegrityError(
            "This document cannot be deleted because it is linked to a finalized sale or invoice handoff."
        )

    if _has_row(
        connection,
        select(InventoryTransaction.id).where(InventoryTransaction.work_order_id == document_id),
    ):
        raise JobIntegrityError(
            "This document cannot be deleted because it is linked to inventory history."
        )

    if _has_row(
        connection,
        select(Receipt.id).where(Receipt.job_id == document_id),
    ):
        raise JobIntegrityError(
            "This document cannot be deleted because a printable document snapshot has already been created."
        )
