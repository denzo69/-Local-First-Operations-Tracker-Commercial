from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import DailyClosing, User
from app.services.sales_service import build_daily_closing_snapshot, create_daily_closing, reopen_daily_closing
from app.template_context import templates

router = APIRouter(prefix="/daily-closings", tags=["daily-closings"])
settings = get_settings()


@router.get("", response_class=HTMLResponse)
def list_daily_closings(request: Request, db: Session = Depends(get_db)):
    closings = db.query(DailyClosing).order_by(DailyClosing.business_date.desc()).all()
    users = db.query(User).filter(User.is_active.is_(True)).order_by(User.name.asc()).all()
    return templates.TemplateResponse(
        "daily_closings/list.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": "daily_closings",
            "closings": closings,
            "users": users,
            "today": date.today(),
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
    snapshot = build_daily_closing_snapshot(db, closing.business_date)
    users = db.query(User).filter(User.is_active.is_(True)).order_by(User.name.asc()).all()
    return templates.TemplateResponse(
        "daily_closings/detail.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": "daily_closings",
            "closing": closing,
            "snapshot": snapshot,
            "users": users,
        },
    )


@router.post("/{closing_id}/reopen")
def reopen_closing(
    closing_id: int,
    user_id: int = Form(...),
    db: Session = Depends(get_db),
):
    try:
        reopen_daily_closing(db, closing_id=closing_id, user_id=user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=f"/daily-closings/{closing_id}", status_code=303)
