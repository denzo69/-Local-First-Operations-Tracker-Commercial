from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.database import get_db
from app.services.i18n_service import get_translations, translate_status
from app.services.settings_service import get_app_settings


def inject_global_template_context(request: Request) -> dict:
    db = next(get_db())
    try:
        app_settings = get_app_settings(db)
        language = app_settings.get("language", "en")
        return {
            "language": language,
            "t": get_translations(language),
            "status_label": lambda status_name: translate_status(status_name, language),
        }
    finally:
        db.close()


_jinja_templates = Jinja2Templates(
    directory="app/templates",
    context_processors=[inject_global_template_context],
)


class AppTemplates:
    def TemplateResponse(self, name: str, context: dict, **kwargs):
        request = context["request"]
        return _jinja_templates.TemplateResponse(request, name, context, **kwargs)


templates = AppTemplates()
