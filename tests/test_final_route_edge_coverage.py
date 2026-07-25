import asyncio
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import app.error_handlers as error_handlers
import app.routes.inventory as inventory_route
import app.routes.jobs as jobs_route
import app.routes.product_import as product_import_route
import app.routes.products as products_route
import app.routes.sales as sales_route
import app.routes.daily_closings as daily_closings_route
import app.services.inventory_service as inventory_service
from app.database import SessionLocal
from app.models import (
    CashRegister,
    GoodsReceipt,
    GoodsReceiptLine,
    InventoryBalance,
    InventoryTransaction,
    Job,
    JobItem,
    JobStatus,
    Payment,
    Product,
    Sale,
    Shift,
    Warehouse,
    WarehouseLocation,
)
from app.services.auth_service import hash_password
from app.models import Role, Supplier, User
from app.services.inventory_service import (
    cancel_goods_receipt,
    create_goods_receipt,
    create_default_warehouse,
    inventory_ledger,
    issue_stock_for_delivery_note,
    issue_stock_for_sale_from_available_locations,
    post_goods_receipt,
    repair_inventory_caches_from_ledger,
)
from app.services.job_integrity_service import JobIntegrityError, assert_job_can_be_deleted
from app.services.sales_service import PaymentInput, SaleLineInput, _delivery_note_line_cogs, create_sale_from_lines, resolve_sale_seller
from app.services.sales_service import ensure_default_roles
from app.services.sales_service import AuthorizationError


def _request(user=None, path="/work-orders"):
    return SimpleNamespace(cookies={}, state=SimpleNamespace(current_user=user), url=SimpleNamespace(path=path))


def _template_response(_template, context):
    return SimpleNamespace(template=_template, context=context)


class FormRequest(SimpleNamespace):
    async def form(self):
        return self.form_data


class FormDataStub(dict):
    def getlist(self, key):
        value = self.get(key, [])
        return value if isinstance(value, list) else [value]


class UploadStub:
    def __init__(self, content: bytes):
        self.content = content

    async def read(self):
        return self.content


class NoHeaderReader:
    fieldnames = None


def _admin(db, name="Admin"):
    ensure_default_roles(db)
    role = db.query(Role).filter(Role.code == "admin").one()
    user = User(
        name=name,
        login_name=name.lower(),
        password_hash=hash_password("secret123"),
        role=role,
        is_active=True,
        can_receive_sales_credit=True,
    )
    db.add(user)
    db.flush()
    return user


def test_inventory_and_product_route_preview_and_filter_edges(monkeypatch):
    monkeypatch.setattr(inventory_route.templates, "TemplateResponse", _template_response)
    monkeypatch.setattr(products_route.templates, "TemplateResponse", _template_response)
    monkeypatch.setattr(inventory_route, "preview_goods_receipt", lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("bad preview")))
    monkeypatch.setattr(products_route, "preview_goods_receipt", lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("bad preview")))

    with SessionLocal() as db:
        admin = _admin(db)
        supplier = Supplier(name="Supplier", is_active=True)
        product = Product(name="Filtered stock", is_stock_item=True, is_active=True)
        warehouse = Warehouse(name="Warehouse", code="WH", is_active=True)
        location = WarehouseLocation(warehouse=warehouse, code="A", name="A", is_active=True)
        db.add_all([supplier, product, warehouse, location])
        db.flush()
        db.add(
            InventoryBalance(
                product=product,
                warehouse_location=location,
                quantity_on_hand=Decimal("1.000"),
                quantity_available=Decimal("1.000"),
                inventory_value_ex_vat=Decimal("2.00"),
                weighted_average_cost_ex_vat=Decimal("2.000000"),
            )
        )
        receipt = create_goods_receipt(db, supplier_id=supplier.id, receipt_date=date.today(), received_by_user_id=admin.id)
        db.add(
            GoodsReceiptLine(
                goods_receipt=receipt,
                product=product,
                destination_location=location,
                quantity=Decimal("1.000"),
                purchase_unit_price_ex_vat=Decimal("2.00"),
                purchase_unit_price_inc_vat=Decimal("2.00"),
                vat_rate=Decimal("0"),
            )
        )
        db.commit()

        inventory_response = inventory_route.goods_receipt_detail(receipt.id, _request(admin), db=db)
        product_response = products_route.product_goods_receipt_detail(receipt.id, _request(admin), db=db)
        assert inventory_response.context["preview"] is None
        assert product_response.context["preview"] is None

        balances = products_route.product_inventory_balances(
            _request(admin),
            product_id=product.id,
            warehouse_id=warehouse.id,
            db=db,
        )
        assert balances.context["balances"]


