from fastapi.testclient import TestClient
import re

from app.config import get_settings, validate_runtime_configuration
from app.database import SessionLocal
from app.main import app
from app.models import AuditLog, Role, User
from app.services.auth_service import hash_password
from app.services.sales_service import ensure_default_roles
from app.services.security_service import reset_login_throttle


def csrf_token_from_html(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match
    return match.group(1)


def create_login_user(name: str, role_code: str, password: str = "secret123") -> User:
    with SessionLocal() as db:
        ensure_default_roles(db)
        role = db.query(Role).filter(Role.code == role_code).one()
        user = User(
            name=name,
            login_name=name.lower().replace(" ", "."),
            password_hash=hash_password(password),
            role=role,
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user


def test_first_admin_setup_enables_login_and_redirects_home():
    with TestClient(app) as client:
        response = client.post(
            "/setup",
            data={
                "name": "First Admin",
                "login_name": "admin",
                "password": "secret123",
            },
            follow_redirects=False,
        )
        home = client.get("/")

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert home.status_code == 200
    assert "First Admin" in home.text


def test_configured_auth_redirects_anonymous_user_to_login():
    create_login_user("Auth Admin", "admin")

    with TestClient(app) as client:
        response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=/"


def test_login_and_logout_flow():
    create_login_user("Login Admin", "admin", password="secret123")

    with TestClient(app) as client:
        login = client.post(
            "/login",
            data={
                "login_name": "login.admin",
                "password": "secret123",
                "next_url": "/",
            },
            follow_redirects=False,
        )
        home = client.get("/")
        logout = client.post(
            "/logout",
            data={"csrf_token": csrf_token_from_html(home.text)},
            follow_redirects=False,
        )
        after_logout = client.get("/", follow_redirects=False)

    assert login.status_code == 303
    assert home.status_code == 200
    assert "Login Admin" in home.text
    assert logout.status_code == 303
    assert after_logout.status_code == 303
    assert after_logout.headers["location"] == "/login?next=/"


def test_seller_cannot_open_admin_user_page_when_auth_is_enabled():
    create_login_user("Seller User", "seller", password="secret123")

    with TestClient(app) as client:
        client.post(
            "/login",
            data={
                "login_name": "seller.user",
                "password": "secret123",
                "next_url": "/",
            },
        )
        response = client.get("/users", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_read_only_user_cannot_modify_data_when_auth_is_enabled():
    create_login_user("Read Only", "read_only", password="secret123")

    with TestClient(app) as client:
        client.post(
            "/login",
            data={
                "login_name": "read.only",
                "password": "secret123",
                "next_url": "/",
            },
        )
        response = client.post(
            "/customers",
            data={"name": "Should Not Save"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_non_development_default_secret_key_is_rejected(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SECRET_KEY", "change-me-local-development-secret")
    try:
        try:
            validate_runtime_configuration()
        except RuntimeError as exc:
            assert "SECRET_KEY" in str(exc)
        else:  # pragma: no cover - assertion clarity
            raise AssertionError("default production SECRET_KEY was accepted")
    finally:
        monkeypatch.setenv("APP_ENV", "development")
        get_settings.cache_clear()


def test_session_cookie_uses_configured_security_attributes(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "true")
    monkeypatch.setenv("SESSION_COOKIE_SAMESITE", "strict")
    monkeypatch.setenv("SESSION_MAX_AGE_SECONDS", "123")
    create_login_user("Cookie Admin", "admin", password="secret123")

    try:
        with TestClient(app) as client:
            response = client.post(
                "/login",
                data={
                    "login_name": "cookie.admin",
                    "password": "secret123",
                    "next_url": "/",
                },
                follow_redirects=False,
            )
    finally:
        monkeypatch.delenv("SESSION_COOKIE_SECURE", raising=False)
        monkeypatch.delenv("SESSION_COOKIE_SAMESITE", raising=False)
        monkeypatch.delenv("SESSION_MAX_AGE_SECONDS", raising=False)
        get_settings.cache_clear()

    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=strict" in cookie
    assert "Max-Age=123" in cookie
    assert "Path=/" in cookie


def test_csrf_required_for_authenticated_write_requests():
    create_login_user("Csrf Admin", "admin", password="secret123")

    with TestClient(app) as client:
        client.post(
            "/login",
            data={
                "login_name": "csrf.admin",
                "password": "secret123",
                "next_url": "/",
            },
        )
        missing = client.post("/customers", data={"name": "Blocked"}, follow_redirects=False)
        home = client.get("/")
        invalid = client.post(
            "/customers",
            data={"name": "Blocked", "csrf_token": "bad-token"},
            follow_redirects=False,
        )
        valid = client.post(
            "/customers",
            data={"name": "Allowed", "csrf_token": csrf_token_from_html(home.text)},
            follow_redirects=False,
        )

    assert missing.status_code == 303
    assert missing.headers["location"] == "/"
    assert invalid.status_code == 303
    assert invalid.headers["location"] == "/"
    assert valid.status_code == 303
    assert valid.headers["location"].startswith("/customers/")


def test_repeated_failed_logins_are_throttled_and_audited():
    reset_login_throttle()
    create_login_user("Throttle Admin", "admin", password="secret123")

    with TestClient(app) as client:
        statuses = [
            client.post(
                "/login",
                data={
                    "login_name": "throttle.admin",
                    "password": "wrong",
                    "next_url": "/",
                },
            ).status_code
            for _ in range(6)
        ]

    with SessionLocal() as db:
        failed = db.query(AuditLog).filter(AuditLog.event_type == "auth.login_failed").count()
        throttled = db.query(AuditLog).filter(AuditLog.event_type == "auth.login_throttled").count()

    assert statuses[:5] == [401, 401, 401, 401, 401]
    assert statuses[5] == 429
    assert failed == 5
    assert throttled == 1
