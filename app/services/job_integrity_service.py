from sqlalchemy import event
from sqlalchemy.orm import Session

from app.models import InventoryTransaction, Job, Receipt, Sale


class JobIntegrityError(RuntimeError):
    """Raised when deleting a document would break operational or financial history."""


def assert_job_can_be_deleted(db: Session, document_id: int) -> None:
    """Reject deletion when operational, financial, stock, or print history depends on it."""
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


@event.listens_for(Session, "before_flush")
def prevent_referenced_job_delete(session: Session, flush_context, instances) -> None:
    """Apply the guard to all ORM deletes, not only requests from the web routes."""
    for target in tuple(session.deleted):
        if isinstance(target, Job) and target.id is not None:
            assert_job_can_be_deleted(session, target.id)
