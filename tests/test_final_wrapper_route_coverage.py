import asyncio
import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import app.auth_middleware as auth_middleware
import app.error_handlers as error_handlers
import app.routes.delivery_notes as delivery_notes_route
import app.routes.jobs as jobs_route
import app.routes.product_import as product_import_route
import app.routes.quotes as quotes_route
import app.routes.seller_reports as seller_reports_route
import app.services.backup_service as backup_service
from app.database import SessionLocal
from app.models import AuditLog, Job, JobItem, Product, Role, User
from app.services.activity_feed_service import format_activity_event
from app.services.auth_service import hash_password
from app.services.i18n_service import translate_status
from app.services.sales_service import ensure_default_roles


class UploadStub:
    def __init__(self, content: bytes):
        self.content = content

    async def read(self):
        return self.content


def _request(user=None, path="/work-orders"):
    return SimpleNamespace(cookies={}, state=SimpleNamespace(current_user=user), url=SimpleNamespace(path=path))


def _template_response(_template, context):
    return SimpleNamespace(template=_template, context=context)


def _admin(db):
    ensure_default_roles(db)
    role = db.query(Role).filter(Role.code == "admin").one()
    user = User(
        name="Admin",
        login_name="admin",
        password_hash=hash_password("secret123"),
        role=role,
        is_active=True,
        can_receive_sales_credit=True,
    )
    db.add(user)
    db.flush()
    return user


def test_delivery_note_and_quote_wrappers_delegate(monkeypatch):
    calls = []

    def record(name):
        def inner(*args, **kwargs):
            calls.append((name, args, kwargs))
            return name

        return inner

    monkeypatch.setattr(delivery_notes_route.jobs, "update_job", record("update_delivery"))
    monkeypatch.setattr(delivery_notes_route.jobs, "add_job_item", record("add_delivery"))
    monkeypatch.setattr(delivery_notes_route.jobs, "delete_job_item", record("delete_delivery_item"))
    monkeypatch.setattr(delivery_notes_route.jobs, "update_job_status", record("status_delivery"))
    monkeypatch.setattr(delivery_notes_route.jobs, "delete_job", record("delete_delivery"))

    request = _request(path="/delivery-notes")
    assert delivery_notes_route.update_delivery_note(request, 1, "T", "", "", "", "", "normal", None, "", db=object()) == "update_delivery"
    assert delivery_notes_route.add_delivery_note_item(request, 1, "", "Line", "1", "1", "24", db=object()) == "add_delivery"
    assert delivery_notes_route.delete_delivery_note_item(request, 1, 2, db=object()) == "delete_delivery_item"
    assert delivery_notes_route.update_delivery_note_status(request, 1, 2, db=object()) == "status_delivery"
    assert delivery_notes_route.delete_delivery_note(request, 1, db=object()) == "delete_delivery"

    monkeypatch.setattr(quotes_route.jobs, "update_job", record("update_quote"))
    monkeypatch.setattr(quotes_route.jobs, "add_job_item", record("add_quote"))
    monkeypatch.setattr(quotes_route.jobs, "delete_job_item", record("delete_quote_item"))
    monkeypatch.setattr(quotes_route.jobs, "update_job_status", record("status_quote"))

    request = _request(path="/quotes")
    assert quotes_route.update_quote(request, 1, "T", "", "", "", "", "normal", None, "", db=object()) == "update_quote"
    assert quotes_route.add_quote_item(request, 1, "", "Line", "1", "1", "24", db=object()) == "add_quote"
    assert quotes_route.delete_quote_item(request, 1, 2, db=object()) == "delete_quote_item"
    assert quotes_route.update_quote_status(request, 1, 2, db=object()) == "status_quote"
    assert [call[0] for call in calls]


def test_product_import_delimiter_and_header_errors():
    with pytest.raises(HTTPException) as delimiter_error:
        asyncio.run(product_import_route.import_products_csv(UploadStub(b"name\nOnly one line"), db=object()))
    assert delimiter_error.value.status_code == 400

    with pytest.raises(HTTPException) as header_error:
        asyncio.run(product_import_route.import_products_csv(UploadStub(b""), db=object()))
    assert header_error.value.status_code == 400


