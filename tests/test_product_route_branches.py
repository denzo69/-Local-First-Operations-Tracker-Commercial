from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import Product, Role, Supplier, User, Warehouse, WarehouseLocation
from app.services.inventory_service import create_default_warehouse
from app.services.sales_service import ensure_default_roles


def _create_operator() -> User:
    with SessionLocal() as db:
        ensure_default_roles(db)
        role = db.query(Role).filter(Role.code == "manager").one()
        user = User(name="Inventory Operator", role=role, is_active=True)
        db.add(user)
        db.commit()
        db.refresh(user)
        return user


def _create_product(name: str = "Route Product", *, stock: bool = True) -> Product:
    with SessionLocal() as db:
        product = Product(
            name=name,
            unit_price=Decimal("10.00"),
            vat_percent=Decimal("24"),
            unit="pcs",
            is_stock_item=stock,
            is_active=True,
        )
        db.add(product)
        db.commit()
        db.refresh(product)
        return product


def _default_location_id() -> int:
    with SessionLocal() as db:
        create_default_warehouse(db)
        db.commit()
        return db.query(WarehouseLocation).filter(WarehouseLocation.code == "DEFAULT").one().id


def test_product_create_edit_detail_and_validation_branches():
    product = _create_product("Editable Product")

    with TestClient(app) as client:
        new_page = client.get("/products/new")
        blank_create = client.post("/products", data={"name": " "})
        create_response = client.post(
            "/products",
            data={
                "name": "Created Product",
                "description": "Created from route test",
                "unit_price": "12.50",
                "vat_percent": "25.5",
                "unit": "m",
                "is_stock_item": "on",
            },
            follow_redirects=False,
        )
        detail_response = client.get(f"/products/{product.id}")
        edit_response = client.get(f"/products/{product.id}/edit")
        missing_detail = client.get("/products/999999")
        missing_edit = client.get("/products/999999/edit")
        blank_update = client.post(f"/products/{product.id}", data={"name": " "})
        missing_update = client.post("/products/999999", data={"name": "Missing"})
        update_response = client.post(
            f"/products/{product.id}",
            data={
                "name": "Updated Product",
                "description": "",
                "unit_price": "13.75",
                "vat_percent": "14",
                "unit": "",
                "is_active": "on",
            },
            follow_redirects=False,
        )

    assert new_page.status_code == 200
    assert blank_create.status_code == 400
    assert create_response.status_code == 303
    assert detail_response.status_code == 200
    assert edit_response.status_code == 200
    assert missing_detail.status_code == 404
    assert missing_edit.status_code == 404
    assert blank_update.status_code == 400
    assert missing_update.status_code == 404
    assert update_response.status_code == 303


def test_warehouse_and_supplier_management_branches_under_products():
    with TestClient(app) as client:
        warehouse_blank = client.post("/products/warehouses", data={"name": " ", "code": " "})
        warehouse_create = client.post(
            "/products/warehouses",
            data={"name": "Coverage Warehouse", "code": "cov"},
            follow_redirects=False,
        )
        missing_warehouse = client.get("/products/warehouses/999999")
        supplier_blank = client.post("/products/suppliers", data={"name": " "})
        supplier_create = client.post(
            "/products/suppliers",
            data={"name": "Coverage Supplier"},
            follow_redirects=False,
        )

    with SessionLocal() as db:
        warehouse = db.query(Warehouse).filter(Warehouse.code == "COV").one()
        supplier = db.query(Supplier).filter(Supplier.name == "Coverage Supplier").one()
        default_location = (
            db.query(WarehouseLocation)
            .filter(WarehouseLocation.warehouse_id == warehouse.id, WarehouseLocation.code == "DEFAULT")
            .one()
        )

    with TestClient(app) as client:
        warehouse_detail = client.get(f"/products/warehouses/{warehouse.id}")

    assert warehouse_blank.status_code == 400
    assert warehouse_create.status_code == 303
    assert missing_warehouse.status_code == 404
    assert supplier_blank.status_code == 400
    assert supplier_create.status_code == 303
    assert supplier.is_active is True
    assert default_location.name == "Default location"
    assert warehouse_detail.status_code == 200


