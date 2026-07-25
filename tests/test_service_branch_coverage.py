from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import get_settings
from app.database import SessionLocal
from app.models import Role, User
from app.services import backup_scheduler_service, backup_service
from app.services.auth_service import (
    authenticate_user,
    create_session_token,
    ensure_first_admin_role,
    get_session_user,
    hash_password,
    path_requires_admin,
    should_skip_auth,
    user_has_role,
    verify_password,
)
from app.services.sales_service import ensure_default_roles


def test_auth_service_rejects_malformed_hashes_and_sessions(monkeypatch):
    with SessionLocal() as db:
        ensure_default_roles(db)
        role = db.query(Role).filter(Role.code == "seller").one()
        user = User(
            name="Auth Branch User",
            login_name="auth.branch",
            password_hash=hash_password("secret123"),
            role=role,
            is_active=True,
        )
        inactive = User(
            name="Inactive Auth User",
            login_name="inactive.auth",
            password_hash=hash_password("secret123"),
            role=role,
            is_active=False,
        )
        db.add_all([user, inactive])
        db.commit()
        db.refresh(user)
        db.refresh(inactive)

        assert verify_password("secret123", user.password_hash) is True
        assert verify_password("wrong", user.password_hash) is False
        assert verify_password("secret123", None) is False
        assert verify_password("secret123", "bad-format") is False
        assert verify_password("secret123", "bcrypt$1000$salt$digest") is False

        assert authenticate_user(db, "auth.branch", "secret123").id == user.id
        assert authenticate_user(db, " auth.branch ", "wrong") is None
        assert authenticate_user(db, "inactive.auth", "secret123") is None

        token = create_session_token(user.id)
        assert get_session_user(db, token).id == user.id
        assert get_session_user(db, None) is None
        assert get_session_user(db, "too:few") is None
        assert get_session_user(db, f"{user.id}:not-int:signature") is None
        assert get_session_user(db, f"{user.id}:1:bad-signature") is None

        expired_issued_at = str(int(datetime.now(UTC).timestamp()) - 60 * 60 * 24)
        payload = f"{user.id}:{expired_issued_at}"
        from app.services import auth_service

        expired = f"{payload}:{auth_service._sign(payload)}"
        assert get_session_user(db, expired) is None

        inactive_token = create_session_token(inactive.id)
        assert get_session_user(db, inactive_token) is None

        user.password_hash = None
        db.commit()
        assert get_session_user(db, token) is None


def test_auth_route_helpers_and_first_admin_creation():
    with SessionLocal() as db:
        admin_role = ensure_first_admin_role(db)
        db.commit()
        assert admin_role.code == "admin"
        assert ensure_first_admin_role(db).id == admin_role.id

        assert user_has_role(None, {"admin"}) is False
        user_without_role = User(name="No Role", is_active=True)
        assert user_has_role(user_without_role, {"admin"}) is False

    assert should_skip_auth("/login") is True
    assert should_skip_auth("/static/app.css") is True
    assert should_skip_auth("/docs") is True
    assert should_skip_auth("/openapi.json") is True
    assert should_skip_auth("/customers") is False
    assert path_requires_admin("/settings") is True
    assert path_requires_admin("/settings/language") is False
    assert path_requires_admin("/settings/statuses") is True
    assert path_requires_admin("/users/1/edit") is True
    assert path_requires_admin("/customers") is False


def test_backup_service_branch_paths(monkeypatch, tmp_path):
    original_settings = backup_service.settings
    monkeypatch.setattr(
        backup_service,
        "settings",
        SimpleNamespace(database_url="postgresql://example/db", backup_dir=str(tmp_path)),
    )
    with pytest.raises(RuntimeError, match="SQLite"):
        backup_service.database_path()

    missing_db = tmp_path / "missing.sqlite"
    monkeypatch.setattr(
        backup_service,
        "settings",
        SimpleNamespace(database_url=f"sqlite:///{missing_db.as_posix()}", backup_dir=str(tmp_path / "backups")),
    )
    with pytest.raises(RuntimeError, match="does not exist"):
        backup_service.create_backup()

    assert backup_service.backup_health()["status"] == "warning"
    assert backup_service.backup_health()["last_backup"] is None

    old_backup = backup_service.backup_dir() / "ops_tracker_2000-01-01_000000_000000_old.sqlite"
    old_backup.write_bytes(b"not really sqlite")
    old_time = (datetime.now() - timedelta(days=3)).timestamp()
    import os

    os.utime(old_backup, (old_time, old_time))
    monkeypatch.setattr(backup_service, "backup_info", lambda path: backup_service.BackupInfo(
        name=path.name,
        path=path,
        created_at=datetime.fromtimestamp(path.stat().st_mtime),
        size=path.stat().st_size,
        checksum="checksum",
    ))
    stale = backup_service.backup_health(stale_after_minutes=1)
    assert stale["status"] == "warning"
    assert stale["message"] == "Backup is stale."

    monkeypatch.setattr(backup_service, "settings", original_settings)


def test_backup_cleanup_retries_permission_errors(monkeypatch, tmp_path):
    newest = tmp_path / "ops_tracker_2026-01-02_000000_000000_new.sqlite"
    removable = tmp_path / "ops_tracker_2026-01-01_000000_000000_old.sqlite"
    newest.write_bytes(b"new")
    removable.write_bytes(b"old")
    import os

    os.utime(newest, (2000, 2000))
    os.utime(removable, (1000, 1000))
    monkeypatch.setattr(
        backup_service,
        "settings",
        SimpleNamespace(database_url=get_settings().database_url, backup_dir=str(tmp_path)),
    )

    attempts = {"count": 0}
    original_unlink = Path.unlink

    def flaky_unlink(self, *args, **kwargs):
        if self == removable and attempts["count"] < 2:
            attempts["count"] += 1
            raise PermissionError("locked")
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", flaky_unlink)
    monkeypatch.setattr(backup_service.time, "sleep", lambda _seconds: None)

    assert backup_service.cleanup_retention(keep=1) == 1
    assert attempts["count"] == 2
    assert newest.exists()
    assert not removable.exists()


def test_backup_scheduler_start_stop_status_and_failure_branches(monkeypatch):
    scheduler = backup_scheduler_service.BackupScheduler(interval_minutes=0, retention_count=1)
    assert scheduler._interval == timedelta(minutes=1)
    assert scheduler.status().enabled is True

    locked = scheduler._lock.acquire(blocking=False)
    try:
        assert locked is True
        assert scheduler.run_once() is None
    finally:
        scheduler._lock.release()

    def fail_backup(label="scheduled"):
        raise RuntimeError("scheduled failure")

    monkeypatch.setattr(backup_scheduler_service, "create_backup", fail_backup)
    assert scheduler.run_once() is None
    assert scheduler.last_error == "scheduled failure"

    fake_settings = SimpleNamespace(
        backup_scheduler_enabled=True,
        backup_scheduler_interval_minutes=1,
        backup_retention_count=2,
    )
    monkeypatch.setattr(backup_scheduler_service, "get_settings", lambda: fake_settings)
    monkeypatch.setattr(backup_scheduler_service.BackupScheduler, "start", lambda self: None)
    started = backup_scheduler_service.start_backup_scheduler()
    assert started is not None
    assert backup_scheduler_service.get_backup_scheduler_status().enabled is True
    backup_scheduler_service.stop_backup_scheduler()
    assert backup_scheduler_service.get_backup_scheduler_status().running is False
