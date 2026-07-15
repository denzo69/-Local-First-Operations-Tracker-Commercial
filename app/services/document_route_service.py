from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Job


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
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="Document not found") from exc

    job = db.get(Job, job_id)
    actual_type = (job.document_type or "work_order") if job is not None else None
    if job is None or actual_type != expected_type:
        raise HTTPException(status_code=404, detail="Document not found")


def require_work_order_route(request: Request, db: Session = Depends(get_db)) -> None:
    _require_document_type(request, db, expected_type="work_order")


def require_delivery_note_route(request: Request, db: Session = Depends(get_db)) -> None:
    _require_document_type(request, db, expected_type="delivery_note")


def require_quote_route(request: Request, db: Session = Depends(get_db)) -> None:
    _require_document_type(request, db, expected_type="quote")
