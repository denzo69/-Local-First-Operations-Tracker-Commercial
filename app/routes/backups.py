from datetime import datetime
from pathlib import Path
from shutil import copy2

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.config import get_settings
from app.template_context import templates

router = APIRouter(prefix="/backups", tags=["backups"])
settings = get_settings()


def backup_dir() -> Path:
    path = Path(settings.backup_dir)
    path.mkdir(exist_ok=True)
    return path


def database_path() -> Path:
    if settings.database_url.startswith("sqlite:///./"):
        return Path(settings.database_url.replace("sqlite:///./", "", 1))
    if settings.database_url.startswith("sqlite:///"):
        return Path(settings.database_url.replace("sqlite:///", "", 1))
    raise RuntimeError("Manual backups are currently supported only for SQLite.")


def list_backup_files() -> list[Path]:
    return sorted(backup_dir().glob("app-*.sqlite"), key=lambda path: path.stat().st_mtime, reverse=True)


@router.get("", response_class=HTMLResponse)
def list_backups(request: Request):
    return templates.TemplateResponse(
        "backups/list.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": "backups",
            "backups": list_backup_files(),
        },
    )


@router.post("")
def create_backup():
    source = database_path()
    if not source.exists():
        raise RuntimeError("Database file does not exist yet.")

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = backup_dir() / f"app-{timestamp}.sqlite"
    copy2(source, target)
    return RedirectResponse(url="/backups", status_code=303)
