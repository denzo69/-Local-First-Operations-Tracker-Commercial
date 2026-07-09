from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.database import init_db

settings = get_settings()

templates = Jinja2Templates(directory="app/templates")

app = FastAPI(title=settings.app_name)
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok", "app": settings.app_name}


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "cards": [
                {"label": "Overdue", "value": 0, "tone": "danger"},
                {"label": "Due today", "value": 0, "tone": "warning"},
                {"label": "Due tomorrow", "value": 0, "tone": "info"},
                {"label": "Ready for pickup", "value": 0, "tone": "success"},
            ],
        },
    )
