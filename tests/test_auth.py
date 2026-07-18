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


def test_setup_remains_available_when_only_seller_users_exist():
    create_login_user("Existing Seller", "seller", password="secret123")

    with TestClient(app) as client:
        login_page = client.get("/login")
        setup_page = client.get("/setup")
        setup = client.post(
            "/setup",
            data={
                "name": "Recovery Admin",
                "login_name": "recovery.admin",
                "password": "secret123",
            },
            follow_redirects=False,
        )
        users_page = client.get("/users")

    assert login_page.status_code == 200
    assert "Create first admin" in login_page.text
    assert setup_page.status_code == 200
    assert setup.status_code == 303
    assert setup.headers["location"] == "/"
    assert users_page.status_code == 200
    assert "Recovery Admin" in users_page.text


def test_setup_is_blocked_after_admin_exists():
    create_login_user("Existing Admin", "admin", password="secret123")

    with TestClient(app) as client:
        setup_page = client.get("/setup", follow_redirects=False)
        setup_post = client.post(
            "/setup",
            data={
                "name": "Second Admin",
                "login_name": "second.admin",
                "password": "secret123",
            },
            follow_redirects=False,
        )

    assert setup_page.status_code == 303
    assert setup_page.headers["location"] == "/login"
    assert setup_post.status_code == 409


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

    assert response.status_code == 403
    assert "Admin or Manager role required." in response.text


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

    assert response.status_code == 403
    assert "Read only users cannot modify data." in response.text


def test_seller_navigation_shows_language_but_hides_restricted_admin_links_when_auth_is_enabled():
    create_login_user("Nav Seller", "seller", password="secret123")

    with TestClient(app) as client:
        client.post(
            "/login",
            data={
                "login_name": "nav.seller",
                "password": "secret123",
                "next_url": "/",
            },
        )
        response = client.get("/")

    assert response.status_code == 200
    assert '<span class="nav-icon" aria-hidden="true">US</span>' not in response.text
    assert '<span class="nav-icon" aria-hidden="true">CR</span>' not in response.text
    assert '<span class="nav-icon" aria-hidden="true">AL</span>' not in response.text
    assert '<span class="nav-icon" aria-hidden="true">BU</span>' not in response.text
    assert '<span class="nav-icon" aria-hidden="true">ST</span>' in response.text
    assert '<span class="nav-icon" aria-hidden="true">LA</span>' in response.text
    assert 'href="/settings/language"' in response.text
    assert "Settings" in response.text


def test_seller_can_change_general_settings_and_language_without_admin_links():
    create_login_user("Language Seller", "seller", password="secret123")

    with TestClient(app) as client:
        client.post(
            "/login",
            data={
                "login_name": "language.seller",
                "password": "secret123",
                "next_url": "/",
            },
        )
        general_settings = client.get("/settings", follow_redirects=False)
        protected_statuses = client.get("/settings/statuses", follow_redirects=False)
        language_page = client.get("/settings/language")
        language_update = client.post(
            "/settings/language",
            data={"language": "fi", "next_url": "/"},
            follow_redirects=False,
        )
        dashboard = client.get("/")

    assert general_settings.status_code == 200
    assert "Dashboard visibility" in general_settings.text
    assert protected_statuses.status_code == 403
    assert language_page.status_code == 200
    assert "Language" in language_page.text
    assert language_update.status_code == 303
    assert dashboard.status_code == 200
    assert "FI" in dashboard.text


def test_admin_can_manage_users_and_roles_from_settings():
    create_login_user("Settings Admin", "admin", password="secret123")
    with SessionLocal() as db:
        ensure_default_roles(db)
        seller_role_id = db.query(Role).filter(Role.code == "seller").one().id
        manager_role_id = db.query(Role).filter(Role.code == "manager").one().id

    with TestClient(app) as client:
        client.post(
            "/login",
            data={
                "login_name": "settings.admin",
                "password": "secret123",
                "next_url": "/",
            },
        )
        settings_page = client.get("/settings")
        new_user_page = client.get("/users/new")
        create_response = client.post(
            "/users",
            data={
                "name": "Role User",
                "login_name": "role.user",
                "password": "secret123",
                "role_id": str(seller_role_id),
                "is_active": "on",
            },
            follow_redirects=False,
        )

    with SessionLocal() as db:
        created_user = db.query(User).filter(User.login_name == "role.user").one()

    with TestClient(app) as client:
        client.post(
            "/login",
            data={
                "login_name": "settings.admin",
                "password": "secret123",
                "next_url": "/",
            },
        )
        update_response = client.post(
            f"/users/{created_user.id}",
            data={
                "name": "Role User",
                "login_name": "role.user",
                "password": "",
                "role_id": str(manager_role_id),
                "is_active": "on",
                "can_receive_sales_credit": "on",
            },
            follow_redirects=False,
        )
        users_page = client.get("/users")

    with SessionLocal() as db:
        updated_user = db.get(User, created_user.id)

    assert settings_page.status_code == 200
    assert "User management" in settings_page.text
    assert 'href="/users"' in settings_page.text
    assert 'href="/users/new"' in settings_page.text
    assert new_user_page.status_code == 200
    assert "Role" in new_user_page.text
    assert create_response.status_code == 303
    assert update_response.status_code == 303
    assert users_page.status_code == 200
    assert "Role User" in users_page.text
    assert updated_user.role_id == manager_role_id
    assert updated_user.can_receive_sales_credit is True
