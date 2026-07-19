from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models import Sale
from app.services.sales_service import (
    INVOICE_ACTIVE_STATUSES,
    invoice_follow_up_status,
    sale_balance_due,
    sale_paid_amount,
)


INVOICE_QUEUE_VIEWS = (
    "action_required",
    "waiting_transfer",
    "transferred",
    "unpaid",
    "paid",
    "cancelled",
    "all",
)


@dataclass(frozen=True)
class InvoiceQueueTab:
    key: str
    label_key: str
    count: int
    href: str


def invoice_related_sales(db: Session) -> list[Sale]:
    return (
        db.query(Sale)
        .filter(
            or_(
                Sale.settlement_status.in_(list(INVOICE_ACTIVE_STATUSES) + ["paid", "cancelled"]),
                Sale.payment_method == "invoice",
                Sale.external_invoice_number.is_not(None),
            )
        )
        .order_by(Sale.due_date.asc(), Sale.next_follow_up_at.asc(), Sale.sold_at.desc())
        .all()
    )


def classify_invoice_sale(sale: Sale, *, as_of: date | None = None) -> str:
    derived_status = invoice_follow_up_status(sale, as_of=as_of)
    if derived_status == "paid":
        return "paid"
    if derived_status == "cancelled":
        return "cancelled"
    if derived_status in {"payment_check_due", "reminder_due", "unpaid", "reminder_sent"}:
        return "unpaid"
    if derived_status == "transferred_to_invoicing":
        return "transferred"
    return "waiting_transfer"


def invoice_sale_matches_view(sale: Sale, view: str, *, as_of: date | None = None) -> bool:
    if view == "all":
        return True
    derived_status = invoice_follow_up_status(sale, as_of=as_of)
    bucket = classify_invoice_sale(sale, as_of=as_of)
    if view == "action_required":
        return bucket in {"waiting_transfer", "unpaid"} or derived_status in {"payment_check_due", "reminder_due"}
    return bucket == view


def filter_invoice_sales(sales: list[Sale], view: str, *, as_of: date | None = None) -> list[Sale]:
    selected_view = view if view in INVOICE_QUEUE_VIEWS else "action_required"
    return [sale for sale in sales if invoice_sale_matches_view(sale, selected_view, as_of=as_of)]


def build_invoice_tabs(sales: list[Sale], *, active_view: str, as_of: date | None = None) -> list[InvoiceQueueTab]:
    return [
        InvoiceQueueTab(
            key=view,
            label_key=f"invoice_view_{view}",
            count=len(filter_invoice_sales(sales, view, as_of=as_of)),
            href=f"/sales/invoice-queue?view={view}",
        )
        for view in INVOICE_QUEUE_VIEWS
    ]


def invoice_row(sale: Sale, *, as_of: date | None = None) -> dict:
    return {
        "sale": sale,
        "derived_status": invoice_follow_up_status(sale, as_of=as_of),
        "bucket": classify_invoice_sale(sale, as_of=as_of),
        "paid_amount": sale_paid_amount(sale),
        "balance_due": sale_balance_due(sale),
    }
