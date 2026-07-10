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

from app.database import Base, SessionLocal, engine, init_db  # noqa: E402
from app.models import AuditLog, Customer, Job, JobItem, JobStatus, Product, Setting  # noqa: E402


TEST_CUSTOMER_NAMES = {
    "Test Customer",
    "Job Customer",
    "Job Owner",
    "Dashboard Customer",
}

TEST_JOB_TITLES = {
    "Dashboard job",
}


def reset_database() -> None:
    Base.metadata.drop_all(bind=engine)
    init_db()


def reset_backups() -> None:
    shutil.rmtree(TEST_BACKUPS, ignore_errors=True)
    TEST_BACKUPS.mkdir(parents=True, exist_ok=True)


def cleanup_test_data() -> None:
    with SessionLocal() as db:
        test_job_ids = db.query(Job.id).filter(
            Job.title.ilike("Test job%") | Job.title.in_(TEST_JOB_TITLES)
        )
        db.query(JobItem).filter(JobItem.job_id.in_(test_job_ids)).delete(
            synchronize_session=False
        )
        db.query(AuditLog).filter(
            (AuditLog.entity_type == "job")
            & AuditLog.entity_id.in_(test_job_ids)
        ).delete(synchronize_session=False)
        db.query(Job).filter(
            Job.title.ilike("Test job%") | Job.title.in_(TEST_JOB_TITLES)
        ).delete(synchronize_session=False)
        db.query(AuditLog).filter(
            (AuditLog.entity_type == "customer")
            & AuditLog.entity_id.in_(
                db.query(Customer.id).filter(Customer.name.in_(TEST_CUSTOMER_NAMES))
            )
        ).delete(synchronize_session=False)
        db.query(Customer).filter(Customer.name.in_(TEST_CUSTOMER_NAMES)).filter(
            ~Customer.jobs.any()
        ).delete(synchronize_session=False)
        db.query(Product).filter(Product.name.ilike("Test product%")).delete(
            synchronize_session=False
        )
        db.query(AuditLog).filter(
            (AuditLog.entity_type == "status")
            & AuditLog.description.ilike("%Test status%")
        ).delete(synchronize_session=False)
        db.query(JobStatus).filter(JobStatus.name.ilike("Test status%")).delete(
            synchronize_session=False
        )
        db.query(Setting).delete(synchronize_session=False)
        db.commit()


@pytest.fixture(scope="session", autouse=True)
def isolated_test_database():
    reset_database()
    yield


@pytest.fixture(autouse=True)
def clean_test_data():
    reset_database()
    reset_backups()
    yield
    reset_database()
    reset_backups()
