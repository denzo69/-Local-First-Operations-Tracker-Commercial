import sqlite3
from contextlib import nullcontext
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import app.services.auth_service as auth_service
from app.database import SessionLocal
from app.migration_bootstrap import (
    BootstrapPlan,
    MigrationBootstrapError,
    SchemaClassification,
    SchemaInspection,
    classify_schema,
    create_sqlite_backup,
    format_plan,
    inspect_database,
    main as migration_bootstrap_main,
    plan_bootstrap,
    sqlite_path_from_url,
)
from app.models import (
    CashRegister,
    DailyClosing,
    DailyClosingSnapshot,
    InventoryBalance,
    InventoryTransaction,
    Job,
    JobItem,
    Product,
    Role,
    Sale,
    Shift,
    Supplier,
    User,
    Warehouse,
    WarehouseLocation,
)
from app.routes.daily_closings import close_business_day, daily_closing_detail, daily_closing_snapshot_detail, reopen_closing
from app.routes.backups import restore_selected_backup
from app.routes.seller_reports import seller_reports
import app.routes.backups as backups_route
from app.services.auth_service import hash_password
from app.services.auth_service import get_session_user
from app.services.inventory_service import (
    cancel_goods_receipt,
    create_goods_receipt,
    allocate_landed_costs,
    issue_stock_for_delivery_note,
    issue_stock_for_sale,
    issue_stock_for_sale_from_available_locations,
    inventory_ledger,
    post_goods_receipt,
    product_cost_profile,
    preview_goods_receipt,
    require_inventory_operational_user,
    reverse_delivery_note_item_issue,
    repair_inventory_caches_from_ledger,
    transfer_stock,
    _location,
    parse_finite_decimal as inventory_parse_finite_decimal,
)
from app.services.sales_service import (
    PaymentInput,
    SaleLineInput,
    _delivery_note_line_cogs,
    add_cash_movement,
    add_refund,
    confirm_invoice_unpaid,
    create_daily_closing,
    create_sale_from_lines,
    open_shift,
    parse_daily_closing_snapshot,
    record_invoice_reminder_sent,
    reopen_daily_closing,
    require_operational_user,
    require_invoice_sale,
    require_payment_method,
    require_sale_seller_override_user,
    require_sales_credit_user,
    resolve_sale_seller,
    resolve_sale_context,
    settlement_status_for,
    transfer_sale_to_invoicing,
    correct_sale_seller,
)
from app.services.settings_service import set_app_settings
from app.services.receipt_number_service import allocate_receipt_number, allocate_sale_document_number


def _request():
    return SimpleNamespace(cookies={}, state=SimpleNamespace(current_user=None))


def _user(db, name: str, role_code: str = "admin", *, active: bool = True, credit: bool = True) -> User:
    from app.services.sales_service import ensure_default_roles

    ensure_default_roles(db)
    role = db.query(Role).filter(Role.code == role_code).one()
    user = User(
        name=name,
        login_name=name.lower().replace(" ", "."),
        password_hash=hash_password("secret123"),
        role=role,
        is_active=active,
        can_receive_sales_credit=credit,
    )
    db.add(user)
    db.flush()
    return user


