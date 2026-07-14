from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import joinedload

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
from app.template_context import templates


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

        if (
            request.method not in {"GET", "HEAD", "OPTIONS"}
            and path != "/settings/language"
            and user_has_role(
            request.state.current_user,
            {"read_only"},
            )
        ):
            return _forbidden_response(request, "Read only users cannot modify data.")

        if path_requires_admin(path) and not user_has_role(
            request.state.current_user,
            {"admin", "manager"},
        ):
            return _forbidden_response(request, "Admin or Manager role required.")

    return await call_next(request)


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
    return templates.TemplateResponse(
        "error.html",
        {
            "request": request,
            "page_title": "Forbidden",
            "status_code": 403,
            "error_title": "Forbidden",
            "error_detail": detail,
        },
        status_code=403,
    )