def test_sales_route_error_and_receipt_context_edges(monkeypatch):
    with SessionLocal() as db:
        admin = _admin(db)
        viewer_role = db.query(Role).filter(Role.code == "seller").one()
        viewer = User(
            name="Viewer",
            login_name="viewer",
            password_hash=hash_password("secret123"),
            role=viewer_role,
            is_active=True,
            can_receive_sales_credit=False,
        )
        db.add(viewer)
        db.commit()

        unpaid_sale = Sale(
            document_number="SALE-EDGE",
            sold_at=datetime(2026, 1, 1, 9, 30),
            business_date=date(2026, 1, 1),
            payment_method="cash",
            settlement_status="unpaid",
            subtotal=Decimal("10.00"),
            vat_total=Decimal("2.40"),
            total=Decimal("12.40"),
            status="completed",
            created_by_user_id=admin.id,
            sold_by_user_id=admin.id,
        )
        db.add(unpaid_sale)
        db.commit()
        receipt_context = sales_route._sale_receipt_context(unpaid_sale, db)
        assert receipt_context["receipt_change_due_raw"] == Decimal("0.00")

        with pytest.raises(HTTPException) as unauthorized:
            sales_route.correct_sale_seller_route(unpaid_sale.id, _request(viewer), sold_by_user_id=admin.id, reason="No", db=db)
        assert unauthorized.value.status_code == 403

        with pytest.raises(HTTPException) as missing_sale:
            sales_route.sale_receipt(999, _request(admin), db=db)
        assert missing_sale.value.status_code == 404

        with pytest.raises(HTTPException) as missing_refund_sale:
            sales_route.create_refund(999, _request(admin), amount="1", payment_method="cash", db=db)
        assert missing_refund_sale.value.status_code == 404

        with pytest.raises(HTTPException) as missing_shift:
            sales_route.create_refund(unpaid_sale.id, _request(admin), refund_shift_id="999", amount="1", payment_method="cash", db=db)
        assert missing_shift.value.status_code == 400


def test_sales_service_remaining_context_branches():
    with SessionLocal() as db:
        admin = _admin(db)
        warehouse, location = create_default_warehouse(db)
        product = Product(name="Service row", unit_price=Decimal("5.00"), vat_percent=Decimal("24"), is_stock_item=False, is_active=True)
        db.add(product)
        db.commit()

        sale = create_sale_from_lines(
            db,
            lines=[
                SaleLineInput(
                    description="Free service",
                    quantity=Decimal("1"),
                    unit_price=Decimal("0"),
                    vat_percent=Decimal("24"),
                    product_id=product.id,
                )
            ],
            payments=[],
            created_by_user_id=admin.id,
            seller_mode="none",
            source_type="pos",
            idempotency_key="free-service",
        )
        assert sale.total == Decimal("0.00")
        assert sale.gross_margin_percent is None