def _stock_context(db):
    user = _user(db, "Inventory User", "admin")
    supplier = Supplier(name="Supplier", is_active=True)
    product = Product(
        name="Stock item",
        is_stock_item=True,
        is_active=True,
        current_inventory_quantity=Decimal("2.000"),
        current_inventory_value_ex_vat=Decimal("20.00"),
        current_weighted_average_cost_ex_vat=Decimal("10.000000"),
    )
    warehouse = Warehouse(name="Warehouse", code="WH", is_active=True)
    source = WarehouseLocation(warehouse=warehouse, code="A", name="A", is_active=True)
    target = WarehouseLocation(warehouse=warehouse, code="B", name="B", is_active=True)
    db.add_all([supplier, product, warehouse, source, target])
    db.flush()
    source_balance = InventoryBalance(
        product=product,
        warehouse_location=source,
        quantity_on_hand=Decimal("2.000"),
        quantity_reserved=Decimal("0.000"),
        quantity_available=Decimal("2.000"),
        inventory_value_ex_vat=Decimal("20.00"),
        weighted_average_cost_ex_vat=Decimal("10.000000"),
    )
    initial_transaction = InventoryTransaction(
        product=product,
        warehouse=warehouse,
        shelf_location=source,
        transaction_type="initial_balance",
        quantity_change=Decimal("2.000"),
        unit_cost_ex_vat=Decimal("10.000000"),
        allocated_freight_cost=Decimal("0.00"),
        allocated_other_cost=Decimal("0.00"),
        total_inventory_cost=Decimal("20.00"),
        inventory_value_before=Decimal("0.00"),
        inventory_value_after=Decimal("20.00"),
        stock_before=Decimal("0.000"),
        stock_after=Decimal("2.000"),
        weighted_average_cost_before=None,
        weighted_average_cost_after=Decimal("10.000000"),
        created_by_user_id=user.id,
    )
    db.add_all([source_balance, initial_transaction])
    db.commit()
    return user, supplier, product, source, target


def test_sales_authorization_and_legacy_seller_edges(monkeypatch):
    with SessionLocal() as db:
        admin = _user(db, "Admin", "admin")
        seller = _user(db, "Seller", "seller")
        viewer = _user(db, "Viewer", "read_only")
        manager_without_credit = _user(db, "Manager", "manager", credit=False)
        inactive = _user(db, "Inactive", "seller", active=False)
        register = CashRegister(name="Register", is_active=True)
        db.add(register)
        db.flush()
        shift = Shift(seller=seller, cash_register=register, business_date=date.today(), status="open")
        db.add(shift)
        db.commit()

        with pytest.raises(ValueError, match="Invalid payment method"):
            require_payment_method("coupon")
        with pytest.raises(ValueError, match="User not found"):
            require_operational_user(None)
        with pytest.raises(ValueError, match="Active user"):
            require_operational_user(inactive)
        with pytest.raises(ValueError, match="not allowed"):
            require_operational_user(viewer)
        with pytest.raises(ValueError, match="not eligible"):
            require_sales_credit_user(manager_without_credit)
        with pytest.raises(ValueError, match="Only Admin or Manager"):
            require_sale_seller_override_user(seller)

        sold_by, operator = resolve_sale_seller(
            db,
            shift=shift,
            selected_seller_id=seller.id,
            created_by_user_id=admin.id,
            seller_selection_mode="authenticated_user",
        )
        assert sold_by.id == admin.id
        assert operator.id == admin.id
        sold_by, _ = resolve_sale_seller(
            db,
            shift=shift,
            selected_seller_id=seller.id,
            created_by_user_id=manager_without_credit.id,
            seller_selection_mode="unknown",
        )
        assert sold_by.id == seller.id
        sold_by, _ = resolve_sale_seller(
            db,
            shift=shift,
            selected_seller_id=None,
            created_by_user_id=manager_without_credit.id,
            seller_selection_mode="selectable_active_seller",
        )
        assert sold_by.id == shift.seller_id
        authenticated_context = resolve_sale_context(
            db,
            shift_id=None,
            cash_register_id=None,
            seller_mode="default",
            selected_seller_id=None,
            created_by_user_id=admin.id,
            seller_selection_mode="authenticated_user",
        )
        assert authenticated_context.sold_by.id == admin.id
        shift_context = resolve_sale_context(
            db,
            shift_id=shift.id,
            cash_register_id=None,
            seller_mode="default",
            selected_seller_id=None,
            created_by_user_id=manager_without_credit.id,
            seller_selection_mode="shift_owner",
        )
        assert shift_context.sold_by.id == seller.id
        assert settlement_status_for(total=Decimal("10"), paid=Decimal("5"), invoice_requested=False) == "partially_paid"

        monkeypatch.setattr("app.services.sales_service.PAYMENT_METHODS", {"cash": "Cash", "delayed": "Delayed"})
        with pytest.raises(ValueError, match="Invalid immediate payment method"):
            create_sale_from_lines(
                db,
                lines=[SaleLineInput(description="Line", quantity="1", unit_price="1", vat_percent="24")],
                payments=[PaymentInput("delayed", "1")],
                created_by_user_id=admin.id,
            )


