from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import app.routes.jobs as jobs_route
import app.routes.sales as sales_route
import app.services.inventory_service as inventory_service
from app.database import SessionLocal
from app.main import app
from app.models import (
    CashRegister,
    InventoryBalance,
    InventoryReservation,
    InventoryTransaction,
    Job,
    JobItem,
    Product,
    Refund,
    RefundLine,
    Role,
    Sale,
    SaleLine,
    Supplier,
    User,
    WarehouseLocation,
)
from app.services.inventory_service import (
    add_goods_receipt_line,
    cancel_goods_receipt,
    create_default_warehouse,
    create_goods_receipt,
    dispatch_delivery_note_reservations,
    issue_stock_for_sale_from_available_locations,
    post_goods_receipt,
    release_delivery_note_item_reservations,
    reserve_stock_for_delivery_note,
    return_stock_for_refund,
)
from app.services.sales_service import (
    PaymentInput,
    SaleLineInput,
    add_line_refund,
    add_refund,
    create_sale_from_lines,
    create_sale_from_work_order,
    ensure_default_roles,
    open_shift,
    remaining_refundable_quantity,
)


def _operator(db, name="Reservation operator", role_code="manager"):
    ensure_default_roles(db)
    role = db.query(Role).filter(Role.code == role_code).one()
    user = User(
        name=name,
        login_name=name.lower().replace(" ", "."),
        role=role,
        is_active=True,
        can_receive_sales_credit=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _seed_stock(db, *, quantities=("5",), unit_cost="10", name="Reserved product"):
    operator = _operator(db)
    supplier = Supplier(name=f"{name} supplier", is_active=True)
    product = Product(
        name=name,
        unit_price="12.40",
        vat_percent="24",
        is_stock_item=True,
        is_active=True,
    )
    db.add_all([supplier, product])
    db.flush()
    warehouse, default_location = create_default_warehouse(db)
    locations = [default_location]
    for index in range(1, len(quantities)):
        location = WarehouseLocation(
            warehouse_id=warehouse.id,
            code=f"BIN-{index}",
            name=f"Bin {index}",
            is_active=True,
        )
        db.add(location)
        db.flush()
        locations.append(location)
    db.commit()

    receipt = create_goods_receipt(
        db,
        supplier_id=supplier.id,
        receipt_date=date.today(),
        received_by_user_id=operator.id,
        delivery_number=f"OPEN-{product.id}",
    )
    for location, quantity in zip(locations, quantities, strict=True):
        add_goods_receipt_line(
            db,
            goods_receipt_id=receipt.id,
            product_id=product.id,
            destination_location_id=location.id,
            quantity_value=quantity,
            purchase_unit_price_ex_vat=unit_cost,
        )
    post_goods_receipt(db, goods_receipt_id=receipt.id, posted_by_user_id=operator.id)
    db.refresh(product)
    return operator, product, locations, receipt


def _delivery_note(db, product, *, quantities=("1",), title="Reserved delivery"):
    job = Job(title=title, document_type="delivery_note", receipt_number=f"DN-{product.id}-{title}")
    db.add(job)
    db.flush()
    items = []
    for index, quantity in enumerate(quantities, start=1):
        item = JobItem(
            job_id=job.id,
            product_id=product.id,
            description=f"{product.name} row {index}",
            quantity=quantity,
            unit_price=product.unit_price,
            vat_percent=product.vat_percent,
            line_total=Decimal(quantity) * Decimal(str(product.unit_price)),
        )
        db.add(item)
        items.append(item)
    db.commit()
    for item in items:
        db.refresh(item)
    return job, items


def _request(path="/delivery-notes", user=None):
    return SimpleNamespace(
        state=SimpleNamespace(current_user=user),
        cookies={},
        url=SimpleNamespace(path=path),
    )


def _template_response(template, context):
    return SimpleNamespace(template=template, context=context)


def test_reservation_splits_locations_blocks_free_stock_and_releases_safely():
    with SessionLocal() as db:
        operator, product, locations, receipt = _seed_stock(
            db,
            quantities=("2", "3", "4"),
            name="Split reservation product",
        )
        job, (item,) = _delivery_note(db, product, quantities=("4",))

        reservations = reserve_stock_for_delivery_note(
            db,
            product_id=product.id,
            quantity_value="4",
            work_order_id=job.id,
            job_item_id=item.id,
            created_by_user_id=operator.id,
        )

        assert [row.quantity for row in reservations] == [Decimal("2.000"), Decimal("2.000")]
        assert [row.warehouse_location_id for row in reservations] == [locations[0].id, locations[1].id]
        assert product.current_inventory_quantity == Decimal("9.000")
        assert sum((row.quantity_reserved for row in product.inventory_balances), Decimal("0")) == Decimal("4.000")
        assert sum((row.quantity_available for row in product.inventory_balances), Decimal("0")) == Decimal("5.000")

        same_reservations = reserve_stock_for_delivery_note(
            db,
            product_id=product.id,
            quantity_value="4",
            work_order_id=job.id,
            job_item_id=item.id,
            created_by_user_id=operator.id,
        )
        assert [row.id for row in same_reservations] == [row.id for row in reservations]
        with pytest.raises(ValueError, match="different active reservation"):
            reserve_stock_for_delivery_note(
                db,
                product_id=product.id,
                quantity_value="3",
                work_order_id=job.id,
                job_item_id=item.id,
                created_by_user_id=operator.id,
            )
        with pytest.raises(ValueError, match="Negative stock"):
            issue_stock_for_sale_from_available_locations(
                db,
                product_id=product.id,
                quantity_value="6",
                sale_id=999,
                created_by_user_id=operator.id,
            )
        with pytest.raises(ValueError, match="stock is reserved"):
            cancel_goods_receipt(db, goods_receipt_id=receipt.id, user_id=operator.id, reason="Cannot remove reserved stock")

        released = release_delivery_note_item_reservations(
            db,
            work_order_id=job.id,
            job_item_id=item.id,
            created_by_user_id=operator.id,
            reason="",
        )
        assert all(row.status == "released" for row in released)
        assert all(row.release_reason == "Reservation released." for row in released)
        assert sum((row.quantity_reserved for row in product.inventory_balances), Decimal("0")) == Decimal("0.000")
        assert sum((row.quantity_available for row in product.inventory_balances), Decimal("0")) == Decimal("9.000")


def test_dispatch_route_consumes_reservation_once_and_sale_reuses_dispatch_cost():
    with SessionLocal() as db:
        operator, product, (location,), _ = _seed_stock(db, quantities=("5",), name="Dispatch product")
        job, (item,) = _delivery_note(db, product, quantities=("2",), title="Dispatch once")
        reserve_stock_for_delivery_note(
            db,
            product_id=product.id,
            quantity_value=item.quantity,
            work_order_id=job.id,
            job_item_id=item.id,
            created_by_user_id=operator.id,
        )
        job_id = job.id
        product_id = product.id
        operator_id = operator.id
        location_id = location.id

    with TestClient(app) as client:
        missing = client.post("/delivery-notes/999999/dispatch", follow_redirects=False)
        dispatched = client.post(f"/delivery-notes/{job_id}/dispatch", follow_redirects=False)
        repeated = client.post(f"/delivery-notes/{job_id}/dispatch", follow_redirects=False)

    assert missing.status_code == 404
    assert dispatched.status_code == 303
    assert repeated.status_code == 303

    with SessionLocal() as db:
        reservation = db.query(InventoryReservation).filter(InventoryReservation.job_id == job_id).one()
        balance = db.query(InventoryBalance).filter(
            InventoryBalance.product_id == product_id,
            InventoryBalance.warehouse_location_id == location_id,
        ).one()
        issues = db.query(InventoryTransaction).filter(
            InventoryTransaction.work_order_id == job_id,
            InventoryTransaction.transaction_type == "delivery_note_issue",
        ).all()
        assert reservation.status == "consumed"
        assert reservation.inventory_transaction_id == issues[0].id
        assert len(issues) == 1
        assert balance.quantity_on_hand == Decimal("3.000")
        assert balance.quantity_reserved == Decimal("0.000")
        assert balance.quantity_available == Decimal("3.000")

        sale = create_sale_from_work_order(
            db,
            work_order_id=job_id,
            payments=[PaymentInput("card")],
            created_by_user_id=operator_id,
        )
        assert sale.source_type == "delivery_note"
        assert sale.cost_of_goods_sold_ex_vat == Decimal("20.00")
        assert db.get(Product, product_id).current_inventory_quantity == Decimal("3.000")
        assert db.query(InventoryTransaction).filter(InventoryTransaction.sale_id == sale.id).count() == 0

        direct_job, (direct_item,) = _delivery_note(db, db.get(Product, product_id), quantities=("1",), title="Direct dispatch")
        reserve_stock_for_delivery_note(
            db,
            product_id=product_id,
            quantity_value="1",
            work_order_id=direct_job.id,
            job_item_id=direct_item.id,
            created_by_user_id=operator_id,
        )
        direct_transactions = dispatch_delivery_note_reservations(
            db,
            work_order_id=direct_job.id,
            created_by_user_id=operator_id,
        )
        assert len(direct_transactions) == 1
        assert direct_transactions[0].transaction_type == "delivery_note_issue"


def test_delivery_note_inventory_states_and_dispatch_error(monkeypatch):
    monkeypatch.setattr(jobs_route.templates, "TemplateResponse", _template_response)
    with SessionLocal() as db:
        operator, product, _, _ = _seed_stock(db, quantities=("5",), name="State product")
        job, items = _delivery_note(db, product, quantities=("1", "1"), title="State delivery")
        for item in items:
            reserve_stock_for_delivery_note(
                db,
                product_id=product.id,
                quantity_value=item.quantity,
                work_order_id=job.id,
                job_item_id=item.id,
                created_by_user_id=operator.id,
            )

        reserved = jobs_route.job_detail(job.id, _request(), db)
        assert reserved.context["inventory_state"] == "reserved"

        inventory_service._consume_delivery_note_item_reservations(
            db,
            work_order_id=job.id,
            job_item_id=items[0].id,
            created_by_user_id=None,
            transaction_type="delivery_note_issue",
        )
        partial = jobs_route.job_detail(job.id, _request(), db)
        assert partial.context["inventory_state"] == "partially_dispatched"

        inventory_service._consume_delivery_note_item_reservations(
            db,
            work_order_id=job.id,
            job_item_id=items[1].id,
            created_by_user_id=operator.id,
            transaction_type="delivery_note_issue",
        )
        dispatched = jobs_route.job_detail(job.id, _request(), db)
        assert dispatched.context["inventory_state"] == "dispatched"

        bad_job, (bad_item,) = _delivery_note(db, product, quantities=("1",), title="Broken reservation")
        reserve_stock_for_delivery_note(
            db,
            product_id=product.id,
            quantity_value=bad_item.quantity,
            work_order_id=bad_job.id,
            job_item_id=bad_item.id,
            created_by_user_id=operator.id,
        )
        bad_balance = db.query(InventoryBalance).filter(InventoryBalance.product_id == product.id).one()
        bad_balance.quantity_reserved = Decimal("0")
        bad_balance.quantity_available = bad_balance.quantity_on_hand
        db.commit()
        with pytest.raises(HTTPException) as error:
            jobs_route.dispatch_delivery_note(_request(user=operator), bad_job.id, db)
        assert error.value.status_code == 400
        assert "inconsistent" in error.value.detail


def test_reservation_service_rejects_invalid_documents_products_and_corruption():
    with SessionLocal() as db:
        operator, product, _, _ = _seed_stock(db, quantities=("2",), name="Validation stock")
        service = Product(name="Service", is_stock_item=False, is_active=True)
        quote = Job(title="Not a delivery", document_type="quote")
        db.add_all([service, quote])
        db.flush()
        quote_item = JobItem(job_id=quote.id, product_id=product.id, description="Quote row", quantity="1")
        db.add(quote_item)
        db.commit()

        with pytest.raises(ValueError, match="Stock product"):
            reserve_stock_for_delivery_note(
                db,
                product_id=service.id,
                quantity_value="1",
                work_order_id=quote.id,
                job_item_id=quote_item.id,
                created_by_user_id=operator.id,
            )
        with pytest.raises(ValueError, match="Delivery note is required"):
            reserve_stock_for_delivery_note(
                db,
                product_id=product.id,
                quantity_value="1",
                work_order_id=quote.id,
                job_item_id=quote_item.id,
                created_by_user_id=operator.id,
            )

        job, (item,) = _delivery_note(db, product, quantities=("1",), title="Validation delivery")
        with pytest.raises(ValueError, match="does not match"):
            reserve_stock_for_delivery_note(
                db,
                product_id=product.id,
                quantity_value="1",
                work_order_id=job.id,
                job_item_id=quote_item.id,
                created_by_user_id=operator.id,
            )
        with pytest.raises(ValueError, match="Insufficient available"):
            reserve_stock_for_delivery_note(
                db,
                product_id=product.id,
                quantity_value="3",
                work_order_id=job.id,
                job_item_id=item.id,
                created_by_user_id=operator.id,
            )

        reservation = reserve_stock_for_delivery_note(
            db,
            product_id=product.id,
            quantity_value="1",
            work_order_id=job.id,
            job_item_id=item.id,
            created_by_user_id=operator.id,
        )[0]
        with pytest.raises(ValueError, match="does not match the document row"):
            inventory_service._consume_delivery_note_item_reservations(
                db,
                work_order_id=job.id,
                job_item_id=item.id,
                created_by_user_id=operator.id,
                transaction_type="sale",
                expected_quantity="2",
            )
        balance = db.query(InventoryBalance).filter(InventoryBalance.product_id == product.id).one()
        balance.quantity_reserved = Decimal("0")
        balance.quantity_available = balance.quantity_on_hand
        db.commit()
        with pytest.raises(ValueError, match="inconsistent"):
            release_delivery_note_item_reservations(
                db,
                work_order_id=job.id,
                job_item_id=item.id,
                created_by_user_id=operator.id,
                reason="corrupt",
            )
        db.rollback()
        reservation = db.get(InventoryReservation, reservation.id)
        reservation.status = "released"
        db.commit()

        with pytest.raises(ValueError, match="Invalid reservation consumption"):
            inventory_service._consume_delivery_note_item_reservations(
                db,
                work_order_id=job.id,
                job_item_id=item.id,
                created_by_user_id=operator.id,
                transaction_type="invalid",
            )
        with pytest.raises(ValueError, match="Delivery note is required"):
            inventory_service._consume_delivery_note_item_reservations(
                db,
                work_order_id=quote.id,
                job_item_id=quote_item.id,
                created_by_user_id=operator.id,
                transaction_type="sale",
            )
        with pytest.raises(ValueError, match="Stock delivery note item"):
            inventory_service._consume_delivery_note_item_reservations(
                db,
                work_order_id=job.id,
                job_item_id=999999,
                created_by_user_id=operator.id,
                transaction_type="sale",
            )
        assert inventory_service._consume_delivery_note_item_reservations(
            db,
            work_order_id=job.id,
            job_item_id=item.id,
            created_by_user_id=operator.id,
            transaction_type="sale",
        ) == []
        with pytest.raises(ValueError, match="Delivery note not found"):
            dispatch_delivery_note_reservations(db, work_order_id=quote.id, created_by_user_id=operator.id)


def test_line_refunds_allocate_exact_vat_and_optionally_return_stock():
    with SessionLocal() as db:
        operator, stock_product, (location,), _ = _seed_stock(db, quantities=("5",), name="Refund stock")
        service_product = Product(
            name="Refund service",
            unit_price="11",
            vat_percent="10",
            is_stock_item=False,
            is_active=True,
        )
        db.add(service_product)
        db.commit()
        sale = create_sale_from_lines(
            db,
            lines=[
                SaleLineInput(
                    product_id=stock_product.id,
                    description=stock_product.name,
                    quantity="2",
                    unit_price="12.40",
                    vat_percent="24",
                ),
                SaleLineInput(
                    product_id=service_product.id,
                    description=service_product.name,
                    quantity="1",
                    unit_price="11",
                    vat_percent="10",
                ),
            ],
            payments=[PaymentInput("card")],
            created_by_user_id=operator.id,
        )
        stock_line_id = next(line.id for line in sale.lines if line.product_id == stock_product.id)
        service_line_id = next(line.id for line in sale.lines if line.product_id == service_product.id)
        sale_id = sale.id
        location_id = location.id
        operator_id = operator.id
        assert stock_product.current_inventory_quantity == Decimal("3.000")

    with TestClient(app) as client:
        response = client.post(
            f"/sales/{sale_id}/refund-lines",
            data={
                "sale_line_id": str(stock_line_id),
                "quantity": "1",
                "refund_shift_id": "",
                "payment_method": "card",
                "reason": "Unopened return",
                "restock": "true",
                "restock_location_id": str(location_id),
            },
            follow_redirects=False,
        )
        excessive = client.post(
            f"/sales/{sale_id}/refund-lines",
            data={
                "sale_line_id": str(stock_line_id),
                "quantity": "99",
                "refund_shift_id": "",
                "payment_method": "card",
            },
            follow_redirects=False,
        )
        detail = client.get(f"/sales/{sale_id}")

    assert response.status_code == 303
    assert excessive.status_code == 400
    assert detail.status_code == 200
    assert 'action="/sales/' in detail.text
    assert "/refund-lines" in detail.text
    assert "Unopened return" in detail.text

    with SessionLocal() as db:
        sale = db.get(Sale, sale_id)
        stock_line = db.get(type(sale.lines[0]), stock_line_id)
        first_refund = sale.refunds[0]
        first_line = first_refund.lines[0]
        returned_product = db.get(Product, stock_line.product_id)
        assert first_refund.amount == Decimal("12.40")
        assert first_refund.vat_amount == Decimal("2.40")
        assert first_line.net_amount == Decimal("10.00")
        assert first_line.quantity == Decimal("1.000")
        assert first_line.restocked is True
        assert first_line.inventory_cost_ex_vat == Decimal("10.00")
        assert first_line.inventory_transaction.transaction_type == "customer_return"
        assert first_line.inventory_transaction.quantity_change == Decimal("1.000")
        assert returned_product.current_inventory_quantity == Decimal("4.000")
        assert remaining_refundable_quantity(stock_line) == Decimal("1.000")
        assert sale.status == "partially_refunded"

        second = add_line_refund(
            db,
            sale_id=sale.id,
            sale_line_id=stock_line.id,
            quantity_value="1",
            refund_shift_id=None,
            seller_id=operator_id,
            payment_method="card",
        )
        assert second.amount == Decimal("12.40")
        assert second.lines[0].restocked is False
        assert second.lines[0].inventory_transaction_id is None
        service_refund = add_line_refund(
            db,
            sale_id=sale.id,
            sale_line_id=service_line_id,
            quantity_value="1",
            refund_shift_id=None,
            seller_id=operator_id,
            payment_method="cash",
            reason="Service cancelled",
        )
        assert service_refund.amount == Decimal("11.00")
        assert service_refund.vat_amount == Decimal("1.00")
        assert db.get(Sale, sale.id).status == "refunded"
        assert remaining_refundable_quantity(stock_line) == Decimal("0.000")


def test_line_refund_validation_and_atomic_rollback(monkeypatch):
    with SessionLocal() as db:
        operator, stock_product, (location,), _ = _seed_stock(db, quantities=("6",), name="Refund validation stock")
        other = _operator(db, "Other refund operator", "seller")
        register = CashRegister(name="Refund register", is_active=True)
        service = Product(name="Refund validation service", is_stock_item=False, is_active=True)
        db.add_all([register, service])
        db.commit()
        shift = open_shift(
            db,
            seller_id=operator.id,
            cash_register_id=register.id,
            business_date=date.today(),
            starting_cash="0",
        )
        sale = create_sale_from_lines(
            db,
            lines=[SaleLineInput(product_id=stock_product.id, description="Stock", quantity="2", unit_price="10", vat_percent="24")],
            payments=[PaymentInput("card")],
            created_by_user_id=operator.id,
        )
        other_sale = create_sale_from_lines(
            db,
            lines=[SaleLineInput(product_id=service.id, description="Service", quantity="1", unit_price="10", vat_percent="24")],
            payments=[PaymentInput("card")],
            created_by_user_id=operator.id,
        )
        zero_sale = create_sale_from_lines(
            db,
            lines=[SaleLineInput(product_id=service.id, description="Free service", quantity="1", unit_price="0", vat_percent="24")],
            payments=[],
            created_by_user_id=operator.id,
        )
        line = sale.lines[0]
        service_line = other_sale.lines[0]
        zero_line = zero_sale.lines[0]

        common = {
            "refund_shift_id": None,
            "seller_id": operator.id,
            "payment_method": "card",
        }
        with pytest.raises(ValueError, match="Sale not found"):
            add_line_refund(db, sale_id=999999, sale_line_id=line.id, quantity_value="1", **common)
        with pytest.raises(ValueError, match="Sale line not found"):
            add_line_refund(db, sale_id=sale.id, sale_line_id=999999, quantity_value="1", **common)
        with pytest.raises(ValueError, match="Sale line not found"):
            add_line_refund(db, sale_id=sale.id, sale_line_id=service_line.id, quantity_value="1", **common)
        with pytest.raises(ValueError, match="open shift"):
            add_line_refund(
                db,
                sale_id=sale.id,
                sale_line_id=line.id,
                quantity_value="1",
                refund_shift_id=999999,
                seller_id=operator.id,
                payment_method="card",
            )
        with pytest.raises(ValueError, match="match refund shift"):
            add_line_refund(
                db,
                sale_id=sale.id,
                sale_line_id=line.id,
                quantity_value="1",
                refund_shift_id=shift.id,
                seller_id=other.id,
                payment_method="card",
            )
        with pytest.raises(ValueError, match="payment method"):
            add_line_refund(db, sale_id=sale.id, sale_line_id=line.id, quantity_value="1", **(common | {"payment_method": "wire"}))
        with pytest.raises(ValueError, match="remaining sale-line"):
            add_line_refund(db, sale_id=sale.id, sale_line_id=line.id, quantity_value="3", **common)
        with pytest.raises(ValueError, match="Only a stock product"):
            add_line_refund(
                db,
                sale_id=other_sale.id,
                sale_line_id=service_line.id,
                quantity_value="1",
                restock=True,
                restock_location_id=location.id,
                **common,
            )
        with pytest.raises(ValueError, match="Restock location"):
            add_line_refund(
                db,
                sale_id=sale.id,
                sale_line_id=line.id,
                quantity_value="1",
                restock=True,
                **common,
            )
        with pytest.raises(ValueError, match="positive refundable amount"):
            add_line_refund(db, sale_id=zero_sale.id, sale_line_id=zero_line.id, quantity_value="1", **common)

        shift_sale = create_sale_from_lines(
            db,
            lines=[SaleLineInput(product_id=service.id, description="Shift refund", quantity="1", unit_price="5", vat_percent="24")],
            payments=[PaymentInput("cash")],
            created_by_user_id=operator.id,
        )
        shifted_refund = add_line_refund(
            db,
            sale_id=shift_sale.id,
            sale_line_id=shift_sale.lines[0].id,
            quantity_value="1",
            refund_shift_id=shift.id,
            seller_id=operator.id,
            payment_method="cash",
        )
        assert shifted_refund.shift_id == shift.id
        assert shifted_refund.business_date == shift.business_date

        add_refund(
            db,
            sale_id=sale.id,
            refund_shift_id=None,
            seller_id=operator.id,
            amount="15",
            payment_method="card",
        )
        with pytest.raises(ValueError, match="remaining refundable sale total"):
            add_line_refund(db, sale_id=sale.id, sale_line_id=line.id, quantity_value="1", **common)

        rollback_sale = create_sale_from_lines(
            db,
            lines=[SaleLineInput(product_id=stock_product.id, description="Rollback", quantity="1", unit_price="10", vat_percent="24")],
            payments=[PaymentInput("card")],
            created_by_user_id=operator.id,
        )
        refund_count = db.query(Refund).count()
        monkeypatch.setattr(inventory_service, "return_stock_for_refund", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("return failed")))
        with pytest.raises(ValueError, match="return failed"):
            add_line_refund(
                db,
                sale_id=rollback_sale.id,
                sale_line_id=rollback_sale.lines[0].id,
                quantity_value="1",
                restock=True,
                restock_location_id=location.id,
                **common,
            )
        assert db.query(Refund).count() == refund_count


