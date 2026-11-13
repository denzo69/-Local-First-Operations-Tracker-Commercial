import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal, engine
from app.main import app
from app.models import Customer
from app.services.maintenance_service import maintenance_mode
from app.services.backup_service import (
    backup_health,
    cleanup_retention,
    create_backup,
    list_backups,
    restore_backup,
    validate_sqlite_database,
)
from app.services.backup_scheduler_service import BackupScheduler, get_backup_scheduler_status


def test_backups_page_loads_and_backup_can_be_created():
    with TestClient(app) as client:
        page_response = client.get("/backups")
        create_response = client.post("/backups", follow_redirects=False)

    assert page_response.status_code == 200
    assert "Create backup" in page_response.text
    assert "Backup status" in page_response.text
    assert "Automatic backup scheduler" in page_response.text
    assert create_response.status_code == 303
    assert create_response.headers["location"] == "/backups"


def test_backup_restore_recovers_database_state():
    with SessionLocal() as db:
        db.add(Customer(name="Test Customer"))
        db.commit()

    backup = create_backup(label="restore_test")

    with SessionLocal() as db:
        customer = db.query(Customer).filter(Customer.name == "Test Customer").first()
        db.delete(customer)
        db.commit()

    restore_backup(backup.name)
    engine.dispose()

    with SessionLocal() as db:
        restored = db.query(Customer).filter(Customer.name == "Test Customer").first()

    assert restored is not None


def test_backup_retention_keeps_newest_files():
    first = create_backup(label="retention_old")
    second = create_backup(label="retention_new")

    removed = cleanup_retention(keep=1)
    remaining_names = {backup.name for backup in list_backups()}

    assert removed >= 1
    assert second.name in remaining_names
    assert first.name not in remaining_names


def test_backup_scheduler_run_once_creates_backup_and_applies_retention():
    scheduler = BackupScheduler(interval_minutes=1440, retention_count=1)
    first = scheduler.run_once()
    second = scheduler.run_once()
    remaining_names = {backup.name for backup in list_backups()}

    assert first is not None
    assert second is not None
    assert scheduler.run_count == 2
    assert scheduler.last_backup_name == second.name
    assert scheduler.last_error is None
    assert second.name in remaining_names
    assert first.name not in remaining_names


def test_backup_scheduler_status_reflects_disabled_test_configuration():
    status = get_backup_scheduler_status()

    assert status.enabled is False
    assert status.running is False


def test_backup_health_reports_latest_backup():
    backup = create_backup(label="health_test")
    health = backup_health()

    assert health["status"] == "ok"
    assert health["last_backup"].name == backup.name


def test_corrupt_backup_fails_integrity_check(tmp_path):
    corrupt = tmp_path / "corrupt.sqlite"
    corrupt.write_bytes(b"not a sqlite database")

    with pytest.raises(RuntimeError, match="Backup integrity check failed.*corrupt.sqlite"):
        validate_sqlite_database(corrupt)


def test_corrupt_backup_restore_is_controlled_preserves_data_and_allows_valid_restore():
    with SessionLocal() as db:
        db.add(Customer(name="Customer before backup"))
        db.commit()
    valid = create_backup(label="before_corrupt_restore")
    corrupt = valid.path.parent / "ops_tracker_corrupt.sqlite"
    corrupt.write_bytes(b"not a sqlite database")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(f"/backups/{corrupt.name}/restore", follow_redirects=False)
        assert response.status_code == 400
        assert "Backup integrity check failed" in response.text
        with SessionLocal() as db:
            assert [row.name for row in db.query(Customer).all()] == ["Customer before backup"]

        # The failed restore must release maintenance mode and keep the app usable.
        assert client.post("/customers", data={"name": "Customer after rejection"}, follow_redirects=False).status_code == 303
        restored = client.post(f"/backups/{valid.name}/restore", follow_redirects=False)
        assert restored.status_code == 303

    with SessionLocal() as db:
        assert [row.name for row in db.query(Customer).all()] == ["Customer before backup"]


def test_write_routes_are_blocked_during_maintenance():
    with TestClient(app) as client:
        with maintenance_mode():
            response = client.post("/customers", data={"name": "Blocked Customer"})

    assert response.status_code == 503
