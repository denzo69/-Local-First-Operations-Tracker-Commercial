from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import AuditLog
from app.template_context import templates

router = APIRouter(prefix="/audit-log", tags=["audit-log"])
settings = get_settings()


@router.get("", response_class=HTMLResponse)
def list_audit_log(request: Request, db: Session = Depends(get_db)):
    events = db.query(AuditLog).order_by(AuditLog.created_at.desc()).limit(200).all()
    return templates.TemplateResponse(
        "audit_log/list.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": "audit_log",
            "events": events,
        },
    )
