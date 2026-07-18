from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.database import init_db
from app.main import app
from app.models import JobStatus, Setting
from app.routes.jobs import ensure_default_job_statuses
from app.services.settings_service import get_app_settings


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


def test_settings_control_dashboard_visible_cards():
    with TestClient(app) as client:
        settings_page = client.get("/settings")
        response = client.post(
            "/settings",
            data={
                "company_name": "JEronAI Operations",
                "default_vat_percent": "24",
                "receipt_prefix": "SALE-",
                "language": "en",
                "ui_density": "large",
                "dashboard_settings_present": "true",
                "dashboard_show_daily_closing": "true",
                "dashboard_show_sales_invoicing": "true",
                "dashboard_show_quick_actions": "true",
            },
            follow_redirects=False,
        )
        dashboard = client.get("/")

    assert settings_page.status_code == 200
    assert "Default VAT percent" in settings_page.text
    assert "Dashboard visibility" in settings_page.text
    assert "Display size" in settings_page.text
    assert response.status_code == 303
    assert 'class="density-large"' in dashboard.text
    assert "Daily closing" in dashboard.text
    assert "Sales and invoicing" in dashboard.text
    assert "Quick actions" in dashboard.text
    assert "Work queues" not in dashboard.text
    assert "Upcoming work orders" not in dashboard.text
    assert "Recent activity" not in dashboard.text


def test_finnish_persists_after_redirect():
    with TestClient(app) as client:
        response = client.post(
            "/settings",
            data={
                "company_name": "Test Company Oy",
                "default_vat_percent": "24",
                "receipt_prefix": "TEST-",
                "language": "fi",
            },
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert "Asetukset" in response.text
    assert 'option value="fi" selected' in response.text


def test_finnish_persists_after_dashboard_navigation():
    with TestClient(app) as client:
        client.post(
            "/settings",
            data={
                "company_name": "Test Company Oy",
                "default_vat_percent": "24",
                "receipt_prefix": "TEST-",
                "language": "fi",
            },
            follow_redirects=False,
        )
        dashboard_response = client.get("/")

    assert dashboard_response.status_code == 200
    assert "Työpöytä" in dashboard_response.text or "TyÃ¶pÃ¶ytÃ¤" in dashboard_response.text


def test_finnish_persists_when_language_field_is_omitted():
    with TestClient(app) as client:
        client.post(
            "/settings",
            data={
                "company_name": "Test Company Oy",
                "default_vat_percent": "24",
                "receipt_prefix": "TEST-",
                "language": "fi",
            },
            follow_redirects=False,
        )
        client.post(
            "/settings",
            data={
                "company_name": "Updated Company Oy",
                "default_vat_percent": "24",
                "receipt_prefix": "TEST-",
            },
            follow_redirects=False,
        )
        settings_response = client.get("/settings")

    assert "Asetukset" in settings_response.text
    assert 'option value="fi" selected' in settings_response.text


def test_invalid_language_does_not_replace_finnish():
    with TestClient(app) as client:
        client.post(
            "/settings",
            data={
                "company_name": "Test Company Oy",
                "default_vat_percent": "24",
                "receipt_prefix": "TEST-",
                "language": "fi",
            },
            follow_redirects=False,
        )
        client.post(
            "/settings",
            data={
                "company_name": "Updated Company Oy",
                "default_vat_percent": "24",
                "receipt_prefix": "TEST-",
                "language": "sv",
            },
            follow_redirects=False,
        )
        settings_response = client.get("/settings")

    assert "Asetukset" in settings_response.text
    assert 'option value="fi" selected' in settings_response.text


def test_application_restart_simulation_preserves_finnish():
    with SessionLocal() as db:
        db.add(Setting(key="language", value="fi"))
        db.commit()

    init_db()

    with SessionLocal() as db:
        settings_values = get_app_settings(db)

    assert settings_values["language"] == "fi"


def test_new_empty_database_defaults_to_english():
    with SessionLocal() as db:
        settings_values = get_app_settings(db)

    assert settings_values["language"] == "en"


def test_initialization_never_overwrites_finnish():
    with SessionLocal() as db:
        db.add(Setting(key="language", value="fi"))
        db.commit()

    init_db()
    init_db()

    with SessionLocal() as db:
        settings_values = get_app_settings(db)

    assert settings_values["language"] == "fi"


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