def test_restocked_partial_refunds_allocate_the_final_cost_cent_exactly():
    with SessionLocal() as db:
        operator, product, (location,), _ = _seed_stock(
            db,
            quantities=("1",),
            unit_cost="10",
            name="Refund rounding stock",
        )
        supplier = db.query(Supplier).one()
        second_receipt = create_goods_receipt(
            db,
            supplier_id=supplier.id,
            receipt_date=date.today(),
            received_by_user_id=operator.id,
            delivery_number="ROUNDING-SECOND",
        )
        add_goods_receipt_line(
            db,
            goods_receipt_id=second_receipt.id,
            product_id=product.id,
            destination_location_id=location.id,
            quantity_value="2",
            purchase_unit_price_ex_vat="10.01",
        )
        post_goods_receipt(db, goods_receipt_id=second_receipt.id, posted_by_user_id=operator.id)
        sale = create_sale_from_lines(
            db,
            lines=[
                SaleLineInput(
                    product_id=product.id,
                    description=product.name,
                    quantity="3",
                    unit_price="1",
                    vat_percent="24",
                )
            ],
            payments=[PaymentInput("card")],
            created_by_user_id=operator.id,
        )
        assert sale.lines[0].cost_of_goods_sold_ex_vat == Decimal("30.02")

        returned_costs = []
        for _ in range(3):
            refund = add_line_refund(
                db,
                sale_id=sale.id,
                sale_line_id=sale.lines[0].id,
                quantity_value="1",
                refund_shift_id=None,
                seller_id=operator.id,
                payment_method="card",
                restock=True,
                restock_location_id=location.id,
            )
            returned_costs.append(refund.lines[0].inventory_cost_ex_vat)

        assert returned_costs == [Decimal("10.01"), Decimal("10.00"), Decimal("10.01")]
        assert sum(returned_costs, Decimal("0")) == Decimal("30.02")
        assert product.current_inventory_quantity == Decimal("3.000")
        assert product.current_inventory_value_ex_vat == Decimal("30.02")


