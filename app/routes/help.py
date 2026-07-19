from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.config import get_settings
from app.template_context import templates

router = APIRouter(tags=["help"])
settings = get_settings()


@router.get("/help", response_class=HTMLResponse)
def help_page(request: Request):
    return templates.TemplateResponse(
        "help/index.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": "help",
            "page_title": "Help",
        },
    )
