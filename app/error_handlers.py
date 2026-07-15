import logging
from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.database import get_db
from app.services.i18n_service import get_translations
from app.services.settings_service import get_app_settings
from app.template_context import templates

logger = logging.getLogger(__name__)


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)


async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    detail = str(exc.detail or _status_phrase(exc.status_code))
    logger.info(
        "HTTP error %s on %s %s: %s",
        exc.status_code,
        request.method,
        request.url.path,
        detail,
    )
    return _error_response(request, status_code=exc.status_code, detail=detail)


async def validation_exception_handler(request: Request, exc: RequestValidationError):
    logger.info(
        "Validation error on %s %s: %s",
        request.method,
        request.url.path,
        exc.errors(),
    )
    return _error_response(
        request,
        status_code=422,
        detail="Request validation failed.",
    )


async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception(
        "Unhandled error on %s %s",
        request.method,
        request.url.path,
    )
    return _error_response(
        request,
        status_code=500,
        detail="Unexpected server error.",
    )


def _error_response(request: Request, *, status_code: int, detail: str):
    if _prefers_json(request):
        return JSONResponse(
            status_code=status_code,
            content={
                "error": {
                    "status_code": status_code,
                    "title": _status_phrase(status_code),
                    "detail": detail,
                }
            },
        )

    title, localized_detail = _localized_error_text(request, status_code, detail)
    return templates.TemplateResponse(
        "error.html",
        {
            "request": request,
            "page_title": title,
            "status_code": status_code,
            "error_title": title,
            "error_detail": localized_detail,
        },
        status_code=status_code,
    )


def _localized_error_text(request: Request, status_code: int, detail: str) -> tuple[str, str]:
    db = next(get_db())
    try:
        language = get_app_settings(db).get("language", "en")
    finally:
        db.close()
    t = get_translations(language)
    title = t.get(f"error_{status_code}_title", _status_phrase(status_code))
    if status_code == 404 and detail == _status_phrase(404):
        detail = t.get("error_404_detail", detail)
    elif status_code == 422 and detail == "Request validation failed.":
        detail = t.get("error_validation_detail", detail)
    return title, detail


def _prefers_json(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    if not accept:
        return False
    return "application/json" in accept and "text/html" not in accept


def _status_phrase(status_code: int) -> str:
    try:
        return HTTPStatus(status_code).phrase
    except ValueError:
        return "Application error"
