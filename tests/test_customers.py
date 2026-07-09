from fastapi.testclient import TestClient

from app.main import app


def test_customers_page_loads():
    client = TestClient(app)

    response = client.get("/customers")

    assert response.status_code == 200
    assert "Customers" in response.text


def test_create_customer_redirects_to_detail():
    client = TestClient(app)

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