def test_inventory_product_and_job_route_error_edges(monkeypatch):
    with SessionLocal() as db:
        admin = _admin(db)
        product = Product(name="Product", is_stock_item=False, is_active=True)
        status = JobStatus(name="Ready", sort_order=1, is_active=True)
        job = Job(title="Job", document_type="work_order")
        db.add_all([product, status, job])
        db.commit()

        monkeypatch.setattr(inventory_route, "create_goods_receipt", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("bad receipt")))
        with pytest.raises(HTTPException) as inventory_create_error:
            inventory_route.create_goods_receipt_route(_request(admin), supplier_name="Supplier", receipt_date=date.today(), received_by_user_id=None, db=db)
        assert inventory_create_error.value.status_code == 400

        monkeypatch.setattr(products_route, "create_goods_receipt", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("bad receipt")))
        with pytest.raises(HTTPException) as product_create_error:
            products_route.create_product_goods_receipt_route(_request(admin), supplier_name="Supplier", receipt_date=date.today(), received_by_user_id=None, db=db)
        assert product_create_error.value.status_code == 400

        monkeypatch.setattr(inventory_route, "cancel_goods_receipt", lambda *args, **kwargs: None)
        assert inventory_route.cancel_goods_receipt_route(1, _request(admin), reason="Cancel", user_id=None, db=db).status_code == 303
        monkeypatch.setattr(products_route, "cancel_goods_receipt", lambda *args, **kwargs: None)
        assert products_route.cancel_product_goods_receipt_route(1, _request(admin), reason="Cancel", user_id=None, db=db).status_code == 303

        monkeypatch.setattr(inventory_route, "repair_inventory_caches_from_ledger", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("bad repair")))
        with pytest.raises(HTTPException) as inventory_repair_error:
            inventory_route.repair_inventory_reconciliation(_request(admin), user_id=None, reason="Repair", db=db)
        assert inventory_repair_error.value.status_code == 400
        monkeypatch.setattr(products_route, "repair_inventory_caches_from_ledger", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("bad repair")))
        with pytest.raises(HTTPException) as product_repair_error:
            products_route.repair_product_inventory_reconciliation(_request(admin), user_id=None, reason="Repair", db=db)
        assert product_repair_error.value.status_code == 400

        monkeypatch.setattr(products_route, "product_cost_profile", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("missing profile")))
        with pytest.raises(HTTPException) as product_detail_error:
            products_route.product_detail(product.id, _request(admin), db=db)
        assert product_detail_error.value.status_code == 404

        response = jobs_route.update_job(
            _request(admin),
            job.id,
            title="Updated",
            customer_id="",
            description="",
            arrival_date="",
            requested_pickup_date="",
            priority="normal",
            status_id=status.id,
            notes="",
            db=db,
        )
        assert response.status_code == 303
        job.document_type = "delivery_note"
        product.is_stock_item = True
        db.commit()
        monkeypatch.setattr(jobs_route, "apply_delivery_note_stock_issue", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("no stock")))
        with pytest.raises(HTTPException) as item_error:
            jobs_route.add_job_item(_request(admin, path="/delivery-notes"), job.id, product_id=str(product.id), description="", quantity="1", unit_price="1", vat_percent="24", db=db)
        assert item_error.value.status_code == 400
        monkeypatch.setattr(jobs_route, "create_sale_from_work_order", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("cannot sell")))
        with pytest.raises(HTTPException) as conversion_error:
            jobs_route.convert_job_document(_request(admin), job.id, "sale", db=db)
        assert conversion_error.value.status_code == 400


