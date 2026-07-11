import logging
from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

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
    request_id = getattr(request.state, "request_id", None)
    if _prefers_json(request):
        response = JSONResponse(
            status_code=status_code,
            content={
                "error": {
                    "status_code": status_code,
                    "title": _status_phrase(status_code),
                    "detail": detail,
                    "request_id": request_id,
                }
            },
        )
        if request_id:
            response.headers["x-request-id"] = request_id
        return response

    response = templates.TemplateResponse(
        "error.html",
        {
            "request": request,
            "page_title": _status_phrase(status_code),
            "status_code": status_code,
            "error_title": _status_phrase(status_code),
            "error_detail": detail,
            "request_id": request_id,
        },
        status_code=status_code,
    )
    if request_id:
        response.headers["x-request-id"] = request_id
    return response


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
