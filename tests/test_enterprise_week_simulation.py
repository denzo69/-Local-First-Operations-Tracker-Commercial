from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import GoodsReceipt, InventoryBalance, InventoryTransaction, Job, Product, Role, Sale, Supplier, User, WarehouseLocation
from app.services.auth_service import hash_password
from app.services.sales_service import ensure_default_roles


def _id_from_location(location: str) -> int:
    return int(location.rstrip("/").rsplit("/", 1)[-1])


def _ensure_operator() -> tuple[int, str, str]:
    login_name = "enterprise.week.operator"
    password = "secret123"
    with SessionLocal() as db:
        ensure_default_roles(db)
        user = db.query(User).filter(User.login_name == login_name).first()
        if user is None:
            admin_role = db.query(Role).filter_by(code="admin").one()
            user = User(
                name="Viikon Simulaatio Operaattori",
                login_name=login_name,
                password_hash=hash_password(password),
                role=admin_role,
                is_active=True,
                can_receive_sales_credit=True,
            )
            db.add(user)
            db.commit()
        return user.id, login_name, password


def test_enterprise_week_simulation_covers_core_business_workflows():
    today = date.today()
    operator_id, login_name, password = _ensure_operator()
    with TestClient(app, raise_server_exceptions=False) as client:
        login = client.post(
            "/login",
            data={"login_name": login_name, "password": password, "next_url": "/"},
            follow_redirects=False,
        )
        assert login.status_code == 303

        settings_response = client.post(
            "/settings",
            data={
                "company_name": "JEronAI Simulation Oy",
                "company_business_id": "1234567-8",
                "company_address": "Testikatu 1, 00100 Helsinki",
                "company_phone": "010 123 4567",
                "company_email": "info@example.test",
                "default_vat_percent": "24",
                "receipt_prefix": "SIM-",
                "language": "fi",
            },
            follow_redirects=False,
        )
        assert settings_response.status_code == 303

        customer_response = client.post(
            "/customers",
            data={
                "name": "Viikon Simulaatioasiakas",
                "company_name": "Simulaatioasiakas Oy",
                "business_id": "8765432-1",
                "email": "asiakas@example.test",
                "default_discount_percent": "5",
            },
            follow_redirects=False,
        )
        assert customer_response.status_code == 303
        customer_id = _id_from_location(customer_response.headers["location"])

        service_response = client.post(
            "/products",
            data={
                "name": "Asennustyö",
                "description": "Palvelutuote, ei varastovähennystä",
                "unit_price": "80",
                "vat_percent": "24",
                "unit": "h",
            },
            follow_redirects=False,
        )
        stock_response = client.post(
            "/products",
            data={
                "name": "Hydraulisuodatin",
                "description": "Varastotuote",
                "unit_price": "45",
                "vat_percent": "24",
                "unit": "pcs",
                "is_stock_item": "on",
            },
            follow_redirects=False,
        )
        assert service_response.status_code == 303
        assert stock_response.status_code == 303

        with SessionLocal() as db:
            service = db.query(Product).filter(Product.name == "Asennustyö").one()
            stock = db.query(Product).filter(Product.name == "Hydraulisuodatin").one()
            location = db.query(WarehouseLocation).order_by(WarehouseLocation.id.asc()).first()
            if location is None:
                client.get("/products/warehouses")
                location = db.query(WarehouseLocation).order_by(WarehouseLocation.id.asc()).first()
            service_id = service.id
            stock_id = stock.id
            location_id = location.id

        receive_form = client.get(f"/products/{stock_id}/receive")
        assert receive_form.status_code == 200
        assert 'name="supplier_name"' in receive_form.text

        receive_response = client.post(
            f"/products/{stock_id}/receive",
            data={
                "supplier_id": "",
                "supplier_name": "Simulaatiotoimittaja Oy",
                "receipt_date": str(today - timedelta(days=6)),
                "received_by_user_id": str(operator_id),
                "destination_location_id": str(location_id),
                "quantity_value": "12",
                "purchase_unit_price_ex_vat": "25",
                "vat_rate": "24",
                "delivery_number": "SIM-DEL-001",
                "invoice_number": "SIM-INV-001",
                "freight_total_ex_vat": "12",
                "freight_vat_rate": "24",
                "other_costs_total_ex_vat": "0",
                "other_costs_vat_rate": "0",
                "allocation_method": "by_value",
            },
            follow_redirects=False,
        )
        assert receive_response.status_code == 303
        receipt_id = _id_from_location(receive_response.headers["location"])

        receipt_detail = client.get(f"/products/goods-receipts/{receipt_id}")
        assert receipt_detail.status_code == 200
        assert "Hydraulisuodatin" in receipt_detail.text
        post_receipt = client.post(
            f"/products/goods-receipts/{receipt_id}/post",
            data={"posted_by_user_id": str(operator_id)},
            follow_redirects=False,
        )
        assert post_receipt.status_code == 303

        with SessionLocal() as db:
            assert db.query(Supplier).filter(Supplier.name == "Simulaatiotoimittaja Oy").one()
            posted_receipt = db.get(GoodsReceipt, receipt_id)
            assert posted_receipt.status == "posted"
            assert db.get(Product, stock_id).current_inventory_quantity == Decimal("12.000")

        quote_response = client.post(
            "/quotes",
            data={
                "title": "Viikon tarjous",
                "customer_id": str(customer_id),
                "requested_pickup_date": str(today + timedelta(days=2)),
            },
            follow_redirects=False,
        )
        assert quote_response.status_code == 303
        quote_id = _id_from_location(quote_response.headers["location"])
        for product_id, quantity in [(service_id, "2"), (stock_id, "3")]:
            item_response = client.post(
                f"/quotes/{quote_id}/items",
                data={"product_id": str(product_id), "quantity": quantity},
                follow_redirects=False,
            )
            assert item_response.status_code == 303

        with SessionLocal() as db:
            assert db.get(Product, stock_id).current_inventory_quantity == Decimal("12.000")

        delivery_response = client.post(f"/quotes/{quote_id}/convert/delivery_note", follow_redirects=False)
        assert delivery_response.status_code == 303
        delivery_id = _id_from_location(delivery_response.headers["location"])
        with SessionLocal() as db:
            assert db.get(Product, stock_id).current_inventory_quantity == Decimal("9.000")
            assert db.query(InventoryTransaction).filter(
                InventoryTransaction.transaction_type == "delivery_note_issue",
                InventoryTransaction.work_order_id == delivery_id,
            ).count() == 1

        work_order_response = client.post(f"/quotes/{quote_id}/convert/work_order", follow_redirects=False)
        assert work_order_response.status_code == 303
        work_order_id = _id_from_location(work_order_response.headers["location"])
        work_order_sale = client.post(f"/work-orders/{work_order_id}/convert/sale", data={"payment_method": "invoice"}, follow_redirects=False)
        assert work_order_sale.status_code == 303
        assert client.get("/sales/invoice-queue").status_code == 200

        delivery_sale = client.post(f"/delivery-notes/{delivery_id}/convert/sale", data={"payment_method": "card"}, follow_redirects=False)
        assert delivery_sale.status_code == 303
        delivery_sale_id = _id_from_location(delivery_sale.headers["location"])
        delivery_receipt = client.get(f"/sales/{delivery_sale_id}/receipt")
        assert delivery_receipt.status_code == 200
        assert "KASSAKUITTI" in delivery_receipt.text
        assert "Viikon Simulaatioasiakas" in delivery_receipt.text

        quick_sale = client.post(
            "/sales/quick",
            data={
                "customer_id": str(customer_id),
                "customer_name": "",
                "product_id": [str(stock_id), str(service_id)],
                "description": ["Hydraulisuodatin", "Asennustyö"],
                "quantity": ["1", "1.5"],
                "unit_price": ["45", "80"],
                "vat_percent": ["24", "24"],
                "discount_percent": ["0", "0"],
                "payment_method": ["cash"],
                "payment_amount": [""],
                "idempotency_key": "enterprise-week-quick-sale",
            },
            follow_redirects=False,
        )
        assert quick_sale.status_code == 303

        with SessionLocal() as db:
            sale = db.query(Sale).filter(Sale.idempotency_key == "enterprise-week-quick-sale").one()
            assert sale.customer_id == customer_id
            assert sale.discount_total == Decimal("8.25")
            assert sale.total == Decimal("156.75")
            assert db.get(Product, stock_id).current_inventory_quantity == Decimal("5.000")
            assert db.query(InventoryTransaction).filter(InventoryTransaction.sale_id == sale.id).count() == 1
            service_transactions = db.query(InventoryTransaction).filter(InventoryTransaction.product_id == service_id).count()
            assert service_transactions == 0

        for path in [
            "/",
            "/customers",
            "/products",
            "/products/inventory",
            "/products/inventory/transactions",
            "/products/inventory/valuation",
            "/products/inventory/reconciliation",
            "/quotes",
            "/delivery-notes",
            "/work-orders",
            "/sales",
            "/reports",
            "/seller-reports",
            "/help",
            "/settings",
        ]:
            response = client.get(path)
            assert response.status_code == 200, (path, response.status_code)

        closing_response = client.post(
            "/daily-closings",
            data={"business_date": str(today), "created_by_user_id": str(operator_id)},
            follow_redirects=False,
        )
        assert closing_response.status_code == 303
        closing_detail = client.get(closing_response.headers["location"])
        assert closing_detail.status_code == 200

    with SessionLocal() as db:
        balance = db.query(InventoryBalance).filter(InventoryBalance.product_id == stock_id).one()
        assert balance.quantity_on_hand == Decimal("5.000")
        assert db.query(Sale).count() == 3
        assert db.query(Job).filter(Job.document_type == "quote").count() == 1
        assert db.query(Job).filter(Job.document_type == "delivery_note").count() == 1
        assert db.query(Job).filter(Job.document_type == "work_order").count() == 1
