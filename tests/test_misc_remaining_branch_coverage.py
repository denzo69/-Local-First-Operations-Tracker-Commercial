import asyncio
from io import BytesIO
from datetime import date
from decimal import Decimal

from fastapi import UploadFile
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import Job, Product, Role, Sale, Setting, Supplier, User
from app.routes.auth import _safe_next
from app.routes.products import import_products
from app.routes.sales import (
    _discount_amount_from_percent,
    _format_decimal,
    _format_receipt_datetime,
    _format_vat_rate,
    _form_discount_percent,
    _optional_int,
    _parse_payments,
    _parse_sale_lines,
)
from app.services import backup_scheduler_service, backup_service
from app.services.auth_service import ensure_first_admin_role, get_session_user, hash_password
from app.services.money_service import parse_decimal, vat_included_breakdown
from app.services.receipt_number_service import allocate_receipt_number, allocate_sale_document_number
from app.services.sales_service import ensure_default_roles
from app.services.settings_service import get_current_language, set_app_settings
from app.services.supplier_service import resolve_goods_receipt_supplier


def _create_user(name: str, role_code: str = "admin", password: str = "secret123") -> User:
    with SessionLocal() as db:
        ensure_default_roles(db)
        role = db.query(Role).filter(Role.code == role_code).one()
        user = User(
            name=name,
            login_name=name.lower().replace(" ", "."),
            password_hash=hash_password(password),
            role=role,
            is_active=True,
            can_receive_sales_credit=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user


def test_auth_error_and_setup_guard_branches_render_expected_responses():
    _create_user("Configured Admin")

    with TestClient(app) as client:
        bad_login = client.post(
            "/login",
            data={"login_name": "configured.admin", "password": "wrong", "next_url": "https://evil.example"},
        )
        setup_redirect = client.get("/setup", follow_redirects=False)
        setup_conflict = client.post(
            "/setup",
            data={"name": "Other", "login_name": "other", "password": "secret123"},
            follow_redirects=False,
        )

    assert bad_login.status_code == 401
    assert 'name="next_url" value="/"' in bad_login.text
    assert setup_redirect.status_code == 303
    assert setup_redirect.headers["location"] == "/login"
    assert setup_conflict.status_code == 409
    assert _safe_next("//external") == "/"
    assert _safe_next("relative") == "/"


def test_first_admin_setup_validation_and_role_creation_branch():
    with SessionLocal() as db:
        db.query(Role).delete()
        db.commit()
        role = ensure_first_admin_role(db)
        role_code = role.code
        db.commit()

    assert role_code == "admin"

    with TestClient(app) as client:
        short_password = client.post(
            "/setup",
            data={"name": "Short Password", "login_name": "short", "password": "short"},
        )
        blank_name = client.post(
            "/setup",
            data={"name": "   ", "login_name": "blank", "password": "secret123"},
    )

    assert short_password.status_code == 400
    assert blank_name.status_code == 400
    assert "Name and login name are required." in blank_name.text


def test_seller_reports_weekly_monthly_and_empty_user_branches():
    with TestClient(app) as client:
        empty_report = client.get("/seller-reports")
    assert empty_report.status_code == 200

    _create_user("Report Seller", "seller")
    with TestClient(app) as client:
        weekly = client.get("/seller-reports?period=weekly")
        monthly = client.get("/seller-reports?period=monthly")
        fallback = client.get("/seller-reports?period=quarterly")

    assert weekly.status_code == 200
    assert monthly.status_code == 200
    assert fallback.status_code == 200
    assert "Seller reports" in weekly.text
    assert "Seller reports" in monthly.text
    assert "Seller reports" in fallback.text


def test_receipt_number_allocation_skips_existing_job_and_sale_numbers():
    with SessionLocal() as db:
        db.add_all(
            [
                Setting(key="next_receipt_sequence", value="1"),
                Setting(key="receipt_prefix", value="WO-"),
                Setting(key="receipt_padding", value="3"),
                Setting(key="next_sale_document_sequence", value="1"),
                Setting(key="sale_document_prefix", value="SALE-"),
                Setting(key="sale_document_padding", value="3"),
            ]
        )
        db.add(Job(title="Existing Job", receipt_number="WO-2026-001"))
        db.add(
            Sale(
                document_number="SALE-2026-001",
                payment_method="cash",
                total=Decimal("0.00"),
                subtotal=Decimal("0.00"),
                vat_total=Decimal("0.00"),
            )
        )
        db.commit()

        job_number = allocate_receipt_number(db, date(2026, 7, 1))
        sale_number = allocate_sale_document_number(db, date(2026, 7, 1))

    assert job_number == "WO-2026-002"
    assert sale_number == "SALE-2026-002"


def test_sales_form_parsing_helper_edges():
    assert _optional_int(None) is None
    assert _optional_int("") is None
    assert _optional_int("42") == 42
    assert _form_discount_percent("", Decimal("7.5")) == Decimal("7.5")
    assert _form_discount_percent("0", Decimal("7.5")) == Decimal("7.5")
    assert _form_discount_percent("12,5", Decimal("0")) == Decimal("12.5")
    assert _discount_amount_from_percent(quantity="2", unit_price="10", discount_percent=Decimal("10")) == Decimal("2.00")
    assert _discount_amount_from_percent(quantity="0", unit_price="10", discount_percent=Decimal("10")) == Decimal("0.00")
    assert _format_decimal("1.2300") == "1.23"
    assert _format_vat_rate("25.5", "fi") == "25,5 %"
    assert _format_receipt_datetime(None, "fi") == ""
    with pytest_raises_value("Discount percent must be a valid number."):
        _form_discount_percent("not-number", Decimal("0"))
    with pytest_raises_value("Discount percent must be between 0 and 100."):
        _form_discount_percent("101", Decimal("0"))
    with pytest_raises_value("Quantity and unit price must be valid numbers."):
        _discount_amount_from_percent(quantity="bad", unit_price="10", discount_percent=Decimal("10"))

    class FakeForm:
        def __init__(self, values):
            self.values = values

        def getlist(self, key):
            value = self.values.get(key, [])
            return value if isinstance(value, list) else [value]

        def get(self, key, default=None):
            return self.values.get(key, default)

    parsed_lines = _parse_sale_lines(
        FakeForm(
            {
                "description": ["", "Manual"],
                "quantity": ["1"],
                "unit_price": ["2"],
                "vat_percent": [],
                "discount_percent": [],
                "product_id": ["", ""],
            }
        )
    )
    assert len(parsed_lines) == 1
    assert parsed_lines[0].vat_percent == "24"
    payments, send_to_invoice = _parse_payments(
        FakeForm(
            {
                "payment_method": ["", "cash", "invoice"],
                "payment_amount": ["", "5"],
                "send_to_invoice": "true",
            }
        )
    )
    assert [payment.payment_method for payment in payments] == ["cash"]
    assert send_to_invoice is True


def test_legacy_products_import_function_and_decimal_setting_edges():
    upload = UploadFile(file=BytesIO(b"name,unit_price,vat_percent,unit\nLegacy import,3.50,10,h\n"), filename="legacy.csv")
    with SessionLocal() as db:
        response = asyncio.run(import_products(upload, db))
        product = db.query(Product).filter(Product.name == "Legacy import").one()
        product_unit_price = product.unit_price

        set_app_settings(db, {"language": "xx"})
        assert get_current_language(db) == "en"

    assert response.status_code == 303
    assert response.headers["location"] == "/products?imported=1"
    assert product_unit_price == Decimal("3.50")
    assert parse_decimal(None, "7") == Decimal("7")
    assert parse_decimal("", "8") == Decimal("8")
    assert vat_included_breakdown(Decimal("0.00"), Decimal("24")) == (Decimal("0.00"), Decimal("0.00"))
    assert vat_included_breakdown(Decimal("10.00"), Decimal("0")) == (Decimal("10.00"), Decimal("0.00"))


def test_supplier_resolution_manual_reactivation_and_invalid_selected_supplier():
    with SessionLocal() as db:
        inactive = Supplier(name="Sleeping Supplier", is_active=False)
        db.add(inactive)
        db.commit()
        inactive_id = inactive.id

        with_selected_missing = None
        try:
            resolve_goods_receipt_supplier(db, supplier_id=inactive_id, supplier_name=None)
        except ValueError as exc:
            with_selected_missing = str(exc)

        reactivated = resolve_goods_receipt_supplier(db, supplier_id=None, supplier_name="Sleeping Supplier")
        created = resolve_goods_receipt_supplier(db, supplier_id="", supplier_name="New Manual Supplier")

    assert with_selected_missing == "Active supplier is required."
    assert reactivated.is_active is True
    assert created.name == "New Manual Supplier"


def test_backup_service_restore_missing_relative_path_and_scheduler_thread_edges(monkeypatch, tmp_path):
    original_settings = backup_service.settings
    monkeypatch.setattr(
        backup_service,
        "settings",
        type("Settings", (), {"database_url": "sqlite:///./relative.sqlite", "backup_dir": str(tmp_path)})(),
    )
    assert backup_service.database_path().as_posix() == "relative.sqlite"
    with pytest_raises_runtime("Selected backup was not found."):
        backup_service.restore_backup("missing.sqlite")
    monkeypatch.setattr(backup_service, "settings", original_settings)

    class FakeThread:
        def __init__(self, *, target, name, daemon):
            self.target = target
            self.name = name
            self.daemon = daemon
            self.started = False
            self.joined = False

        def start(self):
            self.started = True

        def is_alive(self):
            return self.started and not self.joined

        def join(self, timeout=None):
            self.joined = True

    monkeypatch.setattr(backup_scheduler_service.threading, "Thread", FakeThread)
    scheduler = backup_scheduler_service.BackupScheduler(interval_minutes=1, retention_count=1)
    scheduler.start()
    assert scheduler.running is True
    scheduler.start()
    scheduler.stop()
    assert scheduler.running is False

    loop_scheduler = backup_scheduler_service.BackupScheduler(interval_minutes=1, retention_count=1)
    wait_results = iter([False, True])
    loop_calls = {"count": 0}
    monkeypatch.setattr(loop_scheduler._stop_event, "wait", lambda _seconds: next(wait_results))
    monkeypatch.setattr(loop_scheduler, "run_once", lambda: loop_calls.__setitem__("count", loop_calls["count"] + 1))
    loop_scheduler._run_loop()
    assert loop_calls["count"] == 1


def test_auth_session_value_error_branch():
    with SessionLocal() as db:
        assert get_session_user(db, "not-int:1234567890:bad") is None


class pytest_raises_runtime:
    def __init__(self, message: str):
        self.message = message
        self.exception = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, _traceback):
        self.exception = exc
        assert exc_type is RuntimeError
        assert self.message in str(exc)
        return True


class pytest_raises_value:
    def __init__(self, message: str):
        self.message = message

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, _traceback):
        assert exc_type is ValueError
        assert self.message in str(exc)
        return True
