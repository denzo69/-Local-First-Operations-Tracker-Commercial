from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import Product


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


def test_job_status_can_be_marked_ready_for_pickup():
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

        marker = "Ready for pickup"
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
    assert "Ready for pickup" in response.text
    assert "Activity timeline" in response.text
    assert "Status changed from Received to Ready for pickup." in response.text


def test_picked_up_job_is_available_in_history():
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

        before_marker = detail_response.text.split("Picked up", 1)[0]
        status_id = before_marker.rsplit('name="status_id" value="', 1)[1].split('"', 1)[0]
        client.post(f"{job_url}/status", data={"status_id": status_id})

        active_response = client.get("/jobs?view=active")
        history_response = client.get("/jobs?view=history")

    assert active_response.status_code == 200
    assert history_response.status_code == 200
    assert "Test job history only" not in active_response.text
    assert "Test job history only" in history_response.text
    assert "Picked up" in history_response.text


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
