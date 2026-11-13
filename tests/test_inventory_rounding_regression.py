from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import InventoryTransaction, Product, Sale, WarehouseLocation
from app.services.inventory_service import inventory_reconciliation


def _post(client, path, data=None):
    response = client.post(path, data=data, follow_redirects=False)
    assert response.status_code == 303, (path, response.status_code, response.text)
    return response


def _sale_id(response):
    return int(response.headers["location"].rsplit("/", 1)[-1])


def _quick_sale(client, product_id, quantity, key):
    return _sale_id(
        _post(
            client,
            "/sales/quick",
            {
                "idempotency_key": key,
                "product_id": [str(product_id)],
                "description": ["Rounding regression stock"],
                "quantity": [str(quantity)],
                "unit_price": ["125.50"],
                "vat_percent": ["25.5"],
                "payment_method": ["card"],
                "payment_amount": [""],
            },
        )
    )


@pytest.mark.parametrize("first_issue", ["delivery_note", "quick_sale", "legacy_cache_repair"])
def test_fractional_average_allows_following_sale_return_and_full_depletion(first_issue):
    with TestClient(app) as client:
        _post(client, "/setup", {"name": "Rounding admin", "login_name": "rounding-admin", "password": "test-only-password"})
        _post(client, "/products", {"sku": "ROUNDING", "name": "Rounding regression stock", "unit_price": "125.50", "vat_percent": "25.5", "is_stock_item": "on"})
        assert client.get("/products/warehouses").status_code == 200
        with SessionLocal() as db:
            product_id = db.query(Product).filter_by(sku="ROUNDING").one().id
            location_id = db.query(WarehouseLocation).order_by(WarehouseLocation.id).first().id

        # Two landed-cost receipts produce a repeating six-decimal average.
        for quantity, price, freight in [("20", "40", "20"), ("10", "50", "10")]:
            receipt = _post(
                client,
                f"/products/{product_id}/receive",
                {
                    "supplier_name": "Rounding supplier",
                    "receipt_date": str(date.today()),
                    "destination_location_id": str(location_id),
                    "quantity_value": quantity,
                    "purchase_unit_price_ex_vat": price,
                    "freight_total_ex_vat": freight,
                },
            )
            _post(client, receipt.headers["location"] + "/post")

        if first_issue == "delivery_note":
            quote = _post(client, "/quotes", {"title": "Rounding regression quote"}).headers["location"]
            _post(client, quote + "/items", {"product_id": str(product_id), "quantity": "2"})
            delivery = _post(client, quote + "/convert/delivery_note").headers["location"]
            _post(client, delivery + "/dispatch")
            _post(client, delivery + "/dispatch")
            first_sale_id = _sale_id(_post(client, delivery + "/convert/sale", {"payment_method": "card"}))
        else:
            first_sale_id = _quick_sale(client, product_id, 2, "rounding-first")

        with SessionLocal() as db:
            product = db.get(Product, product_id)
            assert product.current_inventory_quantity == Decimal("28.000")
            assert product.current_inventory_value_ex_vat == Decimal("1241.33")
            assert product.current_weighted_average_cost_ex_vat == Decimal("44.333214")
            assert inventory_reconciliation(db, product_ids={product_id})["is_clean"]
            first_sale = db.get(Sale, first_sale_id)
            assert first_sale.cost_of_goods_sold_ex_vat == Decimal("88.67")
            assert first_sale.gross_profit_ex_vat == Decimal("111.33")

        if first_issue == "legacy_cache_repair":
            with SessionLocal() as db:
                # Reproduce the stale average left by a pre-fix installation.
                product = db.get(Product, product_id)
                product.current_weighted_average_cost_ex_vat = Decimal("44.333333")
                db.commit()
                assert not inventory_reconciliation(db, product_ids={product_id})["is_clean"]
                before_repair = [(row.id, row.total_inventory_cost) for row in db.query(InventoryTransaction).order_by(InventoryTransaction.id)]
            _post(client, "/products/inventory/reconciliation/repair", {"reason": "Repair pre-fix rounding cache"})
            with SessionLocal() as db:
                assert inventory_reconciliation(db, product_ids={product_id})["is_clean"]
                assert [(row.id, row.total_inventory_cost) for row in db.query(InventoryTransaction).order_by(InventoryTransaction.id)] == before_repair

        # This ordinary second sale was rejected with a cache/ledger error.
        second_sale_id = _quick_sale(client, product_id, 2, "rounding-second")
        with SessionLocal() as db:
            sale = db.get(Sale, second_sale_id)
            line_id = sale.lines[0].id
            assert sale.cost_of_goods_sold_ex_vat == Decimal("88.67")
            assert inventory_reconciliation(db, product_ids={product_id})["is_clean"]

        _post(
            client,
            f"/sales/{second_sale_id}/refund-lines",
            {"sale_line_id": str(line_id), "quantity": "1", "payment_method": "card", "restock": "true", "restock_location_id": str(location_id)},
        )
        with SessionLocal() as db:
            product = db.get(Product, product_id)
            assert product.current_inventory_quantity == Decimal("27.000")
            # Half of the original rounded 88.67 cost is returned as 44.34.
            assert product.current_inventory_value_ex_vat == Decimal("1197.00")
            assert inventory_reconciliation(db, product_ids={product_id})["is_clean"]

        _quick_sale(client, product_id, 27, "rounding-last")
        with SessionLocal() as db:
            product = db.get(Product, product_id)
            assert product.current_inventory_quantity == Decimal("0.000")
            assert product.current_inventory_value_ex_vat == Decimal("0.00")
            assert product.current_weighted_average_cost_ex_vat is None
            assert inventory_reconciliation(db, product_ids={product_id})["is_clean"]
            # Later movements must never rewrite the first sale or its issue.
            first_sale = db.get(Sale, first_sale_id)
            assert first_sale.cost_of_goods_sold_ex_vat == Decimal("88.67")
            assert first_sale.gross_profit_ex_vat == Decimal("111.33")
            first_issue_row = db.query(InventoryTransaction).filter_by(product_id=product_id).order_by(InventoryTransaction.id).all()[2]
            assert first_issue_row.unit_cost_ex_vat == Decimal("44.333333")
            assert first_issue_row.total_inventory_cost == Decimal("-88.67")
