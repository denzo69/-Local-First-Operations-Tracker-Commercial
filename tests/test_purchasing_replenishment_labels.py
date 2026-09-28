from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

import app.services.purchasing_service as purchasing_service
from app.database import SessionLocal
from app.main import app
from app.models import InventoryBalance, Product, PurchaseOrder, PurchaseOrderLine, Role, Supplier, User, WarehouseLocation
from app.services.barcode_label_service import _ean_bits, barcode_svg
from app.services.inventory_service import create_default_warehouse, post_goods_receipt, cancel_goods_receipt, discard_draft_goods_receipt
from app.services.purchasing_service import (
    add_purchase_order_line,
    cancel_purchase_order,
    create_purchase_order,
    create_order_from_replenishment,
    create_receipt_from_purchase_order,
    order_purchase_order,
    replenishment_suggestions,
    refresh_purchase_order_receipt_status,
)
from app.services.sales_service import ensure_default_roles


def _seed(*, quantity="0", reorder="4", target="10", sku="P-000101"):
    with SessionLocal() as db:
        ensure_default_roles(db)
        role = db.query(Role).filter(Role.code == "manager").one()
        user = User(name="Buyer", login_name=f"buyer-{sku}", role=role, is_active=True)
        supplier = Supplier(name="Parts supplier", is_active=True)
        product = Product(
            sku=sku,
            name="Replacement belt",
            unit_price=15,
            vat_percent=24,
            unit="pcs",
            is_stock_item=True,
            is_active=True,
            preferred_supplier=supplier,
            supplier_product_code="BELT-01",
            reorder_point=Decimal(reorder),
            target_stock_quantity=Decimal(target),
            current_purchase_price_ex_vat=Decimal("2.00"),
        )
        db.add_all([user, supplier, product])
        db.flush()
        _warehouse, location = create_default_warehouse(db)
        db.add(InventoryBalance(
            product_id=product.id,
            warehouse_location_id=location.id,
            quantity_on_hand=Decimal(quantity),
            quantity_reserved=Decimal("0"),
            quantity_available=Decimal(quantity),
            inventory_value_ex_vat=Decimal(quantity) * Decimal("2"),
            weighted_average_cost_ex_vat=Decimal("2") if Decimal(quantity) else None,
        ))
        db.commit()
        return user.id, supplier.id, product.id, location.id


def test_replenishment_accounts_for_ordered_quantities_and_partial_receipt_cancellation():
    user_id, supplier_id, product_id, location_id = _seed()
    with SessionLocal() as db:
        suggestion = replenishment_suggestions(db)
        assert suggestion[0]["suggested_quantity"] == Decimal("10.000")
        order = create_purchase_order(db, supplier_id=supplier_id, created_by_user_id=user_id, expected_date=date.today())
        line = add_purchase_order_line(
            db,
            purchase_order_id=order.id,
            product_id=product_id,
            destination_location_id=location_id,
            ordered_quantity="5",
            unit_cost_ex_vat="2.50",
            vat_rate="24",
        )
        order_purchase_order(db, purchase_order_id=order.id)
        assert replenishment_suggestions(db) == []

        receipt = create_receipt_from_purchase_order(
            db,
            purchase_order_id=order.id,
            quantities={line.id: "2"},
            received_by_user_id=user_id,
        )
        post_goods_receipt(db, goods_receipt_id=receipt.id, posted_by_user_id=user_id)
        db.refresh(order)
        db.refresh(line)
        assert order.status == "partially_received"
        assert line.received_quantity == Decimal("2.000")
        assert replenishment_suggestions(db) == []

        cancel_goods_receipt(db, goods_receipt_id=receipt.id, user_id=user_id, reason="Wrong delivery")
        db.refresh(order)
        db.refresh(line)
        assert order.status == "ordered"
        assert line.received_quantity == Decimal("0.000")

        cancelled = cancel_purchase_order(db, purchase_order_id=order.id)
        assert cancelled.status == "cancelled"
        assert replenishment_suggestions(db)[0]["suggested_quantity"] == Decimal("10.000")


