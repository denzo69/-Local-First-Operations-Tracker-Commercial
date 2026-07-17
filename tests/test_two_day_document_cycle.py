from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import DailyClosing, Job, Product, Sale, User
from app.routes import jobs as jobs_routes
from app.services import sales_service
from app.services.sales_service import create_daily_closing, get_latest_daily_closing_snapshot


ROUTE_BASES = {
    "quote": "/quotes",
    "delivery_note": "/delivery-notes",
    "work_order": "/work-orders",
}
DOCUMENT_TYPES = tuple(ROUTE_BASES)
ORDERED_DOCUMENT_CONVERSIONS = tuple(
    (source_type, target_type)
    for source_type in DOCUMENT_TYPES
    for target_type in DOCUMENT_TYPES
    if source_type != target_type
)


class ControlledBusinessDate(date):
    current = date(2026, 7, 20)

    @classmethod
    def today(cls):
        return cls.current


def _set_business_date(monkeypatch, business_date: date) -> None:
    ControlledBusinessDate.current = business_date
    monkeypatch.setattr(sales_service, "date", ControlledBusinessDate)
    monkeypatch.setattr(jobs_routes, "date", ControlledBusinessDate)


def _create_customer(client: TestClient) -> int:
    response = client.post(
        "/customers",
        data={
            "name": "Two-day cycle customer",
            "company_name": "Cycle Test Oy",
            "business_id": "7654321-0",
            "email": "cycle@example.test",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    return int(response.headers["location"].rsplit("/", 1)[-1])


def _create_service_product(client: TestClient) -> int:
    response = client.post(
        "/products",
        data={
            "name": "Two-day cycle service",
            "description": "Service line for exhaustive document workflow testing",
            "unit_price": "10",
            "vat_percent": "24",
            "unit": "pcs",
            "is_stock_item": "false",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    with SessionLocal() as db:
        return db.query(Product).filter(Product.name == "Two-day cycle service").one().id


def _create_document(
    client: TestClient,
    *,
    document_type: str,
    title: str,
    customer_id: int,
    product_id: int,
) -> tuple[int, str]:
    route_base = ROUTE_BASES[document_type]
    response = client.post(
        route_base,
        data={
            "title": title,
            "customer_id": str(customer_id),
            "description": "Two-day exhaustive workflow document",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    document_url = response.headers["location"]
    document_id = int(document_url.rsplit("/", 1)[-1])

    item_response = client.post(
        f"{document_url}/items",
        data={
            "product_id": str(product_id),
            "description": "",
            "quantity": "1",
            "unit_price": "10",
            "vat_percent": "24",
        },
        follow_redirects=False,
    )
    assert item_response.status_code == 303
    assert client.get(document_url).status_code == 200
    assert client.get(f"{document_url}/receipt").status_code == 200
    return document_id, document_url


def _convert_document(
    client: TestClient,
    *,
    source_url: str,
    target_type: str,
) -> tuple[int, str]:
    response = client.post(
        f"{source_url}/convert/{target_type}",
        follow_redirects=False,
    )
    assert response.status_code == 303
    target_url = response.headers["location"]
    target_id = int(target_url.rsplit("/", 1)[-1])
    assert client.get(target_url).status_code == 200
    return target_id, target_url


def _settle_document(
    client: TestClient,
    *,
    document_url: str,
    settlement: str,
) -> int:
    target_type = "invoice" if settlement == "invoice" else "sale"
    response = client.post(
        f"{document_url}/convert/{target_type}",
        data={"payment_method": "card"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    sale_url = response.headers["location"]
    assert client.get(sale_url).status_code == 200
    return int(sale_url.rsplit("/", 1)[-1])


def _create_quick_sale(
    client: TestClient,
    *,
    customer_id: int,
    product_id: int,
    settlement: str,
    unique_key: str,
) -> int:
    if settlement == "invoice":
        payment_methods = ["invoice"]
        payment_amounts = [""]
        send_to_invoice = "true"
    else:
        payment_methods = ["card"]
        payment_amounts = ["10"]
        send_to_invoice = "false"

    response = client.post(
        "/sales/quick",
        data={
            "customer_id": str(customer_id),
            "customer_name": "",
            "seller_mode": "default",
            "idempotency_key": unique_key,
            "product_id": [str(product_id)],
            "description": ["Two-day direct quick sale"],
            "quantity": ["1"],
            "unit_price": ["10"],
            "vat_percent": ["24"],
            "discount_amount": ["0"],
            "payment_method": payment_methods,
            "payment_amount": payment_amounts,
            "send_to_invoice": send_to_invoice,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    sale_url = response.headers["location"]
    assert client.get(sale_url).status_code == 200
    return int(sale_url.rsplit("/", 1)[-1])


def _run_complete_day(
    client: TestClient,
    *,
    business_date: date,
    day_label: str,
    customer_id: int,
    product_id: int,
) -> set[int]:
    sale_ids: set[int] = set()

    # Direct POS outcomes: one paid sale and one invoice handoff.
    sale_ids.add(
        _create_quick_sale(
            client,
            customer_id=customer_id,
            product_id=product_id,
            settlement="sale",
            unique_key=f"{day_label}-quick-sale",
        )
    )
    sale_ids.add(
        _create_quick_sale(
            client,
            customer_id=customer_id,
            product_id=product_id,
            settlement="invoice",
            unique_key=f"{day_label}-quick-invoice",
        )
    )

    # Each document type must finish directly as both a paid sale and invoice handoff.
    for document_type in DOCUMENT_TYPES:
        for settlement in ("sale", "invoice"):
            _, document_url = _create_document(
                client,
                document_type=document_type,
                title=f"{day_label} {document_type} direct {settlement}",
                customer_id=customer_id,
                product_id=product_id,
            )
            sale_ids.add(
                _settle_document(
                    client,
                    document_url=document_url,
                    settlement=settlement,
                )
            )

    # Every ordered pair verifies both directions independently before a paid sale.
    for source_type, target_type in ORDERED_DOCUMENT_CONVERSIONS:
        source_id, source_url = _create_document(
            client,
            document_type=source_type,
            title=f"{day_label} {source_type} to {target_type} to sale",
            customer_id=customer_id,
            product_id=product_id,
        )
        target_id, target_url = _convert_document(
            client,
            source_url=source_url,
            target_type=target_type,
        )
        with SessionLocal() as db:
            target = db.get(Job, target_id)
            assert target is not None
            assert target.document_type == target_type
            assert target.source_job_id == source_id
        sale_ids.add(
            _settle_document(
                client,
                document_url=target_url,
                settlement="sale",
            )
        )

    assert len(sale_ids) == 14
    with SessionLocal() as db:
        day_sales = db.query(Sale).filter(Sale.id.in_(sale_ids)).all()
        assert len(day_sales) == 14
        assert all(sale.business_date == business_date for sale in day_sales)
        assert sum(1 for sale in day_sales if sale.payment_method == "invoice") == 4
        assert sum(1 for sale in day_sales if sale.payment_method != "invoice") == 10
        assert all(sale.total == Decimal("10.00") for sale in day_sales)

    return sale_ids


def test_all_document_conversion_and_settlement_paths_across_two_closed_days(monkeypatch):
    day_one = date(2026, 7, 20)
    day_two = date(2026, 7, 21)

    with TestClient(app) as client:
        customer_id = _create_customer(client)
        product_id = _create_service_product(client)

        _set_business_date(monkeypatch, day_one)
        day_one_sale_ids = _run_complete_day(
            client,
            business_date=day_one,
            day_label="day-one",
            customer_id=customer_id,
            product_id=product_id,
        )

        with SessionLocal() as db:
            user = db.query(User).filter(User.is_active.is_(True)).order_by(User.id.asc()).first()
            assert user is not None
            day_one_closing = create_daily_closing(
                db,
                business_date=day_one,
                created_by_user_id=user.id,
            )
            snapshot_row, snapshot = get_latest_daily_closing_snapshot(db, day_one_closing)
            assert snapshot_row.version == 1
            assert snapshot["sale_count"] == 14
            assert snapshot["gross_sales"] == "140.00"
            assert snapshot["awaiting_invoice_sales"] == "40.00"

        # A closed business date must reject new financial changes.
        blocked_response = client.post(
            "/sales/quick",
            data={
                "customer_id": str(customer_id),
                "idempotency_key": "closed-day-blocked-sale",
                "product_id": [str(product_id)],
                "description": ["Must not be accepted"],
                "quantity": ["1"],
                "unit_price": ["10"],
                "vat_percent": ["24"],
                "discount_amount": ["0"],
                "payment_method": ["card"],
                "payment_amount": ["10"],
            },
            follow_redirects=False,
        )
        assert blocked_response.status_code == 400
        assert "Business date is closed" in blocked_response.text

        _set_business_date(monkeypatch, day_two)
        day_two_sale_ids = _run_complete_day(
            client,
            business_date=day_two,
            day_label="day-two",
            customer_id=customer_id,
            product_id=product_id,
        )

        with SessionLocal() as db:
            user = db.query(User).filter(User.is_active.is_(True)).order_by(User.id.asc()).first()
            assert user is not None
            day_two_closing = create_daily_closing(
                db,
                business_date=day_two,
                created_by_user_id=user.id,
            )
            _, snapshot = get_latest_daily_closing_snapshot(db, day_two_closing)
            assert snapshot["sale_count"] == 14
            assert snapshot["gross_sales"] == "140.00"
            assert snapshot["awaiting_invoice_sales"] == "40.00"

    assert day_one_sale_ids.isdisjoint(day_two_sale_ids)
    with SessionLocal() as db:
        closings = (
            db.query(DailyClosing)
            .filter(DailyClosing.business_date.in_([day_one, day_two]))
            .order_by(DailyClosing.business_date.asc())
            .all()
        )
        assert [closing.status for closing in closings] == ["closed", "closed"]
        assert [closing.total_sales for closing in closings] == [
            Decimal("140.00"),
            Decimal("140.00"),
        ]
        assert db.query(Sale).filter(Sale.id.in_(day_one_sale_ids | day_two_sale_ids)).count() == 28
