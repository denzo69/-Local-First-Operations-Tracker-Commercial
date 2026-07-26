from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import CashRegister, Role, Sale, User
from app.services.sales_service import create_daily_closing, create_sale_with_payment, ensure_default_roles, open_shift


def _manager_user(name: str = "Route Manager") -> User:
    with SessionLocal() as db:
        ensure_default_roles(db)
        role = db.query(Role).filter(Role.code == "manager").one()
        user = User(name=name, role=role, is_active=True, can_receive_sales_credit=True)
        db.add(user)
        db.commit()
        db.refresh(user)
        return user


def _cash_register() -> CashRegister:
    with SessionLocal() as db:
        register = CashRegister(name="Route Register", is_active=True)
        db.add(register)
        db.commit()
        db.refresh(register)
        return register


def _sale() -> Sale:
    with SessionLocal() as db:
        seller = _manager_user("Sale Route Seller")
        sale = create_sale_with_payment(
            db,
            seller_id=seller.id,
            seller_mode="selected",
            payment_method="cash",
            description="Route sale",
            quantity="1",
            unit_price="10",
            vat_percent="24",
            created_by_user_id=seller.id,
        )
        sale_id = sale.id
    with SessionLocal() as db:
        return db.get(Sale, sale_id)


def test_daily_closing_routes_cover_success_missing_and_validation_branches():
    with TestClient(app) as client:
        no_user_close = client.post(
            "/daily-closings",
            data={"business_date": date.today().isoformat()},
        )

    manager = _manager_user()
    with TestClient(app) as client:
        invalid_date = client.post(
            "/daily-closings",
            data={"business_date": "not-a-date", "created_by_user_id": manager.id},
        )
        close_response = client.post(
            "/daily-closings",
            data={"business_date": date.today().isoformat(), "created_by_user_id": manager.id},
            follow_redirects=False,
        )
        closing_id = close_response.headers["location"].rstrip("/").rsplit("/", 1)[-1]
        detail_response = client.get(f"/daily-closings/{closing_id}")
        missing_detail = client.get("/daily-closings/999999")
        snapshot_response = client.get(f"/daily-closings/{closing_id}/snapshots/1")
        missing_snapshot = client.get(f"/daily-closings/{closing_id}/snapshots/999")
        missing_snapshot_closing = client.get("/daily-closings/999999/snapshots/1")
        missing_reopen = client.post(
            "/daily-closings/999999/reopen",
            data={"user_id": manager.id, "reason": "Missing closing"},
        )

    assert no_user_close.status_code == 400
    assert invalid_date.status_code == 400
    assert close_response.status_code == 303
    assert detail_response.status_code == 200
    assert missing_detail.status_code == 404
    assert snapshot_response.status_code == 200
    assert missing_snapshot.status_code == 404
    assert missing_snapshot_closing.status_code == 404
    assert missing_reopen.status_code == 400


def test_daily_closing_detail_reports_conflict_when_snapshot_is_missing():
    manager = _manager_user()
    with SessionLocal() as db:
        closing = create_daily_closing(db, business_date=date.today(), created_by_user_id=manager.id)
        for snapshot in list(closing.snapshots):
            db.delete(snapshot)
        db.commit()
        closing_id = closing.id

    with TestClient(app) as client:
        response = client.get(f"/daily-closings/{closing_id}")

    assert response.status_code == 409


def test_shift_routes_cover_success_paths_through_http():
    manager = _manager_user("Shift Route Seller")
    register = _cash_register()

    with TestClient(app) as client:
        create_response = client.post(
            "/shifts",
            data={
                "seller_id": manager.id,
                "cash_register_id": register.id,
                "business_date": date.today().isoformat(),
                "starting_cash": "20",
                "notes": "Route shift",
            },
            follow_redirects=False,
        )
        shift_id = create_response.headers["location"].rstrip("/").rsplit("/", 1)[-1]
        detail_response = client.get(f"/shifts/{shift_id}")
        movement_response = client.post(
            f"/shifts/{shift_id}/cash-movements",
            data={
                "seller_id": manager.id,
                "movement_type": "cash_in",
                "amount": "5",
                "reason": "Till correction",
            },
            follow_redirects=False,
        )
        close_response = client.post(
            f"/shifts/{shift_id}/close",
            data={"counted_cash": "25", "notes": "Closed by route"},
            follow_redirects=False,
        )

    assert create_response.status_code == 303
    assert detail_response.status_code == 200
    assert movement_response.status_code == 303
    assert close_response.status_code == 303


