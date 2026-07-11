import logging
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.config import get_settings
from app.services.backup_service import BackupInfo, cleanup_retention, create_backup

logger = logging.getLogger(__name__)


@dataclass
class BackupSchedulerStatus:
    enabled: bool
    interval_minutes: int
    retention_count: int
    running: bool
    run_count: int
    last_run_at: datetime | None
    next_run_at: datetime | None
    last_backup_name: str | None
    last_error: str | None


class BackupScheduler:
    def __init__(self, *, interval_minutes: int, retention_count: int):
        self.interval_minutes = interval_minutes
        self.retention_count = retention_count
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.run_count = 0
        self.last_run_at: datetime | None = None
        self.next_run_at: datetime | None = None
        self.last_backup_name: str | None = None
        self.last_error: str | None = None

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        if self.running:
            return
        self.next_run_at = datetime.now(UTC) + self._interval
        self._thread = threading.Thread(
            target=self._run_loop,
            name="backup-scheduler",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    def run_once(self) -> BackupInfo | None:
        if not self._lock.acquire(blocking=False):
            return None
        try:
            backup = create_backup(label="scheduled")
            cleanup_retention(keep=self.retention_count)
            self.run_count += 1
            self.last_run_at = datetime.now(UTC)
            self.next_run_at = self.last_run_at + self._interval
            self.last_backup_name = backup.name
            self.last_error = None
            logger.info("Scheduled backup created: %s", backup.name)
            return backup
        except Exception as exc:  # pragma: no cover - defensive logging path
            self.last_run_at = datetime.now(UTC)
            self.next_run_at = self.last_run_at + self._interval
            self.last_error = str(exc)
            logger.exception("Scheduled backup failed")
            return None
        finally:
            self._lock.release()

    def status(self) -> BackupSchedulerStatus:
        return BackupSchedulerStatus(
            enabled=True,
            interval_minutes=self.interval_minutes,
            retention_count=self.retention_count,
            running=self.running,
            run_count=self.run_count,
            last_run_at=self.last_run_at,
            next_run_at=self.next_run_at,
            last_backup_name=self.last_backup_name,
            last_error=self.last_error,
        )

    @property
    def _interval(self) -> timedelta:
        return timedelta(minutes=max(1, self.interval_minutes))

    def _run_loop(self) -> None:
        while not self._stop_event.wait(self._interval.total_seconds()):
            self.run_once()


_scheduler: BackupScheduler | None = None


def start_backup_scheduler() -> BackupScheduler | None:
    global _scheduler
    settings = get_settings()
    if not settings.backup_scheduler_enabled:
        _scheduler = None
        return None
    _scheduler = BackupScheduler(
        interval_minutes=settings.backup_scheduler_interval_minutes,
        retention_count=settings.backup_retention_count,
    )
    _scheduler.start()
    return _scheduler


def stop_backup_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.stop()
    _scheduler = None


def get_backup_scheduler_status() -> BackupSchedulerStatus:
    settings = get_settings()
    if _scheduler is None:
        return BackupSchedulerStatus(
            enabled=settings.backup_scheduler_enabled,
            interval_minutes=settings.backup_scheduler_interval_minutes,
            retention_count=settings.backup_retention_count,
            running=False,
            run_count=0,
            last_run_at=None,
            next_run_at=None,
            last_backup_name=None,
            last_error=None,
        )
    return _scheduler.status()
