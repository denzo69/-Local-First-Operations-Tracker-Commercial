import json
from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import (
    AuditLog,
    CashMovement,
    CashRegister,
    Customer,
    DailyClosingSnapshot,
    Job,
    Payment,
    Product,
    Refund,
    Role,
    SaleLine,
    User,
)
from app.services.sales_service import (
    add_cash_movement,
    add_refund,
    build_daily_closing_snapshot,
    assert_business_date_open,
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
        assert snapshot["version"] == 1
        assert snapshot["payment_totals"][0]["payment_method"] == "cash"
        assert snapshot["payment_totals"][0]["gross_received"] == "100.00"
        assert snapshot["seller_totals"][0]["seller_id"] == seller.id
        assert snapshot["seller_totals"][0]["seller_name"] == seller.name
        assert snapshot["seller_totals"][0]["gross_sales"] == "100.00"
        assert snapshot["total_refunds"] == "10.00"
        assert snapshot["total_discounts"] == "24.00"
        assert snapshot["vat_totals"][0]["gross_vat"] == "19.35"

        with pytest.raises(ValueError, match="Only Admin or Manager"):
            reopen_daily_closing(db, closing_id=closing.id, user_id=readonly.id, reason="Test")

        reopened = reopen_daily_closing(db, closing_id=closing.id, user_id=admin.id, reason="Correction")
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

    with SessionLocal() as db:
        close_shift(db, shift_id=shift_id, counted_cash="0")

    assert client.get("/sales").status_code == 200
    assert client.get("/daily-closings").status_code == 200
    response = client.post(
        "/daily-closings",
        data={"business_date": date.today().isoformat(), "created_by_user_id": admin_id},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert client.get("/seller-reports").status_code == 200


def test_daily_closing_requires_closed_shifts_and_closed_date_blocks_writes_until_reopened():
    with SessionLocal() as db:
        admin = create_user(db, "Lock Admin", "admin")
        seller = create_user(db, "Lock Seller")
        register = create_register(db, "Lock Register")
        shift = open_shift(
            db,
            seller_id=seller.id,
            cash_register_id=register.id,
            business_date=date.today(),
            starting_cash="10",
        )

        with pytest.raises(ValueError, match="shift.*open"):
            create_daily_closing(db, business_date=date.today(), created_by_user_id=admin.id)

        sale = create_sale_with_payment(
            db,
            seller_id=seller.id,
            shift_id=shift.id,
            payment_method="cash",
            description="Lock sale",
            quantity="1",
            unit_price="10",
            vat_percent="24",
        )
        close_shift(db, shift_id=shift.id, counted_cash="20")
        closing = create_daily_closing(db, business_date=date.today(), created_by_user_id=admin.id)

        with pytest.raises(ValueError, match="Business date is closed"):
            assert_business_date_open(db, date.today())
        with pytest.raises(ValueError, match="Business date is closed"):
            open_shift(
                db,
                seller_id=seller.id,
                cash_register_id=register.id,
                business_date=date.today(),
                starting_cash="0",
            )
        with pytest.raises(ValueError, match="Refund requires an open shift|Business date is closed"):
            add_refund(
                db,
                sale_id=sale.id,
                seller_id=seller.id,
                amount="1",
                payment_method="cash",
            )
        with pytest.raises(ValueError, match="Cash movement requires an open shift|Business date is closed"):
            add_cash_movement(
                db,
                shift_id=shift.id,
                seller_id=seller.id,
                movement_type="cash_in",
                amount="1",
            )

        reopen_daily_closing(db, closing_id=closing.id, user_id=admin.id, reason="Add correction")
        unlocked_shift = open_shift(
            db,
            seller_id=seller.id,
            cash_register_id=register.id,
            business_date=date.today(),
            starting_cash="0",
        )
        assert unlocked_shift.status == "open"


def test_daily_closing_snapshot_is_immutable_and_reclose_creates_new_version():
    with SessionLocal() as db:
        admin = create_user(db, "Snapshot Admin", "admin")
        seller = create_user(db, "Snapshot Seller")
        register = create_register(db, "Snapshot Register")
        shift = open_shift(
            db,
            seller_id=seller.id,
            cash_register_id=register.id,
            business_date=date.today(),
            starting_cash="0",
        )
        create_sale_with_payment(
            db,
            seller_id=seller.id,
            shift_id=shift.id,
            payment_method="card",
            description="Original sale",
            quantity="1",
            unit_price="50",
            vat_percent="24",
        )
        close_shift(db, shift_id=shift.id, counted_cash="0")
        closing = create_daily_closing(db, business_date=date.today(), created_by_user_id=admin.id)
        snapshot_v1 = (
            db.query(DailyClosingSnapshot)
            .filter(DailyClosingSnapshot.daily_closing_id == closing.id, DailyClosingSnapshot.version == 1)
            .one()
        )
        original_json = snapshot_v1.snapshot_json

        seller.name = "Renamed Seller"
        db.commit()
        response = client.get(f"/daily-closings/{closing.id}")
        assert response.status_code == 200
        assert "Snapshot Seller" in response.text
        assert "Renamed Seller" not in response.text
        assert db.get(DailyClosingSnapshot, snapshot_v1.id).snapshot_json == original_json

        reopen_daily_closing(db, closing_id=closing.id, user_id=admin.id, reason="Late sale")
        shift2 = open_shift(
            db,
            seller_id=seller.id,
            cash_register_id=register.id,
            business_date=date.today(),
            starting_cash="0",
        )
        create_sale_with_payment(
            db,
            seller_id=seller.id,
            shift_id=shift2.id,
            payment_method="card",
            description="Late sale",
            quantity="1",
            unit_price="20",
            vat_percent="24",
        )
        close_shift(db, shift_id=shift2.id, counted_cash="0")
        create_daily_closing(db, business_date=date.today(), created_by_user_id=admin.id)
        snapshots = (
            db.query(DailyClosingSnapshot)
            .filter(DailyClosingSnapshot.daily_closing_id == closing.id)
            .order_by(DailyClosingSnapshot.version.asc())
            .all()
        )
        assert [snapshot.version for snapshot in snapshots] == [1, 2]
        assert snapshots[0].snapshot_json == original_json
        assert json.loads(snapshots[1].snapshot_json)["gross_sales"] == "70.00"


def test_refund_cumulative_limits_status_and_vat_breakdown():
    with SessionLocal() as db:
        seller = create_user(db, "Refund Seller")
        register = create_register(db, "Refund Register")
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
            payment_method="cash",
            description="Refund sale",
            quantity="1",
            unit_price="100",
            vat_percent="24",
        )

        first = add_refund(db, sale_id=sale.id, seller_id=seller.id, amount="25", payment_method="cash")
        assert first.vat_amount == Decimal("4.84")
        assert json.loads(first.vat_breakdown_json)["24"]["vat"] == "4.84"
        assert db.get(type(sale), sale.id).status == "partially_refunded"

        add_refund(db, sale_id=sale.id, seller_id=seller.id, amount="25", payment_method="cash")
        assert db.get(type(sale), sale.id).status == "partially_refunded"
        add_refund(db, sale_id=sale.id, seller_id=seller.id, amount="50", payment_method="cash")
        assert db.get(type(sale), sale.id).status == "refunded"
        with pytest.raises(ValueError, match="exceeds remaining"):
            add_refund(db, sale_id=sale.id, seller_id=seller.id, amount="0.01", payment_method="cash")


def test_multi_vat_refund_without_allocation_is_rejected():
    with SessionLocal() as db:
        seller = create_user(db, "Multi Vat Seller")
        register = create_register(db, "Multi Vat Register")
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
            payment_method="cash",
            description="Line one",
            quantity="1",
            unit_price="10",
            vat_percent="24",
        )
        db.add(
            SaleLine(
                sale_id=sale.id,
                description_snapshot="Line two",
                quantity=Decimal("1"),
                unit_price=Decimal("10"),
                vat_percent=Decimal("14"),
                line_total=Decimal("10"),
                vat_amount=Decimal("1.23"),
            )
        )
        sale.total = Decimal("20")
        db.commit()

        with pytest.raises(ValueError, match="Multi-VAT"):
            add_refund(db, sale_id=sale.id, seller_id=seller.id, amount="5", payment_method="cash")