def test_sale_creation_and_refund_error_edges():
    with SessionLocal() as db:
        admin = _user(db, "Admin", "admin")
        seller = _user(db, "Seller", "seller")
        register = CashRegister(name="Register", is_active=True)
        other_register = CashRegister(name="Other", is_active=True)
        product = Product(name="Product", is_stock_item=False, is_active=True)
        db.add_all([register, other_register, product])
        db.flush()
        shift = Shift(seller=seller, cash_register=register, business_date=date.today(), status="open")
        other_shift = Shift(seller=admin, cash_register=other_register, business_date=date.today(), status="open")
        job = Job(title="Job", document_type="work_order")
        item = JobItem(job=job, product=product, description="Job item", quantity=1, unit_price=1, vat_percent=24, line_total=1)
        other_job = Job(title="Other", document_type="work_order")
        db.add_all([shift, other_shift, job, item, other_job])
        db.commit()

        sale = create_sale_from_lines(
            db,
            shift_id=shift.id,
            lines=[SaleLineInput(description="Line", quantity="1", unit_price="10", vat_percent="24")],
            payments=[PaymentInput("cash", "10")],
            created_by_user_id=admin.id,
        )
        with pytest.raises(ValueError, match="Customer not found"):
            create_sale_from_lines(
                db,
                lines=[SaleLineInput(description="Line", quantity="1", unit_price="1", vat_percent="24")],
                payments=[PaymentInput("cash", "1")],
                customer_id=999,
                created_by_user_id=admin.id,
            )
        with pytest.raises(ValueError, match="at least one line"):
            create_sale_from_lines(db, lines=[], payments=[PaymentInput("cash")], created_by_user_id=admin.id)
        with pytest.raises(ValueError, match="Work Order item not found"):
            create_sale_from_lines(
                db,
                lines=[SaleLineInput(description="Line", quantity="1", unit_price="1", vat_percent="24", work_order_item_id=999)],
                payments=[PaymentInput("cash", "1")],
                created_by_user_id=admin.id,
            )
        with pytest.raises(ValueError, match="does not belong"):
            create_sale_from_lines(
                db,
                work_order_id=other_job.id,
                lines=[
                    SaleLineInput(
                        description="Line",
                        quantity="1",
                        unit_price="1",
                        vat_percent="24",
                        work_order_item_id=item.id,
                    )
                ],
                payments=[PaymentInput("cash", "1")],
                created_by_user_id=admin.id,
            )
        with pytest.raises(ValueError, match="Discount cannot exceed"):
            create_sale_from_lines(
                db,
                lines=[SaleLineInput(description="Line", quantity="1", unit_price="1", vat_percent="24", discount_amount="2")],
                payments=[PaymentInput("cash", "1")],
                created_by_user_id=admin.id,
            )
        with pytest.raises(ValueError, match="requires a Work Order"):
            create_sale_from_lines(
                db,
                lines=[SaleLineInput(description="Line", quantity="1", unit_price="1", vat_percent="24")],
                payments=[PaymentInput("cash", "1")],
                source_type="work_order",
                created_by_user_id=admin.id,
            )
        product_fallback_sale = create_sale_from_lines(
            db,
            lines=[SaleLineInput(description="", product_id=product.id, quantity="1", unit_price="1", vat_percent="24")],
            payments=[PaymentInput("cash", "1")],
            created_by_user_id=admin.id,
        )
        assert product_fallback_sale.lines[0].description_snapshot == "Product"
        existing_work_order_sale = Sale(
            work_order_id=job.id,
            status="completed",
            settlement_status="awaiting_invoice",
            payment_method="invoice",
            subtotal=Decimal("1.00"),
            vat_total=Decimal("0.00"),
            total=Decimal("1.00"),
        )
        db.add(existing_work_order_sale)
        db.commit()
        assert (
            create_sale_from_lines(
                db,
                work_order_id=job.id,
                lines=[SaleLineInput(description="Line", quantity="1", unit_price="1", vat_percent="24")],
                payments=[PaymentInput("invoice")],
                source_type="work_order",
                created_by_user_id=admin.id,
            ).id
            == existing_work_order_sale.id
        )
        assert _delivery_note_line_cogs(db, work_order=job, work_order_item_id=item.id, product_id=product.id) is None
        with pytest.raises(ValueError, match="Sale not found"):
            add_refund(db, sale_id=999, refund_shift_id=shift.id, seller_id=seller.id, amount="1", payment_method="cash")
        with pytest.raises(ValueError, match="open shift"):
            add_refund(db, sale_id=sale.id, refund_shift_id=999, seller_id=seller.id, amount="1", payment_method="cash")
        with pytest.raises(ValueError, match="must match"):
            add_refund(db, sale_id=sale.id, refund_shift_id=other_shift.id, seller_id=seller.id, amount="1", payment_method="cash")
        empty_sale = Sale(
            payment_method="cash",
            settlement_status="paid",
            total=Decimal("1.00"),
            subtotal=Decimal("1.00"),
            vat_total=Decimal("0.00"),
            status="completed",
        )
        db.add(empty_sale)
        db.commit()
        with pytest.raises(ValueError, match="no lines"):
            add_refund(db, sale_id=empty_sale.id, refund_shift_id=None, seller_id=seller.id, amount="1", payment_method="cash")


