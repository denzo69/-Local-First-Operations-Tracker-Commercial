from datetime import date, timedelta
from decimal import Decimal

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DatabaseError

from app.database import SessionLocal
from app.models import AuditLog, CashRegister, InventoryTransaction, Product, Role, Sale, Supplier, User, WarehouseLocation
from app.services.inventory_service import (
    add_goods_receipt_line,
    allocate_landed_costs,
    cancel_goods_receipt,
    create_default_warehouse,
    create_goods_receipt,
    inventory_valuation,
    inventory_reconciliation,
    product_cost_profile,
    issue_stock_for_sale,
    post_goods_receipt,
    preview_goods_receipt,
    repair_inventory_caches_from_ledger,
    transfer_stock,
)
from app.services.sales_service import correct_sale_seller, create_sale_with_payment, ensure_default_roles, open_shift, seller_report


def create_user(db, name="Inventory Manager", role_code="manager"):
    ensure_default_roles(db)
    role = db.query(Role).filter(Role.code == role_code).one()
    user = User(
        name=name,
        login_name=name.lower().replace(" ", "."),
        role=role,
        is_active=True,
        can_receive_sales_credit=role_code == "seller",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def create_supplier(db, name="Test Supplier"):
    supplier = Supplier(name=name, is_active=True)
    db.add(supplier)
    db.commit()
    db.refresh(supplier)
    return supplier


def create_product(db, name="Test stock product", vat=Decimal("24")):
    product = Product(name=name, is_stock_item=True, is_active=True, vat_percent=vat)
    db.add(product)
    db.commit()
    db.refresh(product)
    return product


def create_location(db, code="DEFAULT"):
    warehouse, default_location = create_default_warehouse(db)
    if code == "DEFAULT":
        db.commit()
        return default_location
    location = WarehouseLocation(warehouse_id=warehouse.id, code=code, name=code, is_active=True)
    db.add(location)
    db.commit()
    db.refresh(location)
    return location


def create_register(db, name="Inventory Register"):
    register = CashRegister(name=name, is_active=True)
    db.add(register)
    db.commit()
    db.refresh(register)
    return register


def receipt_with_line(
    db,
    *,
    product,
    location,
    user,
    supplier,
    qty,
    unit_cost,
    freight="0",
    other="0",
    allocation_method="by_value",
    vat="24",
):
    receipt = create_goods_receipt(
        db,
        supplier_id=supplier.id,
        receipt_date=date.today(),
        received_by_user_id=user.id,
        freight_total_ex_vat=freight,
        other_costs_total_ex_vat=other,
        allocation_method=allocation_method,
        delivery_number=f"DN-{qty}-{unit_cost}-{freight}-{other}",
    )
    add_goods_receipt_line(
        db,
        goods_receipt_id=receipt.id,
        product_id=product.id,
        destination_location_id=location.id,
        quantity_value=qty,
        purchase_unit_price_ex_vat=unit_cost,
        vat_rate=vat,
    )
    db.refresh(receipt)
    return receipt


def test_first_and_second_receipts_update_weighted_average_higher_and_lower_costs():
    with SessionLocal() as db:
        user = create_user(db)
        supplier = create_supplier(db)
        location = create_location(db)
        product = create_product(db)

        first = receipt_with_line(db, product=product, location=location, user=user, supplier=supplier, qty="10", unit_cost="105")
        preview_first = preview_goods_receipt(db, first)
        assert preview_first["lines"][0]["projected_weighted_average_cost"] == Decimal("105.000000")
        post_goods_receipt(db, goods_receipt_id=first.id, posted_by_user_id=user.id)
        db.refresh(product)
        assert product.current_inventory_quantity == Decimal("10.000")
        assert product.current_weighted_average_cost_ex_vat == Decimal("105.000000")
        assert product.current_inventory_value_ex_vat == Decimal("1050.00")

        second = receipt_with_line(db, product=product, location=location, user=user, supplier=supplier, qty="5", unit_cost="120", freight="20")
        preview_second = preview_goods_receipt(db, second)
        assert preview_second["lines"][0]["landed_unit_cost_ex_vat"] == Decimal("124.000000")
        assert preview_second["lines"][0]["projected_weighted_average_cost"] == Decimal("111.333333")
        post_goods_receipt(db, goods_receipt_id=second.id, posted_by_user_id=user.id)
        db.refresh(product)
        assert product.current_inventory_quantity == Decimal("15.000")
        assert product.current_weighted_average_cost_ex_vat == Decimal("111.333333")
        assert product.current_inventory_value_ex_vat == Decimal("1670.00")

        third = receipt_with_line(db, product=product, location=location, user=user, supplier=supplier, qty="5", unit_cost="90")
        post_goods_receipt(db, goods_receipt_id=third.id, posted_by_user_id=user.id)
        db.refresh(product)
        assert product.current_inventory_quantity == Decimal("20.000")
        assert product.current_inventory_value_ex_vat == Decimal("2120.00")
        assert product.current_weighted_average_cost_ex_vat == Decimal("106.000000")


def test_duplicate_product_receipt_lines_preview_and_post_reconcile_same_location():
    with SessionLocal() as db:
        user = create_user(db)
        supplier = create_supplier(db)
        location = create_location(db)
        product = create_product(db, "Test Duplicate Lines Same")
        opening = receipt_with_line(db, product=product, location=location, user=user, supplier=supplier, qty="10", unit_cost="10")
        post_goods_receipt(db, goods_receipt_id=opening.id, posted_by_user_id=user.id)

        receipt = create_goods_receipt(
            db,
            supplier_id=supplier.id,
            receipt_date=date.today(),
            received_by_user_id=user.id,
            freight_total_ex_vat="10",
            allocation_method="by_value",
        )
        add_goods_receipt_line(db, goods_receipt_id=receipt.id, product_id=product.id, destination_location_id=location.id, quantity_value="5", purchase_unit_price_ex_vat="12")
        add_goods_receipt_line(db, goods_receipt_id=receipt.id, product_id=product.id, destination_location_id=location.id, quantity_value="5", purchase_unit_price_ex_vat="14")
        db.refresh(receipt)
        preview = preview_goods_receipt(db, receipt)

        assert preview["projected_by_product"][product.id]["quantity"] == Decimal("20.000")
        assert preview["projected_by_product"][product.id]["value"] == Decimal("240.00")
        assert preview["projected_by_product"][product.id]["average"] == Decimal("12.000000")
        assert sum(item["allocated_freight_ex_vat"] for item in preview["lines"]) == Decimal("10.00")

        post_goods_receipt(db, goods_receipt_id=receipt.id, posted_by_user_id=user.id)
        db.refresh(product)
        assert product.current_inventory_quantity == Decimal("20.000")
        assert product.current_inventory_value_ex_vat == Decimal("240.00")
        assert product.current_weighted_average_cost_ex_vat == Decimal("12.000000")
        assert product.inventory_balances[0].quantity_on_hand == Decimal("20.000")
        assert inventory_reconciliation(db, product_ids={product.id})["is_clean"]


def test_duplicate_product_receipt_lines_reconcile_different_locations():
    with SessionLocal() as db:
        user = create_user(db)
        supplier = create_supplier(db)
        loc_one = create_location(db)
        loc_two = create_location(db, "DUP2")
        product = create_product(db, "Test Duplicate Lines Locations")
        receipt = create_goods_receipt(
            db,
            supplier_id=supplier.id,
            receipt_date=date.today(),
            received_by_user_id=user.id,
            freight_total_ex_vat="3",
            other_costs_total_ex_vat="2",
            allocation_method="by_quantity",
        )
        add_goods_receipt_line(db, goods_receipt_id=receipt.id, product_id=product.id, destination_location_id=loc_one.id, quantity_value="1", purchase_unit_price_ex_vat="10")
        add_goods_receipt_line(db, goods_receipt_id=receipt.id, product_id=product.id, destination_location_id=loc_two.id, quantity_value="2", purchase_unit_price_ex_vat="20")
        db.refresh(receipt)
        preview = preview_goods_receipt(db, receipt)
        assert preview["projected_by_product"][product.id]["quantity"] == Decimal("3.000")
        assert preview["total_landed_cost_ex_vat"] == Decimal("55.00")

        post_goods_receipt(db, goods_receipt_id=receipt.id, posted_by_user_id=user.id)
        db.refresh(product)
        balances = {balance.warehouse_location_id: balance for balance in product.inventory_balances}
        assert balances[loc_one.id].quantity_on_hand == Decimal("1.000")
        assert balances[loc_two.id].quantity_on_hand == Decimal("2.000")
        assert product.current_inventory_value_ex_vat == Decimal("55.00")
        assert inventory_reconciliation(db, product_ids={product.id})["is_clean"]


def test_landed_cost_allocation_by_value_quantity_and_rounding_reconciliation():
    with SessionLocal() as db:
        user = create_user(db)
        supplier = create_supplier(db)
        location = create_location(db)
        product_a = create_product(db, "Test Allocation A")
        product_b = create_product(db, "Test Allocation B")
        receipt = create_goods_receipt(
            db,
            supplier_id=supplier.id,
            receipt_date=date.today(),
            received_by_user_id=user.id,
            freight_total_ex_vat="10.01",
            other_costs_total_ex_vat="1.00",
            allocation_method="by_value",
        )
        line_a = add_goods_receipt_line(db, goods_receipt_id=receipt.id, product_id=product_a.id, destination_location_id=location.id, quantity_value="1", purchase_unit_price_ex_vat="100")
        line_b = add_goods_receipt_line(db, goods_receipt_id=receipt.id, product_id=product_b.id, destination_location_id=location.id, quantity_value="3", purchase_unit_price_ex_vat="100")
        db.refresh(receipt)

        by_value = allocate_landed_costs(receipt.lines, Decimal("10.01"), Decimal("1.00"), "by_value")
        assert sum(row["freight"] for row in by_value.values()) == Decimal("10.01")
        assert by_value[line_a.id]["freight"] == Decimal("2.50")
        assert by_value[line_b.id]["freight"] == Decimal("7.51")

        by_quantity = allocate_landed_costs(receipt.lines, Decimal("10.01"), Decimal("1.00"), "by_quantity")
        assert sum(row["freight"] for row in by_quantity.values()) == Decimal("10.01")
        assert by_quantity[line_a.id]["freight"] == Decimal("2.50")
        assert by_quantity[line_b.id]["freight"] == Decimal("7.51")


def test_vat_is_excluded_from_inventory_value_and_draft_has_no_stock_impact():
    with SessionLocal() as db:
        user = create_user(db)
        supplier = create_supplier(db)
        location = create_location(db)
        product = create_product(db, "Test VAT Excluded", vat=Decimal("24"))
        receipt = receipt_with_line(db, product=product, location=location, user=user, supplier=supplier, qty="2", unit_cost="100", vat="24")
        preview = preview_goods_receipt(db, receipt)
        assert preview["vat_total"] == Decimal("48.00")
        assert preview["total_landed_cost_ex_vat"] == Decimal("200.00")
        db.refresh(product)
        assert product.current_inventory_quantity in (None, Decimal("0.000"))
        assert product.current_inventory_value_ex_vat in (None, Decimal("0.00"))
        post_goods_receipt(db, goods_receipt_id=receipt.id, posted_by_user_id=user.id)
        db.refresh(product)
        assert product.current_inventory_value_ex_vat == Decimal("200.00")


def test_freight_and_other_cost_vat_reconcile_without_inventory_valuation():
    with SessionLocal() as db:
        user = create_user(db)
        supplier = create_supplier(db)
        location = create_location(db)
        product = create_product(db, "Test Landed VAT", vat=Decimal("10"))
        receipt = create_goods_receipt(
            db,
            supplier_id=supplier.id,
            receipt_date=date.today(),
            received_by_user_id=user.id,
            freight_total_ex_vat="20",
            freight_vat_rate="24",
            other_costs_total_ex_vat="10",
            other_costs_vat_rate="0",
        )
        add_goods_receipt_line(
            db,
            goods_receipt_id=receipt.id,
            product_id=product.id,
            destination_location_id=location.id,
            quantity_value="2",
            purchase_unit_price_ex_vat="100",
            vat_rate="10",
        )
        db.refresh(receipt)
        preview = preview_goods_receipt(db, receipt)
        assert preview["product_vat_total"] == Decimal("20.00")
        assert preview["freight_vat_amount"] == Decimal("4.80")
        assert preview["other_costs_vat_amount"] == Decimal("0.00")
        assert preview["vat_total"] == Decimal("24.80")
        assert preview["total_landed_cost_ex_vat"] == Decimal("230.00")
        assert preview["total_inc_vat"] == Decimal("254.80")

        post_goods_receipt(db, goods_receipt_id=receipt.id, posted_by_user_id=user.id)
        db.refresh(product)
        db.refresh(receipt)
        assert product.current_inventory_value_ex_vat == Decimal("230.00")
        assert receipt.freight_total_inc_vat == Decimal("24.80")
        assert receipt.other_costs_total_inc_vat == Decimal("10.00")


def test_posting_creates_transactions_balances_audit_and_posted_receipt_is_immutable():
    with SessionLocal() as db:
        user = create_user(db)
        supplier = create_supplier(db)
        location = create_location(db)
        product = create_product(db, "Test Posting Audit")
        receipt = receipt_with_line(db, product=product, location=location, user=user, supplier=supplier, qty="4", unit_cost="25", freight="4", other="1")
        post_goods_receipt(db, goods_receipt_id=receipt.id, posted_by_user_id=user.id)
        db.refresh(receipt)
        assert receipt.status == "posted"
        assert receipt.lines[0].landed_unit_cost_ex_vat == Decimal("26.250000")
        assert len(receipt.transactions) == 1
        assert receipt.transactions[0].transaction_type == "purchase"
        assert receipt.transactions[0].total_inventory_cost == Decimal("105.00")
        assert receipt.lines[0].product.inventory_balances[0].inventory_value_ex_vat == Decimal("105.00")
        assert db.query(AuditLog).filter(AuditLog.event_type == "goods_receipt.posted").count() == 1
        with pytest.raises(ValueError, match="cannot be edited"):
            add_goods_receipt_line(db, goods_receipt_id=receipt.id, product_id=product.id, destination_location_id=location.id, quantity_value="1", purchase_unit_price_ex_vat="1")


def test_cancellation_creates_reversal_and_restores_weighted_average():
    with SessionLocal() as db:
        user = create_user(db)
        supplier = create_supplier(db)
        location = create_location(db)
        product = create_product(db, "Test Cancel")
        receipt = receipt_with_line(db, product=product, location=location, user=user, supplier=supplier, qty="3", unit_cost="10", freight="3")
        post_goods_receipt(db, goods_receipt_id=receipt.id, posted_by_user_id=user.id)
        cancel_goods_receipt(db, goods_receipt_id=receipt.id, user_id=user.id, reason="Wrong delivery")
        db.refresh(receipt)
        db.refresh(product)
        assert receipt.status == "cancelled"
        assert product.current_inventory_quantity == Decimal("0.000")
        assert product.current_inventory_value_ex_vat == Decimal("0.00")
        assert product.current_weighted_average_cost_ex_vat is None
        reversals = [
            transaction
            for transaction in receipt.transactions
            if transaction.reversal_of_transaction_id is not None
        ]
        assert len(reversals) == 1
        assert reversals[0].transaction_type == "inventory_adjustment"
        assert reversals[0].total_inventory_cost == Decimal("-33.00")


def test_transfer_preserves_total_company_inventory_value_and_sale_issue_uses_cost_snapshot():
    with SessionLocal() as db:
        user = create_user(db)
        supplier = create_supplier(db)
        source = create_location(db)
        target = create_location(db, "SECOND")
        product = create_product(db, "Test Transfer")
        receipt = receipt_with_line(db, product=product, location=source, user=user, supplier=supplier, qty="10", unit_cost="50")
        post_goods_receipt(db, goods_receipt_id=receipt.id, posted_by_user_id=user.id)
        before_value = inventory_valuation(db)["total_inventory_value_ex_vat"]
        transfer_stock(db, product_id=product.id, from_location_id=source.id, to_location_id=target.id, quantity_value="4", created_by_user_id=user.id)
        after_transfer_value = inventory_valuation(db)["total_inventory_value_ex_vat"]
        assert after_transfer_value == before_value

        transaction = issue_stock_for_sale(db, product_id=product.id, warehouse_location_id=target.id, quantity_value="2", sale_id=123, created_by_user_id=user.id)
        assert transaction.transaction_type == "sale"
        assert transaction.unit_cost_ex_vat == Decimal("50.000000")
        assert transaction.total_inventory_cost == Decimal("-100.00")
        db.refresh(product)
        assert product.current_inventory_quantity == Decimal("8.000")
        assert product.current_inventory_value_ex_vat == Decimal("400.00")


def test_negative_stock_zero_quantity_negative_cost_and_transaction_rollback():
    with SessionLocal() as db:
        user = create_user(db)
        seller = create_user(db, "Inventory Seller", "seller")
        supplier = create_supplier(db)
        location = create_location(db)
        product = create_product(db, "Test Validation")
        receipt = create_goods_receipt(db, supplier_id=supplier.id, receipt_date=date.today(), received_by_user_id=user.id)
        with pytest.raises(ValueError, match="Quantity"):
            add_goods_receipt_line(db, goods_receipt_id=receipt.id, product_id=product.id, destination_location_id=location.id, quantity_value="0", purchase_unit_price_ex_vat="1")
        with pytest.raises(ValueError, match="Purchase unit price"):
            add_goods_receipt_line(db, goods_receipt_id=receipt.id, product_id=product.id, destination_location_id=location.id, quantity_value="1", purchase_unit_price_ex_vat="-1")
        with pytest.raises(ValueError, match="Only Admin or Manager"):
            create_goods_receipt(db, supplier_id=supplier.id, receipt_date=date.today(), received_by_user_id=seller.id)

        valid = receipt_with_line(db, product=product, location=location, user=user, supplier=supplier, qty="1", unit_cost="10")
        post_goods_receipt(db, goods_receipt_id=valid.id, posted_by_user_id=user.id)
        with pytest.raises(ValueError, match="Negative stock"):
            issue_stock_for_sale(db, product_id=product.id, warehouse_location_id=location.id, quantity_value="2", sale_id=1, created_by_user_id=user.id)
        db.refresh(product)
        assert product.current_inventory_quantity == Decimal("1.000")
        assert product.current_inventory_value_ex_vat == Decimal("10.00")


def test_multi_line_multiple_warehouse_report_reconciles_with_movement_ledger():
    with SessionLocal() as db:
        user = create_user(db)
        supplier = create_supplier(db)
        loc_one = create_location(db)
        loc_two = create_location(db, "B2")
        product_a = create_product(db, "Test Report A")
        product_b = create_product(db, "Test Report B")
        receipt = create_goods_receipt(
            db,
            supplier_id=supplier.id,
            receipt_date=date.today(),
            received_by_user_id=user.id,
            freight_total_ex_vat="6",
            allocation_method="by_quantity",
        )
        add_goods_receipt_line(db, goods_receipt_id=receipt.id, product_id=product_a.id, destination_location_id=loc_one.id, quantity_value="2", purchase_unit_price_ex_vat="10")
        add_goods_receipt_line(db, goods_receipt_id=receipt.id, product_id=product_b.id, destination_location_id=loc_two.id, quantity_value="4", purchase_unit_price_ex_vat="20")
        post_goods_receipt(db, goods_receipt_id=receipt.id, posted_by_user_id=user.id)
        report = inventory_valuation(db)
        assert report["total_inventory_value_ex_vat"] == report["transaction_ledger_value_ex_vat"]
        assert report["total_inventory_value_ex_vat"] == Decimal("106.00")
        assert len(report["by_product"]) == 2
        assert report["recent_cost_changes"]


def test_inventory_reconciliation_detects_and_repairs_cache_tampering_without_changing_ledger():
    with SessionLocal() as db:
        user = create_user(db)
        supplier = create_supplier(db)
        location = create_location(db)
        product = create_product(db, "Test Reconciliation")
        receipt = receipt_with_line(db, product=product, location=location, user=user, supplier=supplier, qty="2", unit_cost="10")
        post_goods_receipt(db, goods_receipt_id=receipt.id, posted_by_user_id=user.id)
        transaction_count = db.query(InventoryTransaction).count()
        assert inventory_reconciliation(db, product_ids={product.id})["is_clean"]

        product.current_inventory_value_ex_vat = Decimal("999.00")
        product.inventory_balances[0].quantity_on_hand = Decimal("999.000")
        db.commit()
        report = inventory_reconciliation(db, product_ids={product.id})
        assert not report["is_clean"]
        assert report["product_mismatches"]
        assert report["location_mismatches"]

        repaired_from = repair_inventory_caches_from_ledger(db, user_id=user.id, reason="Test repair")
        assert repaired_from["product_mismatches"]
        assert db.query(InventoryTransaction).count() == transaction_count
        db.refresh(product)
        assert product.current_inventory_quantity == Decimal("2.000")
        assert product.current_inventory_value_ex_vat == Decimal("20.00")
        assert inventory_reconciliation(db, product_ids={product.id})["is_clean"]


def test_new_posting_is_blocked_on_material_cache_ledger_mismatch_but_allows_rounding_tolerance():
    with SessionLocal() as db:
        user = create_user(db)
        supplier = create_supplier(db)
        location = create_location(db)
        product = create_product(db, "Test Block Mismatch")
        receipt = receipt_with_line(db, product=product, location=location, user=user, supplier=supplier, qty="2", unit_cost="10")
        post_goods_receipt(db, goods_receipt_id=receipt.id, posted_by_user_id=user.id)

        product.current_inventory_value_ex_vat = Decimal("20.004")
        db.commit()
        tolerated = receipt_with_line(db, product=product, location=location, user=user, supplier=supplier, qty="1", unit_cost="10")
        post_goods_receipt(db, goods_receipt_id=tolerated.id, posted_by_user_id=user.id)

        product.current_inventory_value_ex_vat = Decimal("999.00")
        db.commit()
        blocked = receipt_with_line(db, product=product, location=location, user=user, supplier=supplier, qty="1", unit_cost="10")
        with pytest.raises(ValueError, match="cache differs from ledger"):
            post_goods_receipt(db, goods_receipt_id=blocked.id, posted_by_user_id=user.id)


def test_historical_cost_snapshots_do_not_change_when_product_purchase_price_changes():
    with SessionLocal() as db:
        user = create_user(db)
        supplier = create_supplier(db)
        location = create_location(db)
        product = create_product(db, "Test History")
        receipt = receipt_with_line(db, product=product, location=location, user=user, supplier=supplier, qty="2", unit_cost="30")
        post_goods_receipt(db, goods_receipt_id=receipt.id, posted_by_user_id=user.id)
        transaction = receipt.transactions[0]
        assert transaction.unit_cost_ex_vat == Decimal("30.000000")
        product.current_purchase_price_ex_vat = Decimal("999.00")
        db.commit()
        db.refresh(transaction)
        assert transaction.unit_cost_ex_vat == Decimal("30.000000")


def test_inventory_transactions_are_immutable():
    with SessionLocal() as db:
        user = create_user(db)
        supplier = create_supplier(db)
        location = create_location(db)
        product = create_product(db, "Test Immutable Ledger")
        receipt = receipt_with_line(db, product=product, location=location, user=user, supplier=supplier, qty="1", unit_cost="10")
        post_goods_receipt(db, goods_receipt_id=receipt.id, posted_by_user_id=user.id)
        transaction = receipt.transactions[0]

        transaction.reference = "edited"
        with pytest.raises(ValueError, match="Inventory transactions are immutable"):
            db.commit()
        db.rollback()

        transaction = db.get(InventoryTransaction, transaction.id)
        db.delete(transaction)
        with pytest.raises(ValueError, match="Inventory transactions are immutable"):
            db.commit()
        db.rollback()


def test_inventory_transaction_sqlite_triggers_reject_update_delete_but_allow_reversal_insert():
    with SessionLocal() as db:
        user = create_user(db)
        supplier = create_supplier(db)
        location = create_location(db)
        product = create_product(db, "Test Trigger Ledger")
        receipt = receipt_with_line(db, product=product, location=location, user=user, supplier=supplier, qty="1", unit_cost="10")
        post_goods_receipt(db, goods_receipt_id=receipt.id, posted_by_user_id=user.id)
        transaction = receipt.transactions[0]
        transaction_id = transaction.id

        with pytest.raises(DatabaseError, match="immutable"):
            db.execute(text("UPDATE inventory_transactions SET reference = 'raw edit' WHERE id = :id"), {"id": transaction_id})
            db.commit()
        db.rollback()

        with pytest.raises(DatabaseError, match="immutable"):
            db.execute(text("DELETE FROM inventory_transactions WHERE id = :id"), {"id": transaction_id})
            db.commit()
        db.rollback()

        cancel_goods_receipt(db, goods_receipt_id=receipt.id, user_id=user.id, reason="Trigger reversal")
        reversals = db.query(InventoryTransaction).filter(InventoryTransaction.reversal_of_transaction_id == transaction_id).all()
        assert len(reversals) == 1


def test_sale_records_cogs_and_historical_profit_snapshot_from_weighted_average():
    with SessionLocal() as db:
        manager = create_user(db)
        seller = create_user(db, "Inventory Sales", "seller")
        supplier = create_supplier(db)
        location = create_location(db)
        register = create_register(db)
        product = create_product(db, "Test COGS Product", vat=Decimal("0"))
        first_receipt = receipt_with_line(
            db,
            product=product,
            location=location,
            user=manager,
            supplier=supplier,
            qty="10",
            unit_cost="50",
            vat="0",
        )
        post_goods_receipt(db, goods_receipt_id=first_receipt.id, posted_by_user_id=manager.id)
        shift = open_shift(
            db,
            seller_id=seller.id,
            cash_register_id=register.id,
            business_date=date.today(),
            starting_cash="0",
        )

        sale = create_sale_with_payment(
            db,
            seller_id=seller.id,
            shift_id=shift.id,
            payment_method="card",
            description="Stock sale",
            quantity="2",
            unit_price="100",
            vat_percent="0",
            product_id=product.id,
        )
        db.refresh(product)
        assert sale.cost_of_goods_sold_ex_vat == Decimal("100.00")
        assert sale.gross_profit_ex_vat == Decimal("100.00")
        assert sale.gross_margin_percent == Decimal("50.000")
        assert sale.lines[0].cost_of_goods_sold_ex_vat == Decimal("100.00")
        sale_transactions = [row for row in sale.inventory_transactions if row.transaction_type == "sale"]
        assert len(sale_transactions) == 1
        assert sale_transactions[0].unit_cost_ex_vat == Decimal("50.000000")
        assert sale_transactions[0].total_inventory_cost == Decimal("-100.00")

        later_receipt = receipt_with_line(
            db,
            product=product,
            location=location,
            user=manager,
            supplier=supplier,
            qty="10",
            unit_cost="200",
            vat="0",
        )
        post_goods_receipt(db, goods_receipt_id=later_receipt.id, posted_by_user_id=manager.id)
        db.refresh(product)
        db.refresh(sale)
        assert product.current_weighted_average_cost_ex_vat != Decimal("50.000000")
        assert sale.cost_of_goods_sold_ex_vat == Decimal("100.00")
        assert sale.gross_profit_ex_vat == Decimal("100.00")


def test_shared_register_stock_sale_keeps_operator_and_credited_seller_separate():
    with SessionLocal() as db:
        manager = create_user(db, "Shared Register Operator", "manager")
        credited_seller = create_user(db, "Shared Register Seller", "seller")
        replacement_seller = create_user(db, "Shared Register Replacement", "seller")
        supplier = create_supplier(db)
        location = create_location(db)
        register = create_register(db)
        product = create_product(db, "Test Shared Register Stock", vat=Decimal("0"))
        receipt = receipt_with_line(db, product=product, location=location, user=manager, supplier=supplier, qty="5", unit_cost="10", vat="0")
        post_goods_receipt(db, goods_receipt_id=receipt.id, posted_by_user_id=manager.id)
        shift = open_shift(
            db,
            seller_id=manager.id,
            cash_register_id=register.id,
            business_date=date.today(),
            starting_cash="0",
        )

        sale = create_sale_with_payment(
            db,
            seller_id=credited_seller.id,
            shift_id=shift.id,
            payment_method="card",
            description="Shared sale",
            quantity="1",
            unit_price="30",
            vat_percent="0",
            product_id=product.id,
            created_by_user_id=manager.id,
            seller_selection_mode="selectable_active_seller",
        )

        transaction = sale.inventory_transactions[0]
        assert sale.sold_by_user_id == credited_seller.id
        assert sale.created_by_user_id == manager.id
        assert sale.payments[0].received_by_user_id == manager.id
        assert transaction.created_by_user_id == manager.id
        assert sale.cost_of_goods_sold_ex_vat == Decimal("10.00")
        report = seller_report(
            db,
            seller_id=credited_seller.id,
            start_date=date.today(),
            end_date=date.today() + timedelta(days=1),
        )
        assert report["gross_sales"] == Decimal("30.00")

        original_cogs = sale.cost_of_goods_sold_ex_vat
        correct_sale_seller(
            db,
            sale_id=sale.id,
            new_sold_by_user_id=replacement_seller.id,
            corrected_by_user_id=manager.id,
            reason="Wrong seller at shared register",
        )
        db.refresh(transaction)
        db.refresh(sale)
        assert transaction.created_by_user_id == manager.id
        assert sale.cost_of_goods_sold_ex_vat == original_cogs
        assert sale.sold_by_user_id == replacement_seller.id


def test_discounted_fractional_stock_sale_margin_and_service_sale_no_inventory_cogs():
    with SessionLocal() as db:
        manager = create_user(db)
        seller = create_user(db, "Margin Seller", "seller")
        supplier = create_supplier(db)
        location = create_location(db)
        register = create_register(db)
        stock_product = create_product(db, "Test Margin Stock", vat=Decimal("0"))
        service_product = Product(name="Test Service Product", is_stock_item=False, is_active=True, vat_percent=Decimal("0"))
        db.add(service_product)
        db.commit()
        receipt = receipt_with_line(db, product=stock_product, location=location, user=manager, supplier=supplier, qty="2", unit_cost="10", vat="0")
        post_goods_receipt(db, goods_receipt_id=receipt.id, posted_by_user_id=manager.id)
        shift = open_shift(db, seller_id=seller.id, cash_register_id=register.id, business_date=date.today(), starting_cash="0")

        sale = create_sale_with_payment(
            db,
            seller_id=seller.id,
            shift_id=shift.id,
            payment_method="card",
            description="Discounted fractional stock",
            quantity="1.500",
            unit_price="20",
            vat_percent="0",
            discount_amount="5",
            product_id=stock_product.id,
        )
        assert sale.subtotal == Decimal("25.00")
        assert sale.cost_of_goods_sold_ex_vat == Decimal("15.00")
        assert sale.gross_profit_ex_vat == Decimal("10.00")
        assert sale.gross_margin_percent == Decimal("40.000")

        service_sale = create_sale_with_payment(
            db,
            seller_id=seller.id,
            shift_id=shift.id,
            payment_method="card",
            description="Service sale",
            quantity="1",
            unit_price="100",
            vat_percent="0",
            product_id=service_product.id,
        )
        assert service_sale.cost_of_goods_sold_ex_vat == Decimal("0.00")
        assert service_sale.gross_profit_ex_vat == Decimal("100.00")
        assert not service_sale.inventory_transactions

        zero_sale = create_sale_with_payment(
            db,
            seller_id=seller.id,
            shift_id=shift.id,
            payment_method="card",
            description="Zero sale",
            quantity="1",
            unit_price="0",
            vat_percent="0",
        )
        assert zero_sale.gross_profit_ex_vat == Decimal("0.00")
        assert zero_sale.gross_margin_percent is None


def test_refund_does_not_restore_stock_until_customer_return_flow_exists():
    with SessionLocal() as db:
        manager = create_user(db)
        seller = create_user(db, "Refund Stock Seller", "seller")
        supplier = create_supplier(db)
        location = create_location(db)
        register = create_register(db)
        product = create_product(db, "Test Refund No Stock Return", vat=Decimal("0"))
        receipt = receipt_with_line(db, product=product, location=location, user=manager, supplier=supplier, qty="2", unit_cost="10", vat="0")
        post_goods_receipt(db, goods_receipt_id=receipt.id, posted_by_user_id=manager.id)
        shift = open_shift(db, seller_id=seller.id, cash_register_id=register.id, business_date=date.today(), starting_cash="0")
        sale = create_sale_with_payment(db, seller_id=seller.id, shift_id=shift.id, payment_method="card", description="Refunded stock", quantity="1", unit_price="20", vat_percent="0", product_id=product.id)
        before_refund_qty = product.current_inventory_quantity
        from app.services.sales_service import add_refund

        add_refund(db, sale_id=sale.id, refund_shift_id=shift.id, seller_id=seller.id, amount="20", payment_method="card", reason="Customer refund")
        db.refresh(product)
        assert product.current_inventory_quantity == before_refund_qty
        assert len(sale.inventory_transactions) == 1


def test_failed_stock_sale_rolls_back_flushed_sale_rows():
    with SessionLocal() as db:
        seller = create_user(db, "Rollback Inventory Seller", "seller")
        register = create_register(db, "Rollback Inventory Register")
        product = create_product(db, "Test No Stock Product", vat=Decimal("0"))
        shift = open_shift(
            db,
            seller_id=seller.id,
            cash_register_id=register.id,
            business_date=date.today(),
            starting_cash="0",
        )

        before_count = db.query(Sale).count()
        with pytest.raises(ValueError, match="Negative stock"):
            create_sale_with_payment(
                db,
                seller_id=seller.id,
                shift_id=shift.id,
                payment_method="card",
                description="No stock sale",
                quantity="1",
                unit_price="10",
                vat_percent="0",
                product_id=product.id,
            )

        assert db.query(Sale).count() == before_count


def test_product_cost_profile_reconstructs_stock_from_ledger():
    with SessionLocal() as db:
        user = create_user(db)
        supplier = create_supplier(db)
        location = create_location(db)
        product = create_product(db, "Test Profile")
        receipt = receipt_with_line(db, product=product, location=location, user=user, supplier=supplier, qty="3", unit_cost="11", freight="3")
        post_goods_receipt(db, goods_receipt_id=receipt.id, posted_by_user_id=user.id)

        profile = product_cost_profile(db, product.id)

        assert profile["last_supplier"].id == supplier.id
        assert profile["last_purchase_price"] == Decimal("12.000000")
        assert profile["ledger_state"] == (Decimal("3.000"), Decimal("36.00"), Decimal("12.000000"))


def test_existing_database_migrates_without_guessing_historical_cost(tmp_path):
    db_path = tmp_path / "existing.sqlite"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.as_posix()}")
    command.upgrade(config, "3f0d1c9a8b22")
    engine = create_engine(f"sqlite:///{db_path.as_posix()}", future=True)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO products (name, unit_price, vat_percent, unit, is_active, is_stock_item, created_at, updated_at) "
                "VALUES ('Legacy product', 12.34, 24, 'pcs', 1, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
    command.upgrade(config, "head")
    inspector = inspect(engine)
    product_columns = {column["name"] for column in inspector.get_columns("products")}
    assert "current_weighted_average_cost_ex_vat" in product_columns
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT current_weighted_average_cost_ex_vat, current_inventory_quantity, current_inventory_value_ex_vat "
                "FROM products WHERE name = 'Legacy product'"
            )
        ).one()
    assert row[0] is None
    assert Decimal(str(row[1])) == Decimal("0")
    assert Decimal(str(row[2])) == Decimal("0")