def test_return_stock_validation_commit_and_document_source_validation():
    with SessionLocal() as db:
        operator, product, (location,), _ = _seed_stock(db, quantities=("2",), name="Direct return stock")
        sale = create_sale_from_lines(
            db,
            lines=[SaleLineInput(product_id=product.id, description="Sold item", quantity="1", unit_price="10", vat_percent="24")],
            payments=[PaymentInput("card")],
            created_by_user_id=operator.id,
        )
        refund = add_line_refund(
            db,
            sale_id=sale.id,
            sale_line_id=sale.lines[0].id,
            quantity_value="1",
            refund_shift_id=None,
            seller_id=operator.id,
            payment_method="card",
        )
        refund_line = refund.lines[0]

        transaction = return_stock_for_refund(
            db,
            product_id=product.id,
            warehouse_location_id=location.id,
            quantity_value="1",
            unit_cost_ex_vat="10",
            sale_id=sale.id,
            refund_line_id=refund_line.id,
            created_by_user_id=operator.id,
            reason="",
        )
        assert transaction.transaction_type == "customer_return"
        assert transaction.adjustment_reason == "Customer return"
        assert transaction.reference == f"refund_line:{refund_line.id}"

        empty_stock = Product(name="Previously empty return stock", is_stock_item=True, is_active=True)
        db.add(empty_stock)
        db.commit()
        empty_return = return_stock_for_refund(
            db,
            product_id=empty_stock.id,
            warehouse_location_id=location.id,
            quantity_value="1",
            unit_cost_ex_vat="4",
            sale_id=sale.id,
            refund_line_id=refund_line.id,
            created_by_user_id=operator.id,
        )
        assert empty_return.weighted_average_cost_before is None
        assert empty_stock.current_weighted_average_cost_ex_vat == Decimal("4.000000")

        service = Product(name="Direct return service", is_stock_item=False, is_active=True)
        db.add(service)
        db.commit()
        with pytest.raises(ValueError, match="Stock product"):
            return_stock_for_refund(
                db,
                product_id=service.id,
                warehouse_location_id=location.id,
                quantity_value="1",
                unit_cost_ex_vat="10",
                sale_id=sale.id,
                refund_line_id=refund_line.id,
                created_by_user_id=operator.id,
            )
        with pytest.raises(ValueError, match="cannot be negative"):
            return_stock_for_refund(
                db,
                product_id=product.id,
                warehouse_location_id=location.id,
                quantity_value="1",
                unit_cost_ex_vat="-1",
                sale_id=sale.id,
                refund_line_id=refund_line.id,
                created_by_user_id=operator.id,
            )
        with pytest.raises(ValueError, match="Document sale requires"):
            create_sale_from_lines(
                db,
                lines=[SaleLineInput(description="Missing quote", quantity="1", unit_price="1", vat_percent="24")],
                payments=[PaymentInput("card")],
                source_type="quote",
                created_by_user_id=operator.id,
            )

        quote = Job(title="Source validation quote", document_type="quote")
        db.add(quote)
        db.flush()
        quote_item = JobItem(
            job_id=quote.id,
            description="Quoted service",
            quantity="1",
            unit_price="1",
            vat_percent="24",
            line_total="1",
        )
        db.add(quote_item)
        db.commit()
        with pytest.raises(ValueError, match="does not match"):
            create_sale_from_lines(
                db,
                work_order_id=quote.id,
                lines=[
                    SaleLineInput(
                        work_order_item_id=quote_item.id,
                        description=quote_item.description,
                        quantity="1",
                        unit_price="1",
                        vat_percent="24",
                    )
                ],
                payments=[PaymentInput("card")],
                source_type="delivery_note",
                created_by_user_id=operator.id,
            )
        quote_sale = create_sale_from_work_order(
            db,
            work_order_id=quote.id,
            payments=[PaymentInput("card")],
            created_by_user_id=operator.id,
        )
        assert quote_sale.source_type == "quote"
        assert quote_sale.idempotency_key == f"quote:{quote.id}"