def test_invoice_and_seller_correction_error_edges():
    with SessionLocal() as db:
        admin = _user(db, "Admin", "admin")
        seller = _user(db, "Seller", "seller")
        paid_sale = Sale(payment_method="cash", settlement_status="paid", subtotal=1, vat_total=0, total=1)
        non_invoice_sale = Sale(payment_method="cash", settlement_status="open", subtotal=1, vat_total=0, total=1)
        invoice_sale = Sale(payment_method="invoice", settlement_status="awaiting_invoice", subtotal=1, vat_total=0, total=1)
        db.add_all([paid_sale, non_invoice_sale, invoice_sale])
        db.commit()

        with pytest.raises(ValueError, match="not awaiting"):
            require_invoice_sale(paid_sale)
        with pytest.raises(ValueError, match="not an invoice"):
            require_invoice_sale(non_invoice_sale)
        with pytest.raises(ValueError, match="service is required"):
            transfer_sale_to_invoicing(
                db,
                sale_id=invoice_sale.id,
                service_name=" ",
                external_invoice_number="1",
                invoice_date_value=date.today(),
                due_date_value=date.today(),
            )
        with pytest.raises(ValueError, match="invoice number"):
            transfer_sale_to_invoicing(
                db,
                sale_id=invoice_sale.id,
                service_name="Service",
                external_invoice_number=" ",
                invoice_date_value=date.today(),
                due_date_value=date.today(),
            )
        with pytest.raises(ValueError, match="Due date"):
            transfer_sale_to_invoicing(
                db,
                sale_id=invoice_sale.id,
                service_name="Service",
                external_invoice_number="1",
                invoice_date_value=date.today(),
                due_date_value=date.today() - timedelta(days=1),
            )
        with pytest.raises(ValueError, match="Next follow-up"):
            confirm_invoice_unpaid(
                db,
                sale_id=invoice_sale.id,
                checked_date_value=date.today(),
                next_follow_up_date_value=date.today() - timedelta(days=1),
            )
        with pytest.raises(ValueError, match="Next follow-up"):
            record_invoice_reminder_sent(
                db,
                sale_id=invoice_sale.id,
                reminder_date_value=date.today(),
                next_follow_up_date_value=date.today() - timedelta(days=1),
            )
        record_invoice_reminder_sent(
            db,
            sale_id=invoice_sale.id,
            reminder_date_value=date.today(),
            notes="Sent by email",
            actor_user_id=admin.id,
        )
        assert invoice_sale.follow_up_notes == "Sent by email"
        with pytest.raises(ValueError, match="Sale not found"):
            correct_sale_seller(db, sale_id=999, new_sold_by_user_id=seller.id, corrected_by_user_id=admin.id, reason="Fix")
        with pytest.raises(ValueError, match="reason"):
            correct_sale_seller(db, sale_id=invoice_sale.id, new_sold_by_user_id=seller.id, corrected_by_user_id=admin.id, reason=" ")


