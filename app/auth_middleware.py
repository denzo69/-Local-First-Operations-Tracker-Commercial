from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import joinedload
from urllib.parse import parse_qs

from app.database import SessionLocal
from app.models import User
from app.services.auth_service import (
    COOKIE_NAME,
    auth_is_configured,
    get_session_user,
    path_requires_admin,
    should_skip_auth,
    user_has_role,
)
from app.services.security_service import (
    CSRF_FORM_FIELD,
    create_csrf_token,
    get_csrf_token_from_request,
    set_csrf_cookie,
    validate_csrf_token,
)

CSRF_EXEMPT_PATHS = {"/login", "/setup", "/health"}


async def authentication_middleware(request: Request, call_next):
    request.state.current_user = None
    path = request.url.path

    if should_skip_auth(path):
        return await call_next(request)

    with SessionLocal() as db:
        if not auth_is_configured(db):
            return await call_next(request)

        token = request.cookies.get(COOKIE_NAME)
        user = get_session_user(db, token)
        if user is not None:
            request.state.current_user = (
                db.query(User)
                .options(joinedload(User.role))
                .filter(User.id == user.id)
                .first()
            )

        if request.state.current_user is None:
            return _unauthenticated_response(request)

        csrf_token = get_csrf_token_from_request(request)
        if csrf_token is None:
            csrf_token = create_csrf_token()
            request.state.csrf_token = csrf_token
        else:
            request.state.csrf_token = csrf_token

        if _requires_csrf(request) and not await _csrf_is_valid(request):
            return _forbidden_response(request, "Invalid or missing CSRF token.")

        if request.method not in {"GET", "HEAD", "OPTIONS"} and user_has_role(
            request.state.current_user,
            {"read_only"},
        ):
            return _forbidden_response(request, "Read only users cannot modify data.")

        if path_requires_admin(path) and not user_has_role(
            request.state.current_user,
            {"admin", "manager"},
        ):
            return _forbidden_response(request, "Admin or Manager role required.")

    response = await call_next(request)
    if getattr(request.state, "csrf_token", None):
        set_csrf_cookie(response, request.state.csrf_token)
    return response


def _unauthenticated_response(request: Request):
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse(
            status_code=401,
            content={"error": {"status_code": 401, "detail": "Authentication required."}},
        )
    return RedirectResponse(url=f"/login?next={request.url.path}", status_code=303)


def _forbidden_response(request: Request, detail: str):
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse(
            status_code=403,
            content={"error": {"status_code": 403, "detail": detail}},
        )
    return RedirectResponse(url="/", status_code=303)


def _requires_csrf(request: Request) -> bool:
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return False
    path = request.url.path
    if path in CSRF_EXEMPT_PATHS or path.startswith("/static/"):
        return False
    return True


async def _csrf_is_valid(request: Request) -> bool:
    submitted_token = request.headers.get("x-csrf-token")
    if submitted_token is None:
        content_type = request.headers.get("content-type", "")
        if "application/x-www-form-urlencoded" in content_type:
            body = await request.body()
            form = parse_qs(body.decode("utf-8"), keep_blank_values=True)
            values = form.get(CSRF_FORM_FIELD, [])
            submitted_token = values[0] if values else None
            await _restore_request_body(request, body)
        elif "multipart/form-data" in content_type:
            body = await request.body()
            submitted_token = _extract_multipart_csrf_token(body)
            await _restore_request_body(request, body)
    return validate_csrf_token(request, str(submitted_token) if submitted_token else None)


async def _restore_request_body(request: Request, body: bytes) -> None:
    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    request._receive = receive


def _extract_multipart_csrf_token(body: bytes) -> str | None:
    marker = f'name="{CSRF_FORM_FIELD}"'.encode("utf-8")
    start = body.find(marker)
    if start == -1:
        return None
    value_start = body.find(b"\r\n\r\n", start)
    if value_start == -1:
        return None
    value_start += 4
    value_end = body.find(b"\r\n", value_start)
    if value_end == -1:
        return None
    return body[value_start:value_end].decode("utf-8")
