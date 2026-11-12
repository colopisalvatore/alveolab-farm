"""ALVEOLAB Farm modules, plugged into Bambuddy at the points listed in ALVEOLAB_HOOKS.md.

Everything we add lives under this package. Nothing here is imported at package
import time (``printer_manager`` imports ``alveolab.printer_providers.base``), so
``setup`` does its imports when called.
"""

from __future__ import annotations


def setup(app, api_prefix: str) -> None:
    """Single entry point, called once from ``main.py`` after the app is created.

    Mounts our routers and registers our callbacks on the printer manager.
    Setting ``ALVEOLAB_MODULES=0`` skips all of it: Bambuddy then behaves as upstream.
    """
    from backend.app.alveolab.config import modules_enabled

    if not modules_enabled():
        return

    from backend.app.alveolab.plates.routes import router as plates_router
    from backend.app.alveolab.production.routes import router as production_router
    from backend.app.alveolab.provider_jobs import on_provider_job_event
    from backend.app.alveolab.routes import router as provider_router
    from backend.app.services.printer_manager import printer_manager

    app.include_router(provider_router, prefix=api_prefix)
    app.include_router(production_router, prefix=api_prefix)
    app.include_router(plates_router, prefix=api_prefix)
    printer_manager.set_provider_job_event_callback(on_provider_job_event)