def test_cash_shift_and_daily_closing_edges():
    with SessionLocal() as db:
        admin = _user(db, "Admin", "admin")
        seller = _user(db, "Seller", "seller")
        register = CashRegister(name="Register", is_active=True)
        inactive_register = CashRegister(name="Inactive", is_active=False)
        db.add_all([register, inactive_register])
        db.commit()

        with pytest.raises(ValueError, match="Cash register not found"):
            open_shift(db, seller_id=seller.id, cash_register_id=999, business_date=date.today(), starting_cash="0")
        with pytest.raises(ValueError, match="Active cash register"):
            open_shift(db, seller_id=seller.id, cash_register_id=inactive_register.id, business_date=date.today(), starting_cash="0")
        shift = open_shift(db, seller_id=seller.id, cash_register_id=register.id, business_date=date.today(), starting_cash="0")
        with pytest.raises(ValueError, match="Seller already"):
            open_shift(db, seller_id=seller.id, cash_register_id=register.id, business_date=date.today(), starting_cash="0")
        with pytest.raises(ValueError, match="must match"):
            add_cash_movement(db, shift_id=shift.id, seller_id=admin.id, movement_type="cash_in", amount="1")
        with pytest.raises(ValueError, match="Invalid cash movement"):
            add_cash_movement(db, shift_id=shift.id, seller_id=seller.id, movement_type="cash_sideways", amount="1")

        set_app_settings(db, {"require_cashier_shift": "true"})
        with pytest.raises(ValueError, match="Cannot close day"):
            create_daily_closing(db, business_date=date.today(), created_by_user_id=admin.id)
        shift.status = "closed"
        db.commit()
        closing = create_daily_closing(db, business_date=date.today(), created_by_user_id=admin.id)
        with pytest.raises(ValueError, match="already closed"):
            create_daily_closing(db, business_date=date.today(), created_by_user_id=admin.id)
        with pytest.raises(ValueError, match="snapshot schema"):
            parse_daily_closing_snapshot(DailyClosingSnapshot(snapshot_json='{"schema_version": 999}', schema_version=1))
        closing.status = "reopened"
        db.commit()
        with pytest.raises(ValueError, match="Only a closed"):
            reopen_daily_closing(db, closing_id=closing.id, user_id=admin.id, reason="Again")
        closing.status = "closed"
        db.commit()
        with pytest.raises(ValueError, match="Reopen reason"):
            reopen_daily_closing(db, closing_id=closing.id, user_id=admin.id, reason=" ")


def test_auth_receipt_numbers_and_backup_restore_edges(monkeypatch):
    with SessionLocal() as db:
        assert get_session_user(db, f"1:bad:{auth_service._sign('1:bad')}") is None
        old_timestamp = str(int(auth_service.time.time()) - auth_service.SESSION_MAX_AGE_SECONDS - 1)
        assert get_session_user(db, f"1:{old_timestamp}:{auth_service._sign(f'1:{old_timestamp}')}") is None

        set_app_settings(
            db,
            {
                "receipt_annual_reset": "true",
                "receipt_sequence_year": "2025",
                "next_receipt_sequence": "9",
                "receipt_prefix": "WO-",
                "receipt_padding": "3",
                "sale_document_annual_reset": "true",
                "sale_document_sequence_year": "2025",
                "next_sale_document_sequence": "9",
                "sale_document_prefix": "SALE-",
                "sale_document_padding": "3",
            },
        )
        conflicting_job_number = allocate_receipt_number(db, receipt_date=date(2026, 1, 1))
        assert conflicting_job_number == "WO-2026-001"
        db.add(Job(title="Existing receipt", receipt_number="WO-2026-002"))
        db.commit()
        assert allocate_receipt_number(db, receipt_date=date(2026, 1, 2)) == "WO-2026-003"
        assert allocate_sale_document_number(db, document_date=date(2026, 1, 1)) == "SALE-2026-001"

        restored = SimpleNamespace(name="backup.sqlite")
        monkeypatch.setattr(backups_route, "restore_backup", lambda name: restored)
        monkeypatch.setattr(backups_route, "maintenance_mode", lambda: nullcontext())
        response = restore_selected_backup("backup.sqlite", db=db)
        assert response.status_code == 303


