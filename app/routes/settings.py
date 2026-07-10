from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.services.settings_service import DEFAULT_SETTINGS, get_app_settings, set_app_settings
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
    language: str = Form(DEFAULT_SETTINGS["language"]),
    db: Session = Depends(get_db),
):
    if language not in {"en", "fi"}:
        language = "en"

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
            "language": language,
        },
    )
    return RedirectResponse(url="/settings", status_code=303)
