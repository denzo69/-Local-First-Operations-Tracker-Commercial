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
    "receipt_padding": "6",
    "receipt_annual_reset": "false",
    "next_receipt_sequence": "1",
    "receipt_sequence_year": "",
    "language": "en",
    "sale_seller_selection_mode": "shift_owner",
}

SALE_SELLER_SELECTION_MODES = {
    "authenticated_user": "Authenticated user",
    "shift_owner": "Shift owner",
    "selectable_active_seller": "Selectable active seller",
}

SUPPORTED_LANGUAGES = {
    "en": "English",
    "fi": "Suomi",
}


def get_app_settings(db: Session) -> dict[str, str]:
    values = DEFAULT_SETTINGS.copy()
    rows = db.query(Setting).all()
    for row in rows:
        values[row.key] = row.value or ""
    return values


def get_current_language(db: Session) -> str:
    language = get_app_settings(db).get("language") or DEFAULT_SETTINGS["language"]
    if language not in SUPPORTED_LANGUAGES:
        return DEFAULT_SETTINGS["language"]
    return language


def set_app_settings(db: Session, values: dict[str, str]) -> None:
    for key, value in values.items():
        setting = db.query(Setting).filter(Setting.key == key).first()
        if setting is None:
            setting = Setting(key=key, value=value)
            db.add(setting)
        else:
            setting.value = value
    db.commit()
