from decimal import Decimal

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import Customer, Job, JobItem, JobStatus, Product, Role, User
from app.routes import jobs, products
from app.routes.product_import import _first_value, _upsert_product
from app.services.sales_service import ensure_default_roles


def _customer() -> Customer:
    with SessionLocal() as db:
        customer = Customer(name="Branch Customer")
        db.add(customer)
        db.commit()
        db.refresh(customer)
        return customer


def _job() -> Job:
    with SessionLocal() as db:
        status = jobs.get_received_status(db)
        job = Job(title="Branch Job", status=status, document_type="work_order")
        db.add(job)
        db.commit()
        db.refresh(job)
        return job


def _product() -> Product:
    with SessionLocal() as db:
        product = Product(
            name="Branch Product",
            unit_price=Decimal("9.50"),
            vat_percent=Decimal("14"),
            unit="pcs",
            is_stock_item=False,
            is_active=True,
        )
        db.add(product)
        db.commit()
        db.refresh(product)
        return product


def test_job_helper_functions_cover_default_and_invalid_branches():
    assert jobs.active_page_for("delivery_note") == "delivery_notes"
    assert jobs.active_page_for("unknown") == "jobs"
    assert jobs.document_label_key("quote") == "quote"
    assert jobs.document_label_key("unknown") == "work_order"
    assert jobs.optional_form_int(None, "Field") is None
    assert jobs.optional_form_int("", "Field") is None
    assert jobs.optional_form_int("7", "Field") == 7
    with pytest.raises(HTTPException) as exc_info:
        jobs.optional_form_int("bad", "Field")
    assert exc_info.value.status_code == 400


def test_job_create_update_item_status_delete_and_conversion_error_branches():
    customer = _customer()
    job = _job()
    product = _product()

    with TestClient(app) as client:
        invalid_view = client.get("/jobs?view=does-not-exist")
        blank_create = client.post("/jobs", data={"title": " "})
        bad_customer_create = client.post("/jobs", data={"title": "Bad customer", "customer_id": "999999"})
        bad_customer_number = client.post("/jobs", data={"title": "Bad number", "customer_id": "not-number"})
        bad_date_create = client.post(
            "/jobs",
            data={"title": "Bad date", "arrival_date": "not-a-date"},
        )
        bad_status_create = client.post("/jobs", data={"title": "Bad status", "status_id": "999999"})
        create_response = client.post(
            "/jobs",
            data={"title": "Created Branch Job", "customer_id": str(customer.id)},
            follow_redirects=False,
        )
        created_job_id = create_response.headers["location"].rstrip("/").rsplit("/", 1)[-1]
        edit_missing = client.get("/jobs/999999/edit")
        update_missing = client.post("/jobs/999999", data={"title": "Missing"})
        update_blank = client.post(f"/jobs/{created_job_id}", data={"title": " "})
        update_bad_customer = client.post(
            f"/jobs/{created_job_id}",
            data={"title": "Bad customer", "customer_id": "999999"},
        )
        update_bad_status = client.post(
            f"/jobs/{created_job_id}",
            data={"title": "Bad status", "status_id": "999999"},
        )
        update_bad_date = client.post(
            f"/jobs/{created_job_id}",
            data={"title": "Bad date", "arrival_date": "bad-date"},
        )
        update_ok = client.post(
            f"/jobs/{created_job_id}",
            data={"title": "Updated Branch Job", "priority": ""},
            follow_redirects=False,
        )
        missing_item_job = client.post("/jobs/999999/items", data={"description": "Item"})
        missing_item_product = client.post(
            f"/jobs/{created_job_id}/items",
            data={"product_id": "999999", "quantity": "1"},
        )
        missing_description = client.post(
            f"/jobs/{created_job_id}/items",
            data={"quantity": "1", "unit_price": "1"},
        )
        add_product_item = client.post(
            f"/jobs/{created_job_id}/items",
            data={"product_id": str(product.id), "quantity": "1"},
            follow_redirects=False,
        )
        missing_item_delete = client.post(f"/jobs/{created_job_id}/items/999999/delete")
        missing_status_job = client.post("/jobs/999999/status", data={"status_id": "1"})
        bad_status_update = client.post(f"/jobs/{created_job_id}/status", data={"status_id": "999999"})
        missing_delete = client.post("/jobs/999999/delete")
        invalid_conversion = client.post(f"/jobs/{created_job_id}/convert/not-a-target")
        missing_conversion = client.post("/jobs/999999/convert/sale")
        delete_ok = client.post(f"/jobs/{created_job_id}/delete", follow_redirects=False)

    assert invalid_view.status_code == 200
    assert blank_create.status_code == 400
    assert bad_customer_create.status_code == 400
    assert bad_customer_number.status_code == 400
    assert bad_date_create.status_code == 400
    assert bad_status_create.status_code == 400
    assert create_response.status_code == 303
    assert edit_missing.status_code == 404
    assert update_missing.status_code == 404
    assert update_blank.status_code == 400
    assert update_bad_customer.status_code == 400
    assert update_bad_status.status_code == 400
    assert update_bad_date.status_code == 400
    assert update_ok.status_code == 303
    assert missing_item_job.status_code == 404
    assert missing_item_product.status_code == 400
    assert missing_description.status_code == 400
    assert add_product_item.status_code == 303
    assert missing_item_delete.status_code == 404
    assert missing_status_job.status_code == 404
    assert bad_status_update.status_code == 400
    assert missing_delete.status_code == 404
    assert invalid_conversion.status_code == 400
    assert missing_conversion.status_code == 404
    assert delete_ok.status_code == 303