def test_remaining_redirect_and_sales_route_edges(monkeypatch):
    with SessionLocal() as db:
        admin = _admin(db)
        sale = Sale(
            document_number="SALE-ROUTES",
            payment_method="cash",
            settlement_status="paid",
            subtotal=Decimal("10.00"),
            vat_total=Decimal("2.40"),
            total=Decimal("12.40"),
            status="completed",
            created_by_user_id=admin.id,
            sold_by_user_id=admin.id,
        )
        empty_actor_sale = Sale(
            document_number="SALE-NO-ACTOR",
            payment_method="cash",
            settlement_status="paid",
            subtotal=Decimal("1.00"),
            vat_total=Decimal("0.00"),
            total=Decimal("1.00"),
            status="completed",
        )
        db.add_all([sale, empty_actor_sale])
        db.commit()

        monkeypatch.setattr(inventory_route, "repair_inventory_caches_from_ledger", lambda *args, **kwargs: None)
        assert inventory_route.repair_inventory_reconciliation(_request(admin), user_id=None, reason="Repair", db=db).status_code == 303
        monkeypatch.setattr(products_route, "repair_inventory_caches_from_ledger", lambda *args, **kwargs: None)
        assert products_route.repair_product_inventory_reconciliation(_request(admin), user_id=None, reason="Repair", db=db).status_code == 303
        monkeypatch.setattr(daily_closings_route, "reopen_daily_closing", lambda *args, **kwargs: None)
        assert daily_closings_route.reopen_closing(1, user_id=admin.id, reason="Reopen", db=db).status_code == 303

        monkeypatch.setattr(sales_route, "correct_sale_seller", lambda *args, **kwargs: (_ for _ in ()).throw(AuthorizationError("no")))
        with pytest.raises(HTTPException) as correction_auth:
            sales_route.correct_sale_seller_route(sale.id, _request(admin), sold_by_user_id=admin.id, reason="Fix", db=db)
        assert correction_auth.value.status_code == 403
        monkeypatch.setattr(sales_route, "correct_sale_seller", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("bad seller")))
        with pytest.raises(HTTPException) as correction_value:
            sales_route.correct_sale_seller_route(sale.id, _request(admin), sold_by_user_id=admin.id, reason="Fix", db=db)
        assert correction_value.value.status_code == 400
        monkeypatch.setattr(sales_route, "correct_sale_seller", lambda *args, **kwargs: None)
        assert sales_route.correct_sale_seller_route(
            sale.id,
            _request(admin),
            sold_by_user_id=admin.id,
            reason="Fix",
            db=db,
        ).status_code == 303

        monkeypatch.setattr(sales_route, "record_invoice_reminder_sent", lambda *args, **kwargs: None)
        assert sales_route.invoice_reminder_route(sale.id, _request(admin), reminder_date="2026-01-02", db=db).status_code == 303
        with pytest.raises(HTTPException) as refund_no_actor:
            sales_route.create_refund(
                empty_actor_sale.id,
                _request(None),
                refund_shift_id="",
                amount="1",
                payment_method="cash",
                db=db,
            )
        assert refund_no_actor.value.status_code == 400
        monkeypatch.setattr(sales_route, "add_refund", lambda *args, **kwargs: None)
        assert sales_route.create_refund(
            sale.id,
            _request(admin),
            refund_shift_id="",
            amount="1",
            payment_method="cash",
            db=db,
        ).status_code == 303

        monkeypatch.setattr(sales_route, "create_sale_from_work_order", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("bad work order sale")))
        form_request = FormRequest(
            cookies={},
            state=SimpleNamespace(current_user=admin),
            url=SimpleNamespace(path="/sales/work-orders/1"),
            form_data=FormDataStub({"payment_method": ["cash"], "payment_amount": [""]}),
        )
        with pytest.raises(HTTPException) as work_order_sale_error:
            asyncio.run(sales_route.create_work_order_sale(1, form_request, db=db))
        assert work_order_sale_error.value.status_code == 400

        with pytest.raises(HTTPException) as products_csv_error:
            asyncio.run(products_route.import_products(UploadStub(b"description,unit\nOnly description,pcs\n"), db=db))
        assert products_csv_error.value.status_code == 400

        title, detail = error_handlers._localized_error_text(_request(admin), 422, "Request validation failed.")
        assert title
        assert detail


