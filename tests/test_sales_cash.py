import json
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy import create_engine

from app.database import Base, SessionLocal
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
    Sale,
    SaleLine,
    Setting,
    User,
)
from app.services.sales_service import (
    add_cash_movement,
    add_refund,
    build_daily_closing_snapshot,
    assert_business_date_open,
    close_shift,
    correct_sale_seller,
    create_daily_closing,
    create_sale_with_payment,
    ensure_default_roles,
    open_shift,
    reopen_daily_closing,
    seller_report,
)
from app.services.migration_service import ensure_sqlite_schema_compatibility

client = TestClient(app)


def create_user(db, name="Seller One", role_code="seller", can_receive_sales_credit=None):
    ensure_default_roles(db)
    role = db.query(Role).filter(Role.code == role_code).one()
    if can_receive_sales_credit is None:
        can_receive_sales_credit = role_code == "seller"
    user = User(
        name=name,
        login_name=name.lower().replace(" ", "."),
        role=role,
        is_active=True,
        can_receive_sales_credit=can_receive_sales_credit,
    )
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
            refund_shift_id=shift.id,
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
            refund_shift_id=shift.id,
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
            refund_shift_id=shift.id,
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


def test_shared_register_sale_credits_selected_seller_and_tracks_operator():
    with SessionLocal() as db:
        operator = create_user(db, "Shared Operator", "manager")
        shift_owner = create_user(db, "Shared Shift Owner")
        selected_seller = create_user(db, "Selected Seller")
        register = create_register(db, "Shared Register")
        shift = open_shift(
            db,
            seller_id=shift_owner.id,
            cash_register_id=register.id,
            business_date=date.today(),
            starting_cash="0",
        )

        sale = create_sale_with_payment(
            db,
            seller_id=selected_seller.id,
            shift_id=shift.id,
            payment_method="card",
            description="Shared counter sale",
            quantity="1",
            unit_price="42",
            vat_percent="24",
            created_by_user_id=operator.id,
            seller_selection_mode="selectable_active_seller",
        )

        assert sale.seller_id == selected_seller.id
        assert sale.sold_by_user_id == selected_seller.id
        assert sale.created_by_user_id == operator.id
        assert sale.cash_register_id == register.id
        assert sale.payments[0].seller_id == selected_seller.id
        assert sale.payments[0].received_by_user_id == operator.id

        selected_report = seller_report(
            db,
            seller_id=selected_seller.id,
            start_date=date.today(),
            end_date=date.today() + timedelta(days=1),
        )
        owner_report = seller_report(
            db,
            seller_id=shift_owner.id,
            start_date=date.today(),
            end_date=date.today() + timedelta(days=1),
        )
        assert selected_report["gross_sales"] == Decimal("42.00")
        assert owner_report["gross_sales"] == Decimal("0.00")


def test_sale_seller_validation_and_legacy_report_fallback():
    with SessionLocal() as db:
        seller = create_user(db, "Fallback Seller")
        inactive = create_user(db, "Inactive Credit Seller")
        inactive.is_active = False
        register = create_register(db, "Fallback Register")
        shift = open_shift(
            db,
            seller_id=seller.id,
            cash_register_id=register.id,
            business_date=date.today(),
            starting_cash="0",
        )

        with pytest.raises(ValueError, match="Active user"):
            create_sale_with_payment(
                db,
                seller_id=inactive.id,
                shift_id=shift.id,
                payment_method="cash",
                description="Invalid credit",
                quantity="1",
                unit_price="1",
                vat_percent="24",
                seller_selection_mode="selectable_active_seller",
            )

        ineligible_manager = create_user(db, "Ineligible Manager Credit", "manager")
        with pytest.raises(ValueError, match="not eligible"):
            create_sale_with_payment(
                db,
                seller_id=ineligible_manager.id,
                shift_id=shift.id,
                payment_method="cash",
                description="Ineligible credit",
                quantity="1",
                unit_price="1",
                vat_percent="24",
                seller_selection_mode="selectable_active_seller",
            )

        legacy_sale = Sale(
            seller_id=seller.id,
            shift_id=shift.id,
            cash_register_id=register.id,
            payment_method="cash",
            subtotal=Decimal("8.06"),
            vat_total=Decimal("1.94"),
            total=Decimal("10.00"),
            sold_at=datetime.combine(date.today(), time.min, tzinfo=UTC),
        )
        db.add(legacy_sale)
        db.commit()

        report = seller_report(
            db,
            seller_id=seller.id,
            start_date=date.today(),
            end_date=date.today() + timedelta(days=1),
        )
        assert report["gross_sales"] == Decimal("10.00")


