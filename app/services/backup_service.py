import hashlib
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from app.config import get_settings


settings = get_settings()


@dataclass(frozen=True)
class BackupInfo:
    name: str
    path: Path
    created_at: datetime
    size: int
    checksum: str


def backup_dir() -> Path:
    path = Path(settings.backup_dir)
    path.mkdir(exist_ok=True)
    return path


def database_path() -> Path:
    if settings.database_url.startswith("sqlite:///./"):
        return Path(settings.database_url.replace("sqlite:///./", "", 1))
    if settings.database_url.startswith("sqlite:///"):
        return Path(settings.database_url.replace("sqlite:///", "", 1))
    raise RuntimeError("Backups are currently supported only for SQLite.")


def backup_checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_sqlite_database(path: Path) -> None:
    with closing(sqlite3.connect(path)) as connection:
        result = connection.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise RuntimeError(f"Backup integrity check failed for {path.name}.")


def create_backup(label: str = "manual") -> BackupInfo:
    source = database_path()
    if not source.exists():
        raise RuntimeError("Database file does not exist yet.")

    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S_%f")
    target = backup_dir() / f"ops_tracker_{timestamp}_{label}.sqlite"

    with closing(sqlite3.connect(source)) as source_connection:
        with closing(sqlite3.connect(target)) as target_connection:
            source_connection.backup(target_connection)

    validate_sqlite_database(target)
    return backup_info(target)


def list_backups() -> list[BackupInfo]:
    files = sorted(
        backup_dir().glob("ops_tracker_*.sqlite"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return [backup_info(path) for path in files]


def backup_info(path: Path) -> BackupInfo:
    stat = path.stat()
    return BackupInfo(
        name=path.name,
        path=path,
        created_at=datetime.fromtimestamp(stat.st_mtime),
        size=stat.st_size,
        checksum=backup_checksum(path),
    )


def backup_health(stale_after_minutes: int = 60 * 24) -> dict:
    backups = list_backups()
    if not backups:
        return {"status": "warning", "message": "No backups yet.", "last_backup": None}
    last_backup = backups[0]
    stale = last_backup.created_at < datetime.now() - timedelta(minutes=stale_after_minutes)
    return {
        "status": "warning" if stale else "ok",
        "message": "Backup is stale." if stale else "Latest backup is recent.",
        "last_backup": last_backup,
    }


def restore_backup(name: str) -> BackupInfo:
    from app.database import engine

    selected = backup_dir() / Path(name).name
    if not selected.exists() or selected.parent != backup_dir():
        raise RuntimeError("Selected backup was not found.")
    validate_sqlite_database(selected)

    create_backup(label="before_restore")
    target = database_path()
    engine.dispose()
    with closing(sqlite3.connect(selected)) as source_connection:
        with closing(sqlite3.connect(target)) as target_connection:
            source_connection.backup(target_connection)
    engine.dispose()
    validate_sqlite_database(target)
    return backup_info(selected)


def cleanup_retention(keep: int = 50) -> int:
    backups = list_backups()
    removed = 0
    for backup in backups[keep:]:
        for attempt in range(5):
            try:
                backup.path.unlink(missing_ok=True)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.1)
        removed += 1
    return removed
