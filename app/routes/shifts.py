from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import CashRegister, Shift, User
from app.services.sales_service import add_cash_movement, close_shift, open_shift
from app.template_context import templates

router = APIRouter(prefix="/shifts", tags=["shifts"])
settings = get_settings()


@router.get("", response_class=HTMLResponse)
def list_shifts(request: Request, db: Session = Depends(get_db)):
    shifts = db.query(Shift).order_by(Shift.opened_at.desc()).all()
    return templates.TemplateResponse(
        "shifts/list.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": "shifts",
            "shifts": shifts,
        },
    )


@router.get("/open", response_class=HTMLResponse)
def open_shift_form(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        "shifts/open.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": "shifts",
            "users": db.query(User).filter(User.is_active.is_(True)).order_by(User.name.asc()).all(),
            "registers": db.query(CashRegister).filter(CashRegister.is_active.is_(True)).order_by(CashRegister.name.asc()).all(),
            "today": date.today(),
        },
    )


@router.post("")
def create_shift(
    seller_id: int = Form(...),
    cash_register_id: int = Form(...),
    business_date: str = Form(...),
    starting_cash: str = Form("0"),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    try:
        shift = open_shift(
            db,
            seller_id=seller_id,
            cash_register_id=cash_register_id,
            business_date=date.fromisoformat(business_date),
            starting_cash=starting_cash,
            notes=notes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=f"/shifts/{shift.id}", status_code=303)


@router.get("/{shift_id}", response_class=HTMLResponse)
def shift_detail(shift_id: int, request: Request, db: Session = Depends(get_db)):
    shift = db.get(Shift, shift_id)
    if shift is None:
        raise HTTPException(status_code=404, detail="Shift not found")
    expected_cash = shift.expected_closing_cash
    if shift.status == "open":
        from app.services.sales_service import calculate_expected_cash

        expected_cash = calculate_expected_cash(db, shift)
    return templates.TemplateResponse(
        "shifts/detail.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": "shifts",
            "shift": shift,
            "expected_cash": expected_cash,
        },
    )


@router.post("/{shift_id}/cash-movements")
def create_cash_movement(
    shift_id: int,
    seller_id: int = Form(...),
    movement_type: str = Form(...),
    amount: str = Form(...),
    reason: str = Form(""),
    db: Session = Depends(get_db),
):
    try:
        add_cash_movement(
            db,
            shift_id=shift_id,
            seller_id=seller_id,
            movement_type=movement_type,
            amount=amount,
            reason=reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=f"/shifts/{shift_id}", status_code=303)


@router.post("/{shift_id}/close")
def close_shift_route(
    shift_id: int,
    counted_cash: str = Form(...),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    try:
        close_shift(db, shift_id=shift_id, counted_cash=counted_cash, notes=notes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=f"/shifts/{shift_id}", status_code=303)
