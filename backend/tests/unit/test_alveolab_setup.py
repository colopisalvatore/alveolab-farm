"""``alveolab.setup`` must leave Bambuddy as upstream when the modules are off."""

from fastapi import FastAPI

from backend.app import alveolab


def _paths(app: FastAPI) -> set[str]:
    return {getattr(route, "path", "") for route in app.routes}


def test_setup_mounts_provider_routes(monkeypatch):
    monkeypatch.delenv("ALVEOLAB_MODULES", raising=False)
    app = FastAPI()
    alveolab.setup(app, "/api/v1")
    assert "/api/v1/printer-providers/printers/{printer_id}/capabilities" in _paths(app)


def test_setup_is_a_noop_when_disabled(monkeypatch):
    monkeypatch.setenv("ALVEOLAB_MODULES", "0")
    app = FastAPI()
    alveolab.setup(app, "/api/v1")
    assert not [p for p in _paths(app) if "printer-providers" in p]
