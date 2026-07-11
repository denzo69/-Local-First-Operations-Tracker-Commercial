import base64
import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass

from fastapi import Request
from fastapi.responses import Response

from app.config import get_settings


CSRF_FORM_FIELD = "csrf_token"
_login_failures: dict[str, list[float]] = {}


@dataclass(frozen=True)
class CookieSettings:
    secure: bool
    samesite: str
    max_age: int
    path: str = "/"


def session_cookie_settings() -> CookieSettings:
    settings = get_settings()
    return CookieSettings(
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
        max_age=settings.session_max_age_seconds,
    )


def set_session_cookie(response: Response, name: str, value: str) -> None:
    cookie = session_cookie_settings()
    response.set_cookie(
        name,
        value,
        httponly=True,
        secure=cookie.secure,
        samesite=cookie.samesite,
        max_age=cookie.max_age,
        path=cookie.path,
    )


def clear_session_cookie(response: Response, name: str) -> None:
    cookie = session_cookie_settings()
    response.delete_cookie(
        name,
        secure=cookie.secure,
        samesite=cookie.samesite,
        path=cookie.path,
    )


def issue_csrf_cookie(request: Request, response: Response) -> str:
    token = get_csrf_token_from_request(request)
    if token is None:
        token = create_csrf_token()
    _set_csrf_cookie(response, token)
    return token


def get_csrf_token_from_request(request: Request) -> str | None:
    state_token = getattr(request.state, "csrf_token", None)
    if state_token:
        return state_token
    signed = request.cookies.get(get_settings().csrf_cookie_name)
    if not signed:
        return None
    token, signature = _split_signed_value(signed)
    if not token or not signature:
        return None
    if not hmac.compare_digest(_sign(token), signature):
        return None
    return token


def create_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def set_csrf_cookie(response: Response, token: str) -> None:
    _set_csrf_cookie(response, token)


def validate_csrf_token(request: Request, submitted_token: str | None) -> bool:
    cookie_token = get_csrf_token_from_request(request)
    return bool(
        cookie_token
        and submitted_token
        and hmac.compare_digest(cookie_token, submitted_token)
    )


def _set_csrf_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    cookie = session_cookie_settings()
    response.set_cookie(
        settings.csrf_cookie_name,
        f"{token}:{_sign(token)}",
        httponly=True,
        secure=cookie.secure,
        samesite=cookie.samesite,
        max_age=cookie.max_age,
        path=cookie.path,
    )


def login_throttle_key(request: Request, login_name: str) -> str:
    client_host = request.client.host if request.client else "unknown"
    normalized_login = login_name.strip().lower()
    digest = hashlib.sha256(normalized_login.encode("utf-8")).hexdigest()[:16]
    return f"{client_host}:{digest}"


def login_is_throttled(key: str) -> bool:
    settings = get_settings()
    now = time.time()
    recent = [
        timestamp
        for timestamp in _login_failures.get(key, [])
        if timestamp >= now - settings.login_throttle_window_seconds
    ]
    _login_failures[key] = recent
    return len(recent) >= settings.login_throttle_max_attempts


def record_failed_login(key: str) -> None:
    settings = get_settings()
    now = time.time()
    recent = [
        timestamp
        for timestamp in _login_failures.get(key, [])
        if timestamp >= now - settings.login_throttle_window_seconds
    ]
    recent.append(now)
    _login_failures[key] = recent


def clear_failed_logins(key: str) -> None:
    _login_failures.pop(key, None)


def reset_login_throttle() -> None:
    _login_failures.clear()


def _split_signed_value(value: str) -> tuple[str | None, str | None]:
    if ":" not in value:
        return None, None
    token, signature = value.rsplit(":", 1)
    return token, signature


def _sign(value: str) -> str:
    secret_key = get_settings().secret_key.encode("utf-8")
    digest = hmac.new(secret_key, value.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")
