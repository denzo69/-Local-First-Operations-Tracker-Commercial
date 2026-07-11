from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
import uuid

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.auth_middleware import authentication_middleware
from app.config import get_settings, validate_runtime_configuration
from app.database import get_db, init_db
from app.error_handlers import register_error_handlers
from app.models import AuditLog, DailyClosing, Job, Refund, Sale, Shift
from app.routes import (
    audit_log,
    auth,
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
from app.services.backup_scheduler_service import start_backup_scheduler, stop_backup_scheduler
from app.services.maintenance_service import is_maintenance_active
from app.services.money_service import sum_money
from app.services.settings_service import get_app_settings
from app.template_context import templates

settings = get_settings()
BASE_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_runtime_configuration()
    init_db()
    start_backup_scheduler()
    try:
        yield
    finally:
        stop_backup_scheduler()


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
register_error_handlers(app)
app.middleware("http")(authentication_middleware)
app.include_router(auth.router)
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
async def request_id_middleware(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["x-request-id"] = request_id
    return response


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
    t = get_translations(get_app_settings(db).get("language", "en"))
    today_start = datetime.combine(today, time.min, tzinfo=UTC)
    tomorrow_start = datetime.combine(tomorrow, time.min, tzinfo=UTC)
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
    attention_jobs = list(
        dict.fromkeys(overdue_jobs + due_today_jobs + ready_jobs)
    )
    upcoming_jobs = (
        db.query(Job)
        .filter(active_job_filter)
        .filter(
            or_(
                Job.requested_pickup_date.is_(None),
                Job.requested_pickup_date >= tomorrow,
            )
        )
        .order_by(Job.requested_pickup_date.asc(), Job.created_at.desc())
        .limit(8)
        .all()
    )
    todays_sales = (
        db.query(Sale)
        .filter(Sale.sold_at >= today_start, Sale.sold_at < tomorrow_start)
        .order_by(Sale.sold_at.desc())
        .all()
    )
    todays_refunds = (
        db.query(Refund)
        .filter(Refund.refunded_at >= today_start, Refund.refunded_at < tomorrow_start)
        .order_by(Refund.refunded_at.desc())
        .all()
    )
    open_shifts = db.query(Shift).filter(Shift.status == "open").order_by(Shift.opened_at.desc()).all()
    current_shift = open_shifts[0] if open_shifts else None
    daily_closing = (
        db.query(DailyClosing)
        .filter(DailyClosing.business_date == today)
        .first()
    )
    recent_activity = (
        db.query(AuditLog)
        .order_by(AuditLog.created_at.desc())
        .limit(8)
        .all()
    )
    today_sales_total = sum_money(sale.total for sale in todays_sales)

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": "dashboard",
            "page_title": t["dashboard"],
            "quick_action_url": "/work-orders/new",
            "quick_action_label": t["create_work_order"],
            "today": today,
            "cards": [
                {"label": t["overdue"], "value": len(overdue_jobs), "tone": "danger", "icon": "OD", "href": "/work-orders?view=active"},
                {"label": t["due_today"], "value": len(due_today_jobs), "tone": "warning", "icon": "TD", "href": "/work-orders?view=active"},
                {"label": t["ready_for_pickup"], "value": len(ready_jobs), "tone": "success", "icon": "RP", "href": "/work-orders?view=ready"},
                {"label": t["todays_sales"], "value": today_sales_total, "tone": "neutral", "icon": "SA", "href": "/sales"},
                {"label": t["open_shift_status"], "value": len(open_shifts), "tone": "info", "icon": "SH", "href": "/shifts"},
                {"label": t["daily_closing_status"], "value": t["closed"] if daily_closing and daily_closing.status == "closed" else t["not_closed"], "tone": "success" if daily_closing and daily_closing.status == "closed" else "warning", "icon": "DC", "href": "/daily-closings"},
            ],
            "attention_jobs": attention_jobs,
            "overdue_jobs": overdue_jobs,
            "due_today_jobs": due_today_jobs,
            "due_tomorrow_jobs": due_tomorrow_jobs,
            "ready_jobs": ready_jobs,
            "upcoming_jobs": upcoming_jobs,
            "todays_sales": todays_sales[:5],
            "todays_refunds": todays_refunds[:5],
            "open_shifts": open_shifts,
            "current_shift": current_shift,
            "daily_closing": daily_closing,
            "recent_activity": recent_activity,
        },
    )