def test_receive_stock_form_and_post_validation_branches():
    stock_product = _create_product("Receivable Product", stock=True)
    service_product = _create_product("Service Product", stock=False)
    operator = _create_operator()
    location_id = _default_location_id()

    with TestClient(app) as client:
        missing_get = client.get("/products/999999/receive")
        service_get = client.get(f"/products/{service_product.id}/receive")
        stock_get = client.get(f"/products/{stock_product.id}/receive")
        missing_post = client.post(
            "/products/999999/receive",
            data={
                "supplier_name": "Manual Supplier",
                "receipt_date": date.today().isoformat(),
                "destination_location_id": location_id,
                "quantity_value": "1",
                "purchase_unit_price_ex_vat": "5",
                "received_by_user_id": operator.id,
            },
        )
        service_post = client.post(
            f"/products/{service_product.id}/receive",
            data={
                "supplier_name": "Manual Supplier",
                "receipt_date": date.today().isoformat(),
                "destination_location_id": location_id,
                "quantity_value": "1",
                "purchase_unit_price_ex_vat": "5",
                "received_by_user_id": operator.id,
            },
        )
        no_supplier = client.post(
            f"/products/{stock_product.id}/receive",
            data={
                "receipt_date": date.today().isoformat(),
                "destination_location_id": location_id,
                "quantity_value": "1",
                "purchase_unit_price_ex_vat": "5",
                "received_by_user_id": operator.id,
            },
        )
        receive_response = client.post(
            f"/products/{stock_product.id}/receive",
            data={
                "supplier_name": "Typed Supplier",
                "receipt_date": date.today().isoformat(),
                "destination_location_id": location_id,
                "quantity_value": "2",
                "purchase_unit_price_ex_vat": "5",
                "vat_rate": "24",
                "delivery_number": "DN-COV",
                "invoice_number": "INV-COV",
                "received_by_user_id": operator.id,
            },
            follow_redirects=False,
        )

    assert missing_get.status_code == 404
    assert service_get.status_code == 400
    assert stock_get.status_code == 200
    assert missing_post.status_code == 404
    assert service_post.status_code == 400
    assert no_supplier.status_code == 400
    assert receive_response.status_code == 303


def test_goods_receipt_route_validation_branches():
    product = _create_product("Receipt Line Product", stock=True)
    operator = _create_operator()
    location_id = _default_location_id()

    with TestClient(app) as client:
        create_without_operator = client.post(
            "/products/goods-receipts",
            data={"supplier_name": "Supplier", "receipt_date": date.today().isoformat()},
        )
        create_response = client.post(
            "/products/goods-receipts",
            data={
                "supplier_name": "Goods Receipt Supplier",
                "receipt_date": date.today().isoformat(),
                "freight_total_ex_vat": "0",
                "other_costs_total_ex_vat": "0",
                "received_by_user_id": operator.id,
            },
            follow_redirects=False,
        )
        receipt_id = create_response.headers["location"].rstrip("/").rsplit("/", 1)[-1]
        missing_detail = client.get("/products/goods-receipts/999999")
        invalid_line = client.post(
            f"/products/goods-receipts/{receipt_id}/lines",
            data={
                "product_id": product.id,
                "destination_location_id": location_id,
                "quantity_value": "0",
                "purchase_unit_price_ex_vat": "5",
                "vat_rate": "24",
            },
        )
        line_response = client.post(
            f"/products/goods-receipts/{receipt_id}/lines",
            data={
                "product_id": product.id,
                "destination_location_id": location_id,
                "quantity_value": "1",
                "purchase_unit_price_ex_vat": "5",
                "vat_rate": "24",
            },
            follow_redirects=False,
        )
        missing_post = client.post(
            "/products/goods-receipts/999999/post",
            data={"posted_by_user_id": operator.id},
        )
        missing_cancel = client.post(
            "/products/goods-receipts/999999/cancel",
            data={"reason": "Missing", "user_id": operator.id},
        )
        missing_repair_operator = client.post(
            "/products/inventory/reconciliation/repair",
            data={"reason": "Repair without operator"},
        )

    assert create_without_operator.status_code == 400
    assert create_response.status_code == 303
    assert missing_detail.status_code == 404
    assert invalid_line.status_code == 400
    assert line_response.status_code == 303
    assert missing_post.status_code == 400
    assert missing_cancel.status_code == 400
    assert missing_repair_operator.status_code == 400


def test_product_csv_import_validation_errors_do_not_modify_products():
    with TestClient(app) as client:
        invalid_encoding = client.post(
            "/products/import",
            files={"csv_file": ("products.csv", b"\xff\xfe\x00\x00", "text/csv")},
        )
        empty_file = client.post(
            "/products/import",
            files={"csv_file": ("products.csv", b"   \n", "text/csv")},
        )
        no_name = client.post(
            "/products/import",
            files={"csv_file": ("products.csv", b"description,price\nOnly description,1.00\n", "text/csv")},
        )
        bad_decimal = client.post(
            "/products/import",
            files={"csv_file": ("products.csv", b"name,price\nBad Product,not-a-price\n", "text/csv")},
        )

    with SessionLocal() as db:
        assert db.query(Product).count() == 0

    assert invalid_encoding.status_code == 400
    assert empty_file.status_code == 400
    assert no_name.status_code == 400
    assert bad_decimal.status_code == 400