def test_job_status_success_and_clone_idempotency_helpers():
    with SessionLocal() as db:
        status = JobStatus(name="Done", sort_order=10, is_active=True)
        source = Job(title="Quote source", document_type="quote")
        item = JobItem(job=source, description="Quoted work", quantity=Decimal("2"), unit_price=Decimal("5"), vat_percent=Decimal("24"), line_total=Decimal("10"))
        db.add_all([status, source, item])
        db.commit()
        source_id = source.id
        status_id = status.id

    with TestClient(app) as client:
        status_response = client.post(f"/quotes/{source_id}/status", data={"status_id": status_id}, follow_redirects=False)
        first_convert = client.post(f"/quotes/{source_id}/convert/work_order", follow_redirects=False)
        second_convert = client.post(f"/quotes/{source_id}/convert/work_order", follow_redirects=False)

    assert status_response.status_code == 303
    assert first_convert.status_code == 303
    assert second_convert.status_code == 303
    assert first_convert.headers["location"] == second_convert.headers["location"]


def test_product_import_helper_branches_and_legacy_upsert():
    assert _first_value({"price": " 12 "}, ("unit_price", "price"), "0") == "12"
    assert _first_value({"price": " "}, ("unit_price", "price"), "7") == "7"

    with SessionLocal() as db:
        assert _upsert_product(db, {"name": ""}, default_vat_percent="24") is None
        product = _upsert_product(
            db,
            {"name": "Helper Product", "description": " ", "unit": "", "price": "3.50"},
            default_vat_percent="24",
        )
        legacy_none = products.upsert_product_from_row(db, {"name": ""})
        legacy_product = products.upsert_product_from_row(
            db,
            {"name": "Legacy Helper Product", "description": "Legacy", "unit_price": "4.50", "vat_percent": "25.5", "unit": "m"},
        )
        db.commit()
        product_values = {
            "unit_price": product.unit_price,
            "vat_percent": product.vat_percent,
            "unit": product.unit,
        }
        legacy_values = {
            "name": legacy_product.name if legacy_product else None,
        }

    assert product is not None
    assert product_values["unit_price"] == Decimal("3.50")
    assert product_values["vat_percent"] == Decimal("24")
    assert product_values["unit"] == "pcs"
    assert legacy_none is None
    assert legacy_values["name"] == "Legacy Helper Product"
