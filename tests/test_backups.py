from fastapi.testclient import TestClient

from app.main import app


def test_backups_page_loads_and_backup_can_be_created():
    with TestClient(app) as client:
        page_response = client.get("/backups")
        create_response = client.post("/backups", follow_redirects=False)

    assert page_response.status_code == 200
    assert "Create backup" in page_response.text
    assert create_response.status_code == 303
    assert create_response.headers["location"] == "/backups"
