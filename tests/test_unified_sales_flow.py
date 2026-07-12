from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import Customer, InventoryTransaction, Job, JobItem, Payment, Product, Role, Sale, Supplier, User
from app.services.inventory_service import (
    add_goods_receipt_line,
    create_default_warehouse,
    create_goods_receipt,
    post_goods_receipt,
)
from app.services.sales_service import (
    PaymentInput,
    SaleLineInput,
    confirm_invoice_paid,
    confirm_invoice_unpaid,
    create_sale_from_lines,
    create_sale_from_work_order,
    invoice_follow_up_alerts,
    invoice_follow_up_status,
    open_shift,
    record_invoice_reminder_sent,
    sale_balance_due,
    sale_paid_amount,
    transfer_sale_to_invoicing,
)


def role(db, code: str) -> Role:
    existing = db.query(Role).filter(Role.code == code).first()
    if existing:
        return existing
    created = Role(code=code, name=code.title())
    db.add(created)
    db.commit()
    return created


def user(db, name: str, role_code: str = "seller", *, credit: bool = True) -> User:
    created = User(
        name=name,
        login_name=f"{name.lower().replace(' ', '.')}.{role_code}",
        role=role(db, role_code),
        is_active=True,
        can_receive_sales_credit=credit,
    )
    db.add(created)
    db.commit()
    return created


def open_test_shift(db, seller: User):
    from app.models import CashRegister

    register = CashRegister(name=f"Register {seller.name}", is_active=True)
    db.add(register)
    db.commit()
    return open_shift(
        db,
        seller_id=seller.id,
        cash_register_id=register.id,
        business_date=date(2026, 7, 12),
        starting_cash="0",
    )


def service_product(db, name="Service", price="50", vat="24") -> Product:
    product = Product(name=name, unit_price=Decimal(price), vat_percent=Decimal(vat), is_active=True, is_stock_item=False)
    db.add(product)
    db.commit()
    return product


def stocked_product(db, manager: User, name="Stock product") -> Product:
    product = Product(name=name, unit_price=Decimal("20.00"), vat_percent=Decimal("24"), is_active=True, is_stock_item=True)
    supplier = Supplier(name=f"Supplier {name}", is_active=True)
    db.add_all([product, supplier])
    db.commit()
    _, location = create_default_warehouse(db)
    receipt = create_goods_receipt(db, supplier_id=supplier.id, receipt_date=date.today(), received_by_user_id=manager.id)
    add_goods_receipt_line(
        db,
        goods_receipt_id=receipt.id,
        product_id=product.id,
        destination_location_id=location.id,
        quantity_value="5",
        purchase_unit_price_ex_vat="10",
        vat_rate="24",
    )
    post_goods_receipt(db, goods_receipt_id=receipt.id, posted_by_user_id=manager.id)
    db.refresh(product)
    return product


def test_quick_sale_supports_multiple_lines_split_payment_and_identity():
    with SessionLocal() as db:
        seller = user(db, "Credited Seller")
        operator = user(db, "Operator Manager", "manager", credit=False)
        shift = open_test_shift(db, seller)
        service_a = service_product(db, "Massage", "60", "24")
        service_b = service_product(db, "Transport", "40", "10")

        sale = create_sale_from_lines(
            db,
            shift_id=shift.id,
            seller_id=seller.id,
            created_by_user_id=operator.id,
            seller_selection_mode="selectable_active_seller",
            lines=[
                SaleLineInput(product_id=service_a.id, description="Massage", quantity="1", unit_price="60", vat_percent="24"),
                SaleLineInput(product_id=service_b.id, description="Transport", quantity="2", unit_price="40", vat_percent="10"),
            ],
            payments=[
                PaymentInput("cash", "50"),
                PaymentInput("card", "90"),
            ],
            idempotency_key="quick-multi-split",
        )

        assert sale.source_type == "pos"
        assert sale.total == Decimal("140.00")
        assert sale.payment_method == "mixed"
        assert sale.settlement_status == "paid"
        assert sale.sold_by_user_id == seller.id
        assert sale.created_by_user_id == operator.id
        assert [payment.received_by_user_id for payment in sale.payments] == [operator.id, operator.id]
        assert sale_paid_amount(sale) == Decimal("140.00")
        assert sale_balance_due(sale) == Decimal("0.00")