def test_replenishment_groups_use_open_purchase_order_remaining_quantity():
    user_id, supplier_id, product_id, location_id = _seed(quantity="2")
    with SessionLocal() as db:
        order = create_purchase_order(db, supplier_id=supplier_id, created_by_user_id=user_id)
        line = add_purchase_order_line(
            db,
            purchase_order_id=order.id,
            product_id=product_id,
            destination_location_id=location_id,
            ordered_quantity="4",
            unit_cost_ex_vat="2",
        )
        order_purchase_order(db, purchase_order_id=order.id)
        db.refresh(product := db.get(Product, product_id))
        items = replenishment_suggestions(db)
        assert items == []  # available 2 + on order 4 is above the reorder point 4
        assert line.received_quantity == 0
        assert product.preferred_supplier_id == supplier_id


def test_purchase_order_guards_status_and_received_quantity_limits():
    user_id, supplier_id, product_id, location_id = _seed()
    with SessionLocal() as db:
        order = create_purchase_order(db, supplier_id=supplier_id, created_by_user_id=user_id)
        with pytest.raises(ValueError, match="at least one line"):
            order_purchase_order(db, purchase_order_id=order.id)
        line = add_purchase_order_line(db, purchase_order_id=order.id, product_id=product_id, destination_location_id=location_id, ordered_quantity="3", unit_cost_ex_vat="2")
        order_purchase_order(db, purchase_order_id=order.id)
        with pytest.raises(ValueError, match="exceeds"):
            create_receipt_from_purchase_order(db, purchase_order_id=order.id, quantities={line.id: "4"}, received_by_user_id=user_id)
        with pytest.raises(ValueError, match="Only draft"):
            add_purchase_order_line(db, purchase_order_id=order.id, product_id=product_id, destination_location_id=location_id, ordered_quantity="1", unit_cost_ex_vat="2")


def test_ean_svg_uses_valid_guard_pattern_and_sku_gets_scannable_code39():
    bits = _ean_bits("4006381333931")
    assert len(bits) == 95
    assert bits.startswith("101") and bits[45:50] == "01010" and bits.endswith("101")
    assert _ean_bits("96385074").startswith("101")
    assert len(_ean_bits("036000291452")) == 95
    ean_svg = barcode_svg("4006381333931", "ean13")
    sku_svg = barcode_svg("P-000101", "code39")
    assert "viewBox=" in ean_svg and "4006381333931" in ean_svg
    assert "P-000101" in sku_svg and "<rect" in sku_svg
    with pytest.raises(ValueError, match="support"):
        barcode_svg("SKU_WITH_UNSUPPORTED", "code128")
    with pytest.raises(ValueError, match="required"):
        barcode_svg("   ")
    with pytest.raises(ValueError, match="Only EAN"):
        barcode_svg("123456789", "ean13")


def test_purchase_and_label_pages_render_and_print_links_work():
    with TestClient(app) as client:
        created = client.post("/products", data={"sku": "PRINT-001", "primary_barcode": "4006381333931", "name": "Label product", "is_stock_item": "on"}, follow_redirects=False)
        assert created.status_code == 303
        with SessionLocal() as db:
            product = db.query(Product).filter(Product.sku == "PRINT-001").one()
            product_id = product.id
        label = client.get(f"/products/{product_id}/labels?copies=3")
        orders = client.get("/products/purchasing")
        replenishment = client.get("/products/purchasing/replenishment")
    assert label.status_code == 200
    assert label.text.count('class="label"') == 3
    assert "4006381333931" in label.text
    assert orders.status_code == 200 and "Purchase orders" in orders.text
    assert replenishment.status_code == 200 and "Replenishment suggestions" in replenishment.text


def test_purchase_routes_support_order_partial_receipt_and_closure():
    user_id, supplier_id, product_id, location_id = _seed()
    with TestClient(app) as client:
        assert client.get("/products/purchasing/new").status_code == 200
        assert client.get("/products/purchasing/999").status_code == 404
        create = client.post("/products/purchasing", data={
            "supplier_id": supplier_id,
            "expected_date": (date.today() + timedelta(days=5)).isoformat(),
            "created_by_user_id": user_id,
            "notes": "Restock",
        }, follow_redirects=False)
        order_id = int(create.headers["location"].rsplit("/", 1)[1])
        assert create.status_code == 303
        add = client.post(f"/products/purchasing/{order_id}/lines", data={
            "product_id": product_id,
            "destination_location_id": location_id,
            "ordered_quantity": "3",
            "unit_cost_ex_vat": "2.50",
            "vat_rate": "24",
        }, follow_redirects=False)
        assert add.status_code == 303
        assert "Replacement belt" in client.get(f"/products/purchasing/{order_id}").text
        assert client.post(f"/products/purchasing/{order_id}/send", follow_redirects=False).status_code == 303
        receive = client.post(f"/products/purchasing/{order_id}/receive", data={
            "received_by_user_id": user_id,
            "quantity_1": "2",
        }, follow_redirects=False)
        assert receive.status_code == 303
        receipt_id = int(receive.headers["location"].rsplit("/", 1)[1])
        posted = client.post(f"/products/goods-receipts/{receipt_id}/post", data={"posted_by_user_id": user_id}, follow_redirects=False)
        assert posted.status_code == 303
        assert client.post(f"/products/purchasing/{order_id}/cancel", follow_redirects=False).status_code == 303
        assert client.get("/products/purchasing").status_code == 200
    with SessionLocal() as db:
        order = db.get(PurchaseOrder, order_id)
        assert order.status == "cancelled"
        assert order.lines[0].received_quantity == Decimal("2.000")


