import pytest

from app.database import SessionLocal
from app.models import AuditLog, Customer, Job, JobItem, Product, Setting

TEST_CUSTOMER_NAMES = {
    "Test Customer",
    "Job Customer",
    "Job Owner",
    "Dashboard Customer",
}

TEST_JOB_TITLES = {
    "Dashboard job",
}


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
        ).delete(
            synchronize_session=False
        )
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
        db.query(Setting).filter(
            Setting.key.in_(
                [
                    "company_name",
                    "company_business_id",
                    "company_address",
                    "company_phone",
                    "company_email",
                    "default_vat_percent",
                    "receipt_prefix",
                    "language",
                ]
            )
        ).delete(synchronize_session=False)
        db.commit()


@pytest.fixture(autouse=True)
def clean_test_data():
    cleanup_test_data()
    yield
    cleanup_test_data()