def test_work_order_can_be_converted_to_awaiting_invoice_without_fake_payment():
    with SessionLocal() as db:
        seller = user(db, "Invoice Seller")
        shift = open_test_shift(db, seller)
        customer = Customer(name="Invoice Customer", email="invoice@example.test")
        product = service_product(db, "Consulting", "100", "24")
        job = Job(title="Invoice work", customer=customer)
        db.add(job)
        db.commit()
        db.add(JobItem(job_id=job.id, product_id=product.id, description="Consulting", quantity=Decimal("1"), unit_price=Decimal("100"), vat_percent=Decimal("24"), line_total=Decimal("100")))
        db.commit()

        sale = create_sale_from_work_order(
            db,
            work_order_id=job.id,
            shift_id=shift.id,
            seller_id=seller.id,
            created_by_user_id=seller.id,
            payments=[],
            send_to_invoice=True,
        )

        assert sale.source_type == "work_order"
        assert sale.work_order_id == job.id
        assert sale.payment_method == "invoice"
        assert sale.settlement_status == "awaiting_invoice"
        assert sale.invoice_customer_snapshot_json and "Invoice Customer" in sale.invoice_customer_snapshot_json
        assert db.query(Payment).filter(Payment.sale_id == sale.id).count() == 0


def test_work_order_conversion_is_idempotent_and_stock_issues_once():
    with SessionLocal() as db:
        manager = user(db, "Stock Manager", "manager", credit=False)
        seller = user(db, "Stock Seller")
        shift = open_test_shift(db, seller)
        product = stocked_product(db, manager)
        customer = Customer(name="Stock Customer")
        job = Job(title="Stock work", customer=customer)
        db.add(job)
        db.commit()
        db.add(JobItem(job_id=job.id, product_id=product.id, description="Stock material", quantity=Decimal("2"), unit_price=Decimal("20"), vat_percent=Decimal("24"), line_total=Decimal("40")))
        db.commit()

        sale = create_sale_from_work_order(
            db,
            work_order_id=job.id,
            shift_id=shift.id,
            seller_id=seller.id,
            created_by_user_id=manager.id,
            payments=[PaymentInput("card")],
        )
        duplicate = create_sale_from_work_order(
            db,
            work_order_id=job.id,
            shift_id=shift.id,
            seller_id=seller.id,
            created_by_user_id=manager.id,
            payments=[PaymentInput("card")],
        )

        assert duplicate.id == sale.id
        assert db.query(Sale).filter(Sale.work_order_id == job.id).count() == 1
        sale_transactions = db.query(InventoryTransaction).filter(InventoryTransaction.sale_id == sale.id, InventoryTransaction.transaction_type == "sale").all()
        assert len(sale_transactions) == 1
        assert sale.cost_of_goods_sold_ex_vat == Decimal("20.00")
        db.refresh(product)
        assert product.current_inventory_quantity == Decimal("3.000")


def test_partial_payment_with_invoice_remainder_and_overpayment_rejected():
    with SessionLocal() as db:
        seller = user(db, "Partial Seller")
        shift = open_test_shift(db, seller)
        product = service_product(db, "Partial service", "100", "24")
        customer = Customer(name="Partial Customer")
        job = Job(title="Partial work", customer=customer)
        db.add(job)
        db.commit()
        db.add(JobItem(job_id=job.id, product_id=product.id, description="Partial service", quantity=Decimal("1"), unit_price=Decimal("100"), vat_percent=Decimal("24"), line_total=Decimal("100")))
        db.commit()

        sale = create_sale_from_work_order(
            db,
            work_order_id=job.id,
            shift_id=shift.id,
            seller_id=seller.id,
            created_by_user_id=seller.id,
            payments=[PaymentInput("cash", "40"), PaymentInput("invoice")],
            send_to_invoice=True,
        )

        assert sale.settlement_status == "partially_paid_awaiting_invoice"
        assert sale_paid_amount(sale) == Decimal("40.00")
        assert sale_balance_due(sale) == Decimal("60.00")

        with pytest.raises(ValueError, match="cannot exceed"):
            create_sale_from_lines(
                db,
                shift_id=shift.id,
                seller_id=seller.id,
                created_by_user_id=seller.id,
                lines=[SaleLineInput(product_id=product.id, description="Overpaid", quantity="1", unit_price="100", vat_percent="24")],
                payments=[PaymentInput("cash", "120")],
                idempotency_key="overpaid-sale",
            )


def test_work_order_conversion_route_renders():
    with SessionLocal() as db:
        customer = Customer(name="Route Conversion Customer")
        job = Job(title="Route conversion work", customer=customer)
        db.add(job)
        db.commit()
        job_id = job.id

    with TestClient(app) as client:
        response = client.get(f"/sales/work-orders/{job_id}")

    assert response.status_code == 200
    assert "Create sale / take payment" in response.text