def test_replenishment_route_creates_suggested_order_and_rejects_empty_selection():
    user_id, supplier_id, product_id, location_id = _seed()
    with TestClient(app) as client:
        before = client.get("/products/purchasing/replenishment")
        assert before.status_code == 200 and "Replacement belt" in before.text
        empty = client.post("/products/purchasing/from-replenishment", data={
            "supplier_id": supplier_id,
            "destination_location_id": location_id,
            "created_by_user_id": user_id,
        })
        assert empty.status_code == 400
        created = client.post("/products/purchasing/from-replenishment", data={
            "supplier_id": supplier_id,
            "destination_location_id": location_id,
            "created_by_user_id": user_id,
            "product_ids": str(product_id),
        }, follow_redirects=False)
        assert created.status_code == 303
        order_id = int(created.headers["location"].rsplit("/", 1)[1])
        assert client.get("/products/purchasing/replenishment").status_code == 200
    with SessionLocal() as db:
        order = db.get(PurchaseOrder, order_id)
        assert order.lines[0].ordered_quantity == Decimal("10.000")


def test_purchase_order_rejects_invalid_input_and_receipt_draft_can_be_discarded():
    user_id, supplier_id, product_id, location_id = _seed()
    with SessionLocal() as db:
        with pytest.raises(ValueError, match="Active user"):
            create_purchase_order(db, supplier_id=supplier_id, created_by_user_id=999)
        with pytest.raises(ValueError, match="active supplier"):
            create_purchase_order(db, supplier_id=999, created_by_user_id=user_id)
        with pytest.raises(ValueError, match="before"):
            create_purchase_order(db, supplier_id=supplier_id, created_by_user_id=user_id, order_date=date.today(), expected_date=date.today() - timedelta(days=1))
        first = create_purchase_order(db, supplier_id=supplier_id, created_by_user_id=user_id, notes="  ")
        second = create_purchase_order(db, supplier_id=supplier_id, created_by_user_id=user_id)
        assert first.order_number.endswith("00001")
        assert second.order_number.endswith("00002")
        with pytest.raises(ValueError, match="not found"):
            add_purchase_order_line(db, purchase_order_id=999, product_id=product_id, destination_location_id=location_id, ordered_quantity="1", unit_cost_ex_vat="1")
        for value in ("0", "-1", "bad"):
            with pytest.raises(ValueError):
                add_purchase_order_line(db, purchase_order_id=first.id, product_id=product_id, destination_location_id=location_id, ordered_quantity=value, unit_cost_ex_vat="1")
        for cost, vat in (("-1", "24"), ("1", "101")):
            with pytest.raises(ValueError):
                add_purchase_order_line(db, purchase_order_id=first.id, product_id=product_id, destination_location_id=location_id, ordered_quantity="1", unit_cost_ex_vat=cost, vat_rate=vat)
        for cost, vat in (("bad", "24"), ("1", "bad")):
            with pytest.raises(ValueError):
                add_purchase_order_line(db, purchase_order_id=first.id, product_id=product_id, destination_location_id=location_id, ordered_quantity="1", unit_cost_ex_vat=cost, vat_rate=vat)
        with pytest.raises(ValueError, match="active stock"):
            add_purchase_order_line(db, purchase_order_id=first.id, product_id=999, destination_location_id=location_id, ordered_quantity="1", unit_cost_ex_vat="1")
        with pytest.raises(ValueError, match="destination"):
            add_purchase_order_line(db, purchase_order_id=first.id, product_id=product_id, destination_location_id=999, ordered_quantity="1", unit_cost_ex_vat="1")
        line = add_purchase_order_line(db, purchase_order_id=first.id, product_id=product_id, destination_location_id=location_id, ordered_quantity="2", unit_cost_ex_vat="1")
        order_purchase_order(db, purchase_order_id=first.id)
        with pytest.raises(ValueError, match="Only draft"):
            order_purchase_order(db, purchase_order_id=first.id)
        with pytest.raises(ValueError, match="Only sent"):
            create_receipt_from_purchase_order(db, purchase_order_id=second.id, quantities={}, received_by_user_id=user_id)
        with pytest.raises(ValueError, match="positive received"):
            create_receipt_from_purchase_order(db, purchase_order_id=first.id, quantities={line.id: "0"}, received_by_user_id=user_id)
        with pytest.raises(ValueError, match="zero or positive"):
            create_receipt_from_purchase_order(db, purchase_order_id=first.id, quantities={line.id: "-1"}, received_by_user_id=user_id)
        with pytest.raises(ValueError, match="valid zero or positive"):
            create_receipt_from_purchase_order(db, purchase_order_id=first.id, quantities={line.id: "bad"}, received_by_user_id=user_id)
        receipt = create_receipt_from_purchase_order(db, purchase_order_id=first.id, quantities={line.id: "1"}, received_by_user_id=user_id)
        with pytest.raises(ValueError, match="existing draft"):
            create_receipt_from_purchase_order(db, purchase_order_id=first.id, quantities={line.id: "1"}, received_by_user_id=user_id)
        with pytest.raises(ValueError, match="draft goods receipt"):
            cancel_purchase_order(db, purchase_order_id=first.id)
        discard_draft_goods_receipt(db, goods_receipt_id=receipt.id, user_id=user_id, reason="duplicate")
        assert cancel_purchase_order(db, purchase_order_id=first.id).status == "cancelled"
        with pytest.raises(ValueError, match="open purchase order"):
            cancel_purchase_order(db, purchase_order_id=first.id)
        with pytest.raises(ValueError, match="not found"):
            cancel_purchase_order(db, purchase_order_id=999)
        with pytest.raises(ValueError, match="not found"):
            create_receipt_from_purchase_order(db, purchase_order_id=999, quantities={}, received_by_user_id=user_id)


