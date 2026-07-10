from datetime import date

from sqlalchemy.orm import Session

from app.models import Job, Setting
from app.services.settings_service import get_app_settings


def format_receipt_number(year: int, sequence: int, prefix: str = "", padding: int = 6) -> str:
    """Format a receipt number using year and a padded sequence.

    Example: 2026-000001 or PESULA-2026-000001.
    """
    base = f"{year}-{sequence:0{padding}d}"
    return f"{prefix}{base}" if prefix else base


def _set_setting(db: Session, key: str, value: str) -> None:
    setting = db.query(Setting).filter(Setting.key == key).first()
    if setting is None:
        db.add(Setting(key=key, value=value))
    else:
        setting.value = value


def allocate_receipt_number(db: Session, receipt_date: date | None = None) -> str:
    settings = get_app_settings(db)
    current_year = (receipt_date or date.today()).year
    annual_reset = settings.get("receipt_annual_reset", "false").lower() == "true"
    sequence_year = settings.get("receipt_sequence_year") or str(current_year)
    sequence = int(settings.get("next_receipt_sequence") or "1")
    padding = int(settings.get("receipt_padding") or "6")
    prefix = settings.get("receipt_prefix", "")

    if annual_reset and sequence_year != str(current_year):
        sequence = 1
        sequence_year = str(current_year)

    while True:
        receipt_number = format_receipt_number(
            current_year,
            sequence,
            prefix=prefix,
            padding=padding,
        )
        exists = db.query(Job.id).filter(Job.receipt_number == receipt_number).first()
        if not exists:
            break
        sequence += 1

    _set_setting(db, "next_receipt_sequence", str(sequence + 1))
    _set_setting(db, "receipt_sequence_year", sequence_year)
    return receipt_number
