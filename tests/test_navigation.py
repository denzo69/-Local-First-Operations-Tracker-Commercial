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
            "/delivery-notes",
            "/quotes",
            "/jobs",
            "/products",
            "/products/goods-receipts",
            "/products/suppliers",
            "/products/warehouses",
            "/products/inventory",
            "/products/inventory/valuation",
            "/products/inventory/transactions",
            "/products/inventory/reconciliation",
            "/inventory/goods-receipts",
            "/inventory/suppliers",
            "/inventory/warehouses",
            "/inventory/valuation",
            "/inventory/ledger",
            "/inventory/reconciliation",
            "/sales",
            "/sales/quick",
            "/sales/invoice-queue",
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


def test_document_workflow_navigation_uses_finnish_labels_and_routes_load():
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
        dashboard = client.get("/")
        delivery_notes = client.get("/delivery-notes")
        quotes = client.get("/quotes")

    assert dashboard.status_code == 200
    assert delivery_notes.status_code == 200
    assert quotes.status_code == 200
    assert "Lähetteet" in dashboard.text
    assert "Tarjoukset" in dashboard.text
    assert "Delivery Notes" not in dashboard.text
    assert "Quotes" not in dashboard.text
    assert "Lähetteet" in delivery_notes.text
    assert "Tarjoukset" in quotes.text


def test_legacy_document_label_urls_redirect_to_canonical_routes():
    with TestClient(app) as client:
        for path, target in [
            ("/delivery_notes", "/delivery-notes"),
            ("/delivery-note", "/delivery-notes"),
            ("/Delivery%20Notes", "/delivery-notes"),
            ("/quote", "/quotes"),
            ("/Quotes", "/quotes"),
        ]:
            response = client.get(path, follow_redirects=False)
            assert response.status_code == 303
            assert response.headers["location"] == target


def test_finnish_404_error_page_is_translated():
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
        response = client.get("/missing-document-workflow-page")

    assert response.status_code == 404
    assert "Sivua ei löydy" in response.text
    assert "Not Found" not in response.text


def test_products_is_the_visible_inventory_workspace_navigation():
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert 'href="/products"' in response.text
    assert 'href="/products/goods-receipts"' in response.text
    assert 'href="/products/warehouses"' in response.text
    assert 'href="/products/inventory"' in response.text
    assert 'href="/products/inventory/transactions"' in response.text
    assert 'href="/shifts"' not in response.text
    assert 'href="/inventory/goods-receipts"' not in response.text
    assert 'href="/inventory/warehouses"' not in response.text
    assert 'href="/inventory/ledger"' not in response.text


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

    assert "Myyntihistoria" in finnish.text
    assert "Hallinta" in finnish.text
    assert "Audit-loki" in finnish.text
    assert "Sales history" in english.text
    assert "Administration" in english.text
    assert "Audit log" in english.text