def test_remaining_service_edges_for_sales_inventory_and_job_integrity(monkeypatch):
    with SessionLocal() as db:
        admin = _admin(db)
        seller = _admin(db, name="Seller Fallback")
        register = CashRegister(name="Main register", is_active=True)
        shift = Shift(cash_register=register, seller=seller, business_date=date(2026, 1, 2), status="open")
        product = Product(name="Ledger product", is_stock_item=True, is_active=True)
        supplier = Supplier(name="Ledger supplier", is_active=True)
        warehouse = Warehouse(name="Ledger warehouse", code="LW", is_active=True)
        location = WarehouseLocation(warehouse=warehouse, code="L1", name="L1", is_active=True)
        work_order = Job(title="Inventory-linked work", document_type="delivery_note")
        work_order_item = JobItem(job=work_order, product=product, description="No COGS yet")
        receipt = GoodsReceipt(
            supplier=supplier,
            receipt_date=date(2026, 1, 2),
            received_by_user_id=admin.id,
            status="posted",
        )
        db.add_all([register, shift, product, supplier, warehouse, location, work_order, work_order_item, receipt])
        db.flush()

        sold_by, operator = resolve_sale_seller(
            db,
            shift=shift,
            selected_seller_id=None,
            created_by_user_id=None,
            seller_selection_mode="unexpected-mode",
        )
        assert sold_by.id == seller.id
        assert operator is None

        assert _delivery_note_line_cogs(
            db,
            work_order=work_order,
            work_order_item_id=work_order_item.id,
            product_id=product.id,
        ) is None

        transaction = InventoryTransaction(
            product=product,
            warehouse=warehouse,
            shelf_location=location,
            transaction_type="purchase",
            quantity_change=Decimal("1.000"),
            unit_cost_ex_vat=Decimal("2.000000"),
            total_inventory_cost=Decimal("2.00"),
            inventory_value_before=Decimal("0.00"),
            inventory_value_after=Decimal("2.00"),
            stock_before=Decimal("0.000"),
            stock_after=Decimal("1.000"),
            weighted_average_cost_before=None,
            weighted_average_cost_after=Decimal("2.000000"),
            supplier=supplier,
            goods_receipt=receipt,
            work_order=work_order,
            created_by=admin,
        )
        balance = InventoryBalance(
            product=product,
            warehouse_location=location,
            quantity_on_hand=Decimal("1.000"),
            quantity_available=Decimal("1.000"),
            inventory_value_ex_vat=Decimal("2.00"),
            weighted_average_cost_ex_vat=Decimal("2.000000"),
        )
        db.add_all([transaction, balance])
        db.commit()

        supplier_transactions = inventory_ledger(db, supplier_id=supplier.id)
        assert [item.id for item in supplier_transactions] == [transaction.id]

        with pytest.raises(JobIntegrityError):
            assert_job_can_be_deleted(db, work_order.id)

        monkeypatch.setattr("app.services.inventory_service.assert_inventory_cache_consistent", lambda *args, **kwargs: None)
        product.current_inventory_quantity = Decimal("0.000")
        product.current_inventory_value_ex_vat = Decimal("0.00")
        product.current_weighted_average_cost_ex_vat = Decimal("0.000000")
        db.commit()
        with pytest.raises(ValueError, match="negative stock"):
            cancel_goods_receipt(db, goods_receipt_id=receipt.id, user_id=admin.id, reason="Bad product cache")
        db.rollback()

        product.current_inventory_quantity = Decimal("1.000")
        product.current_inventory_value_ex_vat = Decimal("2.00")
        product.current_weighted_average_cost_ex_vat = Decimal("2.000000")
        balance.quantity_on_hand = Decimal("0.000")
        balance.quantity_available = Decimal("0.000")
        balance.inventory_value_ex_vat = Decimal("0.00")
        db.commit()
        with pytest.raises(ValueError, match="location stock"):
            cancel_goods_receipt(db, goods_receipt_id=receipt.id, user_id=admin.id, reason="Bad location cache")

        monkeypatch.setattr(
            "app.services.inventory_service.inventory_reconciliation",
            lambda *args, **kwargs: {
                "is_clean": False,
                "product_mismatches": [{"product": SimpleNamespace(id=999999)}],
                "location_mismatches": [],
            },
        )
        assert repair_inventory_caches_from_ledger(db, user_id=admin.id, reason="Missing product mismatch")["is_clean"] is False

        draft_receipt = GoodsReceipt(
            supplier=supplier,
            receipt_date=date(2026, 1, 3),
            received_by_user_id=admin.id,
            status="draft",
        )
        draft_line = GoodsReceiptLine(
            goods_receipt=draft_receipt,
            product=product,
            destination_location=location,
            quantity=Decimal("1.000"),
            purchase_unit_price_ex_vat=Decimal("1.00"),
            purchase_unit_price_inc_vat=Decimal("1.00"),
        )
        db.add_all([draft_receipt, draft_line])
        db.commit()

        monkeypatch.setattr("app.services.inventory_service.assert_inventory_cache_consistent", lambda *args, **kwargs: None)
        monkeypatch.setattr(
            "app.services.inventory_service.preview_goods_receipt",
            lambda *args, **kwargs: {
                "projected_by_product": {product.id: {"quantity": "0", "value": "0", "average": None}},
                "lines": [
                    {
                        "line": draft_line,
                        "calculation": {
                            "old_quantity": "0",
                            "old_average_cost": "0",
                            "old_value": "0",
                            "combined_quantity": "0",
                            "combined_value": "0",
                            "new_average_cost": "0",
                        },
                        "purchase_value_ex_vat": Decimal("0.00"),
                        "allocated_freight_ex_vat": Decimal("0.00"),
                        "allocated_other_costs_ex_vat": Decimal("0.00"),
                        "landed_unit_cost_ex_vat": Decimal("0.000000"),
                        "purchase_unit_price_inc_vat": Decimal("1.00"),
                    }
                ],
            },
        )
        with pytest.raises(ValueError, match="positive inventory quantity"):
            post_goods_receipt(db, goods_receipt_id=draft_receipt.id, posted_by_user_id=admin.id)


