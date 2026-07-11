import logging
import uuid
from collections.abc import Callable

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import get_settings
from app.template_context import templates

logger = logging.getLogger("app.errors")


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s [request_id=%(request_id)s] %(message)s",
    )
    for handler in logging.getLogger().handlers:
        handler.addFilter(RequestIdFilter())


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = "-"
        return True


def get_request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "unknown")


def wants_json(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    return "application/json" in accept.lower()


def error_payload(status_code: int, message: str, request_id: str, details=None) -> dict:
    payload = {
        "error": {
            "status_code": status_code,
            "message": message,
            "request_id": request_id,
        }
    }
    if details is not None:
        payload["error"]["details"] = details
    return payload


def template_error_response(
    request: Request,
    *,
    status_code: int,
    title: str,
    message: str,
    template_name: str,
    details=None,
) -> Response:
    request_id = get_request_id(request)
    if wants_json(request):
        return JSONResponse(
            error_payload(status_code, message, request_id, details),
            status_code=status_code,
            headers={"X-Request-ID": request_id},
        )
    return templates.TemplateResponse(
        template_name,
        {
            "request": request,
            "app_name": get_settings().app_name,
            "active_page": "error",
            "status_code": status_code,
            "title": title,
            "message": message,
            "request_id": request_id,
            "details": details,
        },
        status_code=status_code,
        headers={"X-Request-ID": request_id},
    )


async def request_id_middleware(request: Request, call_next: Callable) -> Response:
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = request_id
    try:
        response = await call_next(request)
    except Exception:
        logger.exception(
            "Unhandled application error",
            extra={"request_id": request_id},
        )
        response = template_error_response(
            request,
            status_code=500,
            title="Unexpected error",
            message="Something went wrong. Please try again or contact support with the request ID.",
            template_name="errors/500.html",
        )
    response.headers["X-Request-ID"] = request_id
    return response


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> Response:
    status_code = exc.status_code
    message = str(exc.detail or "Request failed.")
    if status_code == 404:
        return template_error_response(
            request,
            status_code=404,
            title="Page not found",
            message="The page you requested could not be found.",
            template_name="errors/404.html",
        )
    return template_error_response(
        request,
        status_code=status_code,
        title="Request error",
        message=message,
        template_name="errors/422.html" if status_code == 422 else "errors/500.html",
    )


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> Response:
    details = exc.errors()
    return template_error_response(
        request,
        status_code=422,
        title="Validation error",
        message="Some submitted data was not valid.",
        template_name="errors/422.html",
        details=details,
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> Response:
    request_id = get_request_id(request)
    logger.exception(
        "Unhandled application error",
        extra={"request_id": request_id},
    )
    return template_error_response(
        request,
        status_code=500,
        title="Unexpected error",
        message="Something went wrong. Please try again or contact support with the request ID.",
        template_name="errors/500.html",
    )


def install_error_handling(app: FastAPI) -> None:
    app.middleware("http")(request_id_middleware)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