def test_inventory_post_cancel_issue_transfer_error_edges():
    with SessionLocal() as db:
        user, supplier, product, source, target = _stock_context(db)
        service_product = Product(name="Service", is_stock_item=False, is_active=True)
        db.add(service_product)
        db.flush()
        db.add(
            InventoryBalance(
                product_id=service_product.id,
                warehouse_location_id=source.id,
                quantity_on_hand=Decimal("1.000"),
                quantity_available=Decimal("1.000"),
                inventory_value_ex_vat=Decimal("0.00"),
                weighted_average_cost_ex_vat=None,
            )
        )
        db.commit()
        receipt = create_goods_receipt(db, supplier_id=supplier.id, receipt_date=date.today(), received_by_user_id=user.id)
        db.commit()

        with pytest.raises(ValueError, match="Goods receipt not found"):
            post_goods_receipt(db, goods_receipt_id=999, posted_by_user_id=user.id)
        receipt.status = "posted"
        db.commit()
        with pytest.raises(ValueError, match="Only draft"):
            post_goods_receipt(db, goods_receipt_id=receipt.id, posted_by_user_id=user.id)
        receipt.status = "draft"
        db.commit()
        with pytest.raises(ValueError, match="at least one line"):
            post_goods_receipt(db, goods_receipt_id=receipt.id, posted_by_user_id=user.id)

        with pytest.raises(ValueError, match="Goods receipt not found"):
            cancel_goods_receipt(db, goods_receipt_id=999, user_id=user.id, reason="No")
        with pytest.raises(ValueError, match="Only posted"):
            cancel_goods_receipt(db, goods_receipt_id=receipt.id, user_id=user.id, reason="No")
        receipt.status = "posted"
        db.commit()
        with pytest.raises(ValueError, match="Cancellation reason"):
            cancel_goods_receipt(db, goods_receipt_id=receipt.id, user_id=user.id, reason=" ")

        with pytest.raises(ValueError, match="Stock product"):
            issue_stock_for_sale(db, product_id=999, warehouse_location_id=source.id, quantity_value="1", sale_id=1, created_by_user_id=user.id)
        with pytest.raises(ValueError, match="Negative stock"):
            issue_stock_for_sale(db, product_id=product.id, warehouse_location_id=source.id, quantity_value="3", sale_id=1, created_by_user_id=user.id)
        with pytest.raises(ValueError, match="Negative stock"):
            issue_stock_for_sale_from_available_locations(db, product_id=product.id, quantity_value="3", sale_id=1, created_by_user_id=user.id)
        with pytest.raises(ValueError, match="Negative stock"):
            issue_stock_for_delivery_note(
                db,
                product_id=product.id,
                quantity_value="3",
                work_order_id=1,
                job_item_id=1,
                created_by_user_id=user.id,
            )
        with pytest.raises(ValueError, match="Negative stock"):
            transfer_stock(
                db,
                product_id=product.id,
                from_location_id=source.id,
                to_location_id=target.id,
                quantity_value="3",
                created_by_user_id=user.id,
            )
        with pytest.raises(ValueError, match="Product not found"):
            product_cost_profile(db, product_id=999)
        with pytest.raises(ValueError, match="Repair reason"):
            repair_inventory_caches_from_ledger(db, user_id=user.id, reason=" ")