def test_manager_can_correct_sale_seller_with_audit_reason():
    with SessionLocal() as db:
        manager = create_user(db, "Correction Manager", "manager")
        readonly = create_user(db, "Correction Readonly", "read_only")
        original_seller = create_user(db, "Correction Original")
        new_seller = create_user(db, "Correction New")
        register = create_register(db, "Correction Register")
        shift = open_shift(
            db,
            seller_id=original_seller.id,
            cash_register_id=register.id,
            business_date=date.today(),
            starting_cash="0",
        )
        sale = create_sale_with_payment(
            db,
            seller_id=original_seller.id,
            shift_id=shift.id,
            payment_method="card",
            description="Correction sale",
            quantity="1",
            unit_price="20",
            vat_percent="24",
        )

        with pytest.raises(ValueError, match="Only Admin or Manager"):
            correct_sale_seller(
                db,
                sale_id=sale.id,
                new_sold_by_user_id=new_seller.id,
                corrected_by_user_id=readonly.id,
                reason="Wrong seller",
            )
        with pytest.raises(ValueError, match="reason"):
            correct_sale_seller(
                db,
                sale_id=sale.id,
                new_sold_by_user_id=new_seller.id,
                corrected_by_user_id=manager.id,
                reason="",
            )

        corrected = correct_sale_seller(
            db,
            sale_id=sale.id,
            new_sold_by_user_id=new_seller.id,
            corrected_by_user_id=manager.id,
            reason="Selected wrong seller at shared register",
        )

        assert corrected.sold_by_user_id == new_seller.id
        assert corrected.seller_id == new_seller.id
        assert corrected.seller_overridden_by_user_id == manager.id
        assert corrected.seller_override_reason == "Selected wrong seller at shared register"
        assert corrected.payments[0].seller_id == original_seller.id
        assert corrected.payments[0].received_by_user_id == original_seller.id
        audit = (
            db.query(AuditLog)
            .filter(AuditLog.event_type == "sale.seller_corrected", AuditLog.entity_id == sale.id)
            .one()
        )
        assert str(original_seller.id) in audit.description
        assert original_seller.name in audit.description
        assert str(new_seller.id) in audit.description
        assert new_seller.name in audit.description


def test_seller_correction_is_blocked_after_daily_closing_until_reopened():
    with SessionLocal() as db:
        admin = create_user(db, "Closed Correction Admin", "admin")
        original_seller = create_user(db, "Closed Correction Original")
        new_seller = create_user(db, "Closed Correction New")
        register = create_register(db, "Closed Correction Register")
        shift = open_shift(
            db,
            seller_id=original_seller.id,
            cash_register_id=register.id,
            business_date=date.today(),
            starting_cash="0",
        )
        sale = create_sale_with_payment(
            db,
            seller_id=original_seller.id,
            shift_id=shift.id,
            payment_method="card",
            description="Closed correction sale",
            quantity="1",
            unit_price="20",
            vat_percent="24",
        )
        close_shift(db, shift_id=shift.id, counted_cash="0")
        closing = create_daily_closing(db, business_date=date.today(), created_by_user_id=admin.id)

        with pytest.raises(ValueError, match="Business date is closed"):
            correct_sale_seller(
                db,
                sale_id=sale.id,
                new_sold_by_user_id=new_seller.id,
                corrected_by_user_id=admin.id,
                reason="Wrong seller",
            )

        reopen_daily_closing(db, closing_id=closing.id, user_id=admin.id, reason="Seller correction")
        corrected = correct_sale_seller(
            db,
            sale_id=sale.id,
            new_sold_by_user_id=new_seller.id,
            corrected_by_user_id=admin.id,
            reason="Wrong seller",
        )
        assert corrected.sold_by_user_id == new_seller.id


