from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.services.audit_service import log_audit_event
from app.services.backup_service import backup_health, cleanup_retention, create_backup, list_backups, restore_backup
from app.services.backup_scheduler_service import get_backup_scheduler_status
from app.services.maintenance_service import maintenance_mode
from app.template_context import templates

router = APIRouter(prefix="/backups", tags=["backups"])
settings = get_settings()


@router.get("", response_class=HTMLResponse)
def show_backups(request: Request):
    return templates.TemplateResponse(
        "backups/list.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": "backups",
            "backups": list_backups(),
            "health": backup_health(),
            "scheduler": get_backup_scheduler_status(),
        },
    )


@router.post("")
@router.post("/create")
def create_manual_backup(db: Session = Depends(get_db)):
    try:
        backup = create_backup()
        cleanup_retention(keep=settings.backup_retention_count)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log_audit_event(
        db,
        event_type="backup_created",
        entity_type="backup",
        entity_id=0,
        description=f"Backup created: {backup.name}.",
    )
    db.commit()
    return RedirectResponse(url="/backups", status_code=303)


@router.post("/{name}/restore")
def restore_selected_backup(name: str, db: Session = Depends(get_db)):
    try:
        with maintenance_mode():
            restored = restore_backup(name)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log_audit_event(
        db,
        event_type="backup_restored",
        entity_type="backup",
        entity_id=0,
        description=f"Backup restored: {restored.name}.",
    )
    db.commit()
    return RedirectResponse(url="/backups", status_code=303)
