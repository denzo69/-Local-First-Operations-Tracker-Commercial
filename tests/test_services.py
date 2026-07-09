from datetime import date

from app.services.receipt_number_service import format_receipt_number
from app.services.reminder_service import next_business_day, should_show_next_business_day_reminder


def test_receipt_number_default_format():
    assert format_receipt_number(2026, 1) == "2026-000001"


def test_receipt_number_with_prefix():
    assert format_receipt_number(2026, 127, prefix="PESULA-") == "PESULA-2026-000127"


def test_next_business_day_skips_weekend():
    assert next_business_day(date(2026, 7, 10)) == date(2026, 7, 13)


def test_next_business_day_reminder_for_unpacked_job():
    assert should_show_next_business_day_reminder(
        today=date(2026, 7, 10),
        pickup_date=date(2026, 7, 13),
        is_ready_or_packed=False,
    )


def test_no_reminder_for_ready_or_packed_job():
    assert not should_show_next_business_day_reminder(
        today=date(2026, 7, 10),
        pickup_date=date(2026, 7, 13),
        is_ready_or_packed=True,
    )
