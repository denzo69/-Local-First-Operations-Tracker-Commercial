from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from app.auth_middleware import authentication_middleware
from app.config import get_settings
from app.database import get_db, init_db
from app.error_handlers import register_error_handlers
from app.routes import (
    audit_log,
    auth,
    backups,
    cash_registers,
    customers,
    daily_closings,
    delivery_notes,
    inventory,
    jobs,
    products,
    quotes,
    reports,
    sales,
    seller_reports,
    settings as settings_routes,
    shifts,
    users,
    work_orders,
)
from app.services.backup_scheduler_service import start_backup_scheduler, stop_backup_scheduler
from app.services.dashboard_service import build_dashboard_context
from app.services.document_route_service import require_work_order_route
from app.services.maintenance_service import is_maintenance_active
from app.services.sales_service import invoice_follow_up_status
from app.template_context import templates

settings = get_settings()
BASE_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(app: FastAPI):
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
app.include_router(delivery_notes.legacy_router)
app.include_router(delivery_notes.router)
app.include_router(quotes.legacy_router)
app.include_router(quotes.router)
app.include_router(jobs.router, dependencies=[Depends(require_work_order_route)])
app.include_router(audit_log.router)
app.include_router(products.router)
app.include_router(users.router)
app.include_router(cash_registers.router)
app.include_router(shifts.router)
app.include_router(sales.router)
app.include_router(daily_closings.router)
app.include_router(inventory.router)
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
    dashboard_data = build_dashboard_context(db)

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": "dashboard",
            "quick_action_url": "/work-orders/new",
            "invoice_follow_up_status": invoice_follow_up_status,
            **dashboard_data,
        },
    )
