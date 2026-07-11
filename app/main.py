from contextlib import asynccontextmanager
from datetime import date, timedelta

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db, init_db
from app.models import Job
from app.routes import (
    audit_log,
    backups,
    cash_registers,
    customers,
    daily_closings,
    jobs,
    products,
    reports,
    sales,
    seller_reports,
    settings as settings_routes,
    shifts,
    users,
    work_orders,
)
from app.services.i18n_service import get_translations
from app.services.maintenance_service import is_maintenance_active
from app.services.reminder_service import next_business_day
from app.services.settings_service import get_app_settings
from app.template_context import templates

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(backups.router)
app.include_router(customers.router)
app.include_router(work_orders.router)
app.include_router(jobs.router)
app.include_router(audit_log.router)
app.include_router(products.router)
app.include_router(users.router)
app.include_router(cash_registers.router)
app.include_router(shifts.router)
app.include_router(sales.router)
app.include_router(daily_closings.router)
app.include_router(seller_reports.router)
app.include_router(reports.router)
app.include_router(settings_routes.router)


@app.middleware("http")
async def block_writes_during_maintenance(request: Request, call_next):
    if (
        is_maintenance_active()
        and request.method not in {"GET", "HEAD", "OPTIONS"}
        and not request.url.path.endswith("/restore")
    ):
        return PlainTextResponse("Maintenance in progress", status_code=503)
    return await call_next(request)


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok", "app": settings.app_name}


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    today = date.today()
    tomorrow = today + timedelta(days=1)
    next_workday = next_business_day(today)
    t = get_translations(get_app_settings(db).get("language", "en"))
    active_job_filter = or_(Job.status_id.is_(None), ~Job.status.has(is_final=True))

    overdue_jobs = (
        db.query(Job)
        .filter(active_job_filter)
        .filter(Job.requested_pickup_date.is_not(None))
        .filter(Job.requested_pickup_date < today)
        .order_by(Job.requested_pickup_date.asc(), Job.created_at.desc())
        .all()
    )
    due_today_jobs = (
        db.query(Job)
        .filter(active_job_filter)
        .filter(Job.requested_pickup_date == today)
        .order_by(Job.created_at.desc())
        .all()
    )
    due_tomorrow_jobs = (
        db.query(Job)
        .filter(active_job_filter)
        .filter(Job.requested_pickup_date == tomorrow)
        .order_by(Job.created_at.desc())
        .all()
    )
    ready_jobs = (
        db.query(Job)
        .filter(active_job_filter)
        .join(Job.status, isouter=True)
        .filter(Job.status.has(is_ready_state=True))
        .order_by(Job.created_at.desc())
        .all()
    )
    next_business_day_jobs = (
        db.query(Job)
        .filter(active_job_filter)
        .filter(Job.requested_pickup_date == next_workday)
        .filter(
            or_(
                Job.status_id.is_(None),
                ~Job.status.has(is_ready_state=True),
            )
        )
        .filter(
            or_(
                Job.status_id.is_(None),
                ~Job.status.has(is_packed_state=True),
            )
        )
        .order_by(Job.created_at.desc())
        .all()
    )
    attention_jobs = list(
        dict.fromkeys(overdue_jobs + due_today_jobs + next_business_day_jobs + ready_jobs)
    )
    upcoming_jobs = (
        db.query(Job)
        .filter(active_job_filter)
        .filter(
            or_(
                Job.requested_pickup_date.is_(None),
                Job.requested_pickup_date > tomorrow,
            )
        )
        .order_by(Job.requested_pickup_date.asc(), Job.created_at.desc())
        .limit(8)
        .all()
    )

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": "dashboard",
            "today": today,
            "next_business_day": next_workday,
            "cards": [
                {"label": t["overdue"], "value": len(overdue_jobs), "tone": "danger"},
                {"label": t["due_today"], "value": len(due_today_jobs), "tone": "warning"},
                {"label": t["due_tomorrow"], "value": len(due_tomorrow_jobs), "tone": "info"},
                {"label": t["ready_for_pickup"], "value": len(ready_jobs), "tone": "success"},
            ],
            "attention_jobs": attention_jobs,
            "upcoming_jobs": upcoming_jobs,
        },
    )