def test_line_refund_route_errors_and_missing_operator():
    with SessionLocal() as db:
        sale = Sale(
            payment_method="card",
            subtotal="10",
            vat_total="2.40",
            total="12.40",
            settlement_status="paid",
            status="completed",
        )
        db.add(sale)
        db.flush()
        sale_line = SaleLine(
            sale_id=sale.id,
            description_snapshot="Anonymous sale",
            quantity="1",
            unit_price="12.40",
            vat_percent="24",
            line_total="12.40",
            vat_amount="2.40",
        )
        db.add(sale_line)
        db.commit()

        with pytest.raises(HTTPException) as missing_sale:
            sales_route.create_line_refund(
                999999,
                _request(path="/sales"),
                sale_line_id=sale_line.id,
                quantity="1",
                refund_shift_id="",
                payment_method="card",
                db=db,
            )
        assert missing_sale.value.status_code == 404

        with pytest.raises(HTTPException) as missing_shift:
            sales_route.create_line_refund(
                sale.id,
                _request(path="/sales"),
                sale_line_id=sale_line.id,
                quantity="1",
                refund_shift_id="999999",
                payment_method="card",
                db=db,
            )
        assert missing_shift.value.status_code == 400

        with pytest.raises(HTTPException) as missing_operator:
            sales_route.create_line_refund(
                sale.id,
                _request(path="/sales"),
                sale_line_id=sale_line.id,
                quantity="1",
                refund_shift_id="",
                payment_method="card",
                db=db,
            )
        assert missing_operator.value.status_code == 400


def test_remaining_refundable_quantity_never_becomes_negative():
    with SessionLocal() as db:
        operator = _operator(db)
        sale = Sale(payment_method="card", subtotal="1", vat_total="0", total="1", status="completed")
        db.add(sale)
        db.flush()
        line = SaleLine(
            sale_id=sale.id,
            description_snapshot="Corrupt historical refund quantity",
            quantity="1",
            unit_price="1",
            vat_percent="0",
            line_total="1",
        )
        db.add(line)
        db.flush()
        refund = Refund(
            sale_id=sale.id,
            seller_id=operator.id,
            amount="1",
            vat_amount="0",
            payment_method="card",
        )
        db.add(refund)
        db.flush()
        db.add(
            RefundLine(
                refund_id=refund.id,
                sale_line_id=line.id,
                quantity="2",
                gross_amount="1",
                net_amount="1",
                vat_amount="0",
                restocked=False,
                inventory_cost_ex_vat="0",
            )
        )
        db.commit()
        db.refresh(line)
        assert remaining_refundable_quantity(line) == Decimal("0")