@pytest.mark.parametrize("field,value,match", [
    ("starting_cash", "-1", "Starting cash"),
    ("starting_cash", "NaN", "finite"),
    ("starting_cash", "Infinity", "finite"),
    ("starting_cash", "bad", "valid decimal"),
])
def test_shift_money_validation(field, value, match):
    with SessionLocal() as db:
        seller = create_user(db, f"Validation Seller {value}")
        register = create_register(db, f"Validation Register {value}")
        with pytest.raises(ValueError, match=match):
            open_shift(
                db,
                seller_id=seller.id,
                cash_register_id=register.id,
                business_date=date.today(),
                starting_cash=value,
            )


@pytest.mark.parametrize("kwargs,match", [
    ({"quantity": "0"}, "Quantity"),
    ({"quantity": "-1"}, "Quantity"),
    ({"unit_price": "-1"}, "Unit price"),
    ({"vat_percent": "-1"}, "VAT percent"),
    ({"vat_percent": "101"}, "VAT percent"),
    ({"discount_amount": "-1"}, "Discount"),
    ({"discount_amount": "11"}, "Discount cannot exceed"),
    ({"description": ""}, "Description"),
    ({"unit_price": "NaN"}, "finite"),
])
def test_sale_input_validation(kwargs, match):
    with SessionLocal() as db:
        seller = create_user(db, f"Sale Validation Seller {match}")
        register = create_register(db, f"Sale Validation Register {match}")
        shift = open_shift(
            db,
            seller_id=seller.id,
            cash_register_id=register.id,
            business_date=date.today(),
            starting_cash="0",
        )
        data = {
            "seller_id": seller.id,
            "shift_id": shift.id,
            "payment_method": "cash",
            "description": "Valid",
            "quantity": "1",
            "unit_price": "10",
            "vat_percent": "24",
            "discount_amount": "0",
        }
        data.update(kwargs)
        with pytest.raises(ValueError, match=match):
            create_sale_with_payment(db, **data)


