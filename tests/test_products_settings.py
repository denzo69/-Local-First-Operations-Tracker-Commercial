from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import JobStatus
from app.routes.jobs import ensure_default_job_statuses


def test_settings_can_be_updated():
    with TestClient(app) as client:
        response = client.post(
            "/settings",
            data={
                "company_name": "Test Company Oy",
                "company_business_id": "1234567-8",
                "company_address": "Test Street 1",
                "company_phone": "040 123 4567",
                "company_email": "test@example.com",
                "default_vat_percent": "25.5",
                "receipt_prefix": "TEST-",
                "language": "fi",
            },
            follow_redirects=False,
        )
        page_response = client.get("/settings")

    assert response.status_code == 303
    assert "Test Company Oy" in page_response.text
    assert "25.5" in page_response.text
    assert "Asetukset" in page_response.text


def test_product_can_be_created():
    with TestClient(app) as client:
        response = client.post(
            "/products",
            data={
                "name": "Test product",
                "unit_price": "12.50",
                "vat_percent": "24",
                "unit": "pcs",
            },
            follow_redirects=False,
        )
        page_response = client.get("/products")

    assert response.status_code == 303
    assert "Test product" in page_response.text


def test_products_can_be_imported_from_csv():
    csv_content = "name,description,unit_price,vat_percent,unit\nTest product import,Imported row,19.90,24,pcs\n"

    with TestClient(app) as client:
        response = client.post(
            "/products/import",
            files={"csv_file": ("products.csv", csv_content, "text/csv")},
            follow_redirects=False,
        )
        page_response = client.get("/products")

    assert response.status_code == 303
    assert "imported=1" in response.headers["location"]
    assert "Test product import" in page_response.text


def test_statuses_can_be_configured():
    with TestClient(app) as client:
        page_response = client.get("/settings/statuses")
        create_response = client.post(
            "/settings/statuses",
            data={
                "name": "Test status",
                "sort_order": "99",
                "is_ready_state": "true",
                "is_active": "true",
            },
            follow_redirects=False,
        )
        status_page_response = client.get("/settings/statuses")

    assert page_response.status_code == 200
    assert create_response.status_code == 303
    assert "Test status" in status_page_response.text
    assert "Ready" in status_page_response.text


def test_default_status_seed_does_not_overwrite_user_modified_statuses():
    with SessionLocal() as db:
        ensure_default_job_statuses(db)
        received = db.query(JobStatus).filter(JobStatus.name == "Received").first()
        received.name = "Custom received"
        received.sort_order = 123
        db.commit()

        ensure_default_job_statuses(db)
        modified = db.query(JobStatus).filter(JobStatus.id == received.id).first()

    assert modified.name == "Custom received"
    assert modified.sort_order == 123