def test_every_main_navigation_label_renders_in_both_languages():
    english_labels = [
        "Dashboard", "Operations", "Customers", "Work orders", "Sales",
        "Quick sale", "Sales history", "Daily closing", "Catalog", "Products",
        "Reports", "Seller reports", "Administration", "Users",
        "Cash registers", "Audit log", "Backups", "Settings",
    ]
    finnish_labels = [
        "Myyntihistoria", "Hallinta", "Asiakkaat", "Tuotteet", "Kassat",
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
    assert "New sale" not in english.text
    assert "Shifts" not in english.text
    assert "Uusi myynti" not in finnish.text
    assert "Kassavuorot" not in finnish.text
    assert 'href="/sales/new"' not in english.text
    assert 'href="/shifts"' not in english.text
    assert 'dropdown-toggle"></a>' not in english.text
    assert 'sidebar-group-label"></div>' not in english.text


def test_quick_sale_page_uses_finnish_labels_without_cashier_shift_noise():
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
        response = client.get("/sales/quick")

    assert response.status_code == 200
    assert "Kassamyynti" in response.text
    assert "Viimeistele myynti" in response.text
    assert "Asiakkaan nimi kuitille" in response.text
    assert "Hyvitettävä myyjä" in response.text
    assert "Cashier shift" not in response.text
    assert "Quick sale" not in response.text
    assert "Complete sale" not in response.text


def test_legacy_new_sale_url_redirects_to_quick_sale():
    with TestClient(app) as client:
        response = client.get("/sales/new", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/sales/quick"


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
    assert ".offcanvas .sidebar-link" in app_css.text
    assert ".offcanvas .sidebar-nav {\n    display: grid;" in app_css.text
    assert ".offcanvas .sidebar-group-label {\n    display: block;" in app_css.text
    assert "border-left: 4px solid var(--primary);" in app_css.text
    assert ".offcanvas .sidebar-link::after" in app_css.text
    assert 'content: "›";' in app_css.text
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
    assert "Current shift" not in response.text
    assert "No open shift." not in response.text
    assert "Daily closing not completed" in response.text
    assert "empty-state compact" in response.text


def test_dashboard_does_not_surface_cashier_shift_panel_when_shift_is_open():
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
    assert "Dashboard Shift Seller" not in response.text
    assert "Dashboard Register" not in response.text
    assert 'href="/shifts"' not in response.text


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


def test_products_workspace_routes_support_inventory_workflow_and_legacy_bookmarks():
    with SessionLocal() as db:
        ensure_default_roles(db)
        role = db.query(Role).filter(Role.code == "seller").one()
        seller = User(name="Products Workspace Seller", login_name="products.workspace.seller", role=role, is_active=True)
        product = Product(
            name="Products Workspace Stock",
            is_active=True,
            is_stock_item=True,
            unit_price="30",
            vat_percent="24",
        )
        db.add_all([seller, product])
        db.commit()
        seller_id = seller.id
        product_id = product.id

    with TestClient(app) as client:
        workspace = client.get("/products")
        assert workspace.status_code == 200
        assert "Goods receipts" in workspace.text
        assert "Stock balances" in workspace.text
        assert "Inventory transactions" in workspace.text

        assert client.get("/products/warehouses").status_code == 200
        supplier_response = client.post(
            "/products/suppliers",
            data={"name": "Products Workspace Supplier"},
            follow_redirects=False,
        )
        assert supplier_response.status_code == 303

        with SessionLocal() as db:
            supplier_id = db.query(Supplier).filter(Supplier.name == "Products Workspace Supplier").one().id
            location_id = db.query(WarehouseLocation).filter(WarehouseLocation.code == "DEFAULT").one().id

        receipt_response = client.post(
            "/products/goods-receipts",
            data={
                "supplier_id": supplier_id,
                "receipt_date": date.today().isoformat(),
                "received_by_user_id": seller_id,
                "delivery_number": "PRODUCTS-DN",
                "invoice_number": "PRODUCTS-INV",
                "freight_total_ex_vat": "0",
                "other_costs_total_ex_vat": "0",
                "allocation_method": "by_value",
            },
            follow_redirects=False,
        )
        assert receipt_response.status_code == 303
        assert receipt_response.headers["location"].startswith("/products/goods-receipts/")
        receipt_id = int(receipt_response.headers["location"].rsplit("/", 1)[-1])

        assert client.post(
            f"/products/goods-receipts/{receipt_id}/lines",
            data={
                "product_id": product_id,
                "destination_location_id": location_id,
                "quantity_value": "3",
                "purchase_unit_price_ex_vat": "10",
                "vat_rate": "24",
            },
            follow_redirects=False,
        ).status_code == 303
        assert client.post(
            f"/products/goods-receipts/{receipt_id}/post",
            data={"posted_by_user_id": seller_id},
            follow_redirects=False,
        ).status_code == 303

        product_detail = client.get(f"/products/{product_id}")
        balances = client.get("/products/inventory")
        transactions = client.get(f"/products/inventory/transactions?product_id={product_id}")
        valuation = client.get("/products/inventory/valuation")
        reconciliation = client.get("/products/inventory/reconciliation")
        legacy = client.get("/inventory/goods-receipts")

    assert product_detail.status_code == 200
    assert "Products Workspace Stock" in product_detail.text
    assert "Products Workspace Supplier" in product_detail.text
    assert "purchase" in product_detail.text
    assert balances.status_code == 200
    assert "Products Workspace Stock" in balances.text
    assert transactions.status_code == 200
    assert "purchase" in transactions.text
    assert valuation.status_code == 200
    assert reconciliation.status_code == 200
    assert legacy.status_code == 200


def test_product_detail_has_direct_stock_receiving_flow():
    with SessionLocal() as db:
        ensure_default_roles(db)
        role = db.query(Role).filter(Role.code == "manager").one()
        manager = User(name="Stock Receiver", login_name="stock.receiver", role=role, is_active=True)
        supplier = Supplier(name="Direct Stock Supplier", is_active=True)
        product = Product(
            name="Direct Receive Product",
            is_active=True,
            is_stock_item=True,
            unit_price="15",
            vat_percent="24",
        )
        service = Product(
            name="Direct Receive Service",
            is_active=True,
            is_stock_item=False,
            unit_price="50",
            vat_percent="24",
        )
        db.add_all([manager, supplier, product, service])
        db.commit()
        manager_id = manager.id
        supplier_id = supplier.id
        product_id = product.id
        service_id = service.id

    with TestClient(app) as client:
        product_detail = client.get(f"/products/{product_id}")
        assert product_detail.status_code == 200
        assert f'href="/products/{product_id}/receive"' in product_detail.text
        assert "Receive stock" in product_detail.text

        receive_form = client.get(f"/products/{product_id}/receive")
        assert receive_form.status_code == 200
        assert "Direct Receive Product" in receive_form.text
        assert "Create receipt draft" in receive_form.text

        with SessionLocal() as db:
            location_id = db.query(WarehouseLocation).filter(WarehouseLocation.code == "DEFAULT").one().id

        draft_response = client.post(
            f"/products/{product_id}/receive",
            data={
                "supplier_id": supplier_id,
                "receipt_date": date.today().isoformat(),
                "destination_location_id": location_id,
                "quantity_value": "4",
                "purchase_unit_price_ex_vat": "12.50",
                "vat_rate": "24",
                "delivery_number": "DIRECT-STOCK",
                "invoice_number": "DIRECT-INV",
                "freight_total_ex_vat": "0",
                "freight_vat_rate": "0",
                "other_costs_total_ex_vat": "0",
                "other_costs_vat_rate": "0",
                "allocation_method": "by_value",
                "received_by_user_id": manager_id,
            },
            follow_redirects=False,
        )
        assert draft_response.status_code == 303
        assert draft_response.headers["location"].startswith("/products/goods-receipts/")
        receipt_id = int(draft_response.headers["location"].rsplit("/", 1)[-1])

        with SessionLocal() as db:
            draft_product = db.get(Product, product_id)
            assert draft_product.current_inventory_quantity == 0

        receipt_detail = client.get(f"/products/goods-receipts/{receipt_id}")
        assert receipt_detail.status_code == 200
        assert "Direct Receive Product" in receipt_detail.text
        assert "Post goods receipt" in receipt_detail.text

        post_response = client.post(
            f"/products/goods-receipts/{receipt_id}/post",
            data={"posted_by_user_id": manager_id},
            follow_redirects=False,
        )
        assert post_response.status_code == 303

        with SessionLocal() as db:
            posted_product = db.get(Product, product_id)
            assert str(posted_product.current_inventory_quantity) == "4.000"
            assert str(posted_product.current_inventory_value_ex_vat) == "50.00"

        service_detail = client.get(f"/products/{service_id}")
        assert service_detail.status_code == 200
        assert f'href="/products/{service_id}/receive"' not in service_detail.text
        assert client.get(f"/products/{service_id}/receive").status_code == 400