def test_seller_register_and_foreign_key_validation():
    with SessionLocal() as db:
        inactive = create_user(db, "Inactive Seller")
        inactive.is_active = False
        readonly = create_user(db, "Read Only Seller", "read_only")
        seller = create_user(db, "Foreign Key Seller")
        inactive_register = create_register(db, "Inactive Register")
        inactive_register.is_active = False
        register = create_register(db, "Foreign Key Register")
        inactive_product = Product(name="Test product inactive", is_active=False)
        db.add(inactive_product)
        db.commit()

        with pytest.raises(ValueError, match="Active user"):
            open_shift(db, seller_id=inactive.id, cash_register_id=register.id, business_date=date.today(), starting_cash="0")
        with pytest.raises(ValueError, match="role"):
            open_shift(db, seller_id=readonly.id, cash_register_id=register.id, business_date=date.today(), starting_cash="0")
        with pytest.raises(ValueError, match="Cash register not found"):
            open_shift(db, seller_id=seller.id, cash_register_id=999999, business_date=date.today(), starting_cash="0")
        with pytest.raises(ValueError, match="Active cash register"):
            open_shift(db, seller_id=seller.id, cash_register_id=inactive_register.id, business_date=date.today(), starting_cash="0")

        shift = open_shift(db, seller_id=seller.id, cash_register_id=register.id, business_date=date.today(), starting_cash="0")
        with pytest.raises(ValueError, match="Active product"):
            create_sale_with_payment(
                db,
                seller_id=seller.id,
                shift_id=shift.id,
                payment_method="cash",
                description="Invalid product",
                quantity="1",
                unit_price="1",
                vat_percent="24",
                product_id=inactive_product.id,
            )
        with pytest.raises(ValueError, match="Work Order not found"):
            create_sale_with_payment(
                db,
                seller_id=seller.id,
                shift_id=shift.id,
                payment_method="cash",
                description="Invalid Work Order",
                quantity="1",
                unit_price="1",
                vat_percent="24",
                work_order_id=999999,
            )


def test_audit_entity_ids_for_shift_sale_refund_cash_movement_and_closing():
    with SessionLocal() as db:
        admin = create_user(db, "Audit Admin", "admin")
        seller = create_user(db, "Audit Seller")
        register = create_register(db, "Audit Register")
        shift = open_shift(db, seller_id=seller.id, cash_register_id=register.id, business_date=date.today(), starting_cash="0")
        sale = create_sale_with_payment(
            db,
            seller_id=seller.id,
            shift_id=shift.id,
            payment_method="cash",
            description="Audit sale",
            quantity="1",
            unit_price="10",
            vat_percent="24",
        )
        movement = add_cash_movement(db, shift_id=shift.id, seller_id=seller.id, movement_type="cash_in", amount="1")
        refund = add_refund(db, sale_id=sale.id, seller_id=seller.id, amount="1", payment_method="cash")
        close_shift(db, shift_id=shift.id, counted_cash="10")
        closing = create_daily_closing(db, business_date=date.today(), created_by_user_id=admin.id)

        assert db.query(AuditLog).filter(AuditLog.event_type == "shift.opened", AuditLog.entity_id == shift.id).count() == 1
        assert db.query(AuditLog).filter(AuditLog.event_type == "sale.created", AuditLog.entity_id == sale.id).count() == 1
        assert db.query(AuditLog).filter(AuditLog.event_type == "cash_movement.created", AuditLog.entity_id == movement.id).count() == 1
        assert db.query(AuditLog).filter(AuditLog.event_type == "sale.refunded", AuditLog.entity_id == refund.id).count() == 1
        assert db.query(AuditLog).filter(AuditLog.event_type == "daily_closing.closed", AuditLog.entity_id == closing.id).count() == 1
