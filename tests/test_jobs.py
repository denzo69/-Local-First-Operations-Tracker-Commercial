from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import AuditLog, Job, Product, Receipt, Setting


def test_new_job_page_has_customer_select():
    with TestClient(app) as client:
        client.post("/customers", data={"name": "Job Customer"}, follow_redirects=False)

        response = client.get("/jobs/new")

    assert response.status_code == 200
    assert 'name="customer_id"' in response.text
    assert "Job Customer" in response.text


def test_create_job_with_customer_redirects_to_detail():
    with TestClient(app) as client:
        customer_response = client.post(
            "/customers",
            data={"name": "Job Owner"},
            follow_redirects=False,
        )
        customer_id = customer_response.headers["location"].rsplit("/", 1)[-1]

        response = client.post(
            "/jobs",
            data={
                "title": "Test job",
                "customer_id": customer_id,
                "arrival_date": "2026-07-10",
                "requested_pickup_date": "2026-07-11",
                "priority": "normal",
            },
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/jobs/")


def test_work_order_routes_create_and_redirect_to_work_order_detail():
    with TestClient(app) as client:
        customer_response = client.post(
            "/customers",
            data={"name": "Job Owner"},
            follow_redirects=False,
        )
        customer_id = customer_response.headers["location"].rsplit("/", 1)[-1]

        form_response = client.get("/work-orders/new")
        response = client.post(
            "/work-orders",
            data={"title": "Test job work order route", "customer_id": customer_id},
            follow_redirects=False,
        )

    assert form_response.status_code == 200
    assert 'action="/work-orders"' in form_response.text
    assert response.status_code == 303
    assert response.headers["location"].startswith("/work-orders/")


def test_job_can_be_edited_and_printed():
    with TestClient(app) as client:
        customer_response = client.post(
            "/customers",
            data={"name": "Job Owner"},
            follow_redirects=False,
        )
        customer_id = customer_response.headers["location"].rsplit("/", 1)[-1]

        job_response = client.post(
            "/jobs",
            data={"title": "Test job history only", "customer_id": customer_id},
            follow_redirects=False,
        )
        job_url = job_response.headers["location"]

        edit_response = client.post(
            job_url,
            data={
                "title": "Test job updated",
                "customer_id": customer_id,
                "requested_pickup_date": "2026-07-12",
                "priority": "high",
            },
            follow_redirects=False,
        )
        receipt_response = client.get(f"{job_url}/receipt")

    assert edit_response.status_code == 303
    assert receipt_response.status_code == 200
    assert "Receipt / work order" in receipt_response.text
    assert "Test job updated" in receipt_response.text


def test_job_receipt_number_and_sales_items_work():
    with TestClient(app) as client:
        customer_response = client.post(
            "/customers",
            data={"name": "Job Owner"},
            follow_redirects=False,
        )
        customer_id = customer_response.headers["location"].rsplit("/", 1)[-1]
        product_response = client.post(
            "/products",
            data={
                "name": "Test product",
                "unit_price": "10",
                "vat_percent": "24",
                "unit": "pcs",
            },
            follow_redirects=False,
        )
        assert product_response.status_code == 303

        with SessionLocal() as db:
            product_id = db.query(Product).filter(Product.name == "Test product").first().id
        job_response = client.post(
            "/jobs",
            data={"title": "Test job", "customer_id": customer_id},
            follow_redirects=False,
        )
        job_url = job_response.headers["location"]
        item_response = client.post(
            f"{job_url}/items",
            data={"product_id": product_id, "quantity": "2"},
            follow_redirects=False,
        )
        assert item_response.status_code == 303
        detail_response = client.get(job_url)
        receipt_response = client.get(f"{job_url}/receipt")

    assert "Test product" in detail_response.text
    assert "20.00" in detail_response.text
    assert "Receipt" in receipt_response.text
    assert "Test product" in receipt_response.text


def test_work_order_status_can_be_marked_ready():
    with TestClient(app) as client:
        customer_response = client.post(
            "/customers",
            data={"name": "Job Owner"},
            follow_redirects=False,
        )
        customer_id = customer_response.headers["location"].rsplit("/", 1)[-1]

        job_response = client.post(
            "/jobs",
            data={
                "title": "Test job",
                "customer_id": customer_id,
                "requested_pickup_date": "2026-07-11",
            },
            follow_redirects=False,
        )
        job_url = job_response.headers["location"]
        detail_response = client.get(job_url)

        marker = "Ready"
        assert marker in detail_response.text

        before_marker = detail_response.text.split(marker, 1)[0]
        status_id = before_marker.rsplit('name="status_id" value="', 1)[1].split('"', 1)[0]
        response = client.post(
            f"{job_url}/status",
            data={"status_id": status_id},
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert "Current status" in response.text
    assert "Ready" in response.text
    assert "Activity timeline" in response.text
    assert "Status changed from Received to Ready." in response.text


def test_completed_work_order_is_available_in_history():
    with TestClient(app) as client:
        customer_response = client.post(
            "/customers",
            data={"name": "Job Owner"},
            follow_redirects=False,
        )
        customer_id = customer_response.headers["location"].rsplit("/", 1)[-1]

        job_response = client.post(
            "/jobs",
            data={"title": "Test job history only", "customer_id": customer_id},
            follow_redirects=False,
        )
        job_url = job_response.headers["location"]
        detail_response = client.get(job_url)

        before_marker = detail_response.text.split("Completed", 1)[0]
        status_id = before_marker.rsplit('name="status_id" value="', 1)[1].split('"', 1)[0]
        client.post(f"{job_url}/status", data={"status_id": status_id})

        active_response = client.get("/jobs?view=active")
        history_response = client.get("/jobs?view=history")

    assert active_response.status_code == 200
    assert history_response.status_code == 200
    assert "Test job history only" not in active_response.text
    assert "Test job history only" in history_response.text
    assert "Completed" in history_response.text


def test_jobs_can_be_searched():
    with TestClient(app) as client:
        customer_response = client.post(
            "/customers",
            data={"name": "Job Owner"},
            follow_redirects=False,
        )
        customer_id = customer_response.headers["location"].rsplit("/", 1)[-1]
        client.post(
            "/jobs",
            data={"title": "Test job searchable", "customer_id": customer_id},
            follow_redirects=False,
        )

        response = client.get("/jobs?q=searchable&view=all")

    assert response.status_code == 200
    assert "Test job searchable" in response.text


def test_job_list_has_live_filter_markup():
    with TestClient(app) as client:
        client.post(
            "/jobs",
            data={"title": "Test job live filter"},
            follow_redirects=False,
        )
        response = client.get("/jobs?view=all")

    assert response.status_code == 200
    assert "data-live-filter-form" in response.text
    assert "data-live-filter-input" in response.text
    assert "data-search-text" in response.text


def test_jobs_can_be_searched_by_receipt_number():
    with TestClient(app) as client:
        customer_response = client.post(
            "/customers",
            data={"name": "Job Owner"},
            follow_redirects=False,
        )
        customer_id = customer_response.headers["location"].rsplit("/", 1)[-1]
        job_response = client.post(
            "/jobs",
            data={"title": "Test job receipt lookup", "customer_id": customer_id},
            follow_redirects=False,
        )
        job_url = job_response.headers["location"]
        detail_response = client.get(job_url)
        receipt_number = detail_response.text.split("Receipt number</dt>", 1)[1].split("<dd", 1)[1].split(">", 1)[1].split("<", 1)[0].strip()

        response = client.get(f"/jobs?q={receipt_number}&view=all")

    assert response.status_code == 200
    assert "Test job receipt lookup" in response.text


def test_receipt_number_uses_sequence_setting_not_job_id_and_reprint_preserves_number():
    with TestClient(app) as client:
        customer_response = client.post(
            "/customers",
            data={"name": "Job Owner"},
            follow_redirects=False,
        )
        customer_id = customer_response.headers["location"].rsplit("/", 1)[-1]

        with SessionLocal() as db:
            db.add(Setting(key="receipt_prefix", value="SEQ-"))
            db.add(Setting(key="next_receipt_sequence", value="900001"))
            db.commit()

        job_response = client.post(
            "/work-orders",
            data={"title": "Test job receipt sequence", "customer_id": customer_id},
            follow_redirects=False,
        )
        job_url = job_response.headers["location"]
        first_receipt = client.get(f"{job_url}/receipt")
        second_receipt = client.get(f"{job_url}/receipt")

        with SessionLocal() as db:
            job_id = int(job_url.rsplit("/", 1)[-1])
            job = db.get(Job, job_id)

    assert first_receipt.status_code == 200
    assert second_receipt.status_code == 200
    assert job.receipt_number.endswith("-900001")
    assert job.id != 900001
    assert first_receipt.text.count(job.receipt_number) >= 1
    assert second_receipt.text.count(job.receipt_number) >= 1


def test_print_snapshot_is_stored_once_and_is_stable_after_work_order_update():
    with TestClient(app) as client:
        customer_response = client.post(
            "/customers",
            data={"name": "Job Owner"},
            follow_redirects=False,
        )
        customer_id = customer_response.headers["location"].rsplit("/", 1)[-1]
        job_response = client.post(
            "/work-orders",
            data={"title": "Test job snapshot original", "customer_id": customer_id},
            follow_redirects=False,
        )
        job_url = job_response.headers["location"]
        receipt_response = client.get(f"{job_url}/receipt")

        job_id = int(job_url.rsplit("/", 1)[-1])
        with SessionLocal() as db:
            snapshot = (
                db.query(Receipt)
                .filter(Receipt.job_id == job_id, Receipt.receipt_type == "customer_receipt")
                .first()
            )
            original_payload = snapshot.editable_snapshot
            printed_at = snapshot.printed_at

        client.post(
            job_url,
            data={
                "title": "Test job snapshot changed",
                "customer_id": customer_id,
                "priority": "normal",
            },
            follow_redirects=False,
        )
        client.get(f"{job_url}/receipt")

        with SessionLocal() as db:
            snapshot_after = (
                db.query(Receipt)
                .filter(Receipt.job_id == job_id, Receipt.receipt_type == "customer_receipt")
                .first()
            )
            print_events = (
                db.query(AuditLog)
                .filter(AuditLog.entity_type == "job", AuditLog.entity_id == job_id)
                .filter(AuditLog.event_type == "document.printed")
                .all()
            )

    assert receipt_response.status_code == 200
    assert "Test job snapshot original" in original_payload
    assert "Test job snapshot changed" not in snapshot_after.editable_snapshot
    assert snapshot_after.editable_snapshot == original_payload
    assert snapshot_after.printed_at == printed_at
    assert len(print_events) == 1