def test_seller_correction_route_returns_403_without_authorized_current_user():
    with SessionLocal() as db:
        original_seller = create_user(db, "Route Correction Original")
        new_seller = create_user(db, "Route Correction New")
        register = create_register(db, "Route Correction Register")
        shift = open_shift(
            db,
            seller_id=original_seller.id,
            cash_register_id=register.id,
            business_date=date.today(),
            starting_cash="0",
        )
        sale = create_sale_with_payment(
            db,
            seller_id=original_seller.id,
            shift_id=shift.id,
            payment_method="card",
            description="Route correction sale",
            quantity="1",
            unit_price="20",
            vat_percent="24",
        )
        sale_id = sale.id
        new_seller_id = new_seller.id

    response = client.post(
        f"/sales/{sale_id}/seller",
        data={"sold_by_user_id": new_seller_id, "reason": "Wrong seller"},
        follow_redirects=False,
    )
    assert response.status_code == 403


def test_sale_form_lists_only_sales_credit_eligible_users():
    with SessionLocal() as db:
        seller = create_user(db, "Eligible Form Seller")
        eligible_manager = create_user(
            db,
            "Eligible Form Manager",
            "manager",
            can_receive_sales_credit=True,
        )
        ineligible_manager = create_user(db, "Ineligible Form Manager", "manager")
        register = create_register(db, "Eligibility Form Register")
        db.add(Setting(key="sale_seller_selection_mode", value="selectable_active_seller"))
        open_shift(
            db,
            seller_id=seller.id,
            cash_register_id=register.id,
            business_date=date.today(),
            starting_cash="0",
        )

    response = client.get("/sales/new")
    assert response.status_code == 200
    assert "Eligible Form Seller" in response.text
    assert "Eligible Form Manager" in response.text
    assert "Ineligible Form Manager" not in response.text


def test_daily_closing_uses_selected_sale_seller_and_receipt_displays_it():
    with SessionLocal() as db:
        admin = create_user(db, "Attribution Admin", "admin")
        operator = create_user(db, "Attribution Operator", "manager")
        shift_owner = create_user(db, "Attribution Shift Owner")
        selected_seller = create_user(db, "Attribution Selected Seller")
        register = create_register(db, "Attribution Register")
        shift = open_shift(
            db,
            seller_id=shift_owner.id,
            cash_register_id=register.id,
            business_date=date.today(),
            starting_cash="0",
        )
        sale = create_sale_with_payment(
            db,
            seller_id=selected_seller.id,
            shift_id=shift.id,
            payment_method="cash",
            description="Receipt sale",
            quantity="1",
            unit_price="30",
            vat_percent="24",
            created_by_user_id=operator.id,
            seller_selection_mode="selectable_active_seller",
        )
        sale_id = sale.id
        close_shift(db, shift_id=shift.id, counted_cash="30")
        closing = create_daily_closing(db, business_date=date.today(), created_by_user_id=admin.id)
        snapshot = json.loads(
            db.query(DailyClosingSnapshot)
            .filter(DailyClosingSnapshot.daily_closing_id == closing.id)
            .one()
            .snapshot_json
        )
        assert snapshot["seller_totals"][0]["seller_id"] == selected_seller.id
        assert snapshot["seller_totals"][0]["seller_name"] == selected_seller.name

    response = client.get(f"/sales/{sale_id}/receipt")
    assert response.status_code == 200
    assert "Attribution Selected Seller" in response.text


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
    shifts_response = client.get("/shifts")
    shift_detail_response = client.get(f"/shifts/{shift_id}")
    assert shifts_response.status_code == 200
    assert shift_detail_response.status_code == 200
    assert "responsive-card-table" in shifts_response.text
    assert "data-label=" in shifts_response.text
    assert "responsive-card-table" in shift_detail_response.text
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

    sales_response = client.get("/sales")
    closings_response = client.get("/daily-closings")
    assert sales_response.status_code == 200
    assert "responsive-card-table" in sales_response.text
    assert "data-label=" in sales_response.text
    assert closings_response.status_code == 200
    assert "responsive-card-table" in closings_response.text
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
                refund_shift_id=shift.id,
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

        first = add_refund(db, sale_id=sale.id, refund_shift_id=shift.id, seller_id=seller.id, amount="25", payment_method="cash")
        assert first.vat_amount == Decimal("4.84")
        assert json.loads(first.vat_breakdown_json)["24"]["vat"] == "4.84"
        assert db.get(type(sale), sale.id).status == "partially_refunded"

        add_refund(db, sale_id=sale.id, refund_shift_id=shift.id, seller_id=seller.id, amount="25", payment_method="cash")
        assert db.get(type(sale), sale.id).status == "partially_refunded"
        add_refund(db, sale_id=sale.id, refund_shift_id=shift.id, seller_id=seller.id, amount="50", payment_method="cash")
        assert db.get(type(sale), sale.id).status == "refunded"
        with pytest.raises(ValueError, match="exceeds remaining"):
            add_refund(db, sale_id=sale.id, refund_shift_id=shift.id, seller_id=seller.id, amount="0.01", payment_method="cash")


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
            add_refund(db, sale_id=sale.id, refund_shift_id=shift.id, seller_id=seller.id, amount="5", payment_method="cash")


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
        refund = add_refund(db, sale_id=sale.id, refund_shift_id=shift.id, seller_id=seller.id, amount="1", payment_method="cash")
        close_shift(db, shift_id=shift.id, counted_cash="10")
        closing = create_daily_closing(db, business_date=date.today(), created_by_user_id=admin.id)

        assert db.query(AuditLog).filter(AuditLog.event_type == "shift.opened", AuditLog.entity_id == shift.id).count() == 1
        assert db.query(AuditLog).filter(AuditLog.event_type == "sale.created", AuditLog.entity_id == sale.id).count() == 1
        assert db.query(AuditLog).filter(AuditLog.event_type == "cash_movement.created", AuditLog.entity_id == movement.id).count() == 1
        assert db.query(AuditLog).filter(AuditLog.event_type == "sale.refunded", AuditLog.entity_id == refund.id).count() == 1
        assert db.query(AuditLog).filter(AuditLog.event_type == "daily_closing.closed", AuditLog.entity_id == closing.id).count() == 1


