from fastapi.testclient import TestClient

from app.main import app


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
