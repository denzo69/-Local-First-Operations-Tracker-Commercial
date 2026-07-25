from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.database import SessionLocal
from app.models import Product, Role, Supplier, User, Warehouse, WarehouseLocation
from app.routes.inventory import (
    cancel_goods_receipt_route,
    create_goods_receipt_route,
    operator_id_from_request as legacy_inventory_operator_id,
    post_goods_receipt_route,
    repair_inventory_reconciliation,
)
from app.routes.products import (
    product_detail,
    receive_product_stock,
    receive_product_stock_form,
)
from app.services.auth_service import hash_password
from app.services.sales_service import ensure_default_roles


def _request():
    return SimpleNamespace(cookies={}, state=SimpleNamespace(current_user=None))


def _admin_and_location():
    with SessionLocal() as db:
        ensure_default_roles(db)
        role = db.query(Role).filter(Role.code == "admin").one()
        admin = User(
            name="Inventory Admin",
            login_name="inventory.admin",
            password_hash=hash_password("secret123"),
            role=role,
            is_active=True,
        )
        stock = Product(name="Stock route product", is_stock_item=True, is_active=True)
        service = Product(name="Service route product", is_stock_item=False, is_active=True)
        supplier = Supplier(name="Route Supplier", is_active=True)
        warehouse = Warehouse(name="Route Warehouse", code="RT", is_active=True)
        location = WarehouseLocation(warehouse=warehouse, code="BIN", name="Bin", is_active=True)
        db.add_all([admin, stock, service, supplier, warehouse, location])
        db.commit()
        return admin.id, stock.id, service.id, supplier.id, location.id


def test_product_receive_functions_cover_not_found_non_stock_and_validation_errors():
    admin_id, stock_id, service_id, _supplier_id, location_id = _admin_and_location()

    with SessionLocal() as db:
        with pytest.raises(HTTPException) as missing_form:
            receive_product_stock_form(999, _request(), db)
        with pytest.raises(HTTPException) as service_form:
            receive_product_stock_form(service_id, _request(), db)
        with pytest.raises(HTTPException) as missing_detail:
            product_detail(999, _request(), db)
        with pytest.raises(HTTPException) as missing_post:
            receive_product_stock(
                999,
                _request(),
                supplier_name="Manual Supplier",
                receipt_date=date.today(),
                destination_location_id=location_id,
                quantity_value="1",
                purchase_unit_price_ex_vat="1",
                received_by_user_id=admin_id,
                db=db,
            )
        with pytest.raises(HTTPException) as service_post:
            receive_product_stock(
                service_id,
                _request(),
                supplier_name="Manual Supplier",
                receipt_date=date.today(),
                destination_location_id=location_id,
                quantity_value="1",
                purchase_unit_price_ex_vat="1",
                received_by_user_id=admin_id,
                db=db,
            )
        with pytest.raises(HTTPException) as invalid_supplier:
            receive_product_stock(
                stock_id,
                _request(),
                supplier_id="",
                supplier_name="",
                receipt_date=date.today(),
                destination_location_id=location_id,
                quantity_value="1",
                purchase_unit_price_ex_vat="1",
                received_by_user_id=admin_id,
                db=db,
            )

    assert missing_form.value.status_code == 404
    assert service_form.value.status_code == 400
    assert missing_detail.value.status_code == 404
    assert missing_post.value.status_code == 404
    assert service_post.value.status_code == 400
    assert invalid_supplier.value.status_code == 400
    assert invalid_supplier.value.detail == "Supplier name is required."


def test_inventory_legacy_functions_cover_operator_and_value_errors():
    _admin_id, stock_id, _service_id, supplier_id, location_id = _admin_and_location()

    with SessionLocal() as db:
        with pytest.raises(HTTPException) as missing_operator:
            legacy_inventory_operator_id(_request(), None)
        with pytest.raises(HTTPException) as create_error:
            create_goods_receipt_route(
                _request(),
                supplier_id=str(supplier_id),
                supplier_name="",
                receipt_date=date.today(),
                received_by_user_id=None,
                db=db,
            )
        with pytest.raises(HTTPException) as post_error:
            post_goods_receipt_route(999, _request(), posted_by_user_id=None, db=db)
        with pytest.raises(HTTPException) as cancel_error:
            cancel_goods_receipt_route(999, _request(), reason="Nope", user_id=None, db=db)
        with pytest.raises(HTTPException) as repair_error:
            repair_inventory_reconciliation(_request(), reason="Fix", user_id=None, db=db)

    assert missing_operator.value.status_code == 400
    assert create_error.value.status_code == 400
    assert post_error.value.status_code == 400
    assert cancel_error.value.status_code == 400
    assert repair_error.value.status_code == 400
