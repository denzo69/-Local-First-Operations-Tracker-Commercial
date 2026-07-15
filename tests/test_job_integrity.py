from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import Job, Sale


def _create_document(client: TestClient, route: str, title: str) -> tuple[int, str]:
    response = client.post(route, data={"title": title}, follow_redirects=False)
    assert response.status_code == 303
    location = response.headers["location"]
    return int(location.rsplit("/", 1)[-1]), location


def test_source_document_with_derived_document_cannot_be_deleted():
    with TestClient(app) as client:
        quote_id, quote_url = _create_document(client, "/quotes", "Protected source quote")
        conversion = client.post(f"{quote_url}/convert/delivery_note", follow_redirects=False)
        delete_response = client.post(f"{quote_url}/delete", follow_redirects=False)

    assert conversion.status_code == 303
    assert delete_response.status_code == 409
    assert "another document was created from it" in delete_response.text

    with SessionLocal() as db:
        assert db.get(Job, quote_id) is not None
        assert db.query(Job).filter(Job.source_job_id == quote_id).count() == 1


def test_document_linked_to_sale_cannot_be_deleted():
    with TestClient(app) as client:
        quote_id, quote_url = _create_document(client, "/quotes", "Protected sold quote")
        item_response = client.post(
            f"{quote_url}/items",
            data={
                "product_id": "",
                "description": "Billable service",
                "quantity": "1",
                "unit_price": "50",
                "vat_percent": "24",
            },
            follow_redirects=False,
        )
        sale_response = client.post(f"{quote_url}/convert/sale", follow_redirects=False)
        delete_response = client.post(f"{quote_url}/delete", follow_redirects=False)

    assert item_response.status_code == 303
    assert sale_response.status_code == 303
    assert delete_response.status_code == 409
    assert "finalized sale or invoice handoff" in delete_response.text

    with SessionLocal() as db:
        assert db.get(Job, quote_id) is not None
        assert db.query(Sale).filter(Sale.work_order_id == quote_id).count() == 1


def test_printed_document_cannot_be_deleted_but_unreferenced_document_can():
    with TestClient(app) as client:
        printed_id, printed_url = _create_document(client, "/work-orders", "Printed protected work order")
        receipt_response = client.get(f"{printed_url}/receipt")
        protected_delete = client.post(f"{printed_url}/delete", follow_redirects=False)

        disposable_id, disposable_url = _create_document(client, "/quotes", "Disposable draft quote")
        allowed_delete = client.post(f"{disposable_url}/delete", follow_redirects=False)

    assert receipt_response.status_code == 200
    assert protected_delete.status_code == 409
    assert "printable document snapshot" in protected_delete.text
    assert allowed_delete.status_code == 303

    with SessionLocal() as db:
        assert db.get(Job, printed_id) is not None
        assert db.get(Job, disposable_id) is None


def test_quote_and_delivery_note_print_routes_exist():
    with TestClient(app) as client:
        _, quote_url = _create_document(client, "/quotes", "Printable quote")
        _, delivery_url = _create_document(client, "/delivery-notes", "Printable delivery note")

        quote_receipt = client.get(f"{quote_url}/receipt")
        delivery_receipt = client.get(f"{delivery_url}/receipt")

    assert quote_receipt.status_code == 200
    assert delivery_receipt.status_code == 200
    assert "Printable quote" in quote_receipt.text
    assert "Printable delivery note" in delivery_receipt.text


def test_document_cannot_be_read_or_modified_through_wrong_route_type():
    with TestClient(app) as client:
        quote_id, _ = _create_document(client, "/quotes", "Route protected quote")

        wrong_work_order_read = client.get(f"/work-orders/{quote_id}")
        wrong_legacy_read = client.get(f"/jobs/{quote_id}")
        wrong_update = client.post(
            f"/work-orders/{quote_id}",
            data={"title": "Wrongly changed title"},
            follow_redirects=False,
        )

    assert wrong_work_order_read.status_code == 404
    assert wrong_legacy_read.status_code == 404
    assert wrong_update.status_code == 404

    with SessionLocal() as db:
        quote = db.get(Job, quote_id)
        assert quote is not None
        assert quote.document_type == "quote"
        assert quote.title == "Route protected quote"
