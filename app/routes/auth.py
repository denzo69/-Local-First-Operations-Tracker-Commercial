from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.services.audit_service import log_audit_event
from app.services.auth_service import (
    COOKIE_NAME,
    authenticate_user,
    auth_is_configured,
    create_session_token,
    ensure_first_admin_role,
    hash_password,
)
from app.template_context import templates

router = APIRouter(tags=["auth"])


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "/", db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        "auth/login.html",
        {
            "request": request,
            "page_title": "Login",
            "next_url": _safe_next(next),
            "auth_configured": auth_is_configured(db),
            "error_key": None,
        },
    )


@router.post("/login")
def login(
    request: Request,
    login_name: str = Form(...),
    password: str = Form(...),
    next_url: str = Form("/"),
    db: Session = Depends(get_db),
):
    user = authenticate_user(db, login_name, password)
    if user is None:
        return templates.TemplateResponse(
            "auth/login.html",
            {
                "request": request,
                "page_title": "Login",
                "next_url": _safe_next(next_url),
                "auth_configured": auth_is_configured(db),
                "error_key": "invalid_login",
            },
            status_code=401,
        )

    response = RedirectResponse(url=_safe_next(next_url), status_code=303)
    response.set_cookie(
        COOKIE_NAME,
        create_session_token(user.id),
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 12,
    )
    return response


@router.post("/logout")
def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(COOKIE_NAME)
    return response


@router.get("/setup", response_class=HTMLResponse)
def setup_form(request: Request, db: Session = Depends(get_db)):
    if auth_is_configured(db):
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse(
        "auth/setup.html",
        {
            "request": request,
            "page_title": "Create admin",
            "error_key": None,
        },
    )


@router.post("/setup")
def create_first_admin(
    request: Request,
    name: str = Form(...),
    login_name: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    if auth_is_configured(db):
        raise HTTPException(status_code=409, detail="Authentication is already configured.")
    if len(password) < 8:
        return templates.TemplateResponse(
            "auth/setup.html",
            {
                "request": request,
                "page_title": "Create admin",
                "error_key": "password_too_short",
            },
            status_code=400,
        )
    if not name.strip() or not login_name.strip():
        raise HTTPException(status_code=400, detail="Name and login name are required.")

    role = ensure_first_admin_role(db)
    user = User(
        name=name.strip(),
        login_name=login_name.strip(),
        password_hash=hash_password(password),
        role_id=role.id,
        is_active=True,
    )
    db.add(user)
    db.flush()
    log_audit_event(
        db,
        event_type="auth.setup",
        entity_type="user",
        entity_id=user.id,
        description=f"Initial admin user created: {user.name}.",
    )
    db.commit()

    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(
        COOKIE_NAME,
        create_session_token(user.id),
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 12,
    )
    return response


def _safe_next(next_url: str) -> str:
    if not next_url.startswith("/") or next_url.startswith("//"):
        return "/"
    return next_url
