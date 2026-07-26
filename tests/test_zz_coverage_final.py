"""Final coverage checks that must run after route-module reload tests."""

import importlib
from types import SimpleNamespace


def test_configured_setup_redirect_uses_final_auth_module(monkeypatch):
    """Exercise the configured-auth branch on the final loaded module object."""
    auth_route = importlib.import_module("app.routes.auth")
    monkeypatch.setattr(auth_route, "auth_is_configured", lambda _db: True)

    request = SimpleNamespace(
        cookies={},
        state=SimpleNamespace(current_user=None),
        url=SimpleNamespace(path="/setup"),
    )
    response = auth_route.setup_form(request, db=object())

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
