import logging

from fastapi.testclient import TestClient

from app.main import app


def raise_unexpected_error():
    raise RuntimeError("sensitive stack detail")


if not any(getattr(route, "path", None) == "/__test-unexpected-error" for route in app.routes):
    app.add_api_route("/__test-unexpected-error", raise_unexpected_error, methods=["GET"])


def test_missing_page_returns_safe_404_with_request_id():
    with TestClient(app) as client:
        response = client.get("/missing-page")

    assert response.status_code == 404
    assert "X-Request-ID" in response.headers
    assert "Request ID:" in response.text
    assert "Traceback" not in response.text


def test_validation_error_returns_understandable_422():
    with TestClient(app) as client:
        response = client.post("/customers", data={})

    assert response.status_code == 422
    assert "X-Request-ID" in response.headers
    assert "Validation error" in response.text
    assert "name" in response.text
    assert "Traceback" not in response.text


def test_http_exception_status_code_remains_correct():
    with TestClient(app) as client:
        response = client.post("/customers", data={"name": ""})

    assert response.status_code == 400
    assert "Customer name is required" in response.text
    assert "X-Request-ID" in response.headers


def test_unexpected_exception_returns_safe_500_and_logs(caplog):
    caplog.set_level(logging.ERROR, logger="app.errors")
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/__test-unexpected-error")

    assert response.status_code == 500
    assert "X-Request-ID" in response.headers
    assert "Something went wrong" in response.text
    assert "sensitive stack detail" not in response.text
    assert "Traceback" not in response.text
    assert any("Unhandled application error" in record.message for record in caplog.records)
    assert any(
        record.exc_info and "sensitive stack detail" in str(record.exc_info[1])
        for record in caplog.records
    )


def test_json_accept_header_returns_structured_error():
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(
            "/__test-unexpected-error",
            headers={"Accept": "application/json", "X-Request-ID": "test-request-id"},
        )

    assert response.status_code == 500
    assert response.headers["X-Request-ID"] == "test-request-id"
    assert response.json() == {
        "error": {
            "status_code": 500,
            "message": "Something went wrong. Please try again or contact support with the request ID.",
            "request_id": "test-request-id",
        }
    }
