from fastapi.testclient import TestClient

from app.config import get_settings
from app.database import engine
from app.main import app


def test_health_check_returns_ok():
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_tests_use_isolated_temporary_database():
    settings = get_settings()

    assert "ops-tracker-tests-" in settings.database_url
    assert not settings.database_url.endswith("data/app.sqlite")


def test_sqlite_unique_receipt_indexes_exist():
    with engine.connect() as connection:
        job_indexes = connection.exec_driver_sql("PRAGMA index_list(jobs)").all()
        receipt_indexes = connection.exec_driver_sql("PRAGMA index_list(receipts)").all()

    assert any(row[1] == "ux_jobs_receipt_number" for row in job_indexes)
    assert any(row[1] == "ux_receipts_receipt_number" for row in receipt_indexes)
