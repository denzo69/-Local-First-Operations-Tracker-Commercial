from fastapi.testclient import TestClient
from datetime import date, timedelta

from app.database import SessionLocal
from app.main import app
from app.models import CashRegister, Job, Product, Role, Supplier, User, WarehouseLocation
from app.services.sales_service import ensure_default_roles, open_shift


def test_dashboard_actions_are_links():
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert 'href="/work-orders/new"' in response.text
    assert 'href="/customers/new"' in response.text


def test_main_navigation_targets_load():
    with TestClient(app) as client:
        for path in [
            "/customers",
            "/work-orders",
            "/jobs",
            "/products",
            "/inventory/goods-receipts",
            "/inventory/suppliers",
            "/inventory/warehouses",
            "/inventory/valuation",
            "/inventory/ledger",
            "/sales",
            "/shifts",
            "/daily-closings",
            "/seller-reports",
            "/users",
            "/cash-registers",
            "/reports",
            "/backups",
            "/audit-log",
            "/settings",
        ]:
            response = client.get(path)
            assert response.status_code == 200


def test_new_navigation_labels_render_in_finnish_and_english():
    with TestClient(app) as client:
        client.post(
            "/settings",
            data={
                "company_name": "Test Company Oy",
                "default_vat_percent": "24",
                "receipt_prefix": "TEST-",
                "language": "fi",
            },
            follow_redirects=False,
        )
        finnish = client.get("/")
        client.post(
            "/settings",
            data={
                "company_name": "Test Company Oy",
                "default_vat_percent": "24",
                "receipt_prefix": "TEST-",
                "language": "en",
            },
            follow_redirects=False,
        )
        english = client.get("/")

    assert "Myynti" in finnish.text
    assert "Hallinta" in finnish.text
    assert "Audit-loki" in finnish.text
    assert "Sales" in english.text
    assert "Administration" in english.text
    assert "Audit log" in english.text


def test_every_main_navigation_label_renders_in_both_languages():
    english_labels = [
        "Dashboard", "Operations", "Customers", "Work Orders", "Sales",
        "New sale", "Shifts", "Daily closing", "Catalog", "Products",
        "Reports", "Seller reports", "Administration", "Users",
        "Cash registers", "Audit log", "Backups", "Settings",
    ]
    finnish_labels = [
        "Myynti", "Hallinta", "Asiakkaat", "Tuotteet", "Kassat",
        "Audit-loki", "Asetukset",
    ]
    with TestClient(app) as client:
        english = client.get("/")
        client.post(
            "/settings",
            data={
                "company_name": "Test Company Oy",
                "default_vat_percent": "24",
                "receipt_prefix": "TEST-",
                "language": "fi",
            },
            follow_redirects=False,
        )
        finnish = client.get("/")

    for label in english_labels:
        assert label in english.text
    for label in finnish_labels:
        assert label in finnish.text
    assert 'dropdown-toggle"></a>' not in english.text
    assert 'sidebar-group-label"></div>' not in english.text


def test_mobile_navigation_markup_is_present():
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert 'href="http://testserver/static/vendor/bootstrap/bootstrap.min.css"' in response.text
    assert 'href="http://testserver/static/css/app.css"' in response.text
    assert 'data-bs-toggle="offcanvas"' in response.text
    assert 'id="mobileNav"' in response.text
    assert 'aria-label="Mobile navigation"' in response.text


def test_administration_tables_have_responsive_markup():
    with SessionLocal() as db:
        ensure_default_roles(db)
        role = db.query(Role).filter(Role.code == "seller").one()
        db.add(User(name="Responsive User", role=role, is_active=True))
        db.add(CashRegister(name="Responsive Register", is_active=True))
        db.commit()

    with TestClient(app) as client:
        responses = [
            client.get("/users"),
            client.get("/cash-registers"),
            client.get("/settings/statuses"),
        ]
        closings_response = client.get("/daily-closings")

    for response in responses:
        assert response.status_code == 200
        assert "responsive-card-table" in response.text
        assert "data-label=" in response.text
    assert closings_response.status_code == 200
    assert "responsive-card-table" in closings_response.text


def test_static_stylesheets_are_served():
    with TestClient(app) as client:
        bootstrap = client.get("/static/vendor/bootstrap/bootstrap.min.css")
        app_css = client.get("/static/css/app.css")

    assert bootstrap.status_code == 200
    assert ".d-none" in bootstrap.text
    assert app_css.status_code == 200
    assert ".app-sidebar" in app_css.text
    assert ".dashboard-hero" in app_css.text


def test_static_scripts_are_served():
    with TestClient(app) as client:
        bootstrap = client.get("/static/vendor/bootstrap/bootstrap.bundle.min.js")
        app_js = client.get("/static/js/app.js")

    assert bootstrap.status_code == 200
    assert "Offcanvas" in bootstrap.text
    assert app_js.status_code == 200
    assert "data-live-filter-form" in app_js.text
    assert "data-search-text" in app_js.text


