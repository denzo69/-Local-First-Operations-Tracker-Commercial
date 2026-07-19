from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import Role, User
from app.services.audit_service import log_audit_event
from app.services.auth_service import hash_password
from app.services.sales_service import ensure_default_roles
from app.template_context import templates

router = APIRouter(prefix="/users", tags=["users"])
settings = get_settings()


@router.get("", response_class=HTMLResponse)
def list_users(request: Request, db: Session = Depends(get_db)):
    ensure_default_roles(db)
    users = db.query(User).order_by(User.name.asc()).all()
    return templates.TemplateResponse(
        "users/list.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": "users",
            "users": users,
        },
    )


@router.get("/new", response_class=HTMLResponse)
def new_user(request: Request, db: Session = Depends(get_db)):
    roles = ensure_default_roles(db)
    return templates.TemplateResponse(
        "users/form.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": "users",
            "roles": roles,
            "user": None,
            "form_action": "/users",
        },
    )


@router.post("")
def create_user(
    name: str = Form(...),
    login_name: str = Form(""),
    password: str = Form(""),
    role_id: int = Form(...),
    is_active: str | None = Form(None),
    can_receive_sales_credit: str | None = Form(None),
    db: Session = Depends(get_db),
):
    if not name.strip():
        raise HTTPException(status_code=400, detail="User name is required")
    roles = ensure_default_roles(db)
    if role_id not in {role.id for role in roles}:
        raise HTTPException(status_code=400, detail="Selected role was not found")
    login_value = login_name.strip() or None
    if login_value and db.query(User).filter(User.login_name == login_value).first():
        raise HTTPException(status_code=400, detail="Login name is already in use")
    if password and len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    user = User(
        name=name.strip(),
        login_name=login_value,
        password_hash=hash_password(password) if password else None,
        role_id=role_id,
        is_active=is_active == "on",
        can_receive_sales_credit=can_receive_sales_credit == "on",
    )
    db.add(user)
    db.flush()
    log_audit_event(
        db,
        event_type="user.created",
        entity_type="user",
        entity_id=user.id,
        description=f"User created: {user.name}.",
    )
    db.commit()
    return RedirectResponse(url="/users", status_code=303)


@router.get("/{user_id}/edit", response_class=HTMLResponse)
def edit_user(user_id: int, request: Request, db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return templates.TemplateResponse(
        "users/form.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": "users",
            "roles": ensure_default_roles(db),
            "user": user,
            "form_action": f"/users/{user.id}",
        },
    )


@router.post("/{user_id}")
def update_user(
    user_id: int,
    name: str = Form(...),
    login_name: str = Form(""),
    password: str = Form(""),
    role_id: int = Form(...),
    is_active: str | None = Form(None),
    can_receive_sales_credit: str | None = Form(None),
    db: Session = Depends(get_db),
):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if not name.strip():
        raise HTTPException(status_code=400, detail="User name is required")
    roles = ensure_default_roles(db)
    if role_id not in {role.id for role in roles}:
        raise HTTPException(status_code=400, detail="Selected role was not found")
    selected_role = next(role for role in roles if role.id == role_id)
    removing_admin_access = (
        user.role is not None
        and user.role.code == "admin"
        and (selected_role.code != "admin" or is_active != "on")
    )
    if removing_admin_access:
        other_active_admin = (
            db.query(User)
            .join(User.role)
            .filter(
                User.id != user.id,
                User.is_active.is_(True),
                User.password_hash.isnot(None),
                Role.code == "admin",
            )
            .first()
        )
        if other_active_admin is None:
            raise HTTPException(
                status_code=400,
                detail="Create another active Admin before removing the last Admin's access.",
            )
    login_value = login_name.strip() or None
    if login_value:
        existing = db.query(User).filter(User.login_name == login_value, User.id != user.id).first()
        if existing:
            raise HTTPException(status_code=400, detail="Login name is already in use")
    if password and len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    user.name = name.strip()
    user.login_name = login_value
    if password:
        user.password_hash = hash_password(password)
    user.role_id = role_id
    user.is_active = is_active == "on"
    user.can_receive_sales_credit = can_receive_sales_credit == "on"
    db.commit()
    return RedirectResponse(url="/users", status_code=303)
