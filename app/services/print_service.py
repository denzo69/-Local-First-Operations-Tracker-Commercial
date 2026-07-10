import json
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models import Job, Receipt
from app.services.audit_service import log_audit_event
from app.services.money_service import sum_money
from app.services.settings_service import get_app_settings


def build_snapshot_payload(job: Job, document_type: str) -> str:
    payload = {
        "document_type": document_type,
        "receipt_number": job.receipt_number,
        "work_order_id": job.id,
        "title": job.title,
        "customer": {
            "name": job.customer.name if job.customer else None,
            "phone": job.customer.phone if job.customer else None,
            "email": job.customer.email if job.customer else None,
        },
        "status": job.status.name if job.status else "Received",
        "arrival_date": job.arrival_date.isoformat() if job.arrival_date else None,
        "requested_pickup_date": (
            job.requested_pickup_date.isoformat() if job.requested_pickup_date else None
        ),
        "description": job.description,
        "notes": job.notes,
        "items": [
            {
                "description": item.description,
                "quantity": str(item.quantity),
                "unit_price": str(item.unit_price),
                "vat_percent": str(item.vat_percent),
                "line_total": str(item.line_total),
            }
            for item in job.items
        ],
        "total": str(sum_money(item.line_total for item in job.items)),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def ensure_print_snapshot(db: Session, job: Job, document_type: str) -> Receipt:
    receipt = (
        db.query(Receipt)
        .filter(Receipt.job_id == job.id, Receipt.receipt_type == document_type)
        .first()
    )
    if receipt is not None:
        return receipt

    receipt = Receipt(
        job_id=job.id,
        receipt_number=job.receipt_number,
        receipt_type=document_type,
        printed_at=datetime.now(UTC),
        editable_snapshot=build_snapshot_payload(job, document_type),
    )
    db.add(receipt)
    log_audit_event(
        db,
        event_type="document.printed",
        entity_type="job",
        entity_id=job.id,
        description=f"Print snapshot created for {document_type}.",
    )
    db.commit()
    db.refresh(receipt)
    return receipt


def build_print_context(db: Session, job: Job, document_type: str) -> dict:
    receipt = ensure_print_snapshot(db, job, document_type)
    return {
        "company": get_app_settings(db),
        "document_type": document_type,
        "printed_at": receipt.printed_at,
        "print_snapshot": receipt,
        "job_total": sum_money(item.line_total for item in job.items),
    }
