from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import app.routes.auth as auth_route
import app.routes.delivery_notes as delivery_notes_route
import app.routes.jobs as jobs_route
import app.routes.products as products_route
import app.routes.quotes as quotes_route
import app.routes.sales as sales_route
import app.routes.work_orders as work_orders_route
from app.database import SessionLocal


def _request(path="/"):
    return SimpleNamespace(
        cookies={},
        state=SimpleNamespace(current_user=None),
        url=SimpleNamespace(path=path),
    )


def _template_response(template, context):
    return SimpleNamespace(template=template, context=context)


def test_setup_form_redirects_when_auth_is_configured(monkeypatch):
    monkeypatch.setattr(auth_route, "auth_is_configured", lambda _db: True)
    response = auth_route.setup_form(_request("/setup"), db=object())
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_document_wrapper_edit_and_receipt_routes_delegate(monkeypatch):
    monkeypatch.setattr(delivery_notes_route.jobs, "edit_job", lambda **kwargs: ("delivery-edit", kwargs))
    monkeypatch.setattr(delivery_notes_route.jobs, "job_receipt", lambda **kwargs: ("delivery-receipt", kwargs))
    monkeypatch.setattr(quotes_route.jobs, "edit_job", lambda **kwargs: ("quote-edit", kwargs))
    monkeypatch.setattr(quotes_route.jobs, "job_receipt", lambda **kwargs: ("quote-receipt", kwargs))
    monkeypatch.setattr(work_orders_route.jobs, "job_receipt", lambda **kwargs: ("work-order-receipt", kwargs))

    request = _request()
    assert delivery_notes_route.edit_delivery_note(1, request, db=object())[0] == "delivery-edit"
    assert delivery_notes_route.delivery_note_receipt(1, request, db=object())[0] == "delivery-receipt"
    assert quotes_route.edit_quote(1, request, db=object())[0] == "quote-edit"
    assert quotes_route.quote_receipt(1, request, db=object())[0] == "quote-receipt"
    assert work_orders_route.print_work_order(1, request, db=object())[0] == "work-order-receipt"
    assert work_orders_route.print_receipt(1, request, db=object())[0] == "work-order-receipt"


def test_missing_job_receipt_raises_404():
    with SessionLocal() as db:
        with pytest.raises(HTTPException) as exc:
            jobs_route.job_receipt(999999, _request("/work-orders/999999/receipt"), db=db)
    assert exc.value.status_code == 404


def test_new_product_goods_receipt_renders_form(monkeypatch):
    monkeypatch.setattr(products_route.templates, "TemplateResponse", _template_response)
    with SessionLocal() as db:
        response = products_route.new_product_goods_receipt(_request("/products/goods-receipts/new"), db=db)
        assert response.template == "inventory/goods_receipts/form.html"
        assert response.context["active_page"] == "products"
        assert response.context["products_inventory_base"] == "/products"


def test_sales_missing_work_order_and_missing_sale_raise_404():
    with SessionLocal() as db:
        with pytest.raises(HTTPException) as work_order_exc:
            sales_route.work_order_sale_form(999999, _request("/sales/work-orders/999999"), db=db)
        assert work_order_exc.value.status_code == 404

        with pytest.raises(HTTPException) as sale_exc:
            sales_route.sale_detail(999999, _request("/sales/999999"), db=db)
        assert sale_exc.value.status_code == 404