def test_seller_report_period_branches(monkeypatch):
    monkeypatch.setattr(seller_reports_route.templates, "TemplateResponse", _template_response)
    with SessionLocal() as db:
        weekly = seller_reports_route.seller_reports(_request(), seller_id=None, period="weekly", db=db)
        monthly = seller_reports_route.seller_reports(_request(), seller_id=None, period="monthly", db=db)
        assert weekly.context["period"] == "weekly"
        assert monthly.context["period"] == "monthly"


def test_job_conversion_and_inventory_actor_edges():
    with SessionLocal() as db:
        with pytest.raises(HTTPException) as no_actor:
            jobs_route.inventory_actor_id_for_request(_request(), db)
        assert no_actor.value.status_code == 400

        admin = _admin(db)
        product = Product(name="Part", is_stock_item=True, is_active=True, unit_price=Decimal("1.00"), vat_percent=Decimal("24"))
        source = Job(document_type="quote", title="Source")
        source.items.append(JobItem(product=product, description="Part", quantity=Decimal("1"), unit_price=Decimal("1"), vat_percent=Decimal("24"), line_total=Decimal("1")))
        db.add_all([product, source])
        db.commit()

        assert jobs_route.inventory_actor_id_for_request(_request(admin), db) == admin.id
        with pytest.raises(HTTPException) as invalid_target:
            jobs_route.clone_job_as_type(db, source_job=source, target_type="unknown", request=_request(admin))
        assert invalid_target.value.status_code == 400

        with pytest.raises(HTTPException) as missing_request:
            jobs_route.clone_job_as_type(db, source_job=source, target_type="delivery_note", request=None)
        assert missing_request.value.status_code == 400

        with pytest.raises(HTTPException) as invalid_conversion:
            jobs_route.convert_job_document(_request(admin), source.id, "unknown", db=db)
        assert invalid_conversion.value.status_code == 400


def test_remaining_small_service_edges(monkeypatch, tmp_path):
    json_request = SimpleNamespace(headers={"accept": "application/json"}, url=SimpleNamespace(path="/private"))
    html_request = SimpleNamespace(headers={}, url=SimpleNamespace(path="/private"))
    assert auth_middleware._unauthenticated_response(json_request).status_code == 401
    assert auth_middleware._forbidden_response(json_request, "No").status_code == 403
    monkeypatch.setattr(auth_middleware.templates, "TemplateResponse", lambda *args, **kwargs: SimpleNamespace(args=args, kwargs=kwargs))
    assert auth_middleware._forbidden_response(html_request, "No").kwargs["status_code"] == 403

    assert error_handlers._prefers_json(SimpleNamespace(headers={})) is False
    assert error_handlers._status_phrase(799) == "Application error"
    assert translate_status(None, "en") == "Received"
    assert format_activity_event(AuditLog(event_type="daily_closing.closed", entity_type="daily_closing", entity_id=7, description="Closed")).href == "/daily-closings/7"

    class BadIntegrityConnection:
        def execute(self, _sql):
            return SimpleNamespace(fetchone=lambda: ("bad",))

        def close(self):
            pass

    monkeypatch.setattr(backup_service.sqlite3, "connect", lambda _path: BadIntegrityConnection())
    with pytest.raises(RuntimeError, match="integrity"):
        backup_service.validate_sqlite_database(tmp_path / "invalid.sqlite")
    monkeypatch.undo()

    backup_path = tmp_path / "ops_tracker_2026_manual.sqlite"
    with sqlite3.connect(backup_path) as conn:
        conn.execute("create table example (id integer primary key)")
    monkeypatch.setattr(backup_service, "backup_dir", lambda: tmp_path)
    monkeypatch.setattr(backup_service.time, "sleep", lambda _seconds: None)
    original_unlink = Path.unlink
    calls = {"count": 0}

    def always_locked(self, *args, **kwargs):
        calls["count"] += 1
        raise PermissionError("locked")

    monkeypatch.setattr(Path, "unlink", always_locked)
    with pytest.raises(PermissionError):
        backup_service.cleanup_retention(keep=0)
    assert calls["count"] == 5
    monkeypatch.setattr(Path, "unlink", original_unlink)
