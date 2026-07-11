from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import Role, User
from app.services.auth_service import hash_password
from app.services.sales_service import ensure_default_roles


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
        logout = client.post("/logout", follow_redirects=False)
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