def test_refund_can_be_recorded_on_later_shift_and_later_business_date():
    with SessionLocal() as db:
        admin = create_user(db, "Later Refund Admin", "admin")
        original_seller = create_user(db, "Original Seller")
        refunding_seller = create_user(db, "Refunding Seller")
        register = create_register(db, "Later Refund Register")
        day2 = date.today()
        day1 = day2 - timedelta(days=1)
        sale_shift = open_shift(db, seller_id=original_seller.id, cash_register_id=register.id, business_date=day1, starting_cash="0")
        sale = create_sale_with_payment(
            db,
            seller_id=original_seller.id,
            shift_id=sale_shift.id,
            payment_method="cash",
            description="Day one sale",
            quantity="1",
            unit_price="100",
            vat_percent="24",
        )
        sale.sold_at = datetime.combine(day1, time.min, tzinfo=UTC)
        db.commit()
        close_shift(db, shift_id=sale_shift.id, counted_cash="100")
        day1_closing = create_daily_closing(db, business_date=day1, created_by_user_id=admin.id)
        day1_snapshot_before = db.query(DailyClosingSnapshot).filter(DailyClosingSnapshot.daily_closing_id == day1_closing.id).one().snapshot_json

        refund_shift = open_shift(db, seller_id=refunding_seller.id, cash_register_id=register.id, business_date=day2, starting_cash="0")
        refund = add_refund(
            db,
            sale_id=sale.id,
            refund_shift_id=refund_shift.id,
            seller_id=refunding_seller.id,
            amount="25",
            payment_method="cash",
            reason="Next day correction",
        )
        assert sale.shift_id == sale_shift.id
        assert refund.shift_id == refund_shift.id
        assert refund.seller_id == refunding_seller.id
        close_shift(db, shift_id=refund_shift.id, counted_cash="0")
        day2_closing = create_daily_closing(db, business_date=day2, created_by_user_id=admin.id)

        day1_snapshot_after = db.query(DailyClosingSnapshot).filter(DailyClosingSnapshot.daily_closing_id == day1_closing.id).one().snapshot_json
        day2_snapshot = json.loads(db.query(DailyClosingSnapshot).filter(DailyClosingSnapshot.daily_closing_id == day2_closing.id).one().snapshot_json)
        assert day1_snapshot_after == day1_snapshot_before
        assert day2_snapshot["gross_sales"] == "0.00"
        assert day2_snapshot["total_refunds"] == "25.00"
        assert day2_snapshot["net_sales"] == "-25.00"
        assert day2_snapshot["seller_totals"][0]["seller_id"] == refunding_seller.id
        assert day2_snapshot["seller_totals"][0]["refunds"] == "25.00"
        assert day2_snapshot["payment_totals"][0]["refunds"] == "25.00"
        assert day2_snapshot["payment_totals"][0]["net"] == "-25.00"


