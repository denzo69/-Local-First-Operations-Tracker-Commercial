from fastapi.testclient import TestClient

from app.main import app


def test_customers_page_loads():
    with TestClient(app) as client:
        response = client.get("/customers")

    assert response.status_code == 200
    assert "Customers" in response.text


def test_new_customer_page_loads():
    with TestClient(app) as client:
        response = client.get("/customers/new")

    assert response.status_code == 200
    assert "Save customer" in response.text


def test_create_customer_redirects_to_detail():
    with TestClient(app) as client:
        response = client.post(
            "/customers",
            data={
                "name": "Test Customer",
                "phone": "040 123 4567",
                "email": "test@example.com",
                "address": "Test Street 1",
                "company_name": "Test Company",
                "business_id": "1234567-8",
                "notes": "Created in test",
            },
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/customers/")


def test_customer_detail_shows_work_history():
    with TestClient(app) as client:
        customer_response = client.post(
            "/customers",
            data={"name": "Test Customer"},
            follow_redirects=False,
        )
        customer_url = customer_response.headers["location"]
        customer_id = customer_url.rsplit("/", 1)[-1]

        client.post(
            "/jobs",
            data={
                "title": "Test job",
                "customer_id": customer_id,
                "requested_pickup_date": "2026-07-11",
            },
            follow_redirects=False,
        )

        response = client.get(customer_url)

    assert response.status_code == 200
    assert "Work history" in response.text
    assert "Test job" in response.text
    assert "Activity timeline" in response.text


def test_customers_can_be_searched():
    with TestClient(app) as client:
        client.post(
            "/customers",
            data={"name": "Test Customer", "phone": "040 123 4567"},
            follow_redirects=False,
        )

        response = client.get("/customers?q=040")

    assert response.status_code == 200
    assert "Test Customer" in response.text


def test_customer_with_job_history_cannot_be_deleted():
    with TestClient(app) as client:
        customer_response = client.post(
            "/customers",
            data={"name": "Test Customer"},
            follow_redirects=False,
        )
        customer_url = customer_response.headers["location"]
        customer_id = customer_url.rsplit("/", 1)[-1]
        client.post(
            "/jobs",
            data={"title": "Test job", "customer_id": customer_id},
            follow_redirects=False,
        )

        response = client.post(f"{customer_url}/delete")

    assert response.status_code == 400
