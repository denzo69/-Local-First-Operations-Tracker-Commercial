from __future__ import annotations

import re
from urllib.parse import urlsplit

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import Job, Product, Sale


DYNAMIC_SAMPLE_VALUES = {
    "job_id": "999999",
    "item_id": "999999",
    "customer_id": "999999",
    "product_id": "999999",
    "sale_id": "999999",
    "closing_id": "999999",
    "version": "1",
    "receipt_id": "999999",
    "supplier_id": "999999",
    "warehouse_id": "999999",
    "location_id": "999999",
    "user_id": "999999",
    "register_id": "999999",
    "shift_id": "999999",
    "refund_id": "999999",
    "target_type": "quote",
}


def _concrete_path(route: APIRoute) -> str:
    path = route.path
    for parameter_name in route.param_convertors:
        value = DYNAMIC_SAMPLE_VALUES.get(parameter_name, "999999")
        path = re.sub(r"\{" + re.escape(parameter_name) + r"(?::[^}]+)?\}", value, path)
    return path


def _internal_href_targets(html: str) -> set[str]:
    targets: set[str] = set()
    for raw_href in re.findall(r'href=["\']([^"\']+)["\']', html, flags=re.IGNORECASE):
        href = raw_href.strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        parsed = urlsplit(href)
        if parsed.scheme and parsed.scheme not in {"http", "https"}:
            continue
        if parsed.netloc and parsed.netloc != "testserver":
            continue
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        targets.add(path)
    return targets


def test_every_registered_get_route_handles_missing_records_without_server_error():
    failures: list[tuple[str, int]] = []
    with TestClient(app) as client:
        for route in app.routes:
            if not isinstance(route, APIRoute) or "GET" not in route.methods:
                continue
            path = _concrete_path(route)
            response = client.get(path, follow_redirects=False)
            if response.status_code >= 500:
                failures.append((path, response.status_code))

    assert failures == []


def test_rendered_navigation_and_detail_links_do_not_point_to_missing_pages():
    with TestClient(app) as client:
        customer_response = client.post(
            "/customers",
            data={"name": "Smoke Navigation Customer"},
            follow_redirects=False,
        )
        assert customer_response.status_code == 303
        customer_url = customer_response.headers["location"]

        product_response = client.post(
            "/products",
            data={
                "name": "Smoke Navigation Service",
                "unit_price": "25",
                "vat_percent": "24",
                "unit": "pcs",
                "is_stock_item": "false",
            },
            follow_redirects=False,
        )
        assert product_response.status_code == 303

        work_order_response = client.post(
            "/work-orders",
            data={"title": "Smoke Navigation Work Order"},
            follow_redirects=False,
        )
        assert work_order_response.status_code == 303
        work_order_url = work_order_response.headers["location"]

        seed_paths = {
            "/",
            "/customers",
            customer_url,
            "/products",
            work_order_url,
            "/work-orders",
            "/delivery-notes",
            "/quotes",
            "/sales",
            "/sales/quick",
            "/sales/invoice-queue",
            "/daily-closings",
            "/reports",
            "/settings",
            "/backups",
        }

        discovered: set[str] = set(seed_paths)
        for path in list(seed_paths):
            response = client.get(path, follow_redirects=False)
            assert response.status_code < 500, (path, response.status_code)
            if response.status_code == 200 and "text/html" in response.headers.get("content-type", ""):
                discovered.update(_internal_href_targets(response.text))

        failures: list[tuple[str, int]] = []
        for path in sorted(discovered):
            if path in {"/logout"}:
                continue
            response = client.get(path, follow_redirects=False)
            if response.status_code in {404, 405} or response.status_code >= 500:
                failures.append((path, response.status_code))

    assert failures == []


def test_primary_quote_to_documents_and_sale_workflow_is_operational_and_idempotent():
    with TestClient(app) as client:
        customer_response = client.post(
            "/customers",
            data={
                "name": "Smoke Workflow Customer",
                "company_name": "Smoke Workflow Oy",
                "business_id": "1234567-8",
                "email": "smoke@example.test",
            },
            follow_redirects=False,
        )
        assert customer_response.status_code == 303
        customer_id = customer_response.headers["location"].rsplit("/", 1)[-1]

        product_response = client.post(
            "/products",
            data={
                "name": "Smoke Workflow Service",
                "description": "End-to-end test service",
                "unit_price": "50",
                "vat_percent": "24",
                "unit": "h",
                "is_stock_item": "false",
            },
            follow_redirects=False,
        )
        assert product_response.status_code == 303

        with SessionLocal() as db:
            product = db.query(Product).filter(Product.name == "Smoke Workflow Service").one()
            product_id = product.id

        quote_response = client.post(
            "/quotes",
            data={
                "title": "Smoke Workflow Quote",
                "customer_id": customer_id,
                "description": "Complete workflow verification",
            },
            follow_redirects=False,
        )
        assert quote_response.status_code == 303
        quote_url = quote_response.headers["location"]
        quote_id = int(quote_url.rsplit("/", 1)[-1])

        item_response = client.post(
            f"{quote_url}/items",
            data={
                "product_id": str(product_id),
                "description": "",
                "quantity": "2",
                "unit_price": "50",
                "vat_percent": "24",
            },
            follow_redirects=False,
        )
        assert item_response.status_code == 303

        quote_detail = client.get(quote_url)
        quote_print = client.get(f"{quote_url}/receipt")
        assert quote_detail.status_code == 200
        assert quote_print.status_code == 200
        assert "Smoke Workflow Service" in quote_detail.text
        assert "100.00" in quote_detail.text
        assert '<h1 class="h3 mb-1">Quote</h1>' in quote_print.text

        delivery_response = client.post(
            f"{quote_url}/convert/delivery_note",
            follow_redirects=False,
        )
        work_order_response = client.post(
            f"{quote_url}/convert/work_order",
            follow_redirects=False,
        )
        assert delivery_response.status_code == 303
        assert work_order_response.status_code == 303
        assert client.get(delivery_response.headers["location"]).status_code == 200
        assert client.get(work_order_response.headers["location"]).status_code == 200

        first_sale_response = client.post(
            f"{quote_url}/convert/sale",
            data={"payment_method": "card"},
            follow_redirects=False,
        )
        second_sale_response = client.post(
            f"{quote_url}/convert/sale",
            data={"payment_method": "card"},
            follow_redirects=False,
        )
        assert first_sale_response.status_code == 303
        assert second_sale_response.status_code == 303
        assert first_sale_response.headers["location"] == second_sale_response.headers["location"]
        assert client.get(first_sale_response.headers["location"]).status_code == 200

    with SessionLocal() as db:
        quote = db.get(Job, quote_id)
        sales = db.query(Sale).filter(Sale.work_order_id == quote_id).all()
        derived_types = {
            row.document_type
            for row in db.query(Job).filter(Job.source_job_id == quote_id).all()
        }
        assert quote is not None
        assert quote.document_type == "quote"
        assert len(sales) == 1
        assert sales[0].total == 100
        assert sales[0].payment_method == "card"
        assert derived_types == {"delivery_note", "work_order"}