def test_replenishment_validates_supplier_and_user_and_skips_unconfigured_products():
    user_id, supplier_id, product_id, location_id = _seed()
    with SessionLocal() as db:
        product = db.get(Product, product_id)
        product.target_stock_quantity = product.reorder_point
        no_supplier = Product(name="No supplier", sku="NO-SUP", is_stock_item=True, is_active=True, reorder_point=4, target_stock_quantity=10)
        service = Product(name="Service", sku="SERVICE", is_stock_item=False, is_active=True, reorder_point=0, target_stock_quantity=10)
        inactive = Product(name="Inactive", sku="INACTIVE", is_stock_item=True, is_active=False, reorder_point=4, target_stock_quantity=10)
        db.add_all([no_supplier, service, inactive])
        db.commit()
        suggestions = replenishment_suggestions(db)
        assert len(suggestions) == 1 and suggestions[0]["supplier"] is None
        product.target_stock_quantity = 10
        db.commit()
        with pytest.raises(ValueError, match="current replenishment"):
            create_order_from_replenishment(db, supplier_id=supplier_id, product_quantities={999: "1"}, destination_location_id=location_id, created_by_user_id=user_id)
        with pytest.raises(ValueError, match="positive quantity"):
            create_order_from_replenishment(db, supplier_id=supplier_id, product_quantities={product_id: "0"}, destination_location_id=location_id, created_by_user_id=user_id)
        with pytest.raises(ValueError, match="at least one"):
            create_order_from_replenishment(db, supplier_id=supplier_id, product_quantities={}, destination_location_id=location_id, created_by_user_id=user_id)
        with pytest.raises(ValueError, match="active destination"):
            create_order_from_replenishment(db, supplier_id=supplier_id, product_quantities={product_id: "1"}, destination_location_id=999, created_by_user_id=user_id)
        with pytest.raises(ValueError, match="before"):
            create_order_from_replenishment(db, supplier_id=supplier_id, product_quantities={product_id: "1"}, destination_location_id=location_id, created_by_user_id=user_id, expected_date=date.today() - timedelta(days=1))
        supplier = db.get(Supplier, supplier_id)
        supplier.is_active = False
        db.commit()
        with pytest.raises(ValueError, match="active supplier"):
            create_order_from_replenishment(db, supplier_id=supplier_id, product_quantities={product_id: "1"}, destination_location_id=location_id, created_by_user_id=user_id)
        with pytest.raises(ValueError, match="Active user"):
            create_order_from_replenishment(db, supplier_id=supplier_id, product_quantities={product_id: "1"}, destination_location_id=location_id, created_by_user_id=999)
        supplier.is_active = True
        db.commit()
        order = create_purchase_order(db, supplier_id=supplier_id, created_by_user_id=user_id)
        add_purchase_order_line(db, purchase_order_id=order.id, product_id=product_id, destination_location_id=location_id, ordered_quantity="1", unit_cost_ex_vat="1")
        order_purchase_order(db, purchase_order_id=order.id)
        complete_line = order.lines[0]
        complete_line.received_quantity = 1
        refresh_purchase_order_receipt_status(db, order)
        assert order.status == "received"
        order.status = "cancelled"
        complete_line.received_quantity = 0
        refresh_purchase_order_receipt_status(db, order)
        assert order.status == "cancelled"


