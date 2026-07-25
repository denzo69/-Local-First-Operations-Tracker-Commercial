from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import Job, JobItem, JobStatus


def test_document_wrapper_routes_cover_update_delete_and_convert_error_edges():
    with TestClient(app) as client:
        missing_work_order_status = client.post("/work-orders/999/status", data={"status_id": 1})
        missing_work_order_delete = client.post("/work-orders/999/delete")
        missing_work_order_item_delete = client.post("/work-orders/999/items/123/delete")
        missing_work_order_convert = client.post("/work-orders/999/convert/sale")
        invalid_quote_target = client.post("/quotes/999/convert/unknown")

    assert missing_work_order_status.status_code == 404
    assert missing_work_order_delete.status_code == 404
    assert missing_work_order_item_delete.status_code == 404
    assert missing_work_order_convert.status_code == 404
    assert invalid_quote_target.status_code == 404


def test_document_routes_cover_invalid_status_and_item_reverse_errors(monkeypatch):
    with SessionLocal() as db:
        inactive_status = JobStatus(name="Inactive", is_active=False)
        job = Job(title="Branch job", document_type="work_order")
        item = JobItem(job=job, description="Row", quantity=1, unit_price=1, vat_percent=24, line_total=1)
        db.add_all([inactive_status, job, item])
        db.commit()
        job_id = job.id
        item_id = item.id
        inactive_status_id = inactive_status.id

    with TestClient(app) as client:
        invalid_status = client.post(f"/work-orders/{job_id}/status", data={"status_id": inactive_status_id})

    assert invalid_status.status_code == 400
    assert "Selected status was not found" in invalid_status.text

    from app.routes import jobs

    def fail_reverse(*_args, **_kwargs):
        raise ValueError("cannot reverse")

    monkeypatch.setattr(jobs, "reverse_delivery_note_stock_issue", fail_reverse)
    with TestClient(app) as client:
        delete_item = client.post(f"/work-orders/{job_id}/items/{item_id}/delete")
        delete_job = client.post(f"/work-orders/{job_id}/delete")

    assert delete_item.status_code == 400
    assert "cannot reverse" in delete_item.text
    assert delete_job.status_code == 400
    assert "cannot reverse" in delete_job.text


def test_quote_and_delivery_wrapper_routes_call_shared_job_handlers():
    with TestClient(app) as client:
        quote_create = client.post(
            "/quotes",
            data={"title": "Quote branch", "customer_id": "", "description": "", "arrival_date": "bad-date"},
        )
        delivery_create = client.post(
            "/delivery-notes",
            data={"title": "Delivery branch", "customer_id": "", "description": "", "arrival_date": "bad-date"},
        )

    assert quote_create.status_code == 400
    assert delivery_create.status_code == 400