def test_refund_shift_validation_and_seller_ownership():
    with SessionLocal() as db:
        seller = create_user(db, "Refund Owner")
        other_seller = create_user(db, "Other Refund Seller")
        readonly = create_user(db, "Readonly Refund", "read_only")
        inactive = create_user(db, "Inactive Refund")
        inactive.is_active = False
        register = create_register(db, "Refund Owner Register")
        other_register = create_register(db, "Other Refund Register")
        shift = open_shift(db, seller_id=seller.id, cash_register_id=register.id, business_date=date.today(), starting_cash="0")
        sale = create_sale_with_payment(
            db,
            seller_id=seller.id,
            shift_id=shift.id,
            payment_method="cash",
            description="Refund ownership sale",
            quantity="1",
            unit_price="30",
            vat_percent="24",
        )
        other_shift = open_shift(db, seller_id=other_seller.id, cash_register_id=other_register.id, business_date=date.today(), starting_cash="0")
        close_shift(db, shift_id=shift.id, counted_cash="30")

        with pytest.raises(ValueError, match="refund shift seller"):
            add_refund(db, sale_id=sale.id, refund_shift_id=other_shift.id, seller_id=seller.id, amount="1", payment_method="cash")
        with pytest.raises(ValueError, match="role"):
            add_refund(db, sale_id=sale.id, refund_shift_id=other_shift.id, seller_id=readonly.id, amount="1", payment_method="cash")
        other_shift.seller_id = inactive.id
        db.commit()
        with pytest.raises(ValueError, match="Active user"):
            add_refund(db, sale_id=sale.id, refund_shift_id=other_shift.id, seller_id=inactive.id, amount="1", payment_method="cash")


def test_seller_reports_attribute_sales_and_refunds_to_actual_event_sellers():
    with SessionLocal() as db:
        seller1 = create_user(db, "Report Seller One")
        seller2 = create_user(db, "Report Seller Two")
        register = create_register(db, "Report Register")
        other_register = create_register(db, "Report Register Two")
        day2 = date.today()
        day1 = day2 - timedelta(days=1)
        shift1 = open_shift(db, seller_id=seller1.id, cash_register_id=register.id, business_date=day1, starting_cash="0")
        sale = create_sale_with_payment(
            db,
            seller_id=seller1.id,
            shift_id=shift1.id,
            payment_method="card",
            description="Seller report sale",
            quantity="1",
            unit_price="100",
            vat_percent="24",
        )
        sale.sold_at = datetime.combine(day1, time.min, tzinfo=UTC)
        db.commit()
        close_shift(db, shift_id=shift1.id, counted_cash="0")
        shift2 = open_shift(db, seller_id=seller2.id, cash_register_id=other_register.id, business_date=day2, starting_cash="0")
        add_refund(db, sale_id=sale.id, refund_shift_id=shift2.id, seller_id=seller2.id, amount="40", payment_method="card")

        seller1_report = seller_report(db, seller_id=seller1.id, start_date=day1, end_date=day2)
        seller2_report = seller_report(db, seller_id=seller2.id, start_date=day2, end_date=day2 + timedelta(days=1))
        assert seller1_report["gross_sales"] == Decimal("100.00")
        assert seller1_report["refunds"] == Decimal("0.00")
        assert seller1_report["net_sales"] == Decimal("100.00")
        assert seller2_report["gross_sales"] == Decimal("0.00")
        assert seller2_report["refunds"] == Decimal("40.00")
        assert seller2_report["net_sales"] == Decimal("-40.00")


