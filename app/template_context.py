from datetime import date
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.database import get_db
from app.models import Shift
from app.services.i18n_service import get_translations, translate_status
from app.services.settings_service import get_app_settings


def inject_global_template_context(request: Request) -> dict:
    db = next(get_db())
    try:
        app_settings = get_app_settings(db)
        language = app_settings.get("language", "en")
        current_user = getattr(request.state, "current_user", None)
        current_role_code = (
            current_user.role.code
            if current_user is not None and current_user.role is not None
            else None
        )
        return {
            "language": language,
            "t": get_translations(language),
            "current_date": date.today(),
            "current_user": current_user,
            "is_authenticated": current_user is not None,
            "can_manage_administration": current_user is None or current_role_code in {"admin", "manager"},
            "current_operator_label": (
                current_user.name
                if current_user is not None
                else get_translations(language)["operator_placeholder"]
            ),
            "open_shift_count": db.query(Shift).filter(Shift.status == "open").count(),
            "status_label": lambda status_name: translate_status(status_name, language),
        }
    finally:
        db.close()


_jinja_templates = Jinja2Templates(
    directory=Path(__file__).resolve().parent / "templates",
    context_processors=[inject_global_template_context],
)


class AppTemplates:
    def TemplateResponse(self, name: str, context: dict, **kwargs):
        request = context["request"]
        return _jinja_templates.TemplateResponse(request, name, context, **kwargs)


templates = AppTemplates()