def test_sales_invoice_refund_and_seller_routes_return_expected_errors():
    sale = _sale()
    with TestClient(app) as client:
        seller_correction_unauthorized = client.post(
            f"/sales/{sale.id}/seller",
            data={"sold_by_user_id": sale.sold_by_user_id or sale.seller_id, "reason": "No login"},
        )
        missing_refund_sale = client.post(
            "/sales/999999/refunds",
            data={"refund_shift_id": "", "amount": "1", "payment_method": "cash", "reason": "Missing"},
        )
        bad_refund_shift = client.post(
            f"/sales/{sale.id}/refunds",
            data={"refund_shift_id": "999999", "amount": "1", "payment_method": "cash", "reason": "Bad shift"},
        )
        bad_refund_amount = client.post(
            f"/sales/{sale.id}/refunds",
            data={"refund_shift_id": "", "amount": "-1", "payment_method": "cash", "reason": "Bad amount"},
        )
        missing_transfer = client.post(
            "/sales/999999/invoice-transfer",
            data={
                "external_invoice_service": "Books",
                "external_invoice_number": "INV-404",
                "invoice_date": date.today().isoformat(),
                "due_date": date.today().isoformat(),
            },
        )
        missing_paid = client.post(
            "/sales/999999/invoice-paid",
            data={"payment_date": date.today().isoformat(), "received_amount": "10"},
        )
        missing_unpaid = client.post("/sales/999999/invoice-unpaid", data={"checked_date": date.today().isoformat()})
        missing_reminder = client.post(
            "/sales/999999/invoice-reminder",
            data={"reminder_date": date.today().isoformat()},
        )
        bad_quick_discount = client.post(
            "/sales/quick",
            data={
                "seller_mode": "none",
                "description": ["Manual sale"],
                "quantity": ["1"],
                "unit_price": ["10"],
                "vat_percent": ["24"],
                "discount_percent": ["150"],
                "payment_method": ["cash"],
                "payment_amount": [""],
            },
        )

    assert seller_correction_unauthorized.status_code == 403
    assert missing_refund_sale.status_code == 404
    assert bad_refund_shift.status_code == 400
    assert bad_refund_amount.status_code == 400
    assert missing_transfer.status_code == 400
    assert missing_paid.status_code == 400
    assert missing_unpaid.status_code == 400
    assert missing_reminder.status_code == 400
    assert bad_quick_discount.status_code == 400


def test_refund_route_blank_amount_uses_remaining_refundable_total():
    sale = _sale()

    with TestClient(app) as client:
        response = client.post(
            f"/sales/{sale.id}/refunds",
            data={"refund_shift_id": "", "amount": "", "payment_method": "cash", "reason": "Full customer refund"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    with SessionLocal() as db:
        refreshed = db.get(Sale, sale.id)
        assert refreshed is not None
        assert refreshed.status == "refunded"
        assert len(refreshed.refunds) == 1
        assert refreshed.refunds[0].amount == Decimal("10.00")


def test_legacy_sale_route_handles_optional_ids_and_discount_validation():
    manager = _manager_user("Legacy Route Seller")
    register = _cash_register()
    with SessionLocal() as db:
        shift = open_shift(
            db,
            seller_id=manager.id,
            cash_register_id=register.id,
            business_date=date.today(),
            starting_cash=Decimal("0"),
        )
        shift_id = shift.id

    with TestClient(app) as client:
        blank_optional_ids = client.post(
            "/sales",
            data={
                "shift_id": "",
                "cash_register_id": "",
                "seller_id": "",
                "seller_mode": "none",
                "payment_method": "cash",
                "description": "Legacy route sale",
                "quantity": "1",
                "unit_price": "10",
                "vat_percent": "24",
                "discount_amount": "0",
            },
            follow_redirects=False,
        )
        invalid_optional_id = client.post(
            "/sales",
            data={
                "shift_id": "not-an-id",
                "payment_method": "cash",
                "description": "Bad optional id",
                "quantity": "1",
                "unit_price": "10",
                "vat_percent": "24",
            },
        )
        valid_shift_sale = client.post(
            "/sales",
            data={
                "shift_id": str(shift_id),
                "cash_register_id": "",
                "seller_id": str(manager.id),
                "seller_mode": "selected",
                "payment_method": "card",
                "description": "Shift sale",
                "quantity": "1",
                "unit_price": "10",
                "vat_percent": "24",
                "discount_amount": "1",
            },
            follow_redirects=False,
        )

    assert blank_optional_ids.status_code == 303
    assert invalid_optional_id.status_code == 400
    assert valid_shift_sale.status_code == 303
