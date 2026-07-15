import os
import shutil
import tempfile
from pathlib import Path

import pytest

TEST_ROOT = Path(tempfile.mkdtemp(prefix="ops-tracker-tests-"))
TEST_DB = TEST_ROOT / "test.sqlite"
TEST_BACKUPS = TEST_ROOT / "backups"

os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB.as_posix()}"
os.environ["BACKUP_DIR"] = str(TEST_BACKUPS)
os.environ["PASSWORD_ITERATIONS"] = "1000"
os.environ["BACKUP_SCHEDULER_ENABLED"] = "false"

from app.database import Base, engine, init_db  # noqa: E402


def reset_database() -> None:
    Base.metadata.drop_all(bind=engine)
    init_db()


def reset_backups() -> None:
    shutil.rmtree(TEST_BACKUPS, ignore_errors=True)
    TEST_BACKUPS.mkdir(parents=True, exist_ok=True)


@pytest.fixture(scope="session", autouse=True)
def isolated_test_database():
    reset_database()
    reset_backups()
    yield
    engine.dispose()
    shutil.rmtree(TEST_ROOT, ignore_errors=True)


@pytest.fixture(autouse=True)
def clean_test_data():
    # Every test starts from a fresh schema. Rebuilding again after the test is
    # redundant because the next setup performs the same reset.
    reset_database()
    reset_backups()
    yield
