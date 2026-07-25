from datetime import date
from decimal import Decimal

import pytest

from app.database import SessionLocal
from app.models import GoodsReceipt, GoodsReceiptLine, Product, Role, Supplier, User, Warehouse, WarehouseLocation
from app.services.auth_service import hash_password
from app.services.inventory_service import (
    allocate_landed_costs,
    create_default_warehouse,
    create_goods_receipt,
    require_inventory_manager,
    require_inventory_operational_user,
    require_non_negative_money,
    require_positive_quantity,
    require_vat_rate,
    vat_amount_from_ex_vat,
    add_goods_receipt_line,
)
from app.services.sales_service import ensure_default_roles


def _user(db, name: str, role_code: str, *, active: bool = True) -> User:
    ensure_default_roles(db)
    role = db.query(Role).filter(Role.code == role_code).one()
    user = User(
        name=name,
        login_name=name.lower().replace(" ", "."),
        password_hash=hash_password("secret123"),
        role=role,
        is_active=active,
    )
    db.add(user)
    db.flush()
    return user


def test_inventory_validation_and_role_edges():
    with SessionLocal() as db:
        seller = _user(db, "Seller", "seller")
        viewer = _user(db, "Viewer", "read_only")
        inactive_admin = _user(db, "Inactive Admin", "admin", active=False)

        assert require_inventory_operational_user(seller).id == seller.id
        with pytest.raises(ValueError, match="Active Admin or Manager"):
            require_inventory_manager(None)
        with pytest.raises(ValueError, match="Active Admin or Manager"):
            require_inventory_manager(inactive_admin)
        with pytest.raises(ValueError, match="Only Admin or Manager"):
            require_inventory_manager(seller)
        with pytest.raises(ValueError, match="Only Admin, Manager, or Seller"):
            require_inventory_operational_user(viewer)

    with pytest.raises(ValueError, match="Cost must be a valid decimal"):
        require_non_negative_money("not-money", "Cost")
    with pytest.raises(ValueError, match="Cost cannot be negative"):
        require_non_negative_money("-1", "Cost")
    with pytest.raises(ValueError, match="VAT must be between"):
        require_vat_rate("101", "VAT")
    with pytest.raises(ValueError, match="Quantity must be positive"):
        require_positive_quantity("0")
    assert vat_amount_from_ex_vat(Decimal("10.00"), Decimal("24")) == Decimal("2.40")


def test_default_warehouse_and_goods_receipt_creation_error_edges():
    with SessionLocal() as db:
        admin = _user(db, "Admin", "admin")
        inactive_supplier = Supplier(name="Inactive", is_active=False)
        db.add(inactive_supplier)
        db.commit()

        warehouse, location = create_default_warehouse(db)
        same_warehouse, same_location = create_default_warehouse(db)
        assert same_warehouse.id == warehouse.id
        assert same_location.id == location.id

        with pytest.raises(ValueError, match="Active supplier"):
            create_goods_receipt(
                db,
                supplier_id=inactive_supplier.id,
                receipt_date=date.today(),
                received_by_user_id=admin.id,
            )
        active_supplier = Supplier(name="Active", is_active=True)
        db.add(active_supplier)
        db.commit()
        with pytest.raises(ValueError, match="Invalid landed cost allocation"):
            create_goods_receipt(
                db,
                supplier_id=active_supplier.id,
                receipt_date=date.today(),
                received_by_user_id=admin.id,
                allocation_method="random",
            )


def test_goods_receipt_line_and_allocation_error_edges():
    with SessionLocal() as db:
        admin = _user(db, "Admin", "admin")
        supplier = Supplier(name="Supplier", is_active=True)
        service = Product(name="Service", is_stock_item=False, is_active=True)
        inactive_product = Product(name="Inactive Product", is_stock_item=True, is_active=False)
        stock = Product(name="Stock", is_stock_item=True, is_active=True)
        warehouse = Warehouse(name="Warehouse", code="WH", is_active=True)
        inactive_location = WarehouseLocation(warehouse=warehouse, code="OLD", name="Old", is_active=False)
        active_location = WarehouseLocation(warehouse=warehouse, code="BIN", name="Bin", is_active=True)
        db.add_all([supplier, service, inactive_product, stock, warehouse, inactive_location, active_location])
        db.commit()
        receipt = create_goods_receipt(
            db,
            supplier_id=supplier.id,
            receipt_date=date.today(),
            received_by_user_id=admin.id,
        )

        with pytest.raises(ValueError, match="Goods receipt not found"):
            add_goods_receipt_line(
                db,
                goods_receipt_id=999,
                product_id=stock.id,
                destination_location_id=active_location.id,
                quantity_value="1",
                purchase_unit_price_ex_vat="1",
            )
        with pytest.raises(ValueError, match="Active product"):
            add_goods_receipt_line(
                db,
                goods_receipt_id=receipt.id,
                product_id=inactive_product.id,
                destination_location_id=active_location.id,
                quantity_value="1",
                purchase_unit_price_ex_vat="1",
            )
        with pytest.raises(ValueError, match="Only stock products"):
            add_goods_receipt_line(
                db,
                goods_receipt_id=receipt.id,
                product_id=service.id,
                destination_location_id=active_location.id,
                quantity_value="1",
                purchase_unit_price_ex_vat="1",
            )
        with pytest.raises(ValueError, match="Active warehouse location"):
            add_goods_receipt_line(
                db,
                goods_receipt_id=receipt.id,
                product_id=stock.id,
                destination_location_id=inactive_location.id,
                quantity_value="1",
                purchase_unit_price_ex_vat="1",
            )
        with pytest.raises(ValueError, match="VAT rate cannot be negative"):
            add_goods_receipt_line(
                db,
                goods_receipt_id=receipt.id,
                product_id=stock.id,
                destination_location_id=active_location.id,
                quantity_value="1",
                purchase_unit_price_ex_vat="1",
                vat_rate="-1",
            )

        receipt.status = "posted"
        db.commit()
        with pytest.raises(ValueError, match="Posted or cancelled"):
            add_goods_receipt_line(
                db,
                goods_receipt_id=receipt.id,
                product_id=stock.id,
                destination_location_id=active_location.id,
                quantity_value="1",
                purchase_unit_price_ex_vat="1",
            )

    with pytest.raises(ValueError, match="requires at least one line"):
        allocate_landed_costs([], Decimal("1"), Decimal("0"), "by_value")
    with pytest.raises(ValueError, match="Invalid landed cost allocation"):
        allocate_landed_costs([GoodsReceiptLine(quantity=1, purchase_unit_price_ex_vat=1)], Decimal("1"), Decimal("0"), "bad")
    with pytest.raises(ValueError, match="positive line weight"):
        allocate_landed_costs([GoodsReceiptLine(quantity=0, purchase_unit_price_ex_vat=0)], Decimal("1"), Decimal("0"), "by_quantity")

