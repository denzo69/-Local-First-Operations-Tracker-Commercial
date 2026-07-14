from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import JobStatus
from app.routes.jobs import ensure_default_job_statuses
from app.services.audit_service import log_audit_event
from app.services.settings_service import (
    DEFAULT_SETTINGS,
    SUPPORTED_LANGUAGES,
    get_app_settings,
    get_current_language,
    set_app_settings,
)
from app.template_context import templates

router = APIRouter(prefix="/settings", tags=["settings"])
settings = get_settings()


@router.get("", response_class=HTMLResponse)
def edit_settings(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        "settings/form.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": "settings",
            "settings_values": get_app_settings(db),
            "supported_languages": SUPPORTED_LANGUAGES,
        },
    )


@router.post("")
def update_settings(
    company_name: str = Form(DEFAULT_SETTINGS["company_name"]),
    company_business_id: str = Form(""),
    company_address: str = Form(""),
    company_phone: str = Form(""),
    company_email: str = Form(""),
    default_vat_percent: str = Form(DEFAULT_SETTINGS["default_vat_percent"]),
    receipt_prefix: str = Form(DEFAULT_SETTINGS["receipt_prefix"]),
    language: str | None = Form(None),
    db: Session = Depends(get_db),
):
    current_language = get_current_language(db)
    selected_language = language if language in SUPPORTED_LANGUAGES else current_language

    set_app_settings(
        db,
        {
            "company_name": company_name.strip(),
            "company_business_id": company_business_id.strip(),
            "company_address": company_address.strip(),
            "company_phone": company_phone.strip(),
            "company_email": company_email.strip(),
            "default_vat_percent": default_vat_percent.strip() or "24",
            "receipt_prefix": receipt_prefix.strip(),
            "language": selected_language,
        },
    )
    log_audit_event(
        db,
        event_type="settings_changed",
        entity_type="settings",
        entity_id=0,
        description="Application settings updated.",
    )
    db.commit()
    return RedirectResponse(url="/settings", status_code=303)


@router.get("/language", response_class=HTMLResponse)
def edit_language(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        "settings/language.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": "language",
            "settings_values": get_app_settings(db),
            "supported_languages": SUPPORTED_LANGUAGES,
        },
    )


@router.post("/language")
def update_language(
    request: Request,
    language: str = Form(...),
    next_url: str = Form("/"),
    db: Session = Depends(get_db),
):
    if language not in SUPPORTED_LANGUAGES:
        raise HTTPException(status_code=400, detail="Unsupported language")

    current_settings = get_app_settings(db)
    set_app_settings(db, {**current_settings, "language": language})
    log_audit_event(
        db,
        event_type="language_changed",
        entity_type="settings",
        entity_id=0,
        description=f"Language changed to {language}.",
    )
    db.commit()

    redirect_url = next_url if next_url.startswith("/") and not next_url.startswith("//") else "/"
    return RedirectResponse(url=redirect_url, status_code=303)


@router.get("/statuses", response_class=HTMLResponse)
def list_statuses(request: Request, db: Session = Depends(get_db)):
    ensure_default_job_statuses(db)
    statuses = db.query(JobStatus).order_by(JobStatus.sort_order.asc(), JobStatus.name.asc()).all()
    return templates.TemplateResponse(
        "settings/statuses.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": "settings",
            "statuses": statuses,
        },
    )


@router.get("/statuses/new", response_class=HTMLResponse)
def new_status(request: Request):
    return templates.TemplateResponse(
        "settings/status_form.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": "settings",
            "status": None,
            "form_action": "/settings/statuses",
        },
    )


@router.post("/statuses")
def create_status(
    name: str = Form(...),
    sort_order: int = Form(0),
    is_ready_state: bool = Form(False),
    is_packed_state: bool = Form(False),
    is_final: bool = Form(False),
    is_active: bool = Form(True),
    db: Session = Depends(get_db),
):
    if not name.strip():
        raise HTTPException(status_code=400, detail="Status name is required")
    status = JobStatus(
        name=name.strip(),
        sort_order=sort_order,
        is_ready_state=is_ready_state,
        is_packed_state=is_packed_state,
        is_final=is_final,
        is_active=is_active,
    )
    db.add(status)
    log_audit_event(
        db,
        event_type="status_created",
        entity_type="status",
        entity_id=0,
        description=f"Status created: {status.name}.",
    )
    db.commit()
    return RedirectResponse(url="/settings/statuses", status_code=303)


@router.get("/statuses/{status_id}/edit", response_class=HTMLResponse)
def edit_status(status_id: int, request: Request, db: Session = Depends(get_db)):
    status = db.get(JobStatus, status_id)
    if status is None:
        raise HTTPException(status_code=404, detail="Status not found")
    return templates.TemplateResponse(
        "settings/status_form.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": "settings",
            "status": status,
            "form_action": f"/settings/statuses/{status.id}",
        },
    )


@router.post("/statuses/{status_id}")
def update_status(
    status_id: int,
    name: str = Form(...),
    sort_order: int = Form(0),
    is_ready_state: bool = Form(False),
    is_packed_state: bool = Form(False),
    is_final: bool = Form(False),
    is_active: bool = Form(False),
    db: Session = Depends(get_db),
):
    status = db.get(JobStatus, status_id)
    if status is None:
        raise HTTPException(status_code=404, detail="Status not found")
    if not name.strip():
        raise HTTPException(status_code=400, detail="Status name is required")
    status.name = name.strip()
    status.sort_order = sort_order
    status.is_ready_state = is_ready_state
    status.is_packed_state = is_packed_state
    status.is_final = is_final
    status.is_active = is_active
    log_audit_event(
        db,
        event_type="status_updated",
        entity_type="status",
        entity_id=status.id,
        description=f"Status updated: {status.name}.",
    )
    db.commit()
    return RedirectResponse(url="/settings/statuses", status_code=303)


@router.post("/statuses/{status_id}/deactivate")
def deactivate_status(status_id: int, db: Session = Depends(get_db)):
    status = db.get(JobStatus, status_id)
    if status is None:
        raise HTTPException(status_code=404, detail="Status not found")
    status.is_active = False
    log_audit_event(
        db,
        event_type="status_deactivated",
        entity_type="status",
        entity_id=status.id,
        description=f"Status deactivated: {status.name}.",
    )
    db.commit()
    return RedirectResponse(url="/settings/statuses", status_code=303)
