from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Job
from app.services.job_integrity_service import assert_job_can_be_deleted


def _require_document_type(
    request: Request,
    db: Session,
    *,
    expected_type: str,
) -> None:
    raw_job_id = request.path_params.get("job_id")
    if raw_job_id is None:
        return

    try:
        job_id = int(raw_job_id)
    except (TypeError, ValueError):
        # Let FastAPI's normal path validation return the established 422 response.
        return

    job = db.get(Job, job_id)
    if job is None:
        # Let the endpoint preserve its document-specific not-found message.
        return

    actual_type = job.document_type or "work_order"
    if actual_type != expected_type:
        raise HTTPException(status_code=404, detail="Document not found")

    is_document_delete = (
        request.method == "POST"
        and request.url.path.endswith("/delete")
        and "item_id" not in request.path_params
    )
    if is_document_delete:
        assert_job_can_be_deleted(db, job_id)


def require_work_order_route(request: Request, db: Session = Depends(get_db)) -> None:
    _require_document_type(request, db, expected_type="work_order")


def require_delivery_note_route(request: Request, db: Session = Depends(get_db)) -> None:
    _require_document_type(request, db, expected_type="delivery_note")


def require_quote_route(request: Request, db: Session = Depends(get_db)) -> None:
    _require_document_type(request, db, expected_type="quote")
