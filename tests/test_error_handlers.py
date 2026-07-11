from fastapi.testclient import TestClient

from app.main import app


def test_http_error_uses_branded_html_page():
    with TestClient(app) as client:
        response = client.get("/work-orders/999999")

    assert response.status_code == 404
    assert "error-page" in response.text
    assert "Work order not found" in response.text
    assert 'href="http://testserver/static/css/app.css"' in response.text


def test_http_error_can_return_json():
    with TestClient(app) as client:
        response = client.get(
            "/work-orders/999999",
            headers={"accept": "application/json"},
        )

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "status_code": 404,
            "title": "Not Found",
            "detail": "Work order not found",
        }
    }


def test_validation_error_can_return_json():
    with TestClient(app) as client:
        response = client.get(
            "/work-orders/not-a-number",
            headers={"accept": "application/json"},
        )

    assert response.status_code == 422
    assert response.json()["error"]["status_code"] == 422
    assert response.json()["error"]["title"].startswith("Unprocessable")
    assert response.json()["error"]["detail"] == "Request validation failed."


def test_unhandled_error_returns_safe_response():
    route_path = "/__test_unhandled_error"
    if not any(route.path == route_path for route in app.routes):
        @app.get(route_path)
        def _raise_unhandled_error():
            raise RuntimeError("sensitive internal detail")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(route_path)

    assert response.status_code == 500
    assert "Unexpected server error." in response.text
    assert "sensitive internal detail" not in response.text
