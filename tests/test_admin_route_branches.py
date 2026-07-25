from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import CashRegister, Customer, Job, JobStatus, Role, User
from app.services.auth_service import hash_password
from app.services.sales_service import ensure_default_roles


def _role_id(code: str) -> int:
    with SessionLocal() as db:
        ensure_default_roles(db)
        return db.query(Role).filter(Role.code == code).one().id


def _create_user(name: str = "Existing User", role_code: str = "seller") -> User:
    with SessionLocal() as db:
        ensure_default_roles(db)
        role = db.query(Role).filter(Role.code == role_code).one()
        user = User(
            name=name,
            login_name=name.lower().replace(" ", "."),
            role=role,
            is_active=True,
            can_receive_sales_credit=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user


def _create_login_user(name: str = "Admin User", role_code: str = "admin", password: str = "secret123") -> User:
    with SessionLocal() as db:
        ensure_default_roles(db)
        role = db.query(Role).filter(Role.code == role_code).one()
        user = User(
            name=name,
            login_name=name.lower().replace(" ", "."),
            password_hash=hash_password(password),
            role=role,
            is_active=True,
            can_receive_sales_credit=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user


def test_user_management_pages_and_create_update_branches():
    _create_login_user()
    seller_role_id = _role_id("seller")
    manager_role_id = _role_id("manager")

    with TestClient(app) as client:
        client.post(
            "/login",
            data={"login_name": "admin.user", "password": "secret123", "next_url": "/"},
        )
        list_response = client.get("/users")
        new_response = client.get("/users/new")
        blank_name = client.post(
            "/users",
            data={"name": " ", "role_id": seller_role_id},
        )
        invalid_role = client.post(
            "/users",
            data={"name": "Invalid Role", "role_id": 999999},
        )
        short_password = client.post(
            "/users",
            data={"name": "Short Password", "role_id": seller_role_id, "password": "short"},
        )
        create_response = client.post(
            "/users",
            data={
                "name": "Route User",
                "login_name": "route.user",
                "password": "secret123",
                "role_id": seller_role_id,
                "is_active": "on",
                "can_receive_sales_credit": "on",
            },
            follow_redirects=False,
        )
        with SessionLocal() as db:
            created_user_id = db.query(User).filter(User.login_name == "route.user").one().id
        duplicate_login = client.post(
            "/users",
            data={"name": "Duplicate", "login_name": "route.user", "role_id": seller_role_id},
        )
        edit_response = client.get(f"/users/{created_user_id}/edit")
        missing_edit = client.get("/users/999999/edit")
        update_blank = client.post(
            f"/users/{created_user_id}",
            data={"name": " ", "role_id": seller_role_id},
        )
        update_invalid_role = client.post(
            f"/users/{created_user_id}",
            data={"name": "Route User", "role_id": 999999},
        )
        update_short_password = client.post(
            f"/users/{created_user_id}",
            data={"name": "Route User", "role_id": seller_role_id, "password": "short"},
        )
        update_response = client.post(
            f"/users/{created_user_id}",
            data={
                "name": "Updated Route User",
                "login_name": "updated.route.user",
                "password": "newsecret123",
                "role_id": manager_role_id,
            },
            follow_redirects=False,
        )
        missing_update = client.post(
            "/users/999999",
            data={"name": "Missing User", "role_id": seller_role_id},
        )

    assert list_response.status_code == 200
    assert new_response.status_code == 200
    assert blank_name.status_code == 400
    assert invalid_role.status_code == 400
    assert short_password.status_code == 400
    assert create_response.status_code == 303
    assert duplicate_login.status_code == 400
    assert edit_response.status_code == 200
    assert missing_edit.status_code == 404
    assert update_blank.status_code == 400
    assert update_invalid_role.status_code == 400
    assert update_short_password.status_code == 400
    assert update_response.status_code == 303
    assert missing_update.status_code == 404


def test_user_update_rejects_login_name_already_used_by_another_user():
    seller_role_id = _role_id("seller")
    first = _create_user("First User")
    second = _create_user("Second User")

    with TestClient(app) as client:
        response = client.post(
            f"/users/{second.id}",
            data={
                "name": "Second User",
                "login_name": first.login_name,
                "role_id": seller_role_id,
            },
        )

    assert response.status_code == 400
    assert "already in use" in response.text


def test_cash_register_routes_cover_success_and_validation_branches():
    with TestClient(app) as client:
        list_response = client.get("/cash-registers")
        new_response = client.get("/cash-registers/new")
        blank_name = client.post("/cash-registers", data={"name": " "})
        create_response = client.post(
            "/cash-registers",
            data={"name": "Front Counter", "location": "Shop", "is_active": "on"},
            follow_redirects=False,
        )
        edit_response = client.get("/cash-registers/1/edit")
        missing_edit = client.get("/cash-registers/999999/edit")
        update_blank = client.post("/cash-registers/1", data={"name": " "})
        update_response = client.post(
            "/cash-registers/1",
            data={"name": "Back Counter", "location": "", "is_active": ""},
            follow_redirects=False,
        )
        missing_update = client.post("/cash-registers/999999", data={"name": "Missing"})

    assert list_response.status_code == 200
    assert new_response.status_code == 200
    assert blank_name.status_code == 400
    assert create_response.status_code == 303
    assert edit_response.status_code == 200
    assert missing_edit.status_code == 404
    assert update_blank.status_code == 400
    assert update_response.status_code == 303
    assert missing_update.status_code == 404


def test_customer_routes_cover_invalid_missing_update_and_delete_success():
    with TestClient(app) as client:
        blank_create = client.post("/customers", data={"name": " "})
        invalid_discount = client.post(
            "/customers",
            data={"name": "Bad Discount", "default_discount_percent": "not-number"},
        )
        create_response = client.post(
            "/customers",
            data={"name": "Disposable Customer", "default_discount_percent": "0"},
            follow_redirects=False,
        )
        customer_url = create_response.headers["location"]
        edit_response = client.get(f"{customer_url}/edit")
        missing_detail = client.get("/customers/999999")
        missing_edit = client.get("/customers/999999/edit")
        missing_update = client.post("/customers/999999", data={"name": "Missing"})
        blank_update = client.post(f"{customer_url}", data={"name": " "})
        update_response = client.post(
            f"{customer_url}",
            data={
                "name": "Disposable Customer Updated",
                "phone": "040",
                "email": "customer@example.com",
                "address": "Street 1",
                "company_name": "Customer Co",
                "business_id": "1234567-8",
                "default_discount_percent": "15",
                "notes": "Updated",
            },
            follow_redirects=False,
        )
        delete_response = client.post(f"{customer_url}/delete", follow_redirects=False)
        missing_delete = client.post("/customers/999999/delete")

    assert blank_create.status_code == 400
    assert invalid_discount.status_code == 400
    assert edit_response.status_code == 200
    assert missing_detail.status_code == 404
    assert missing_edit.status_code == 404
    assert missing_update.status_code == 404
    assert blank_update.status_code == 400
    assert update_response.status_code == 303
    assert delete_response.status_code == 303
    assert missing_delete.status_code == 404


def test_shift_routes_cover_missing_and_validation_branches():
    with SessionLocal() as db:
        ensure_default_roles(db)
        role = db.query(Role).filter(Role.code == "seller").one()
        seller = User(name="Shift Seller", role=role, is_active=True)
        register = CashRegister(name="Shift Register", is_active=True)
        db.add_all([seller, register])
        db.commit()
        seller_id = seller.id
        register_id = register.id

    with TestClient(app) as client:
        list_response = client.get("/shifts")
        open_form = client.get("/shifts/open")
        invalid_create = client.post(
            "/shifts",
            data={
                "seller_id": seller_id,
                "cash_register_id": register_id,
                "business_date": "not-a-date",
                "starting_cash": "0",
            },
        )
        missing_detail = client.get("/shifts/999999")
        missing_movement = client.post(
            "/shifts/999999/cash-movements",
            data={
                "seller_id": seller_id,
                "movement_type": "cash_in",
                "amount": "10",
                "reason": "Test",
            },
        )
        missing_close = client.post("/shifts/999999/close", data={"counted_cash": "0"})

    assert list_response.status_code == 200
    assert open_form.status_code == 200
    assert invalid_create.status_code == 400
    assert missing_detail.status_code == 404
    assert missing_movement.status_code == 400
    assert missing_close.status_code == 400


def test_customer_delete_with_existing_job_still_blocks_after_direct_setup():
    with SessionLocal() as db:
        customer = Customer(name="Locked Customer")
        job = Job(title="Locked Job", customer=customer)
        db.add(job)
        db.commit()
        customer_id = customer.id

    with TestClient(app) as client:
        response = client.post(f"/customers/{customer_id}/delete")

    assert response.status_code == 400


def test_settings_status_routes_cover_edit_update_deactivate_and_errors():
    with SessionLocal() as db:
        status = JobStatus(name="Coverage status", sort_order=55, is_active=True)
        db.add(status)
        db.commit()
        status_id = status.id

    with TestClient(app) as client:
        new_response = client.get("/settings/statuses/new")
        edit_response = client.get(f"/settings/statuses/{status_id}/edit")
        missing_edit = client.get("/settings/statuses/999999/edit")
        create_blank = client.post("/settings/statuses", data={"name": " "})
        update_blank = client.post(f"/settings/statuses/{status_id}", data={"name": " "})
        missing_update = client.post("/settings/statuses/999999", data={"name": "Missing"})
        update_response = client.post(
            f"/settings/statuses/{status_id}",
            data={
                "name": "Updated coverage status",
                "sort_order": "56",
                "is_ready_state": "true",
                "is_packed_state": "true",
                "is_final": "true",
                "is_active": "true",
            },
            follow_redirects=False,
        )
        deactivate_response = client.post(f"/settings/statuses/{status_id}/deactivate", follow_redirects=False)
        missing_deactivate = client.post("/settings/statuses/999999/deactivate")
        invalid_language = client.post("/settings/language", data={"language": "sv"})
        safe_next_url = client.post(
            "/settings/language",
            data={"language": "en", "next_url": "//evil.example"},
            follow_redirects=False,
        )

    assert new_response.status_code == 200
    assert edit_response.status_code == 200
    assert missing_edit.status_code == 404
    assert create_blank.status_code == 400
    assert update_blank.status_code == 400
    assert missing_update.status_code == 404
    assert update_response.status_code == 303
    assert deactivate_response.status_code == 303
    assert missing_deactivate.status_code == 404
    assert invalid_language.status_code == 400
    assert safe_next_url.status_code == 303
    assert safe_next_url.headers["location"] == "/"


def test_backup_route_runtime_errors_are_returned_as_bad_request(monkeypatch):
    import app.routes.backups as backup_routes

    def fail_create_backup():
        raise RuntimeError("backup create failed")

    def fail_restore_backup(name: str):
        raise RuntimeError(f"restore failed for {name}")

    with TestClient(app) as client:
        monkeypatch.setattr(backup_routes, "create_backup", fail_create_backup)
        create_response = client.post("/backups", follow_redirects=False)

        monkeypatch.setattr(backup_routes, "restore_backup", fail_restore_backup)
        restore_response = client.post("/backups/missing.sqlite/restore", follow_redirects=False)

    assert create_response.status_code == 400
    assert "backup create failed" in create_response.text
    assert restore_response.status_code == 400
    assert "restore failed" in restore_response.text
