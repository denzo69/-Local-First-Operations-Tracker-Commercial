from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import User
from app.services.sales_service import seller_report
from app.template_context import templates

router = APIRouter(prefix="/seller-reports", tags=["seller-reports"])
settings = get_settings()


@router.get("", response_class=HTMLResponse)
def seller_reports(
    request: Request,
    seller_id: int | None = Query(None),
    period: str = Query("daily"),
    db: Session = Depends(get_db),
):
    today = date.today()
    if period == "weekly":
        start_date = today - timedelta(days=today.weekday())
        end_date = start_date + timedelta(days=7)
    elif period == "monthly":
        start_date = today.replace(day=1)
        end_date = (start_date.replace(day=28) + timedelta(days=4)).replace(day=1)
    else:
        period = "daily"
        start_date = today
        end_date = today + timedelta(days=1)

    users = db.query(User).filter(User.is_active.is_(True)).order_by(User.name.asc()).all()
    selected_seller_id = seller_id or (users[0].id if users else None)
    report = (
        seller_report(db, seller_id=selected_seller_id, start_date=start_date, end_date=end_date)
        if selected_seller_id
        else None
    )
    return templates.TemplateResponse(
        "seller_reports/index.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": "seller_reports",
            "users": users,
            "selected_seller_id": selected_seller_id,
            "period": period,
            "start_date": start_date,
            "end_date": end_date,
            "report": report,
        },
    )
