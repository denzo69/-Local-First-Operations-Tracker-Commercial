from sqlalchemy.orm import Session

from app.models import Setting

DEFAULT_SETTINGS = {
    "company_name": "Local-First Operations Tracker",
    "company_business_id": "",
    "company_address": "",
    "company_phone": "",
    "company_email": "",
    "default_vat_percent": "24",
    "receipt_prefix": "LOT-",
    "language": "en",
}


def get_app_settings(db: Session) -> dict[str, str]:
    values = DEFAULT_SETTINGS.copy()
    rows = db.query(Setting).all()
    for row in rows:
        values[row.key] = row.value or ""
    return values


def set_app_settings(db: Session, values: dict[str, str]) -> None:
    for key, value in values.items():
        setting = db.query(Setting).filter(Setting.key == key).first()
        if setting is None:
            setting = Setting(key=key, value=value)
            db.add(setting)
        else:
            setting.value = value
    db.commit()
