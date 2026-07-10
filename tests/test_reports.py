from datetime import date

from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import Product


def test_reports_show_sales_totals():
    with TestClient(app) as client:
        customer_response = client.post(
            "/customers",
            data={"name": "Job Owner"},
            follow_redirects=False,
        )
        customer_id = customer_response.headers["location"].rsplit("/", 1)[-1]
        client.post(
            "/products",
            data={
                "name": "Test product",
                "unit_price": "15",
                "vat_percent": "24",
                "unit": "pcs",
            },
            follow_redirects=False,
        )
        with SessionLocal() as db:
            product_id = db.query(Product).filter(Product.name == "Test product").first().id

        job_response = client.post(
            "/jobs",
            data={"title": "Test job report", "customer_id": customer_id},
            follow_redirects=False,
        )
        job_url = job_response.headers["location"]
        item_response = client.post(
            f"{job_url}/items",
            data={"product_id": product_id, "quantity": "3"},
            follow_redirects=False,
        )
        assert item_response.status_code == 303

        today = date.today().isoformat()
        month = date.today().strftime("%Y-%m")
        response = client.get(f"/reports?day={today}&month={month}")

    assert response.status_code == 200
    assert "Day sales" in response.text
    assert "45.00" in response.text
    assert "Test job report" in response.text


def test_reports_load_when_work_order_has_no_items():
    with TestClient(app) as client:
        customer_response = client.post(
            "/customers",
            data={"name": "Job Owner"},
            follow_redirects=False,
        )
        customer_id = customer_response.headers["location"].rsplit("/", 1)[-1]
        client.post(
            "/work-orders",
            data={"title": "Test job empty report", "customer_id": customer_id},
            follow_redirects=False,
        )

        today = date.today().isoformat()
        month = date.today().strftime("%Y-%m")
        response = client.get(f"/reports?day={today}&month={month}")

    assert response.status_code == 200
    assert "0.00" in response.text
    assert "Test job empty report" in response.text
