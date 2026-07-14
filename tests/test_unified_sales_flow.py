from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import CashRegister, Customer, InventoryTransaction, Job, JobItem, Payment, Product, Role, Sale, Setting, Supplier, User
from app.services.inventory_service import (
    add_goods_receipt_line,
    create_default_warehouse,
    create_goods_receipt,
    post_goods_receipt,
)
from app.services.sales_service import (
    PaymentInput,
    SaleLineInput,
    build_daily_closing_snapshot,
    calculate_expected_cash,
    confirm_invoice_paid,
    confirm_invoice_unpaid,
    create_sale_from_lines,
    create_sale_from_work_order,
    create_daily_closing,
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


def test_local_setup_has_default_cash_register_for_first_shift():
    with SessionLocal() as db:
        register = db.query(CashRegister).order_by(CashRegister.id.asc()).first()

    assert register is not None
    assert register.name == "Main register"
    assert register.is_active is True


def test_quick_sale_form_allows_submit_when_no_eligible_credited_seller():
    with SessionLocal() as db:
        admin = user(db, "Setup Admin", "admin", credit=False)
        register = db.query(CashRegister).order_by(CashRegister.id.asc()).first()
        assert register is not None
        open_shift(
            db,
            seller_id=admin.id,
            cash_register_id=register.id,
            business_date=date(2026, 7, 12),
            starting_cash="0",
        )

    with TestClient(app) as client:
        response = client.get("/sales/quick")

    assert response.status_code == 200
    assert "No seller on receipt" in response.text
    assert "Complete sale" in response.text
    assert "Cashier shift" not in response.text
    assert "disabled" not in response.text


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


def test_quick_sale_accepts_registered_or_manual_customer_name():
    with SessionLocal() as db:
        registered = Customer(name="Cash Buyer")
        db.add(registered)
        db.commit()
        customer_id = registered.id

    with TestClient(app) as client:
        registered_response = client.post(
            "/sales/quick",
            data={
                "customer_id": str(customer_id),
                "customer_name": "",
                "product_id": [""],
                "description": ["Customer route service"],
                "quantity": ["1"],
                "unit_price": ["12"],
                "vat_percent": ["24"],
                "discount_amount": ["0"],
                "payment_method": ["cash"],
                "payment_amount": [""],
                "idempotency_key": "route-quick-sale-customer",
            },
            follow_redirects=False,
        )
        manual_response = client.post(
            "/sales/quick",
            data={
                "customer_id": "",
                "customer_name": "Walk-in Customer",
                "product_id": [""],
                "description": ["Manual customer service"],
                "quantity": ["1"],
                "unit_price": ["8"],
                "vat_percent": ["24"],
                "discount_amount": ["0"],
                "payment_method": ["cash"],
                "payment_amount": [""],
                "idempotency_key": "route-quick-sale-manual-customer",
            },
            follow_redirects=False,
        )
        registered_detail = client.get(registered_response.headers["location"])
        manual_detail = client.get(manual_response.headers["location"])
        manual_receipt = client.get(f"{manual_response.headers['location']}/receipt")

    assert registered_response.status_code == 303
    assert manual_response.status_code == 303
    assert "Cash Buyer" in registered_detail.text
    assert "Walk-in Customer" in manual_detail.text
    assert "Walk-in Customer" in manual_receipt.text


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


def configure_sale_document_sequence(db, *, sequence: str = "1") -> None:
    db.add_all(
        [
            Setting(key="sale_document_prefix", value="SALE-"),
            Setting(key="sale_document_padding", value="6"),
            Setting(key="sale_document_annual_reset", value="false"),
            Setting(key="next_sale_document_sequence", value=sequence),
            Setting(key="sale_document_sequence_year", value="2026"),
        ]
    )
    db.commit()


def set_require_cashier_shift(db, value: bool) -> None:
    existing = db.query(Setting).filter(Setting.key == "require_cashier_shift").first()
    if existing is None:
        db.add(Setting(key="require_cashier_shift", value="true" if value else "false"))
    else:
        existing.value = "true" if value else "false"
    db.commit()


def test_quick_pos_cash_card_and_split_sales_work_without_shift():
    with SessionLocal() as db:
        seller = user(db, "Shiftless Seller")
        service = service_product(db, "Shiftless Service", "30", "24")

        cash_sale = create_sale_from_lines(
            db,
            seller_id=seller.id,
            created_by_user_id=seller.id,
            lines=[SaleLineInput(product_id=service.id, description="Cash", quantity="1", unit_price="30", vat_percent="24")],
            payments=[PaymentInput("cash")],
            idempotency_key="shiftless-cash",
        )
        card_sale = create_sale_from_lines(
            db,
            seller_id=seller.id,
            created_by_user_id=seller.id,
            lines=[SaleLineInput(product_id=service.id, description="Card", quantity="1", unit_price="30", vat_percent="24")],
            payments=[PaymentInput("card")],
            idempotency_key="shiftless-card",
        )
        split_sale = create_sale_from_lines(
            db,
            seller_id=seller.id,
            created_by_user_id=seller.id,
            lines=[SaleLineInput(product_id=service.id, description="Split", quantity="1", unit_price="30", vat_percent="24")],
            payments=[PaymentInput("cash", "10"), PaymentInput("card", "20")],
            idempotency_key="shiftless-split",
        )
        observed = {
            "cash_shift_id": cash_sale.shift_id,
            "card_shift_id": card_sale.shift_id,
            "split_shift_id": split_sale.shift_id,
            "cash_business_date": cash_sale.business_date,
            "card_status": card_sale.settlement_status,
            "split_method": split_sale.payment_method,
        }

    assert observed["cash_shift_id"] is None
    assert observed["card_shift_id"] is None
    assert observed["split_shift_id"] is None
    assert observed["cash_business_date"] == date.today()
    assert observed["card_status"] == "paid"
    assert observed["split_method"] == "mixed"


def test_work_order_cash_card_and_invoice_conversion_work_without_shift():
    with SessionLocal() as db:
        seller = user(db, "Shiftless Work Seller")
        product = service_product(db, "Shiftless Work Service", "50", "24")
        customer = Customer(name="Shiftless Work Customer")

        def make_job(title: str) -> Job:
            job = Job(title=title, customer=customer)
            db.add(job)
            db.commit()
            db.add(JobItem(job_id=job.id, product_id=product.id, description=title, quantity=Decimal("1"), unit_price=Decimal("50"), vat_percent=Decimal("24"), line_total=Decimal("50")))
            db.commit()
            return job

        cash_sale = create_sale_from_work_order(
            db,
            work_order_id=make_job("Shiftless cash work").id,
            seller_id=seller.id,
            created_by_user_id=seller.id,
            payments=[PaymentInput("cash")],
        )
        card_sale = create_sale_from_work_order(
            db,
            work_order_id=make_job("Shiftless card work").id,
            seller_id=seller.id,
            created_by_user_id=seller.id,
            payments=[PaymentInput("card")],
        )
        invoice_sale_record = create_sale_from_work_order(
            db,
            work_order_id=make_job("Shiftless invoice work").id,
            seller_id=seller.id,
            created_by_user_id=seller.id,
            payments=[],
            send_to_invoice=True,
        )
        observed = {
            "cash_shift_id": cash_sale.shift_id,
            "card_shift_id": card_sale.shift_id,
            "invoice_shift_id": invoice_sale_record.shift_id,
            "cash_method": cash_sale.payment_method,
            "card_method": card_sale.payment_method,
            "invoice_status": invoice_sale_record.settlement_status,
        }

    assert observed["cash_shift_id"] is None
    assert observed["card_shift_id"] is None
    assert observed["invoice_shift_id"] is None
    assert observed["cash_method"] == "cash"
    assert observed["card_method"] == "card"
    assert observed["invoice_status"] == "awaiting_invoice"


def test_require_cashier_shift_blocks_shiftless_sale_and_closed_shift_is_rejected():
    with SessionLocal() as db:
        seller = user(db, "Required Shift Seller")
        service = service_product(db, "Required Shift Service", "10", "24")
        set_require_cashier_shift(db, True)
        with pytest.raises(ValueError, match="cashier shift is required"):
            create_sale_from_lines(
                db,
                seller_id=seller.id,
                created_by_user_id=seller.id,
                lines=[SaleLineInput(product_id=service.id, description="No shift", quantity="1", unit_price="10", vat_percent="24")],
                payments=[PaymentInput("cash")],
                idempotency_key="blocked-shiftless",
            )

        shift = open_test_shift(db, seller)
        shift.status = "closed"
        db.commit()
        with pytest.raises(ValueError, match="must be open"):
            create_sale_from_lines(
                db,
                shift_id=shift.id,
                seller_id=seller.id,
                created_by_user_id=seller.id,
                lines=[SaleLineInput(product_id=service.id, description="Closed shift", quantity="1", unit_price="10", vat_percent="24")],
                payments=[PaymentInput("cash")],
                idempotency_key="blocked-closed-shift",
            )


def test_business_date_rules_for_shiftless_and_shift_linked_sales():
    with SessionLocal() as db:
        seller = user(db, "Business Date Seller")
        service = service_product(db, "Business Date Service", "15", "24")
        shift = open_test_shift(db, seller)

        shift_sale = create_sale_from_lines(
            db,
            shift_id=shift.id,
            seller_id=seller.id,
            created_by_user_id=seller.id,
            lines=[SaleLineInput(product_id=service.id, description="Shift date", quantity="1", unit_price="15", vat_percent="24")],
            payments=[PaymentInput("card")],
            idempotency_key="shift-business-date",
        )
        shiftless_sale = create_sale_from_lines(
            db,
            seller_id=seller.id,
            created_by_user_id=seller.id,
            lines=[SaleLineInput(product_id=service.id, description="Today date", quantity="1", unit_price="15", vat_percent="24")],
            payments=[PaymentInput("card")],
            idempotency_key="shiftless-business-date",
        )
        observed = {
            "shift_sale_business_date": shift_sale.business_date,
            "shiftless_sale_business_date": shiftless_sale.business_date,
        }

    assert observed["shift_sale_business_date"] == date(2026, 7, 12)
    assert observed["shiftless_sale_business_date"] == date.today()


def test_closed_business_date_blocks_shiftless_sale():
    with SessionLocal() as db:
        admin = user(db, "Closing Admin", "admin", credit=False)
        seller = user(db, "Closed Date Seller")
        service = service_product(db, "Closed Date Service", "20", "24")
        create_daily_closing(db, business_date=date.today(), created_by_user_id=admin.id)

        with pytest.raises(ValueError, match="Business date is closed"):
            create_sale_from_lines(
                db,
                seller_id=seller.id,
                created_by_user_id=seller.id,
                lines=[SaleLineInput(product_id=service.id, description="Closed date", quantity="1", unit_price="20", vat_percent="24")],
                payments=[PaymentInput("card")],
                idempotency_key="closed-date-shiftless",
            )


def test_optional_seller_modes_preserve_operator_payment_and_inventory_actor():
    with SessionLocal() as db:
        manager = user(db, "Optional Seller Manager", "manager", credit=False)
        credited = user(db, "Explicit Credited Seller")
        service = service_product(db, "Optional Seller Service", "10", "24")
        stock = stocked_product(db, manager, name="Optional Seller Stock")

        default_sale = create_sale_from_lines(
            db,
            created_by_user_id=credited.id,
            lines=[SaleLineInput(product_id=service.id, description="Default seller", quantity="1", unit_price="10", vat_percent="24")],
            payments=[PaymentInput("card")],
            idempotency_key="seller-default",
        )
        selected_sale = create_sale_from_lines(
            db,
            seller_id=credited.id,
            seller_mode="selected",
            created_by_user_id=manager.id,
            lines=[SaleLineInput(product_id=service.id, description="Selected seller", quantity="1", unit_price="10", vat_percent="24")],
            payments=[PaymentInput("card")],
            idempotency_key="seller-selected",
        )
        none_sale = create_sale_from_lines(
            db,
            seller_mode="none",
            created_by_user_id=manager.id,
            lines=[SaleLineInput(product_id=stock.id, description="No seller stock", quantity="1", unit_price="20", vat_percent="24")],
            payments=[PaymentInput("card")],
            idempotency_key="seller-none",
        )
        transaction = db.query(InventoryTransaction).filter(InventoryTransaction.sale_id == none_sale.id).one()
        observed = {
            "default_sold_by": default_sale.sold_by_user_id,
            "selected_sold_by": selected_sale.sold_by_user_id,
            "selected_created_by": selected_sale.created_by_user_id,
            "none_sold_by": none_sale.sold_by_user_id,
            "none_seller": none_sale.seller_id,
            "none_created_by": none_sale.created_by_user_id,
            "none_payment_receiver": none_sale.payments[0].received_by_user_id,
            "transaction_created_by": transaction.created_by_user_id,
            "credited_id": credited.id,
            "manager_id": manager.id,
        }

    assert observed["default_sold_by"] == observed["credited_id"]
    assert observed["selected_sold_by"] == observed["credited_id"]
    assert observed["selected_created_by"] == observed["manager_id"]
    assert observed["none_sold_by"] is None
    assert observed["none_seller"] is None
    assert observed["none_created_by"] == observed["manager_id"]
    assert observed["none_payment_receiver"] == observed["manager_id"]
    assert observed["transaction_created_by"] == observed["manager_id"]


def test_shiftless_cash_register_and_daily_closing_buckets():
    with SessionLocal() as db:
        seller = user(db, "Cash Register Seller")
        service = service_product(db, "Cash Register Service", "25", "24")
        register = CashRegister(name="Optional cash register", is_active=True)
        db.add(register)
        db.commit()
        shift = open_test_shift(db, seller)

        shift_sale = create_sale_from_lines(
            db,
            shift_id=shift.id,
            seller_id=seller.id,
            created_by_user_id=seller.id,
            lines=[SaleLineInput(product_id=service.id, description="Shift cash", quantity="1", unit_price="25", vat_percent="24")],
            payments=[PaymentInput("cash")],
            idempotency_key="cash-shift",
        )
        assigned_sale = create_sale_from_lines(
            db,
            cash_register_id=register.id,
            seller_id=seller.id,
            created_by_user_id=seller.id,
            lines=[SaleLineInput(product_id=service.id, description="Assigned cash", quantity="1", unit_price="25", vat_percent="24")],
            payments=[PaymentInput("cash")],
            idempotency_key="cash-assigned",
        )
        unassigned_sale = create_sale_from_lines(
            db,
            seller_id=seller.id,
            created_by_user_id=seller.id,
            lines=[SaleLineInput(product_id=service.id, description="Unassigned cash", quantity="1", unit_price="25", vat_percent="24")],
            payments=[PaymentInput("cash")],
            idempotency_key="cash-unassigned",
        )
        expected_shift_cash = calculate_expected_cash(db, shift)
        today_snapshot = build_daily_closing_snapshot(db, date.today())
        observed = {
            "shift_sale_register": shift_sale.cash_register_id,
            "shift_register": shift.cash_register_id,
            "assigned_shift_id": assigned_sale.shift_id,
            "assigned_register": assigned_sale.cash_register_id,
            "register_id": register.id,
            "unassigned_register": unassigned_sale.cash_register_id,
            "expected_shift_cash": expected_shift_cash,
            "today_snapshot": today_snapshot,
            "unassigned_document": unassigned_sale.document_number,
        }

    assert observed["shift_sale_register"] == observed["shift_register"]
    assert observed["assigned_shift_id"] is None
    assert observed["assigned_register"] == observed["register_id"]
    assert observed["unassigned_register"] is None
    assert observed["expected_shift_cash"] == Decimal("25.00")
    assert observed["today_snapshot"]["shiftless_cash_assigned"] == "25.00"
    assert observed["today_snapshot"]["shiftless_cash_unassigned"] == "25.00"
    assert observed["today_snapshot"]["unassigned_cash_sales"][0]["document_number"] == observed["unassigned_document"]


def test_reports_count_sales_once_and_unconverted_work_orders_as_pipeline():
    with SessionLocal() as db:
        seller = user(db, "No Double Count Seller")
        service = service_product(db, "No Double Count Service", "50", "24")
        customer = Customer(name="No Double Count Customer")

        create_sale_from_lines(
            db,
            seller_id=seller.id,
            created_by_user_id=seller.id,
            lines=[SaleLineInput(product_id=service.id, description="Direct POS", quantity="1", unit_price="30", vat_percent="24")],
            payments=[PaymentInput("card")],
            idempotency_key="no-double-direct",
        )

        converted_job = Job(title="Converted revenue", customer=customer)
        pipeline_job = Job(title="Pipeline only", customer=customer)
        db.add_all([converted_job, pipeline_job])
        db.commit()
        db.add_all(
            [
                JobItem(job_id=converted_job.id, product_id=service.id, description="Converted revenue", quantity=Decimal("1"), unit_price=Decimal("50"), vat_percent=Decimal("24"), line_total=Decimal("50")),
                JobItem(job_id=pipeline_job.id, product_id=service.id, description="Pipeline only", quantity=Decimal("1"), unit_price=Decimal("70"), vat_percent=Decimal("24"), line_total=Decimal("70")),
            ]
        )
        db.commit()
        create_sale_from_work_order(
            db,
            work_order_id=converted_job.id,
            seller_id=seller.id,
            created_by_user_id=seller.id,
            payments=[PaymentInput("card")],
            idempotency_key="no-double-work-order",
        )

    with TestClient(app) as client:
        response = client.get(f"/reports?day={date.today().isoformat()}&month={date.today().strftime('%Y-%m')}")

    assert response.status_code == 200
    assert "Day sales" in response.text
    assert "80.00" in response.text
    assert "Day billable pipeline" in response.text
    assert "70.00" in response.text
    assert "Converted" in response.text
    assert "150.00" not in response.text


def test_quick_pos_and_work_order_cash_sale_use_same_sale_document_sequence():
    with SessionLocal() as db:
        configure_sale_document_sequence(db)
        seller = user(db, "Number Seller")
        shift = open_test_shift(db, seller)
        service = service_product(db, "Number Service", "25", "24")

        quick_sale = create_sale_from_lines(
            db,
            shift_id=shift.id,
            seller_id=seller.id,
            created_by_user_id=seller.id,
            lines=[SaleLineInput(product_id=service.id, description="Counter sale", quantity="1", unit_price="25", vat_percent="24")],
            payments=[PaymentInput("cash")],
            idempotency_key="number-quick",
        )

        customer = Customer(name="Number Customer")
        job = Job(title="Number work", customer=customer, receipt_number="WO-2026-000123")
        db.add(job)
        db.commit()
        db.add(JobItem(job_id=job.id, product_id=service.id, description="Number work", quantity=Decimal("1"), unit_price=Decimal("25"), vat_percent=Decimal("24"), line_total=Decimal("25")))
        db.commit()

        work_order_sale = create_sale_from_work_order(
            db,
            work_order_id=job.id,
            shift_id=shift.id,
            seller_id=seller.id,
            created_by_user_id=seller.id,
            payments=[PaymentInput("cash")],
            idempotency_key="number-work-order",
        )
        quick_document_number = quick_sale.document_number
        work_order_document_number = work_order_sale.document_number

    assert quick_document_number == "SALE-2026-000001"
    assert work_order_document_number == "SALE-2026-000002"
    assert work_order_document_number != "WO-2026-000123"


def test_work_order_duplicate_conversion_does_not_allocate_second_sale_number():
    with SessionLocal() as db:
        configure_sale_document_sequence(db)
        seller = user(db, "Duplicate Number Seller")
        shift = open_test_shift(db, seller)
        service = service_product(db, "Duplicate Number Service", "40", "24")
        customer = Customer(name="Duplicate Number Customer")
        job = Job(title="Duplicate number work", customer=customer, receipt_number="WO-DUP-1")
        db.add(job)
        db.commit()
        db.add(JobItem(job_id=job.id, product_id=service.id, description="Duplicate number work", quantity=Decimal("1"), unit_price=Decimal("40"), vat_percent=Decimal("24"), line_total=Decimal("40")))
        db.commit()

        first = create_sale_from_work_order(
            db,
            work_order_id=job.id,
            shift_id=shift.id,
            seller_id=seller.id,
            created_by_user_id=seller.id,
            payments=[PaymentInput("card")],
            idempotency_key="duplicate-number",
        )
        second = create_sale_from_work_order(
            db,
            work_order_id=job.id,
            shift_id=shift.id,
            seller_id=seller.id,
            created_by_user_id=seller.id,
            payments=[PaymentInput("card")],
            idempotency_key="duplicate-number",
        )
        sequence = db.query(Setting).filter(Setting.key == "next_sale_document_sequence").one().value

    assert second.id == first.id
    assert first.document_number == "SALE-2026-000001"
    assert sequence == "2"


def test_external_invoice_number_never_replaces_sale_document_number():
    with SessionLocal() as db:
        configure_sale_document_sequence(db)
        sale = invoice_sale(db, title="External number")
        sale_id = sale.id
        sale_document_number = sale.document_number

        transfer_sale_to_invoicing(
            db,
            sale_id=sale.id,
            service_name="External Books",
            external_invoice_number="EXT-INV-900",
            invoice_date_value=date(2026, 7, 1),
            due_date_value=date(2026, 7, 14),
            actor_user_id=sale.sold_by_user_id,
        )
        db.refresh(sale)

    assert sale_document_number == "SALE-2026-000001"
    assert sale.external_invoice_number == "EXT-INV-900"
    assert sale.document_number == sale_document_number
    assert sale.document_number != sale.external_invoice_number


def test_sales_register_and_receipt_show_same_sale_document_number_and_work_order_reference():
    with SessionLocal() as db:
        configure_sale_document_sequence(db)
        seller = user(db, "Register Number Seller")
        shift = open_test_shift(db, seller)
        service = service_product(db, "Register Number Service", "30", "24")
        customer = Customer(name="Register Number Customer")
        job = Job(title="Register number work", customer=customer, receipt_number="WO-REF-900")
        db.add(job)
        db.commit()
        db.add(JobItem(job_id=job.id, product_id=service.id, description="Register number work", quantity=Decimal("1"), unit_price=Decimal("30"), vat_percent=Decimal("24"), line_total=Decimal("30")))
        db.commit()
        sale = create_sale_from_work_order(
            db,
            work_order_id=job.id,
            shift_id=shift.id,
            seller_id=seller.id,
            created_by_user_id=seller.id,
            payments=[PaymentInput("cash")],
            idempotency_key="register-receipt-number",
        )
        sale_id = sale.id
        sale_document_number = sale.document_number

    with TestClient(app) as client:
        sales_register = client.get("/sales")
        receipt = client.get(f"/sales/{sale_id}/receipt")

    assert sales_register.status_code == 200
    assert receipt.status_code == 200
    assert sale_document_number in sales_register.text
    assert sale_document_number in receipt.text
    assert "WO-REF-900" in receipt.text


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
