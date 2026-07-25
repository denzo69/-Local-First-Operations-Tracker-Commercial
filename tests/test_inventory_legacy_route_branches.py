from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import Product, Role, Supplier, User, Warehouse, WarehouseLocation
from app.services.inventory_service import create_default_warehouse
from app.services.sales_service import ensure_default_roles


def _operator() -> User:
    with SessionLocal() as db:
        ensure_default_roles(db)
        role = db.query(Role).filter(Role.code == "manager").one()
        user = User(name="Legacy Inventory Operator", role=role, is_active=True)
        db.add(user)
        db.commit()
        db.refresh(user)
        return user


def _product() -> Product:
    with SessionLocal() as db:
        product = Product(
            name="Legacy Inventory Product",
            unit_price=Decimal("8.00"),
            vat_percent=Decimal("24"),
            unit="pcs",
            is_stock_item=True,
            is_active=True,
        )
        db.add(product)
        db.commit()
        db.refresh(product)
        return product


def _location() -> WarehouseLocation:
    with SessionLocal() as db:
        create_default_warehouse(db)
        db.commit()
        location = db.query(WarehouseLocation).filter(WarehouseLocation.code == "DEFAULT").one()
        db.refresh(location)
        return location


def test_legacy_inventory_goods_receipt_routes_cover_success_and_errors():
    product = _product()
    operator = _operator()
    location = _location()

    with TestClient(app) as client:
        list_response = client.get("/inventory/goods-receipts")
        new_response = client.get("/inventory/goods-receipts/new")
        create_missing_operator = client.post(
            "/inventory/goods-receipts",
            data={"supplier_name": "Legacy Supplier", "receipt_date": date.today().isoformat()},
        )
        create_response = client.post(
            "/inventory/goods-receipts",
            data={
                "supplier_name": "Legacy Goods Supplier",
                "receipt_date": date.today().isoformat(),
                "received_by_user_id": operator.id,
            },
            follow_redirects=False,
        )
        receipt_id = create_response.headers["location"].rstrip("/").rsplit("/", 1)[-1]
        detail_response = client.get(f"/inventory/goods-receipts/{receipt_id}")
        missing_detail = client.get("/inventory/goods-receipts/999999")
        invalid_line = client.post(
            f"/inventory/goods-receipts/{receipt_id}/lines",
            data={
                "product_id": product.id,
                "destination_location_id": location.id,
                "quantity_value": "0",
                "purchase_unit_price_ex_vat": "5",
                "vat_rate": "24",
            },
        )
        line_response = client.post(
            f"/inventory/goods-receipts/{receipt_id}/lines",
            data={
                "product_id": product.id,
                "destination_location_id": location.id,
                "quantity_value": "1",
                "purchase_unit_price_ex_vat": "5",
                "vat_rate": "24",
            },
            follow_redirects=False,
        )
        post_response = client.post(
            f"/inventory/goods-receipts/{receipt_id}/post",
            data={"posted_by_user_id": operator.id},
            follow_redirects=False,
        )
        cancel_missing_operator = client.post(
            f"/inventory/goods-receipts/{receipt_id}/cancel",
            data={"reason": "No operator"},
        )
        missing_post = client.post("/inventory/goods-receipts/999999/post", data={"posted_by_user_id": operator.id})
        missing_cancel = client.post(
            "/inventory/goods-receipts/999999/cancel",
            data={"reason": "Missing", "user_id": operator.id},
        )

    assert list_response.status_code == 200
    assert new_response.status_code == 200
    assert create_missing_operator.status_code == 400
    assert create_response.status_code == 303
    assert detail_response.status_code == 200
    assert missing_detail.status_code == 404
    assert invalid_line.status_code == 400
    assert line_response.status_code == 303
    assert post_response.status_code == 303
    assert cancel_missing_operator.status_code == 400
    assert missing_post.status_code == 400
    assert missing_cancel.status_code == 400


def test_legacy_inventory_report_supplier_and_warehouse_routes():
    with TestClient(app) as client:
        valuation = client.get("/inventory/valuation")
        reconciliation = client.get("/inventory/reconciliation")
        repair_without_operator = client.post(
            "/inventory/reconciliation/repair",
            data={"reason": "Missing operator"},
        )
        ledger = client.get(
            "/inventory/ledger",
            params={
                "transaction_type": "purchase",
                "date_from": date.today().isoformat(),
                "date_to": date.today().isoformat(),
            },
        )
        suppliers = client.get("/inventory/suppliers")
        blank_supplier = client.post("/inventory/suppliers", data={"name": " "})
        supplier_create = client.post(
            "/inventory/suppliers",
            data={"name": "Legacy Supplier Route"},
            follow_redirects=False,
        )
        warehouses = client.get("/inventory/warehouses")
        blank_warehouse = client.post("/inventory/warehouses", data={"name": " ", "code": " "})
        warehouse_create = client.post(
            "/inventory/warehouses",
            data={"name": "Legacy Warehouse", "code": "lg"},
            follow_redirects=False,
        )

    with SessionLocal() as db:
        supplier = db.query(Supplier).filter(Supplier.name == "Legacy Supplier Route").one()
        warehouse = db.query(Warehouse).filter(Warehouse.code == "LG").one()
        default_location = (
            db.query(WarehouseLocation)
            .filter(WarehouseLocation.warehouse_id == warehouse.id, WarehouseLocation.code == "DEFAULT")
            .one()
        )

    assert valuation.status_code == 200
    assert reconciliation.status_code == 200
    assert repair_without_operator.status_code == 400
    assert ledger.status_code == 200
    assert suppliers.status_code == 200
    assert blank_supplier.status_code == 400
    assert supplier_create.status_code == 303
    assert supplier.is_active is True
    assert warehouses.status_code == 200
    assert blank_warehouse.status_code == 400
    assert warehouse_create.status_code == 303
    assert default_location.name == "Default location"