def test_inventory_additional_hard_edges(monkeypatch):
    with pytest.raises(ValueError, match="Active user"):
        require_inventory_operational_user(None)
    with pytest.raises(ValueError, match="finite"):
        inventory_parse_finite_decimal("NaN", "Quantity")
    zero_value_lines = [
        SimpleNamespace(id=1, quantity=Decimal("1"), purchase_unit_price_ex_vat=Decimal("0")),
        SimpleNamespace(id=2, quantity=Decimal("3"), purchase_unit_price_ex_vat=Decimal("0")),
    ]
    allocations = allocate_landed_costs(zero_value_lines, Decimal("4.00"), Decimal("0.00"), "by_value")
    assert allocations[1]["freight"] == Decimal("1.00")
    assert allocations[2]["freight"] == Decimal("3.00")

    with SessionLocal() as db:
        user, supplier, product, source, target = _stock_context(db)
        inactive_location = WarehouseLocation(warehouse_id=source.warehouse_id, code="OLD", name="Old", is_active=False)
        db.add(inactive_location)
        db.commit()
        with pytest.raises(ValueError, match="Active warehouse location"):
            _location(db, inactive_location.id)

        product.current_inventory_quantity = Decimal("-1.000")
        line = SimpleNamespace(
            id=1,
            product_id=product.id,
            product=product,
            quantity=Decimal("1"),
            purchase_unit_price_ex_vat=Decimal("1"),
            vat_rate=Decimal("0"),
        )
        receipt = SimpleNamespace(lines=[line], freight_total_ex_vat=0, other_costs_total_ex_vat=0, allocation_method="by_value")
        with pytest.raises(ValueError, match="Negative stock"):
            preview_goods_receipt(db, receipt)
        db.rollback()

        monkeypatch.setattr("app.services.inventory_service.assert_inventory_cache_consistent", lambda *args, **kwargs: None)
        source_balance = db.query(InventoryBalance).filter(InventoryBalance.product_id == product.id).first()
        source_balance.quantity_on_hand = Decimal("0.000")
        source_balance.quantity_available = Decimal("0.000")
        service_product = Product(name="Service only", is_stock_item=False, is_active=True)
        db.add(service_product)
        db.flush()
        db.add(
            InventoryBalance(
                product_id=service_product.id,
                warehouse_location_id=source.id,
                quantity_on_hand=Decimal("1.000"),
                quantity_available=Decimal("1.000"),
                inventory_value_ex_vat=Decimal("0.00"),
                weighted_average_cost_ex_vat=None,
            )
        )
        product.current_inventory_quantity = Decimal("2.000")
        product.current_inventory_value_ex_vat = Decimal("20.00")
        product.current_weighted_average_cost_ex_vat = Decimal("10.000000")
        db.commit()
        with pytest.raises(ValueError, match="Negative stock"):
            issue_stock_for_sale(db, product_id=product.id, warehouse_location_id=source.id, quantity_value="1", sale_id=1, created_by_user_id=user.id)
        with pytest.raises(ValueError, match="Stock product"):
            issue_stock_for_delivery_note(
                db,
                product_id=service_product.id,
                quantity_value="1",
                work_order_id=1,
                job_item_id=1,
                created_by_user_id=user.id,
            )
        source_balance.quantity_on_hand = Decimal("2.000")
        source_balance.quantity_available = Decimal("2.000")
        source_balance.inventory_value_ex_vat = Decimal("20.00")
        db.add(
            InventoryBalance(
                product_id=product.id,
                warehouse_location_id=target.id,
                quantity_on_hand=Decimal("1.000"),
                quantity_available=Decimal("1.000"),
                inventory_value_ex_vat=Decimal("10.00"),
                weighted_average_cost_ex_vat=Decimal("10.000000"),
            )
        )
        db.commit()
        sale_transactions = issue_stock_for_sale_from_available_locations(
            db,
            product_id=product.id,
            quantity_value="1",
            sale_id=1,
            created_by_user_id=user.id,
        )
        assert sale_transactions
        delivery_transactions = issue_stock_for_delivery_note(
            db,
            product_id=product.id,
            quantity_value="1",
            work_order_id=1,
            job_item_id=1,
            created_by_user_id=user.id,
        )
        assert delivery_transactions
        reversals = reverse_delivery_note_item_issue(
            db,
            work_order_id=1,
            job_item_id=1,
            created_by_user_id=user.id,
            reason="Customer cancelled",
        )
        assert reversals
        assert reverse_delivery_note_item_issue(
            db,
            work_order_id=1,
            job_item_id=1,
            created_by_user_id=user.id,
            reason="Already reversed",
        ) == []
        assert inventory_ledger(
            db,
            product_id=product.id,
            warehouse_id=source.warehouse_id,
            user_id=user.id,
            date_from=datetime.now(UTC) - timedelta(days=1),
            date_to=datetime.now(UTC) + timedelta(days=1),
        )
        with pytest.raises(ValueError, match="Stock product"):
            transfer_stock(db, product_id=999, from_location_id=source.id, to_location_id=target.id, quantity_value="1", created_by_user_id=user.id)