def test_replenishment_order_creation_rolls_back_on_audit_failure(monkeypatch):
    user_id, supplier_id, product_id, location_id = _seed()
    with SessionLocal() as db:
        def fail_audit(*args, **kwargs):
            raise RuntimeError("audit storage unavailable")

        monkeypatch.setattr(purchasing_service, "log_audit_event", fail_audit)
        with pytest.raises(RuntimeError, match="audit storage"):
            create_order_from_replenishment(
                db,
                supplier_id=supplier_id,
                product_quantities={product_id: "10"},
                destination_location_id=location_id,
                created_by_user_id=user_id,
            )
        db.expire_all()
        assert db.query(PurchaseOrder).count() == 0


def test_purchase_and_receipt_routes_cover_errors_and_draft_discard():
    user_id, supplier_id, product_id, location_id = _seed()
    with TestClient(app) as client:
        assert client.get("/products/999/labels").status_code == 404
        assert client.get("/products/purchasing/999").status_code == 404
        assert client.post("/products/purchasing/999/send").status_code == 400
        assert client.post("/products/purchasing/999/cancel").status_code == 400
        assert client.post("/products/purchasing/999/receive", data={"received_by_user_id": user_id}).status_code == 404
        bad_order = client.post("/products/purchasing", data={
            "supplier_id": supplier_id,
            "created_by_user_id": user_id,
            "expected_date": (date.today() - timedelta(days=1)).isoformat(),
        })
        assert bad_order.status_code == 400
        order_response = client.post("/products/purchasing", data={"supplier_id": supplier_id, "created_by_user_id": user_id}, follow_redirects=False)
        order_id = int(order_response.headers["location"].rsplit("/", 1)[1])
        assert client.post(f"/products/purchasing/{order_id}/send").status_code == 400
        assert client.post(f"/products/purchasing/{order_id}/lines", data={
            "product_id": product_id,
            "destination_location_id": location_id,
            "ordered_quantity": "0",
            "unit_cost_ex_vat": "1",
        }, follow_redirects=False).status_code == 400
        assert client.post(f"/products/purchasing/{order_id}/lines", data={
            "product_id": product_id,
            "destination_location_id": location_id,
            "ordered_quantity": "1",
            "unit_cost_ex_vat": "1",
        }, follow_redirects=False).status_code == 303
        assert client.post(f"/products/purchasing/{order_id}/send", follow_redirects=False).status_code == 303
        assert client.post(f"/products/purchasing/{order_id}/receive", data={
            "received_by_user_id": user_id,
            "quantity_1": "0",
        }, follow_redirects=False).status_code == 400
        assert client.post(f"/products/purchasing/{order_id}/receive", data={
            "received_by_user_id": user_id,
            "quantity_1": "1",
        }, follow_redirects=False).status_code == 303
        assert client.post(f"/products/purchasing/{order_id}/cancel").status_code == 400
        with SessionLocal() as db:
            receipt_id = db.query(PurchaseOrder).filter(PurchaseOrder.id == order_id).one().goods_receipts[0].id
        discarded = client.post(f"/products/goods-receipts/{receipt_id}/discard", data={"user_id": user_id, "reason": "wrong quantities"}, follow_redirects=False)
        assert discarded.status_code == 303
        assert client.post(f"/products/purchasing/{order_id}/cancel", follow_redirects=False).status_code == 303
        assert client.post(f"/products/goods-receipts/{receipt_id}/discard", data={"user_id": user_id}).status_code == 400


