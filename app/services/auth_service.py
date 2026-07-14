import base64
import hashlib
import hmac
import secrets
import time

from fastapi import Request
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Role, User

COOKIE_NAME = "ops_tracker_session"
SESSION_MAX_AGE_SECONDS = 60 * 60 * 12
PASSWORD_ITERATIONS = 260_000
AUTH_EXEMPT_PATHS = {
    "/login",
    "/logout",
    "/setup",
    "/health",
}
ADMIN_PATH_PREFIXES = (
    "/users",
    "/cash-registers",
    "/settings",
    "/backups",
    "/audit-log",
)
NON_ADMIN_SETTINGS_PATHS = {
    "/settings/language",
}


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    iterations = get_settings().password_iterations
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
    )
    return (
        f"pbkdf2_sha256${iterations}$"
        f"{base64.b64encode(salt).decode()}$"
        f"{base64.b64encode(digest).decode()}"
    )


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    try:
        algorithm, iterations, salt_b64, digest_b64 = password_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        expected = base64.b64decode(digest_b64.encode())
        salt = base64.b64decode(salt_b64.encode())
        actual = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            int(iterations),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def auth_is_configured(db: Session) -> bool:
    return (
        db.query(User)
        .filter(User.is_active.is_(True), User.password_hash.isnot(None))
        .first()
        is not None
    )


def authenticate_user(db: Session, login_name: str, password: str) -> User | None:
    user = (
        db.query(User)
        .filter(User.login_name == login_name.strip(), User.is_active.is_(True))
        .first()
    )
    if user is None or not verify_password(password, user.password_hash):
        return None
    return user


def create_session_token(user_id: int) -> str:
    issued_at = str(int(time.time()))
    payload = f"{user_id}:{issued_at}"
    signature = _sign(payload)
    return f"{payload}:{signature}"


def get_session_user(db: Session, token: str | None) -> User | None:
    if not token:
        return None
    parts = token.split(":")
    if len(parts) != 3:
        return None
    user_id, issued_at, signature = parts
    payload = f"{user_id}:{issued_at}"
    if not hmac.compare_digest(_sign(payload), signature):
        return None
    try:
        if int(issued_at) < int(time.time()) - SESSION_MAX_AGE_SECONDS:
            return None
        parsed_user_id = int(user_id)
    except ValueError:
        return None
    user = db.get(User, parsed_user_id)
    if user is None or not user.is_active or not user.password_hash:
        return None
    return user


def user_has_role(user: User | None, allowed_roles: set[str]) -> bool:
    return bool(user and user.role and user.role.code in allowed_roles)


def ensure_first_admin_role(db: Session) -> Role:
    role = db.query(Role).filter(Role.code == "admin").first()
    if role is None:
        role = Role(code="admin", name="Admin")
        db.add(role)
        db.flush()
    return role


def should_skip_auth(path: str) -> bool:
    return (
        path in AUTH_EXEMPT_PATHS
        or path.startswith("/static/")
        or path.startswith("/docs")
        or path.startswith("/openapi.json")
    )


def path_requires_admin(path: str) -> bool:
    if path in NON_ADMIN_SETTINGS_PATHS:
        return False
    return any(path == prefix or path.startswith(f"{prefix}/") for prefix in ADMIN_PATH_PREFIXES)


def request_current_user(request: Request) -> User | None:
    return getattr(request.state, "current_user", None)


def _sign(payload: str) -> str:
    secret_key = get_settings().secret_key.encode("utf-8")
    digest = hmac.new(secret_key, payload.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")