def test_dashboard_shows_created_job():
    with TestClient(app) as client:
        customer_response = client.post(
            "/customers",
            data={"name": "Dashboard Customer"},
            follow_redirects=False,
        )
        customer_id = customer_response.headers["location"].rsplit("/", 1)[-1]

        client.post(
            "/jobs",
            data={
                "title": "Dashboard job",
                "customer_id": customer_id,
                "requested_pickup_date": "2099-01-15",
            },
            follow_redirects=False,
        )

        response = client.get("/")

    assert response.status_code == 200
    assert "Dashboard job" in response.text
    assert "Dashboard Customer" in response.text


def test_dashboard_due_tomorrow_is_consistent_with_upcoming_section():
    tomorrow = date.today() + timedelta(days=1)
    with TestClient(app) as client:
        customer_response = client.post(
            "/customers",
            data={"name": "Dashboard Customer"},
            follow_redirects=False,
        )
        customer_id = customer_response.headers["location"].rsplit("/", 1)[-1]
        client.post(
            "/jobs",
            data={
                "title": "Dashboard job tomorrow",
                "customer_id": customer_id,
                "requested_pickup_date": tomorrow.isoformat(),
            },
            follow_redirects=False,
        )
        response = client.get("/")

    assert response.status_code == 200
    assert "Dashboard job tomorrow" in response.text
    assert "Upcoming work orders" in response.text
    assert "No upcoming work orders yet." not in response.text


def test_dashboard_compact_empty_and_operational_panels_render():
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "Current shift" in response.text
    assert "No open shift." in response.text
    assert "Daily closing not completed" in response.text
    assert "empty-state compact" in response.text


def test_dashboard_current_shift_panel_when_shift_is_open():
    with SessionLocal() as db:
        ensure_default_roles(db)
        role = db.query(Role).filter(Role.code == "seller").one()
        user = User(name="Dashboard Shift Seller", role=role, is_active=True)
        register = CashRegister(name="Dashboard Register", is_active=True)
        db.add_all([user, register])
        db.commit()
        open_shift(
            db,
            seller_id=user.id,
            cash_register_id=register.id,
            business_date=date.today(),
            starting_cash="10",
        )

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "Dashboard Shift Seller" in response.text
    assert "Dashboard Register" in response.text


def test_inventory_routes_support_virtual_goods_receipt_workflow():
    with SessionLocal() as db:
        ensure_default_roles(db)
        role = db.query(Role).filter(Role.code == "manager").one()
        manager = User(name="Route Inventory Manager", login_name="route.manager", role=role, is_active=True)
        product = Product(
            name="Route Inventory Product",
            is_active=True,
            is_stock_item=True,
            unit_price="20",
            vat_percent="24",
        )
        db.add_all([manager, product])
        db.commit()
        manager_id = manager.id
        product_id = product.id

    with TestClient(app) as client:
        assert client.get("/inventory/warehouses").status_code == 200
        supplier_response = client.post(
            "/inventory/suppliers",
            data={"name": "Route Supplier"},
            follow_redirects=False,
        )
        assert supplier_response.status_code == 303

        with SessionLocal() as db:
            supplier_id = db.query(Supplier).filter(Supplier.name == "Route Supplier").one().id
            location_id = db.query(WarehouseLocation).filter(WarehouseLocation.code == "DEFAULT").one().id

        receipt_response = client.post(
            "/inventory/goods-receipts",
            data={
                "supplier_id": supplier_id,
                "receipt_date": date.today().isoformat(),
                "received_by_user_id": manager_id,
                "delivery_number": "ROUTE-DN",
                "invoice_number": "ROUTE-INV",
                "freight_total_ex_vat": "5",
                "other_costs_total_ex_vat": "0",
                "allocation_method": "by_value",
            },
            follow_redirects=False,
        )
        assert receipt_response.status_code == 303
        receipt_url = receipt_response.headers["location"]
        receipt_id = int(receipt_url.rsplit("/", 1)[-1])

        line_response = client.post(
            f"/inventory/goods-receipts/{receipt_id}/lines",
            data={
                "product_id": product_id,
                "destination_location_id": location_id,
                "quantity_value": "2",
                "purchase_unit_price_ex_vat": "10",
                "vat_rate": "24",
            },
            follow_redirects=False,
        )
        assert line_response.status_code == 303
        assert "Route Inventory Product" in client.get(receipt_url).text

        post_response = client.post(
            f"/inventory/goods-receipts/{receipt_id}/post",
            data={"posted_by_user_id": manager_id},
            follow_redirects=False,
        )
        assert post_response.status_code == 303

        valuation = client.get("/inventory/valuation")
        ledger = client.get("/inventory/ledger")

    assert valuation.status_code == 200
    assert "25.00" in valuation.text
    assert ledger.status_code == 200
    assert "Route Inventory Product" in ledger.text
    assert "purchase" in ledger.text
