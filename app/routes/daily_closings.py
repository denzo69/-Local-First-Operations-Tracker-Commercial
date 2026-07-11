from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import DailyClosing, DailyClosingSnapshot, Role, Shift, User
from app.services.sales_service import (
    create_daily_closing,
    get_daily_closing_snapshot_by_version,
    get_latest_daily_closing_snapshot,
    reopen_daily_closing,
)
from app.template_context import templates

router = APIRouter(prefix="/daily-closings", tags=["daily-closings"])
settings = get_settings()


def closing_manager_query(db: Session):
    return (
        db.query(User)
        .join(Role)
        .filter(User.is_active.is_(True), Role.code.in_(["admin", "manager"]))
        .order_by(User.name.asc())
    )


@router.get("", response_class=HTMLResponse)
def list_daily_closings(request: Request, db: Session = Depends(get_db)):
    closings = db.query(DailyClosing).order_by(DailyClosing.business_date.desc()).all()
    users = closing_manager_query(db).all()
    open_shifts = (
        db.query(Shift)
        .filter(Shift.status == "open")
        .order_by(Shift.business_date.asc(), Shift.id.asc())
        .all()
    )
    return templates.TemplateResponse(
        "daily_closings/list.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": "daily_closings",
            "closings": closings,
            "users": users,
            "today": date.today(),
            "open_shifts": open_shifts,
        },
    )


@router.post("")
def close_business_day(
    business_date: str = Form(...),
    created_by_user_id: int = Form(...),
    db: Session = Depends(get_db),
):
    try:
        closing = create_daily_closing(
            db,
            business_date=date.fromisoformat(business_date),
            created_by_user_id=created_by_user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=f"/daily-closings/{closing.id}", status_code=303)


@router.get("/{closing_id}", response_class=HTMLResponse)
def daily_closing_detail(closing_id: int, request: Request, db: Session = Depends(get_db)):
    closing = db.get(DailyClosing, closing_id)
    if closing is None:
        raise HTTPException(status_code=404, detail="Daily closing not found")
    try:
        snapshot_row, snapshot = get_latest_daily_closing_snapshot(db, closing)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    users = closing_manager_query(db).all()
    return templates.TemplateResponse(
        "daily_closings/detail.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": "daily_closings",
            "closing": closing,
            "snapshot_row": snapshot_row,
            "snapshot": snapshot,
            "snapshots": (
                db.query(DailyClosingSnapshot)
                .filter(DailyClosingSnapshot.daily_closing_id == closing.id)
                .order_by(DailyClosingSnapshot.version.asc())
                .all()
            ),
            "users": users,
        },
    )


@router.get("/{closing_id}/snapshots/{version}", response_class=HTMLResponse)
def daily_closing_snapshot_detail(
    closing_id: int,
    version: int,
    request: Request,
    db: Session = Depends(get_db),
):
    closing = db.get(DailyClosing, closing_id)
    if closing is None:
        raise HTTPException(status_code=404, detail="Daily closing not found")
    try:
        snapshot_row, snapshot = get_daily_closing_snapshot_by_version(db, closing, version)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    users = closing_manager_query(db).all()
    return templates.TemplateResponse(
        "daily_closings/detail.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": "daily_closings",
            "closing": closing,
            "snapshot_row": snapshot_row,
            "snapshot": snapshot,
            "snapshots": (
                db.query(DailyClosingSnapshot)
                .filter(DailyClosingSnapshot.daily_closing_id == closing.id)
                .order_by(DailyClosingSnapshot.version.asc())
                .all()
            ),
            "users": users,
        },
    )


@router.post("/{closing_id}/reopen")
def reopen_closing(
    closing_id: int,
    user_id: int = Form(...),
    reason: str = Form(...),
    db: Session = Depends(get_db),
):
    try:
        reopen_daily_closing(db, closing_id=closing_id, user_id=user_id, reason=reason)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=f"/daily-closings/{closing_id}", status_code=303)
