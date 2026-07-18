from datetime import UTC, date, datetime, timedelta

from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import AuditLog, Customer, DailyClosing, Job, Sale
from app.services.dashboard_service import daily_closing_state


def _invoice_sale(
    *,
    settlement_status: str,
    due_date: date | None = None,
    external_invoice_number: str | None = None,
    document_number: str = "SALE-DASH-1",
) -> Sale:
    return Sale(
        document_number=document_number,
        payment_method="invoice",
        settlement_status=settlement_status,
        source_type="pos",
        sold_at=datetime.now(UTC),
        business_date=date.today(),
        total=100,
        subtotal=100,
        vat_total=0,
        due_date=due_date,
        external_invoice_number=external_invoice_number,
    )


def test_invoice_queue_tabs_keep_paid_transferred_cancelled_and_action_required_separate():
    today = date.today()
    with SessionLocal() as db:
        db.add_all(
            [
                _invoice_sale(settlement_status="awaiting_invoice", document_number="SALE-DASH-WAIT"),
                _invoice_sale(
                    settlement_status="transferred_to_invoicing",
                    external_invoice_number="EXT-1",
                    document_number="SALE-DASH-TRANSFER",
                ),
                _invoice_sale(
                    settlement_status="transferred_to_invoicing",
                    due_date=today - timedelta(days=1),
                    external_invoice_number="EXT-2",
                    document_number="SALE-DASH-OVERDUE",
                ),
                _invoice_sale(
                    settlement_status="paid",
                    external_invoice_number="EXT-3",
                    document_number="SALE-DASH-PAID",
                ),
                _invoice_sale(
                    settlement_status="cancelled",
                    external_invoice_number="EXT-4",
                    document_number="SALE-DASH-CANCEL",
                ),
            ]
        )
        db.commit()

    with TestClient(app) as client:
        action_required = client.get("/sales/invoice-queue")
        transferred = client.get("/sales/invoice-queue?view=transferred")
        unpaid = client.get("/sales/invoice-queue?view=unpaid")
        paid = client.get("/sales/invoice-queue?view=paid")
        cancelled = client.get("/sales/invoice-queue?view=cancelled")
        all_rows = client.get("/sales/invoice-queue?view=all")

    assert "SALE-DASH-WAIT" in action_required.text
    assert "SALE-DASH-OVERDUE" in action_required.text
    assert "SALE-DASH-PAID" not in action_required.text
    assert "SALE-DASH-TRANSFER" in transferred.text
    assert "SALE-DASH-OVERDUE" in unpaid.text
    assert "SALE-DASH-PAID" in paid.text
    assert "SALE-DASH-CANCEL" in cancelled.text
    for document_number in ["SALE-DASH-WAIT", "SALE-DASH-TRANSFER", "SALE-DASH-OVERDUE", "SALE-DASH-PAID", "SALE-DASH-CANCEL"]:
        assert document_number in all_rows.text


def test_dashboard_daily_closing_states_are_neutral_warning_red_and_green():
    today = date.today()
    with SessionLocal() as db:
        neutral = daily_closing_state(db, today=today, now=datetime(2026, 7, 18, 10, 0))
        db.add(_invoice_sale(settlement_status="paid", document_number="SALE-DASH-TODAY"))
        db.commit()
        warning = daily_closing_state(db, today=today, now=datetime(2026, 7, 18, 18, 0))
        db.add(
            Sale(
                document_number="SALE-DASH-OLD",
                payment_method="cash",
                settlement_status="paid",
                source_type="pos",
                sold_at=datetime.now(UTC),
                business_date=today - timedelta(days=1),
                total=50,
                subtotal=50,
                vat_total=0,
            )
        )
        db.commit()
        danger = daily_closing_state(db, today=today, now=datetime(2026, 7, 18, 18, 0))
        db.add(DailyClosing(business_date=today - timedelta(days=1), created_by_user_id=1, status="closed"))
        db.add(DailyClosing(business_date=today, created_by_user_id=1, status="closed"))
        db.commit()
        success = daily_closing_state(db, today=today, now=datetime(2026, 7, 18, 18, 0))

    assert neutral.tone == "neutral"
    assert warning.tone == "warning"
    assert danger.tone == "danger"
    assert success.tone == "success"


def test_dashboard_activity_feed_uses_friendly_labels_and_unknown_fallback():
    with SessionLocal() as db:
        db.add(AuditLog(event_type="invoice.paid_confirmed", entity_type="sale", entity_id=42, description="Invoice 42 paid"))
        db.add(AuditLog(event_type="custom.unmapped", entity_type="custom", entity_id=99, description="Custom activity"))
        db.commit()

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "Invoice marked paid" in response.text
    assert "Activity recorded" in response.text
    assert "custom.unmapped" not in response.text
    assert "/audit-log" in response.text


def test_dashboard_v2_renders_in_finnish_and_english_with_mobile_nav_groups():
    with TestClient(app) as client:
        english = client.get("/")
        client.post(
            "/settings",
            data={
                "company_name": "Test Company Oy",
                "default_vat_percent": "24",
                "receipt_prefix": "TEST-",
                "language": "fi",
            },
            follow_redirects=False,
        )
        finnish = client.get("/")

    assert "Critical tasks" in english.text
    assert "Sales and documents" in english.text
    assert "Reports and history" in english.text
    assert "Kriittiset tehtävät" in finnish.text
    assert "Myynti ja dokumentit" in finnish.text
    assert "Raportit ja historia" in finnish.text
    assert 'aria-controls="mobileNavSales"' in finnish.text
    assert 'aria-controls="mobileNavStock"' in finnish.text


def test_dashboard_work_queues_include_work_orders_only_not_quotes_or_delivery_notes():
    today = date.today()
    with SessionLocal() as db:
        customer = Customer(name="Dashboard Customer")
        db.add(customer)
        db.flush()
        db.add_all(
            [
                Job(title="Dashboard work order", customer_id=customer.id, document_type="work_order", requested_pickup_date=today),
                Job(title="Dashboard quote", customer_id=customer.id, document_type="quote", requested_pickup_date=today),
                Job(title="Dashboard delivery", customer_id=customer.id, document_type="delivery_note", requested_pickup_date=today),
            ]
        )
        db.commit()

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "Dashboard work order" not in response.text
    assert "Dashboard quote" not in response.text
    assert "Dashboard delivery" not in response.text
    assert ">1</strong>" in response.text
