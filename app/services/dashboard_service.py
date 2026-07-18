from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.models import AuditLog, DailyClosing, Job, Refund, Sale
from app.services.activity_feed_service import format_activity_feed
from app.services.invoice_query_service import (
    filter_invoice_sales,
    invoice_related_sales,
    invoice_sale_matches_view,
)
from app.services.money_service import sum_money


@dataclass(frozen=True)
class DashboardStatus:
    tone: str
    label_key: str
    detail_key: str


@dataclass(frozen=True)
class DailyClosingCard:
    tone: str
    label_key: str
    detail_key: str
    count: int
    href: str


def active_work_order_filter():
    return and_(
        Job.document_type == "work_order",
        or_(Job.status_id.is_(None), ~Job.status.has(is_final=True)),
        ~Job.sales.any(Sale.status != "cancelled"),
    )


def daily_closing_state(db: Session, *, today: date, now: datetime | None = None) -> DailyClosingCard:
    closing = (
        db.query(DailyClosing)
        .filter(DailyClosing.business_date == today)
        .first()
    )
    sale_dates = [
        row[0]
        for row in db.query(Sale.business_date)
        .filter(Sale.business_date.is_not(None), Sale.business_date < today, Sale.status != "cancelled")
        .distinct()
        .all()
    ]
    if sale_dates:
        closed_dates = {
            row[0]
            for row in db.query(DailyClosing.business_date)
            .filter(DailyClosing.business_date.in_(sale_dates), DailyClosing.status == "closed")
            .all()
        }
        unclosed_dates = [business_date for business_date in sale_dates if business_date not in closed_dates]
        if unclosed_dates:
            return DailyClosingCard(
                "danger",
                "previous_day_unclosed",
                "previous_day_unclosed_detail",
                len(unclosed_dates),
                "/daily-closings",
            )

    if closing and closing.status == "closed":
        return DailyClosingCard("success", "daily_closing_done", "daily_closing_done_detail", 0, "/daily-closings")

    current_time = now or datetime.now()
    has_today_sales = (
        db.query(Sale.id)
        .filter(Sale.business_date == today, Sale.status != "cancelled")
        .first()
        is not None
    )
    if has_today_sales and current_time.hour >= 17:
        return DailyClosingCard("warning", "daily_closing_expected", "daily_closing_expected_detail", 1, "/daily-closings")
    return DailyClosingCard("neutral", "business_day_open", "business_day_open_detail", 0, "/daily-closings")


def build_dashboard_context(db: Session, *, today: date | None = None, now: datetime | None = None) -> dict:
    business_date = today or date.today()
    tomorrow = business_date + timedelta(days=1)
    today_start = datetime.combine(business_date, time.min, tzinfo=UTC)
    tomorrow_start = datetime.combine(tomorrow, time.min, tzinfo=UTC)
    active_filter = active_work_order_filter()

    overdue_jobs = (
        db.query(Job)
        .filter(active_filter)
        .filter(Job.requested_pickup_date.is_not(None), Job.requested_pickup_date < business_date)
        .order_by(Job.requested_pickup_date.asc(), Job.created_at.desc())
        .all()
    )
    due_today_jobs = (
        db.query(Job)
        .filter(active_filter)
        .filter(Job.requested_pickup_date == business_date)
        .order_by(Job.created_at.desc())
        .all()
    )
    ready_jobs = (
        db.query(Job)
        .filter(active_filter)
        .join(Job.status, isouter=True)
        .filter(Job.status.has(is_ready_state=True))
        .order_by(Job.created_at.desc())
        .all()
    )
    upcoming_jobs = (
        db.query(Job)
        .filter(active_filter)
        .filter(
            or_(
                Job.requested_pickup_date.is_(None),
                Job.requested_pickup_date > business_date,
            )
        )
        .order_by(Job.requested_pickup_date.asc(), Job.created_at.desc())
        .limit(8)
        .all()
    )
    todays_sales = (
        db.query(Sale)
        .filter(Sale.sold_at >= today_start, Sale.sold_at < tomorrow_start, Sale.status != "cancelled")
        .order_by(Sale.sold_at.desc())
        .all()
    )
    todays_refunds = (
        db.query(Refund)
        .filter(Refund.refunded_at >= today_start, Refund.refunded_at < tomorrow_start)
        .order_by(Refund.refunded_at.desc())
        .all()
    )
    invoices = invoice_related_sales(db)
    invoice_action_required = filter_invoice_sales(invoices, "action_required", as_of=business_date)
    recent_events = (
        db.query(AuditLog)
        .order_by(AuditLog.created_at.desc())
        .limit(8)
        .all()
    )
    closing_card = daily_closing_state(db, today=business_date, now=now)
    critical_count = len(overdue_jobs) + len(invoice_action_required)
    if closing_card.tone in {"danger", "warning"}:
        critical_count += max(1, closing_card.count)

    if any([overdue_jobs, closing_card.tone == "danger"]):
        status = DashboardStatus("danger", "dashboard_status_critical", "dashboard_status_critical_detail")
    elif invoice_action_required or closing_card.tone == "warning":
        status = DashboardStatus("warning", "dashboard_status_attention", "dashboard_status_attention_detail")
    else:
        status = DashboardStatus("success", "dashboard_status_ok", "dashboard_status_ok_detail")

    return {
        "today": business_date,
        "dashboard_status": status,
        "critical_count": critical_count,
        "daily_closing_card": closing_card,
        "overdue_jobs": overdue_jobs,
        "due_today_jobs": due_today_jobs,
        "ready_jobs": ready_jobs,
        "upcoming_jobs": upcoming_jobs,
        "todays_sales_total": sum_money(sale.total for sale in todays_sales),
        "todays_refund_total": sum_money(refund.amount for refund in todays_refunds),
        "invoice_action_required": invoice_action_required,
        "invoice_waiting_transfer_count": len(filter_invoice_sales(invoices, "waiting_transfer", as_of=business_date)),
        "invoice_transferred_count": len(filter_invoice_sales(invoices, "transferred", as_of=business_date)),
        "invoice_unpaid_count": len(filter_invoice_sales(invoices, "unpaid", as_of=business_date)),
        "invoice_paid_count": len(filter_invoice_sales(invoices, "paid", as_of=business_date)),
        "invoice_cancelled_count": len(filter_invoice_sales(invoices, "cancelled", as_of=business_date)),
        "activity_feed": format_activity_feed(recent_events),
        "invoice_requires_action": lambda sale: invoice_sale_matches_view(sale, "action_required", as_of=business_date),
    }
