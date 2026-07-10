from datetime import date

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import Job
from app.template_context import templates

router = APIRouter(prefix="/reports", tags=["reports"])
settings = get_settings()


def parse_report_date(value: str | None) -> date:
    if value:
        return date.fromisoformat(value)
    return date.today()


@router.get("", response_class=HTMLResponse)
def sales_report(
    request: Request,
    day: str | None = Query(None),
    month: str | None = Query(None),
    db: Session = Depends(get_db),
):
    selected_day = parse_report_date(day)
    selected_month = month or selected_day.strftime("%Y-%m")

    jobs = db.query(Job).order_by(Job.created_at.desc()).all()

    day_jobs = [job for job in jobs if job.created_at and job.created_at.date() == selected_day]
    month_jobs = [
        job
        for job in jobs
        if job.created_at and job.created_at.strftime("%Y-%m") == selected_month
    ]

    def job_total(job: Job) -> float:
        return round(sum(item.line_total or 0 for item in job.items), 2)

    return templates.TemplateResponse(
        "reports/sales.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": "reports",
            "selected_day": selected_day,
            "selected_month": selected_month,
            "day_jobs": day_jobs,
            "month_jobs": month_jobs,
            "day_total": sum(job_total(job) for job in day_jobs),
            "month_total": sum(job_total(job) for job in month_jobs),
            "job_total": job_total,
        },
    )