def test_daily_closing_snapshot_versions_are_browsable():
    with SessionLocal() as db:
        admin = create_user(db, "Version Admin", "admin")
        seller = create_user(db, "Version Seller")
        register = create_register(db, "Version Register")
        shift1 = open_shift(db, seller_id=seller.id, cash_register_id=register.id, business_date=date.today(), starting_cash="0")
        create_sale_with_payment(db, seller_id=seller.id, shift_id=shift1.id, payment_method="card", description="V1", quantity="1", unit_price="10", vat_percent="24")
        close_shift(db, shift_id=shift1.id, counted_cash="0")
        closing = create_daily_closing(db, business_date=date.today(), created_by_user_id=admin.id)
        snapshot_v1 = db.query(DailyClosingSnapshot).filter(DailyClosingSnapshot.daily_closing_id == closing.id, DailyClosingSnapshot.version == 1).one().snapshot_json
        reopen_daily_closing(db, closing_id=closing.id, user_id=admin.id, reason="Version test")
        shift2 = open_shift(db, seller_id=seller.id, cash_register_id=register.id, business_date=date.today(), starting_cash="0")
        create_sale_with_payment(db, seller_id=seller.id, shift_id=shift2.id, payment_method="card", description="V2", quantity="1", unit_price="5", vat_percent="24")
        close_shift(db, shift_id=shift2.id, counted_cash="0")
        create_daily_closing(db, business_date=date.today(), created_by_user_id=admin.id)
        closing_id = closing.id

    response_v1 = client.get(f"/daily-closings/{closing_id}/snapshots/1")
    response_v2 = client.get(f"/daily-closings/{closing_id}/snapshots/2")
    response_missing = client.get(f"/daily-closings/{closing_id}/snapshots/999")
    assert response_v1.status_code == 200
    assert "10.00" in response_v1.text
    assert response_v2.status_code == 200
    assert "15.00" in response_v2.text
    assert response_missing.status_code == 404
    with SessionLocal() as db:
        assert db.query(DailyClosingSnapshot).filter(DailyClosingSnapshot.daily_closing_id == closing_id, DailyClosingSnapshot.version == 1).one().snapshot_json == snapshot_v1


def test_open_shift_partial_unique_index_migration_diagnostics(tmp_path):
    db_path = tmp_path / "migration.sqlite"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Base.metadata.create_all(bind=engine)
    diagnostics = ensure_sqlite_schema_compatibility(engine)
    with engine.connect() as connection:
        indexes = {row[1] for row in connection.execute(text("PRAGMA index_list(shifts)")).fetchall()}
    assert "ux_open_shift_seller" in indexes
    assert "ux_open_shift_register" in indexes
    assert diagnostics == []

    duplicate_path = tmp_path / "migration-duplicates.sqlite"
    duplicate_engine = create_engine(f"sqlite:///{duplicate_path}", future=True)
    Base.metadata.create_all(bind=duplicate_engine)
    with duplicate_engine.begin() as connection:
        connection.execute(text("INSERT INTO roles (id, code, name, created_at) VALUES (1, 'seller', 'Seller', CURRENT_TIMESTAMP)"))
        connection.execute(text("INSERT INTO users (id, name, is_active, role_id, created_at, updated_at) VALUES (1, 'One', 1, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"))
        connection.execute(text("INSERT INTO users (id, name, is_active, role_id, created_at, updated_at) VALUES (2, 'Two', 1, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"))
        connection.execute(text("INSERT INTO cash_registers (id, name, is_active, created_at, updated_at) VALUES (1, 'R1', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"))
        connection.execute(text("INSERT INTO cash_registers (id, name, is_active, created_at, updated_at) VALUES (2, 'R2', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"))
        connection.execute(text("INSERT INTO shifts (cash_register_id, seller_id, business_date, starting_cash, status, opened_at) VALUES (1, 1, '2026-07-11', 0, 'open', CURRENT_TIMESTAMP)"))
        connection.execute(text("INSERT INTO shifts (cash_register_id, seller_id, business_date, starting_cash, status, opened_at) VALUES (2, 1, '2026-07-11', 0, 'open', CURRENT_TIMESTAMP)"))
        connection.execute(text("INSERT INTO shifts (cash_register_id, seller_id, business_date, starting_cash, status, opened_at) VALUES (1, 2, '2026-07-11', 0, 'open', CURRENT_TIMESTAMP)"))
    diagnostics = ensure_sqlite_schema_compatibility(duplicate_engine)
    with duplicate_engine.connect() as connection:
        indexes = {row[1] for row in connection.execute(text("PRAGMA index_list(shifts)")).fetchall()}
    assert "ux_open_shift_seller" not in indexes
    assert "ux_open_shift_register" not in indexes
    assert any("ux_open_shift_seller" in diagnostic for diagnostic in diagnostics)
    assert any("ux_open_shift_register" in diagnostic for diagnostic in diagnostics)
