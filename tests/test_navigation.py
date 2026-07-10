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
