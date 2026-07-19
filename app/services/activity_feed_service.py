from __future__ import annotations

from dataclasses import dataclass

from app.models import AuditLog


@dataclass(frozen=True)
class ActivityFeedItem:
    label_key: str
    fallback_label: str
    detail: str
    href: str
    created_at: object


EVENT_LABELS: dict[str, tuple[str, str]] = {
    "invoice.transferred": ("activity_invoice_transferred", "Invoice transferred to invoicing"),
    "invoice.paid_confirmed": ("activity_invoice_paid", "Invoice marked paid"),
    "invoice.unpaid_confirmed": ("activity_invoice_unpaid", "Invoice marked unpaid"),
    "invoice.reminder_sent": ("activity_invoice_reminder_sent", "Invoice reminder recorded"),
    "daily_closing.closed": ("activity_daily_closing_completed", "Daily closing completed"),
    "daily_closing.reopened": ("activity_daily_closing_reopened", "Daily closing reopened"),
    "sale.created": ("activity_sale_created", "Sale created"),
    "sale.seller_corrected": ("activity_sale_seller_corrected", "Sale seller corrected"),
    "refund.created": ("activity_refund_created", "Refund created"),
    "work_order.created": ("activity_work_order_created", "Work order created"),
    "work_order.completed": ("activity_work_order_completed", "Work order completed"),
    "customer.created": ("activity_customer_created", "Customer created"),
}


def _href_for_event(event: AuditLog) -> str:
    if event.entity_type in {"sale", "sales"} and event.entity_id:
        return f"/sales/{event.entity_id}"
    if event.entity_type in {"job", "work_order", "work_orders"} and event.entity_id:
        return f"/work-orders/{event.entity_id}"
    if event.entity_type in {"customer", "customers"} and event.entity_id:
        return f"/customers/{event.entity_id}"
    if event.entity_type in {"daily_closing", "daily_closings"} and event.entity_id:
        return f"/daily-closings/{event.entity_id}"
    return "/audit-log"


def format_activity_event(event: AuditLog) -> ActivityFeedItem:
    is_known = event.event_type in EVENT_LABELS
    label_key, fallback_label = EVENT_LABELS.get(event.event_type, ("activity_generic_event", "Activity recorded"))
    return ActivityFeedItem(
        label_key=label_key,
        fallback_label=fallback_label,
        detail=(event.description or fallback_label) if is_known else "See technical audit log.",
        href=_href_for_event(event),
        created_at=event.created_at,
    )


def format_activity_feed(events: list[AuditLog]) -> list[ActivityFeedItem]:
    return [format_activity_event(event) for event in events]
