from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import CashRegister
from app.services.audit_service import log_audit_event
from app.template_context import templates

router = APIRouter(prefix="/cash-registers", tags=["cash-registers"])
settings = get_settings()


@router.get("", response_class=HTMLResponse)
def list_cash_registers(request: Request, db: Session = Depends(get_db)):
    registers = db.query(CashRegister).order_by(CashRegister.name.asc()).all()
    return templates.TemplateResponse(
        "cash_registers/list.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": "cash_registers",
            "registers": registers,
        },
    )


@router.get("/new", response_class=HTMLResponse)
def new_cash_register(request: Request):
    return templates.TemplateResponse(
        "cash_registers/form.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": "cash_registers",
            "register": None,
            "form_action": "/cash-registers",
        },
    )


@router.post("")
def create_cash_register(
    name: str = Form(...),
    location: str = Form(""),
    is_active: str | None = Form(None),
    db: Session = Depends(get_db),
):
    if not name.strip():
        raise HTTPException(status_code=400, detail="Cash register name is required")
    register = CashRegister(
        name=name.strip(),
        location=location.strip() or None,
        is_active=is_active == "on",
    )
    db.add(register)
    db.flush()
    log_audit_event(
        db,
        event_type="cash_register.created",
        entity_type="cash_register",
        entity_id=register.id,
        description=f"Cash register created: {register.name}.",
    )
    db.commit()
    return RedirectResponse(url="/cash-registers", status_code=303)


@router.get("/{register_id}/edit", response_class=HTMLResponse)
def edit_cash_register(register_id: int, request: Request, db: Session = Depends(get_db)):
    register = db.get(CashRegister, register_id)
    if register is None:
        raise HTTPException(status_code=404, detail="Cash register not found")
    return templates.TemplateResponse(
        "cash_registers/form.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": "cash_registers",
            "register": register,
            "form_action": f"/cash-registers/{register.id}",
        },
    )


@router.post("/{register_id}")
def update_cash_register(
    register_id: int,
    name: str = Form(...),
    location: str = Form(""),
    is_active: str | None = Form(None),
    db: Session = Depends(get_db),
):
    register = db.get(CashRegister, register_id)
    if register is None:
        raise HTTPException(status_code=404, detail="Cash register not found")
    if not name.strip():
        raise HTTPException(status_code=400, detail="Cash register name is required")
    register.name = name.strip()
    register.location = location.strip() or None
    register.is_active = is_active == "on"
    db.commit()
    return RedirectResponse(url="/cash-registers", status_code=303)