def test_route_and_bootstrap_remaining_edges(monkeypatch, tmp_path):
    assert sqlite_path_from_url("sqlite:///:memory:") is None
    assert sqlite_path_from_url("postgresql://example/db") is None
    assert str(sqlite_path_from_url("sqlite://server/share/app.db")).endswith("server\\share\\app.db")
    non_sqlite_inspection = inspect_database("sqlite:///:memory:")
    assert non_sqlite_inspection.sqlite is False

    inspection = SchemaInspection(
        database_url="postgresql://example/db",
        database_path=None,
        sqlite=False,
        tables=set(),
        columns_by_table={},
        nullable_columns_by_table={},
        indexes=set(),
        foreign_keys=set(),
        triggers=set(),
        alembic_versions=(),
        settings_keys=set(),
        missing_finalized_sale_document_numbers=0,
    )
    classification = classify_schema(inspection)
    assert classification.classification.endswith("unknown")
    plan = BootstrapPlan(
        inspection=inspection,
        classification=SchemaClassification("known", "abc", "because", ("missing",), ("unexpected",)),
        dry_run=True,
        backup_path=tmp_path / "backup.sqlite",
        stamp_revision="abc",
        upgrade_target="head",
        actions=("one",),
    )
    formatted = format_plan(plan)
    assert "Missing schema details" in formatted
    assert "Unexpected schema details" in formatted

    sqlite_db = tmp_path / "app.sqlite"
    with sqlite3.connect(sqlite_db) as conn:
        conn.execute("create table example (id integer primary key)")
        conn.execute("insert into example (id) values (1)")
    backup_dir = tmp_path / "backups"
    first_backup = create_sqlite_backup(sqlite_db, backup_dir)
    second_backup = create_sqlite_backup(sqlite_db, backup_dir)
    assert first_backup.exists()
    assert second_backup.exists()
    assert first_backup != second_backup

    monkeypatch.setattr("app.migration_bootstrap.inspect_database", lambda url: inspection)
    non_sqlite_plan = plan_bootstrap("postgresql://example/db", dry_run=True)
    assert non_sqlite_plan.upgrade_target == "head"

    monkeypatch.setattr("sys.argv", ["app.migration_bootstrap", "--dry-run"])
    monkeypatch.setattr("app.migration_bootstrap.run_bootstrap", lambda *args, **kwargs: plan)
    assert migration_bootstrap_main() == 0
    monkeypatch.setattr("app.migration_bootstrap.run_bootstrap", lambda *args, **kwargs: (_ for _ in ()).throw(MigrationBootstrapError("nope")))
    assert migration_bootstrap_main() == 2
    monkeypatch.setattr("app.migration_bootstrap.run_bootstrap", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))
    assert migration_bootstrap_main() == 1

    with SessionLocal() as db:
        monkeypatch.setattr(
            "app.routes.seller_reports.templates.TemplateResponse",
            lambda name, context, **kwargs: SimpleNamespace(template=SimpleNamespace(name=name), context=context),
        )
        response = seller_reports(_request(), seller_id=None, period="weekly", db=db)
        assert response.template.name == "seller_reports/index.html"
        with pytest.raises(HTTPException) as no_user:
            close_business_day(_request(), business_date=date.today().isoformat(), created_by_user_id=None, db=db)
        assert no_user.value.status_code == 400
        with pytest.raises(HTTPException) as no_closing:
            daily_closing_detail(999, _request(), db=db)
        assert no_closing.value.status_code == 404
        with pytest.raises(HTTPException) as no_snapshot:
            daily_closing_snapshot_detail(999, 1, _request(), db=db)
        assert no_snapshot.value.status_code == 404
        with pytest.raises(HTTPException) as bad_reopen:
            reopen_closing(999, user_id=999, reason="No", db=db)
        assert bad_reopen.value.status_code == 400
