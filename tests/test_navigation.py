from fastapi.testclient import TestClient

from app.main import app


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
