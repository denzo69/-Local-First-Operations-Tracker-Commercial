from datetime import date, timedelta


def next_business_day(start_date: date) -> date:
    """Return the next weekday after start_date.

    Weekends are skipped. Public holidays are not handled in the MVP.
    """
    candidate = start_date + timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


def should_show_next_business_day_reminder(
    today: date,
    pickup_date: date | None,
    is_ready_or_packed: bool,
) -> bool:
    """Return True when a job needs attention before the next business day."""
    if pickup_date is None or is_ready_or_packed:
        return False
    return pickup_date == next_business_day(today)
