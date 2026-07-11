import json
from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import AuditLog, CashRegister, Customer, DailyClosingSnapshot, Job, Payment, Product, Role, Sale, User
from app.services.sales_service import (
    add_cash_movement,
    add_refund,
    build_daily_closing_snapshot,
    close_shift,
    create_daily_closing,
    create_sale_with_payment,
    ensure_default_roles,
    open_shift,
    reopen_daily_closing,
    seller_report,
)

client = TestClient(app)


def create_user(db, name="Seller One", role_code="seller"):
    ensure_default_roles(db)
    role = db.query(Role).filter(Role.code == role_code).one()
    user = User(name=name, login_name=name.lower().replace(" ", "."), role=role, is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def create_register(db, name="Front register"):
    register = CashRegister(name=name, is_active=True)
    db.add(register)
    db.commit()
    db.refresh(register)
    return register


def test_default_roles_are_seeded_idempotently():
    with SessionLocal() as db:
        first = ensure_default_roles(db)
        second = ensure_default_roles(db)
        assert [role.code for role in first] == ["admin", "manager", "seller", "read_only"]
        assert db.query(Role).count() == len(second) == 4


def test_open_shift_allows_only_one_open_shift_per_seller_and_register():
    with SessionLocal() as db:
        seller = create_user(db)
        other_seller = create_user(db, "Seller Two")
        register = create_register(db)
        other_register = create_register(db, "Back register")

        shift = open_shift(
            db,
            seller_id=seller.id,
            cash_register_id=register.id,
            business_date=date.today(),
            starting_cash="100",
        )

        assert shift.status == "open"
        with pytest.raises(ValueError, match="Seller already"):
            open_shift(
                db,
                seller_id=seller.id,
                cash_register_id=other_register.id,
                business_date=date.today(),
                starting_cash="0",
            )
        with pytest.raises(ValueError, match="Cash register already"):
            open_shift(
                db,
                seller_id=other_seller.id,
                cash_register_id=register.id,
                business_date=date.today(),
                starting_cash="0",
            )


def test_sale_links_work_order_and_keeps_payment_separate():
    with SessionLocal() as db:
        seller = create_user(db)
        register = create_register(db)
        customer = Customer(name="Test Customer")
        job = Job(title="Test job sale link", customer=customer)
        db.add(job)
        db.commit()
        shift = open_shift(
            db,
            seller_id=seller.id,
            cash_register_id=register.id,
            business_date=date.today(),
            starting_cash="20",
        )

        sale = create_sale_with_payment(
            db,
            seller_id=seller.id,
            shift_id=shift.id,
            work_order_id=job.id,
            payment_method="card",
            description="Wash",
            quantity="2",
            unit_price="10",
            vat_percent="24",
            discount_amount="1",
        )

        assert sale.work_order_id == job.id
        assert sale.total == Decimal("19.00")
        assert db.query(Payment).filter(Payment.sale_id == sale.id).count() == 1
        assert db.get(Job, job.id).sales[0].id == sale.id


def test_cash_sales_movements_refunds_and_shift_close_reconcile_cash():
    with SessionLocal() as db:
        seller = create_user(db)
        register = create_register(db)
        shift = open_shift(
            db,
            seller_id=seller.id,
            cash_register_id=register.id,
            business_date=date.today(),
            starting_cash="50",
        )
        sale = create_sale_with_payment(
            db,
            seller_id=seller.id,
            shift_id=shift.id,
            payment_method="cash",
            description="Cash sale",
            quantity="1",
            unit_price="30",
            vat_percent="24",
        )
        create_sale_with_payment(
            db,
            seller_id=seller.id,
            shift_id=shift.id,
            payment_method="card",
            description="Card sale",
            quantity="1",
            unit_price="99",
            vat_percent="24",
        )
        add_cash_movement(
            db,
            shift_id=shift.id,
            seller_id=seller.id,
            movement_type="cash_out",
            amount="5",
            reason="Petty cash",
        )
        add_refund(
            db,
            sale_id=sale.id,
            seller_id=seller.id,
            amount="10",
            payment_method="cash",
            reason="Partial refund",
        )

        closed = close_shift(db, shift_id=shift.id, counted_cash="64")
        assert closed.expected_closing_cash == Decimal("65.00")
        assert closed.cash_over_short == Decimal("-1.00")
        assert closed.status == "closed"


def test_daily_closing_snapshot_and_reopen_audit():
    with SessionLocal() as db:
        admin = create_user(db, "Admin User", "admin")
        seller = create_user(db)
        readonly = create_user(db, "Read Only", "read_only")
        register = create_register(db)
        shift = open_shift(
            db,
            seller_id=seller.id,
            cash_register_id=register.id,
            business_date=date.today(),
            starting_cash="100",
        )
        sale = create_sale_with_payment(
            db,
            seller_id=seller.id,
            shift_id=shift.id,
            payment_method="cash",
            description="Discounted sale",
            quantity="1",
            unit_price="124",
            vat_percent="24",
            discount_amount="24",
        )
        add_refund(
            db,
            sale_id=sale.id,
            seller_id=seller.id,
            amount="10",
            payment_method="cash",
            reason="Correction",
        )
        close_shift(db, shift_id=shift.id, counted_cash="190")

        closing = create_daily_closing(
            db,
            business_date=date.today(),
            created_by_user_id=admin.id,
        )
        snapshot_row = (
            db.query(DailyClosingSnapshot)
            .filter(DailyClosingSnapshot.daily_closing_id == closing.id)
            .one()
        )
        snapshot = json.loads(snapshot_row.snapshot_json)

        assert closing.total_sales == Decimal("100.00")
        assert snapshot["payment_totals"]["cash"] == "100.00"
        assert snapshot["seller_totals"][seller.name] == "100.00"
        assert snapshot["total_refunds"] == "10.00"
        assert snapshot["total_discounts"] == "24.00"
        assert snapshot["vat_totals"]["24"]["vat"] == "19.35"

        with pytest.raises(ValueError, match="Only Admin or Manager"):
            reopen_daily_closing(db, closing_id=closing.id, user_id=readonly.id)

        reopened = reopen_daily_closing(db, closing_id=closing.id, user_id=admin.id)
        assert reopened.status == "reopened"
        assert (
            db.query(AuditLog)
            .filter(AuditLog.event_type == "daily_closing.reopened")
            .count()
            == 1
        )


def test_seller_report_daily_weekly_monthly_source_metrics():
    with SessionLocal() as db:
        seller = create_user(db)
        register = create_register(db)
        shift = open_shift(
            db,
            seller_id=seller.id,
            cash_register_id=register.id,
            business_date=date.today(),
            starting_cash="0",
        )
        sale = create_sale_with_payment(
            db,
            seller_id=seller.id,
            shift_id=shift.id,
            payment_method="mobile",
            description="Report sale",
            quantity="2",
            unit_price="20",
            vat_percent="24",
            discount_amount="5",
        )
        add_refund(
            db,
            sale_id=sale.id,
            seller_id=seller.id,
            amount="4",
            payment_method="mobile",
        )

        report = seller_report(
            db,
            seller_id=seller.id,
            start_date=date.today(),
            end_date=date.today() + timedelta(days=1),
        )
        assert report["total_sales"] == Decimal("35.00")
        assert report["transaction_count"] == 1
        assert report["average_sale"] == Decimal("35.00")
        assert report["discounts"] == Decimal("5.00")
        assert report["refunds"] == Decimal("4.00")
        assert report["payment_totals"]["mobile"] == Decimal("35.00")


def test_new_sales_shift_closing_and_report_routes_load():
    with SessionLocal() as db:
        admin = create_user(db, "Route Admin", "admin")
        seller = create_user(db, "Route Seller")
        register = create_register(db, "Route Register")
        product = Product(name="Test product route", unit_price=Decimal("12.00"), vat_percent=Decimal("24"))
        db.add(product)
        db.commit()
        shift = open_shift(
            db,
            seller_id=seller.id,
            cash_register_id=register.id,
            business_date=date.today(),
            starting_cash="0",
        )
        admin_id = admin.id
        seller_id = seller.id
        shift_id = shift.id

    assert client.get("/users").status_code == 200
    assert client.get("/cash-registers").status_code == 200
    assert client.get("/shifts").status_code == 200
    assert client.get(f"/shifts/{shift_id}").status_code == 200
    assert client.get("/sales/new").status_code == 200

    response = client.post(
        "/sales",
        data={
            "shift_id": shift_id,
            "seller_id": seller_id,
            "payment_method": "card",
            "description": "Route sale",
            "quantity": "1",
            "unit_price": "12",
            "vat_percent": "24",
            "discount_amount": "0",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    assert client.get("/sales").status_code == 200
    assert client.get("/daily-closings").status_code == 200
    response = client.post(
        "/daily-closings",
        data={"business_date": date.today().isoformat(), "created_by_user_id": admin_id},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert client.get("/seller-reports").status_code == 200