def test_quick_sale_route_creates_multiline_sale_and_receipt_loads():
    with SessionLocal() as db:
        seller = user(db, "Route Seller")
        shift = open_test_shift(db, seller)
        product_a = service_product(db, "Route product A", "12", "24")
        product_b = service_product(db, "Route product B", "8", "10")
        shift_id = shift.id
        seller_id = seller.id
        product_a_id = product_a.id
        product_b_id = product_b.id

    with TestClient(app) as client:
        response = client.post(
            "/sales/quick",
            data={
                "shift_id": str(shift_id),
                "seller_id": str(seller_id),
                "product_id": [str(product_a_id), str(product_b_id)],
                "description": ["Route product A", "Route product B"],
                "quantity": ["1", "2"],
                "unit_price": ["12", "8"],
                "vat_percent": ["24", "10"],
                "discount_amount": ["0", "0"],
                "payment_method": ["cash", "card"],
                "payment_amount": ["10", "18"],
                "idempotency_key": "route-quick-sale",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        sale_path = response.headers["location"]
        detail = client.get(sale_path)
        receipt = client.get(f"{sale_path}/receipt")

    assert detail.status_code == 200
    assert "Balance due" in detail.text
    assert receipt.status_code == 200
    assert "Sale summary" in receipt.text


def test_work_order_invoice_post_route_and_invoice_queue_render():
    with SessionLocal() as db:
        seller = user(db, "Queue Seller")
        shift = open_test_shift(db, seller)
        customer = Customer(name="Queue Customer")
        product = service_product(db, "Queue Service", "75", "24")
        job = Job(title="Queue work", customer=customer)
        db.add(job)
        db.commit()
        db.add(JobItem(job_id=job.id, product_id=product.id, description="Queue Service", quantity=Decimal("1"), unit_price=Decimal("75"), vat_percent=Decimal("24"), line_total=Decimal("75")))
        db.commit()
        job_id = job.id
        shift_id = shift.id
        seller_id = seller.id

    with TestClient(app) as client:
        response = client.post(
            f"/sales/work-orders/{job_id}",
            data={
                "shift_id": str(shift_id),
                "seller_id": str(seller_id),
                "payment_method": ["invoice"],
                "payment_amount": [""],
                "send_to_invoice": "true",
                "idempotency_key": f"route-work-order:{job_id}",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        queue = client.get("/sales/invoice-queue")

    assert queue.status_code == 200
    assert "Queue Customer" in queue.text
    assert "Awaiting invoice" in queue.text


def invoice_sale(db, *, title="Follow-up work") -> Sale:
    seller = user(db, f"{title} Seller")
    shift = open_test_shift(db, seller)
    customer = Customer(name=f"{title} Customer")
    product = service_product(db, f"{title} Service", "100", "24")
    job = Job(title=title, customer=customer)
    db.add(job)
    db.commit()
    db.add(
        JobItem(
            job_id=job.id,
            product_id=product.id,
            description=f"{title} Service",
            quantity=Decimal("1"),
            unit_price=Decimal("100"),
            vat_percent=Decimal("24"),
            line_total=Decimal("100"),
        )
    )
    db.commit()
    return create_sale_from_work_order(
        db,
        work_order_id=job.id,
        shift_id=shift.id,
        seller_id=seller.id,
        created_by_user_id=seller.id,
        payments=[],
        send_to_invoice=True,
    )


def test_invoice_due_date_creates_dashboard_followup_alert():
    with SessionLocal() as db:
        sale = invoice_sale(db, title="Overdue invoice")
        transfer_sale_to_invoicing(
            db,
            sale_id=sale.id,
            service_name="External Books",
            external_invoice_number="EXT-100",
            invoice_date_value=date(2026, 7, 1),
            due_date_value=date(2026, 7, 5),
            actor_user_id=sale.sold_by_user_id,
        )
        alerts = invoice_follow_up_alerts(db, as_of=date(2026, 7, 12))

    assert len(alerts) == 1
    assert alerts[0]["status"] == "payment_check_due"
    assert alerts[0]["message_key"] == "check_external_payment_status"


def test_invoice_paid_confirmation_clears_followup_alert_and_does_not_create_cash_or_card_payment():
    with SessionLocal() as db:
        sale = invoice_sale(db, title="Paid invoice")
        transfer_sale_to_invoicing(
            db,
            sale_id=sale.id,
            service_name="External Books",
            external_invoice_number="EXT-PAID",
            invoice_date_value=date(2026, 7, 1),
            due_date_value=date(2026, 7, 5),
            actor_user_id=sale.sold_by_user_id,
        )
        confirm_invoice_paid(
            db,
            sale_id=sale.id,
            payment_date_value=date(2026, 7, 10),
            received_amount="100",
            notes="Confirmed in external system",
            actor_user_id=sale.sold_by_user_id,
        )
        db.refresh(sale)
        alerts = invoice_follow_up_alerts(db, as_of=date(2026, 7, 12))
        payments = db.query(Payment).filter(Payment.sale_id == sale.id).all()

    assert sale.settlement_status == "paid"
    assert sale.paid_at.date() == date(2026, 7, 10)
    assert alerts == []
    assert [payment.payment_method for payment in payments] == ["bank_transfer"]


def test_invoice_unpaid_confirmation_schedules_default_seven_day_reminder():
    with SessionLocal() as db:
        sale = invoice_sale(db, title="Unpaid invoice")
        transfer_sale_to_invoicing(
            db,
            sale_id=sale.id,
            service_name="External Books",
            external_invoice_number="EXT-UNPAID",
            invoice_date_value=date(2026, 7, 1),
            due_date_value=date(2026, 7, 5),
            actor_user_id=sale.sold_by_user_id,
        )
        confirm_invoice_unpaid(
            db,
            sale_id=sale.id,
            checked_date_value=date(2026, 7, 12),
            actor_user_id=sale.sold_by_user_id,
        )
        db.refresh(sale)

    assert sale.settlement_status == "unpaid"
    assert sale.payment_status_checked_at.date() == date(2026, 7, 12)
    assert sale.next_follow_up_at.date() == date(2026, 7, 19)


def test_invoice_reminder_due_and_sent_reschedules_next_check():
    with SessionLocal() as db:
        sale = invoice_sale(db, title="Reminder invoice")
        transfer_sale_to_invoicing(
            db,
            sale_id=sale.id,
            service_name="External Books",
            external_invoice_number="EXT-REM",
            invoice_date_value=date(2026, 7, 1),
            due_date_value=date(2026, 7, 5),
            actor_user_id=sale.sold_by_user_id,
        )
        confirm_invoice_unpaid(
            db,
            sale_id=sale.id,
            checked_date_value=date(2026, 7, 12),
            next_follow_up_date_value=date(2026, 7, 13),
            actor_user_id=sale.sold_by_user_id,
        )

        assert invoice_follow_up_status(sale, as_of=date(2026, 7, 13)) == "reminder_due"

        record_invoice_reminder_sent(
            db,
            sale_id=sale.id,
            reminder_date_value=date(2026, 7, 13),
            actor_user_id=sale.sold_by_user_id,
        )
        db.refresh(sale)

    assert sale.settlement_status == "reminder_sent"
    assert sale.reminder_count == 1
    assert sale.last_reminder_sent_at.date() == date(2026, 7, 13)
    assert sale.next_follow_up_at.date() == date(2026, 7, 20)


def test_invoice_followup_routes_render_and_dashboard_alert_disappears_after_payment():
    today = date.today()
    with SessionLocal() as db:
        sale = invoice_sale(db, title="Route followup")
        sale_id = sale.id

    with TestClient(app) as client:
        transfer = client.post(
            f"/sales/{sale_id}/invoice-transfer",
            data={
                "external_invoice_service": "External Books",
                "external_invoice_number": "ROUTE-EXT",
                "invoice_date": str(today - timedelta(days=10)),
                "due_date": str(today - timedelta(days=1)),
                "external_invoice_reference": "https://example.test/invoice/ROUTE-EXT",
                "notes": "Transferred manually",
            },
            follow_redirects=False,
        )
        assert transfer.status_code == 303
        dashboard = client.get("/")
        assert "Payment check due" in dashboard.text

        unpaid = client.post(
            f"/sales/{sale_id}/invoice-unpaid",
            data={"checked_date": str(today), "notes": "Not paid yet"},
            follow_redirects=False,
        )
        assert unpaid.status_code == 303

        paid = client.post(
            f"/sales/{sale_id}/invoice-paid",
            data={"payment_date": str(today), "received_amount": "100"},
            follow_redirects=False,
        )
        assert paid.status_code == 303
        dashboard_after_payment = client.get("/")

    assert "Payment check due" not in dashboard_after_payment.text
    assert "Reminder due" not in dashboard_after_payment.text
