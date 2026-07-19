from datetime import date

from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import AuditLog, InventoryBalance, InventoryTransaction, Job, Product, Receipt, Role, Sale, Setting, Supplier, User
from app.services.inventory_service import (
    add_goods_receipt_line,
    create_default_warehouse,
    create_goods_receipt,
    post_goods_receipt,
)
from app.services.sales_service import ensure_default_roles


def create_inventory_user(db):
    ensure_default_roles(db)
    role = db.query(Role).filter(Role.code == "manager").one()
    user = User(
        name="Inventory Operator",
        login_name="inventory.operator",
        role=role,
        is_active=True,
        can_receive_sales_credit=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def seed_stock(db, *, product_name="Delivery stock product", quantity="5", unit_cost="10"):
    user = create_inventory_user(db)
    supplier = Supplier(name="Delivery Supplier", is_active=True)
    db.add(supplier)
    db.flush()
    product = Product(name=product_name, unit_price="25", vat_percent="24", is_stock_item=True, is_active=True)
    db.add(product)
    db.flush()
    _, location = create_default_warehouse(db)
    db.commit()
    receipt = create_goods_receipt(
        db,
        supplier_id=supplier.id,
        receipt_date=date(2026, 7, 19),
        received_by_user_id=user.id,
        delivery_number=f"STOCK-{product_name}",
    )
    add_goods_receipt_line(
        db,
        goods_receipt_id=receipt.id,
        product_id=product.id,
        destination_location_id=location.id,
        quantity_value=quantity,
        purchase_unit_price_ex_vat=unit_cost,
    )
    post_goods_receipt(db, goods_receipt_id=receipt.id, posted_by_user_id=user.id)
    db.refresh(product)
    return product


def test_new_job_page_has_customer_select():
    with TestClient(app) as client:
        client.post("/customers", data={"name": "Job Customer"}, follow_redirects=False)

        response = client.get("/jobs/new")

    assert response.status_code == 200
    assert 'name="customer_id"' in response.text
    assert "Job Customer" in response.text


def test_create_job_with_customer_redirects_to_detail():
    with TestClient(app) as client:
        customer_response = client.post(
            "/customers",
            data={"name": "Job Owner"},
            follow_redirects=False,
        )
        customer_id = customer_response.headers["location"].rsplit("/", 1)[-1]

        response = client.post(
            "/jobs",
            data={
                "title": "Test job",
                "customer_id": customer_id,
                "arrival_date": "2026-07-10",
                "requested_pickup_date": "2026-07-11",
                "priority": "normal",
            },
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/jobs/")


def test_work_order_routes_create_and_redirect_to_work_order_detail():
    with TestClient(app) as client:
        customer_response = client.post(
            "/customers",
            data={"name": "Job Owner"},
            follow_redirects=False,
        )
        customer_id = customer_response.headers["location"].rsplit("/", 1)[-1]

        form_response = client.get("/work-orders/new")
        response = client.post(
            "/work-orders",
            data={"title": "Test job work order route", "customer_id": customer_id},
            follow_redirects=False,
        )

    assert form_response.status_code == 200
    assert 'action="/work-orders"' in form_response.text
    assert response.status_code == 303
    assert response.headers["location"].startswith("/work-orders/")


def test_quote_does_not_reduce_stock_delivery_note_reduces_once_and_sale_reuses_issue():
    with TestClient(app) as client:
        customer_response = client.post("/customers", data={"name": "Document Customer"}, follow_redirects=False)
        customer_id = customer_response.headers["location"].rsplit("/", 1)[-1]
        with SessionLocal() as db:
            product = seed_stock(db, product_name="Document Stock")
            product_id = product.id

        quote_response = client.post(
            "/quotes",
            data={"title": "Quote document", "customer_id": customer_id},
            follow_redirects=False,
        )
        quote_id = quote_response.headers["location"].rsplit("/", 1)[-1]
        item_response = client.post(
            f"/quotes/{quote_id}/items",
            data={"product_id": str(product_id), "quantity": "2", "unit_price": "25", "vat_percent": "24"},
            follow_redirects=False,
        )
        with SessionLocal() as db:
            stock_after_quote = db.get(Product, product_id).current_inventory_quantity
            quote_issue_count = db.query(InventoryTransaction).filter(InventoryTransaction.product_id == product_id).count()
        delivery_response = client.post(f"/quotes/{quote_id}/convert/delivery_note", follow_redirects=False)
        with SessionLocal() as db:
            delivery = db.query(Job).filter(Job.source_job_id == int(quote_id), Job.document_type == "delivery_note").one()
            stock_after_delivery = db.get(Product, product_id).current_inventory_quantity
            delivery_issue_count = (
                db.query(InventoryTransaction)
                .filter(InventoryTransaction.transaction_type == "delivery_note_issue", InventoryTransaction.work_order_id == delivery.id)
                .count()
            )
            delivery_id = delivery.id
        sale_response = client.post(f"/delivery-notes/{delivery_id}/convert/sale", follow_redirects=False)

    with SessionLocal() as db:
        quote = db.get(Job, int(quote_id))
        delivery = db.get(Job, delivery_id)
        sale = db.query(Sale).filter(Sale.work_order_id == delivery.id).one()
        inventory_transactions = db.query(InventoryTransaction).filter(InventoryTransaction.sale_id == sale.id).count()

    assert quote_response.status_code == 303
    assert item_response.status_code == 303
    assert delivery_response.status_code == 303
    assert sale_response.status_code == 303
    assert quote.document_type == "quote"
    assert delivery.document_type == "delivery_note"
    assert sale.total == 50
    assert stock_after_quote == 5
    assert quote_issue_count == 1
    assert stock_after_delivery == 3
    assert delivery_issue_count == 1
    assert inventory_transactions == 0
    assert sale.cost_of_goods_sold_ex_vat == 20


def test_delivery_note_item_reduces_stock_and_deleting_item_restores_stock_with_reversal():
    with TestClient(app) as client:
        with SessionLocal() as db:
            product = seed_stock(db, product_name="Delivery Delete Stock")
            product_id = product.id

        delivery_response = client.post("/delivery-notes", data={"title": "Delivery stock reservation"}, follow_redirects=False)
        delivery_id = int(delivery_response.headers["location"].rsplit("/", 1)[-1])
        item_response = client.post(
            f"/delivery-notes/{delivery_id}/items",
            data={"product_id": str(product_id), "quantity": "2"},
            follow_redirects=False,
        )

        with SessionLocal() as db:
            item = db.get(Job, delivery_id).items[0]
            stock_after_item = db.get(Product, product_id).current_inventory_quantity
            balance_after_item = db.query(InventoryBalance).filter(InventoryBalance.product_id == product_id).one().quantity_on_hand
            issue_count = (
                db.query(InventoryTransaction)
                .filter(InventoryTransaction.transaction_type == "delivery_note_issue", InventoryTransaction.work_order_id == delivery_id)
                .count()
            )
            item_id = item.id

        delete_response = client.post(f"/delivery-notes/{delivery_id}/items/{item_id}/delete", follow_redirects=False)

    with SessionLocal() as db:
        product = db.get(Product, product_id)
        reversal = (
            db.query(InventoryTransaction)
            .filter(InventoryTransaction.transaction_type == "delivery_note_reversal", InventoryTransaction.work_order_id == delivery_id)
            .one()
        )

    assert delivery_response.status_code == 303
    assert item_response.status_code == 303
    assert delete_response.status_code == 303
    assert stock_after_item == 3
    assert balance_after_item == 3
    assert issue_count == 1
    assert product.current_inventory_quantity == 5
    assert reversal.quantity_change == 2


def test_delivery_note_service_row_does_not_reduce_stock():
    with TestClient(app) as client:
        with SessionLocal() as db:
            user = create_inventory_user(db)
            product = Product(name="Delivery service row", unit_price="75", vat_percent="24", is_stock_item=False, is_active=True)
            db.add(product)
            db.commit()
            product_id = product.id
            user_id = user.id

        delivery_response = client.post("/delivery-notes", data={"title": "Delivery service"}, follow_redirects=False)
        delivery_id = int(delivery_response.headers["location"].rsplit("/", 1)[-1])
        item_response = client.post(
            f"/delivery-notes/{delivery_id}/items",
            data={"product_id": str(product_id), "quantity": "2"},
            follow_redirects=False,
        )

    with SessionLocal() as db:
        product = db.get(Product, product_id)
        transactions = db.query(InventoryTransaction).filter(InventoryTransaction.product_id == product_id).count()
        user_exists = db.get(User, user_id) is not None

    assert delivery_response.status_code == 303
    assert item_response.status_code == 303
    assert product.current_inventory_quantity in (None, 0)
    assert transactions == 0
    assert user_exists is True


def test_work_order_can_be_created_with_empty_customer_selection():
    with TestClient(app) as client:
        response = client.post(
            "/work-orders",
            data={
                "title": "Test job no customer",
                "customer_id": "",
                "priority": "normal",
            },
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/work-orders/")


def test_job_can_be_edited_and_printed():
    with TestClient(app) as client:
        customer_response = client.post(
            "/customers",
            data={"name": "Job Owner"},
            follow_redirects=False,
        )
        customer_id = customer_response.headers["location"].rsplit("/", 1)[-1]

        job_response = client.post(
            "/jobs",
            data={"title": "Test job history only", "customer_id": customer_id},
            follow_redirects=False,
        )
        job_url = job_response.headers["location"]

        edit_response = client.post(
            job_url,
            data={
                "title": "Test job updated",
                "customer_id": customer_id,
                "requested_pickup_date": "2026-07-12",
                "priority": "high",
            },
            follow_redirects=False,
        )
        receipt_response = client.get(f"{job_url}/receipt")

    assert edit_response.status_code == 303
    assert receipt_response.status_code == 200
    assert "Receipt / work order" in receipt_response.text
    assert "Test job updated" in receipt_response.text


def test_job_receipt_number_and_sales_items_work():
    with TestClient(app) as client:
        customer_response = client.post(
            "/customers",
            data={"name": "Job Owner"},
            follow_redirects=False,
        )
        customer_id = customer_response.headers["location"].rsplit("/", 1)[-1]
        product_response = client.post(
            "/products",
            data={
                "name": "Test product",
                "unit_price": "10",
                "vat_percent": "24",
                "unit": "pcs",
            },
            follow_redirects=False,
        )
        assert product_response.status_code == 303

        with SessionLocal() as db:
            product_id = db.query(Product).filter(Product.name == "Test product").first().id
        job_response = client.post(
            "/jobs",
            data={"title": "Test job", "customer_id": customer_id},
            follow_redirects=False,
        )
        job_url = job_response.headers["location"]
        item_response = client.post(
            f"{job_url}/items",
            data={"product_id": product_id, "quantity": "2"},
            follow_redirects=False,
        )
        assert item_response.status_code == 303
        detail_response = client.get(job_url)
        receipt_response = client.get(f"{job_url}/receipt")

    assert "Test product" in detail_response.text
    assert "20.00" in detail_response.text
    assert "Receipt" in receipt_response.text
    assert "Test product" in receipt_response.text


def test_work_order_manual_item_empty_product_id_is_accepted():
    with TestClient(app) as client:
        job_response = client.post(
            "/work-orders",
            data={"title": "Test job manual item"},
            follow_redirects=False,
        )
        job_url = job_response.headers["location"]
        item_response = client.post(
            f"{job_url}/items",
            data={
                "product_id": "",
                "description": "Manual wash",
                "quantity": "2",
                "unit_price": "12.50",
                "vat_percent": "24",
            },
            follow_redirects=False,
        )
        detail_response = client.get(job_url)

    assert item_response.status_code == 303
    assert "Manual wash" in detail_response.text
    assert "25.00" in detail_response.text


def test_work_order_blank_manual_item_returns_clear_validation_error_not_422():
    with TestClient(app) as client:
        job_response = client.post(
            "/work-orders",
            data={"title": "Test job blank manual item"},
            follow_redirects=False,
        )
        job_url = job_response.headers["location"]
        item_response = client.post(
            f"{job_url}/items",
            data={
                "product_id": "",
                "description": "",
                "quantity": "1",
                "unit_price": "0",
                "vat_percent": "24",
            },
            follow_redirects=False,
        )

    assert item_response.status_code == 400
    assert "Item description is required" in item_response.text


def test_work_order_status_can_be_marked_ready():
    with TestClient(app) as client:
        customer_response = client.post(
            "/customers",
            data={"name": "Job Owner"},
            follow_redirects=False,
        )
        customer_id = customer_response.headers["location"].rsplit("/", 1)[-1]

        job_response = client.post(
            "/jobs",
            data={
                "title": "Test job",
                "customer_id": customer_id,
                "requested_pickup_date": "2026-07-11",
            },
            follow_redirects=False,
        )
        job_url = job_response.headers["location"]
        detail_response = client.get(job_url)

        marker = "Ready"
        assert marker in detail_response.text

        before_marker = detail_response.text.split(marker, 1)[0]
        status_id = before_marker.rsplit('name="status_id" value="', 1)[1].split('"', 1)[0]
        response = client.post(
            f"{job_url}/status",
            data={"status_id": status_id},
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert "Current status" in response.text
    assert "Ready" in response.text
    assert "Activity timeline" in response.text
    assert "Status changed from Received to Ready." in response.text


def test_completed_work_order_is_available_in_history():
    with TestClient(app) as client:
        customer_response = client.post(
            "/customers",
            data={"name": "Job Owner"},
            follow_redirects=False,
        )
        customer_id = customer_response.headers["location"].rsplit("/", 1)[-1]

        job_response = client.post(
            "/jobs",
            data={"title": "Test job history only", "customer_id": customer_id},
            follow_redirects=False,
        )
        job_url = job_response.headers["location"]
        detail_response = client.get(job_url)

        before_marker = detail_response.text.split("Completed", 1)[0]
        status_id = before_marker.rsplit('name="status_id" value="', 1)[1].split('"', 1)[0]
        client.post(f"{job_url}/status", data={"status_id": status_id})

        active_response = client.get("/jobs?view=active")
        history_response = client.get("/jobs?view=history")

    assert active_response.status_code == 200
    assert history_response.status_code == 200
    assert "Test job history only" not in active_response.text
    assert "Test job history only" in history_response.text
    assert "Completed" in history_response.text


def test_sold_work_order_is_completed_and_removed_from_active_and_overdue_lists():
    with TestClient(app) as client:
        job_response = client.post(
            "/work-orders",
            data={
                "title": "Sold overdue work order",
                "requested_pickup_date": "2026-07-12",
            },
            follow_redirects=False,
        )
        job_url = job_response.headers["location"]
        item_response = client.post(
            f"{job_url}/items",
            data={
                "product_id": "",
                "description": "Billable service",
                "quantity": "1",
                "unit_price": "25",
                "vat_percent": "24",
            },
            follow_redirects=False,
        )
        sale_response = client.post(f"{job_url}/convert/sale", follow_redirects=False)
        active_response = client.get("/work-orders?view=active")
        ready_response = client.get("/work-orders?view=ready")
        history_response = client.get("/work-orders?view=history")
        dashboard_response = client.get("/")

    with SessionLocal() as db:
        job = db.get(Job, int(job_url.rsplit("/", 1)[-1]))
        sale = db.query(Sale).filter(Sale.work_order_id == job.id).one()
        sale_id = sale.id
        job_is_final = job.status is not None and job.status.is_final is True
        job_converted_at = job.converted_at

    assert item_response.status_code == 303
    assert sale_response.status_code == 303
    assert sale_response.headers["location"] == f"/sales/{sale_id}"
    assert job_is_final is True
    assert job_converted_at is not None
    assert "Sold overdue work order" not in active_response.text
    assert "Sold overdue work order" not in ready_response.text
    assert "Sold overdue work order" in history_response.text
    assert "Sold overdue work order" not in dashboard_response.text


def test_invoice_handoff_work_order_is_completed_and_history_only():
    with TestClient(app) as client:
        customer_response = client.post("/customers", data={"name": "Invoice Customer"}, follow_redirects=False)
        customer_id = customer_response.headers["location"].rsplit("/", 1)[-1]
        job_response = client.post(
            "/work-orders",
            data={
                "title": "Invoice handoff work order",
                "customer_id": customer_id,
                "requested_pickup_date": "2026-07-12",
            },
            follow_redirects=False,
        )
        job_url = job_response.headers["location"]
        client.post(
            f"{job_url}/items",
            data={
                "product_id": "",
                "description": "Invoice service",
                "quantity": "1",
                "unit_price": "40",
                "vat_percent": "24",
            },
            follow_redirects=False,
        )
        invoice_response = client.post(f"{job_url}/convert/invoice", follow_redirects=False)
        active_response = client.get("/work-orders?view=active")
        history_response = client.get("/work-orders?view=history")

    with SessionLocal() as db:
        job = db.get(Job, int(job_url.rsplit("/", 1)[-1]))
        sale = db.query(Sale).filter(Sale.work_order_id == job.id).one()
        settlement_status = sale.settlement_status
        job_is_final = job.status is not None and job.status.is_final is True

    assert invoice_response.status_code == 303
    assert settlement_status == "awaiting_invoice"
    assert job_is_final is True
    assert "Invoice handoff work order" not in active_response.text
    assert "Invoice handoff work order" in history_response.text


def test_jobs_can_be_searched():
    with TestClient(app) as client:
        customer_response = client.post(
            "/customers",
            data={"name": "Job Owner"},
            follow_redirects=False,
        )
        customer_id = customer_response.headers["location"].rsplit("/", 1)[-1]
        client.post(
            "/jobs",
            data={"title": "Test job searchable", "customer_id": customer_id},
            follow_redirects=False,
        )

        response = client.get("/jobs?q=searchable&view=all")

    assert response.status_code == 200
    assert "Test job searchable" in response.text


def test_job_list_has_live_filter_markup():
    with TestClient(app) as client:
        client.post(
            "/jobs",
            data={"title": "Test job live filter"},
            follow_redirects=False,
        )
        response = client.get("/jobs?view=all")

    assert response.status_code == 200
    assert "data-live-filter-form" in response.text
    assert "data-live-filter-input" in response.text
    assert "data-search-text" in response.text


def test_jobs_can_be_searched_by_receipt_number():
    with TestClient(app) as client:
        customer_response = client.post(
            "/customers",
            data={"name": "Job Owner"},
            follow_redirects=False,
        )
        customer_id = customer_response.headers["location"].rsplit("/", 1)[-1]
        job_response = client.post(
            "/jobs",
            data={"title": "Test job receipt lookup", "customer_id": customer_id},
            follow_redirects=False,
        )
        job_url = job_response.headers["location"]
        detail_response = client.get(job_url)
        receipt_number = detail_response.text.split("Receipt number</dt>", 1)[1].split("<dd", 1)[1].split(">", 1)[1].split("<", 1)[0].strip()

        response = client.get(f"/jobs?q={receipt_number}&view=all")

    assert response.status_code == 200
    assert "Test job receipt lookup" in response.text


def test_receipt_number_uses_sequence_setting_not_job_id_and_reprint_preserves_number():
    with TestClient(app) as client:
        customer_response = client.post(
            "/customers",
            data={"name": "Job Owner"},
            follow_redirects=False,
        )
        customer_id = customer_response.headers["location"].rsplit("/", 1)[-1]

        with SessionLocal() as db:
            db.add(Setting(key="receipt_prefix", value="SEQ-"))
            db.add(Setting(key="next_receipt_sequence", value="900001"))
            db.commit()

        job_response = client.post(
            "/work-orders",
            data={"title": "Test job receipt sequence", "customer_id": customer_id},
            follow_redirects=False,
        )
        job_url = job_response.headers["location"]
        first_receipt = client.get(f"{job_url}/receipt")
        second_receipt = client.get(f"{job_url}/receipt")

        with SessionLocal() as db:
            job_id = int(job_url.rsplit("/", 1)[-1])
            job = db.get(Job, job_id)

    assert first_receipt.status_code == 200
    assert second_receipt.status_code == 200
    assert job.receipt_number.endswith("-900001")
    assert job.id != 900001
    assert first_receipt.text.count(job.receipt_number) >= 1
    assert second_receipt.text.count(job.receipt_number) >= 1


def test_print_snapshot_is_stored_once_and_is_stable_after_work_order_update():
    with TestClient(app) as client:
        customer_response = client.post(
            "/customers",
            data={"name": "Job Owner"},
            follow_redirects=False,
        )
        customer_id = customer_response.headers["location"].rsplit("/", 1)[-1]
        job_response = client.post(
            "/work-orders",
            data={"title": "Test job snapshot original", "customer_id": customer_id},
            follow_redirects=False,
        )
        job_url = job_response.headers["location"]
        receipt_response = client.get(f"{job_url}/receipt")

        job_id = int(job_url.rsplit("/", 1)[-1])
        with SessionLocal() as db:
            snapshot = (
                db.query(Receipt)
                .filter(Receipt.job_id == job_id, Receipt.receipt_type == "customer_receipt")
                .first()
            )
            original_payload = snapshot.editable_snapshot
            printed_at = snapshot.printed_at

        client.post(
            job_url,
            data={
                "title": "Test job snapshot changed",
                "customer_id": customer_id,
                "priority": "normal",
            },
            follow_redirects=False,
        )
        client.get(f"{job_url}/receipt")

        with SessionLocal() as db:
            snapshot_after = (
                db.query(Receipt)
                .filter(Receipt.job_id == job_id, Receipt.receipt_type == "customer_receipt")
                .first()
            )
            print_events = (
                db.query(AuditLog)
                .filter(AuditLog.entity_type == "job", AuditLog.entity_id == job_id)
                .filter(AuditLog.event_type == "document.printed")
                .all()
            )

    assert receipt_response.status_code == 200
    assert "Test job snapshot original" in original_payload
    assert "Test job snapshot changed" not in snapshot_after.editable_snapshot
    assert snapshot_after.editable_snapshot == original_payload
    assert snapshot_after.printed_at == printed_at
    assert len(print_events) == 1