def test_csv_import_header_validation_edges(monkeypatch):
    monkeypatch.setattr(product_import_route.csv.Sniffer, "sniff", lambda *_args, **_kwargs: SimpleNamespace())
    monkeypatch.setattr(product_import_route.csv, "DictReader", lambda *_args, **_kwargs: NoHeaderReader())
    monkeypatch.setattr(products_route.csv.Sniffer, "sniff", lambda *_args, **_kwargs: SimpleNamespace())
    monkeypatch.setattr(products_route.csv, "DictReader", lambda *_args, **_kwargs: NoHeaderReader())

    with SessionLocal() as db:
        with pytest.raises(HTTPException) as import_error:
            asyncio.run(product_import_route.import_products_csv(UploadStub(b"anything"), db=db))
        assert import_error.value.status_code == 400

        with pytest.raises(HTTPException) as products_error:
            asyncio.run(products_route.import_products(UploadStub(b"anything"), db=db))
        assert products_error.value.status_code == 400


def test_inventory_issue_race_condition_residual_guards(monkeypatch):
    original_quantity = inventory_service.quantity
    original_issue_stock_for_sale = inventory_service.issue_stock_for_sale

    def residual_quantity(value):
        parsed = Decimal(str(value))
        if parsed == Decimal("0"):
            return Decimal("0.001")
        return original_quantity(value)

    with SessionLocal() as db:
        admin = _admin(db)
        product = Product(
            name="Race guard stock",
            is_stock_item=True,
            is_active=True,
            current_inventory_quantity=Decimal("1.000"),
            current_inventory_value_ex_vat=Decimal("1.00"),
            current_weighted_average_cost_ex_vat=Decimal("1.000000"),
        )
        warehouse = Warehouse(name="Race warehouse", code="RW", is_active=True)
        location = WarehouseLocation(warehouse=warehouse, code="R1", name="R1", is_active=True)
        balance = InventoryBalance(
            product=product,
            warehouse_location=location,
            quantity_on_hand=Decimal("1.000"),
            quantity_available=Decimal("1.000"),
            inventory_value_ex_vat=Decimal("1.00"),
            weighted_average_cost_ex_vat=Decimal("1.000000"),
        )
        db.add_all([product, warehouse, location, balance])
        db.commit()

        monkeypatch.setattr(inventory_service, "assert_inventory_cache_consistent", lambda *args, **kwargs: None)
        monkeypatch.setattr(inventory_service, "issue_stock_for_sale", lambda *args, **kwargs: SimpleNamespace(id=1))
        monkeypatch.setattr(inventory_service, "quantity", residual_quantity)
        with pytest.raises(ValueError, match="Negative stock"):
            issue_stock_for_sale_from_available_locations(
                db,
                product_id=product.id,
                quantity_value="1",
                sale_id=1,
                created_by_user_id=admin.id,
            )
        db.rollback()

        monkeypatch.setattr(inventory_service, "issue_stock_for_sale", original_issue_stock_for_sale)
        product.current_inventory_quantity = Decimal("1.000")
        product.current_inventory_value_ex_vat = Decimal("1.00")
        product.current_weighted_average_cost_ex_vat = Decimal("1.000000")
        balance.quantity_on_hand = Decimal("1.000")
        balance.quantity_available = Decimal("1.000")
        balance.inventory_value_ex_vat = Decimal("1.00")
        db.commit()
        with pytest.raises(ValueError, match="Negative stock"):
            issue_stock_for_delivery_note(
                db,
                product_id=product.id,
                quantity_value="1",
                work_order_id=1,
                job_item_id=1,
                created_by_user_id=admin.id,
            )
