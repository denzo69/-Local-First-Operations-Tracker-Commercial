from sqlalchemy import event, select

from app.models import InventoryTransaction, Job, Receipt, Sale


class JobIntegrityError(RuntimeError):
    """Raised when deleting a document would break operational or financial history."""


def _has_row(connection, statement) -> bool:
    return connection.execute(statement.limit(1)).first() is not None


@event.listens_for(Job, "before_delete")
def prevent_referenced_job_delete(mapper, connection, target: Job) -> None:
    """Keep document, sales, print, and inventory history internally consistent."""
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