def test_product_reorder_setting_validation_and_unencodable_label_route():
    with TestClient(app) as client:
        invalid_create = client.post("/products", data={
            "sku": "STOCK-BAD",
            "name": "Bad stock settings",
            "is_stock_item": "on",
            "reorder_point": "5",
            "target_stock_quantity": "4",
        })
        assert invalid_create.status_code == 400
        created = client.post("/products", data={"sku": "BAD_SKU", "name": "Bad label SKU"}, follow_redirects=False)
        assert created.status_code == 303
        with SessionLocal() as db:
            product_id = db.query(Product).filter(Product.sku == "BAD_SKU").one().id
        assert client.get(f"/products/{product_id}/labels").status_code == 400
        updated = client.post(f"/products/{product_id}", data={
            "sku": "BAD_SKU",
            "name": "Bad label SKU",
            "reorder_point": "3",
            "target_stock_quantity": "1",
        })
        assert updated.status_code == 400


def _make_order_receipt(db, user_id, supplier_id, product_id, location_id, *, ordered="2", received="1"):
    order = create_purchase_order(db, supplier_id=supplier_id, created_by_user_id=user_id)
    line = add_purchase_order_line(db, purchase_order_id=order.id, product_id=product_id, destination_location_id=location_id, ordered_quantity=ordered, unit_cost_ex_vat="2")
    order_purchase_order(db, purchase_order_id=order.id)
    receipt = create_receipt_from_purchase_order(db, purchase_order_id=order.id, quantities={line.id: received}, received_by_user_id=user_id)
    return order, line, receipt


def test_receipt_post_and_cancel_reject_broken_purchase_order_quantities():
    user_id, supplier_id, product_id, location_id = _seed(sku="P-000102")
    with SessionLocal() as db:
        _order, _line, receipt = _make_order_receipt(db, user_id, supplier_id, product_id, location_id)
        receipt_line = receipt.lines[0]
        receipt_line.purchase_order_line_id = 999
        db.commit()
        receipt_id = receipt.id
        db.expire_all()
        with pytest.raises(ValueError, match="linked to receipt"):
            post_goods_receipt(db, goods_receipt_id=receipt_id, posted_by_user_id=user_id)
        db.rollback()

    # The receipt quantity is within the order when drafted, but can no longer
    # be posted if another concurrent receipt has already consumed the remainder.
    user_id, supplier_id, product_id, location_id = _seed(sku="P-000103")
    with SessionLocal() as db:
        _order, line, receipt = _make_order_receipt(db, user_id, supplier_id, product_id, location_id, ordered="2", received="1")
        line.received_quantity = 2
        db.commit()
        with pytest.raises(ValueError, match="exceed the outstanding"):
            post_goods_receipt(db, goods_receipt_id=receipt.id, posted_by_user_id=user_id)
        db.rollback()

    user_id, supplier_id, product_id, location_id = _seed()
    with SessionLocal() as db:
        _order, line, receipt = _make_order_receipt(db, user_id, supplier_id, product_id, location_id)
        post_goods_receipt(db, goods_receipt_id=receipt.id, posted_by_user_id=user_id)
        line.received_quantity = 0
        db.commit()
        with pytest.raises(ValueError, match="cannot become negative"):
            cancel_goods_receipt(db, goods_receipt_id=receipt.id, user_id=user_id, reason="corrupt received quantity")
        db.rollback()


def test_draft_receipt_discard_rejects_missing_posted_and_blank_reason():
    user_id, supplier_id, product_id, location_id = _seed()
    with SessionLocal() as db:
        with pytest.raises(ValueError, match="not found"):
            discard_draft_goods_receipt(db, goods_receipt_id=999, user_id=user_id)
        _order, _line, receipt = _make_order_receipt(db, user_id, supplier_id, product_id, location_id)
        with pytest.raises(ValueError, match="reason is required"):
            discard_draft_goods_receipt(db, goods_receipt_id=receipt.id, user_id=user_id, reason=" ")
        post_goods_receipt(db, goods_receipt_id=receipt.id, posted_by_user_id=user_id)
        with pytest.raises(ValueError, match="Only draft"):
            discard_draft_goods_receipt(db, goods_receipt_id=receipt.id, user_id=user_id)
