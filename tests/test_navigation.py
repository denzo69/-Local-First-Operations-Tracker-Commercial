from fastapi.testclient import TestClient
from datetime import date, timedelta

from app.database import SessionLocal
from app.main import app
from app.models import CashRegister, Job, Role, User
from app.services.sales_service import ensure_default_roles, open_shift


def test_dashboard_actions_are_links():
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert 'href="/work-orders/new"' in response.text
    assert 'href="/customers/new"' in response.text


def test_main_navigation_targets_load():
    with TestClient(app) as client:
        for path in [
            "/customers",
            "/work-orders",
            "/jobs",
            "/products",
            "/sales",
            "/shifts",
            "/daily-closings",
            "/seller-reports",
            "/users",
            "/cash-registers",
            "/reports",
            "/backups",
            "/audit-log",
            "/settings",
        ]:
            response = client.get(path)
            assert response.status_code == 200


def test_new_navigation_labels_render_in_finnish_and_english():
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
        finnish = client.get("/")
        client.post(
            "/settings",
            data={
                "company_name": "Test Company Oy",
                "default_vat_percent": "24",
                "receipt_prefix": "TEST-",
                "language": "en",
            },
            follow_redirects=False,
        )
        english = client.get("/")

    assert "Myynti" in finnish.text
    assert "Hallinta" in finnish.text
    assert "Audit-loki" in finnish.text
    assert "Sales" in english.text
    assert "Administration" in english.text
    assert "Audit log" in english.text


def test_every_main_navigation_label_renders_in_both_languages():
    english_labels = [
        "Dashboard", "Operations", "Customers", "Work Orders", "Sales",
        "New sale", "Shifts", "Daily closing", "Catalog", "Products",
        "Reports", "Seller reports", "Administration", "Users",
        "Cash registers", "Audit log", "Backups", "Settings",
    ]
    finnish_labels = [
        "Myynti", "Hallinta", "Asiakkaat", "Tuotteet", "Kassat",
        "Audit-loki", "Asetukset",
    ]
    with TestClient(app) as client:
        english = client.get("/")
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
        finnish = client.get("/")

    for label in english_labels:
        assert label in english.text
    for label in finnish_labels:
        assert label in finnish.text
    assert 'dropdown-toggle"></a>' not in english.text
    assert 'sidebar-group-label"></div>' not in english.text


def test_mobile_navigation_markup_is_present():
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert 'data-bs-toggle="offcanvas"' in response.text
    assert 'id="mobileNav"' in response.text
    assert 'aria-label="Mobile navigation"' in response.text


def test_dashboard_shows_created_job():
    with TestClient(app) as client:
        customer_response = client.post(
            "/customers",
            data={"name": "Dashboard Customer"},
            follow_redirects=False,
        )
        customer_id = customer_response.headers["location"].rsplit("/", 1)[-1]

        client.post(
            "/jobs",
            data={
                "title": "Dashboard job",
                "customer_id": customer_id,
                "requested_pickup_date": "2099-01-15",
            },
            follow_redirects=False,
        )

        response = client.get("/")

    assert response.status_code == 200
    assert "Dashboard job" in response.text
    assert "Dashboard Customer" in response.text


def test_dashboard_due_tomorrow_is_consistent_with_upcoming_section():
    tomorrow = date.today() + timedelta(days=1)
    with TestClient(app) as client:
        customer_response = client.post(
            "/customers",
            data={"name": "Dashboard Customer"},
            follow_redirects=False,
        )
        customer_id = customer_response.headers["location"].rsplit("/", 1)[-1]
        client.post(
            "/jobs",
            data={
                "title": "Dashboard job tomorrow",
                "customer_id": customer_id,
                "requested_pickup_date": tomorrow.isoformat(),
            },
            follow_redirects=False,
        )
        response = client.get("/")

    assert response.status_code == 200
    assert "Dashboard job tomorrow" in response.text
    assert "Upcoming work orders" in response.text
    assert "No upcoming work orders yet." not in response.text


def test_dashboard_compact_empty_and_operational_panels_render():
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "Current shift" in response.text
    assert "No open shift." in response.text
    assert "Daily closing not completed" in response.text
    assert "empty-state compact" in response.text


def test_dashboard_current_shift_panel_when_shift_is_open():
    with SessionLocal() as db:
        ensure_default_roles(db)
        role = db.query(Role).filter(Role.code == "seller").one()
        user = User(name="Dashboard Shift Seller", role=role, is_active=True)
        register = CashRegister(name="Dashboard Register", is_active=True)
        db.add_all([user, register])
        db.commit()
        open_shift(
            db,
            seller_id=user.id,
            cash_register_id=register.id,
            business_date=date.today(),
            starting_cash="10",
        )

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "Dashboard Shift Seller" in response.text
    assert "Dashboard Register" in response.text
