"""ALVEOLAB Farm provider-layer API: capabilities, normalised telemetry, ELEGOO discovery.

Kept in its own router (instead of ``printers.py``) so the upstream Bambuddy
routes stay untouched and merges from upstream remain cheap.
"""

from __future__ import annotations

import asyncio
import ipaddress
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import RequirePermissionIfAuthEnabled
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.printer import Printer
from backend.app.models.user import User
from backend.app.services.printer_manager import printer_manager
from backend.app.services.printer_providers import capabilities_for, parse_provider
from backend.app.services.printer_providers.bambu import normalize_bambu_state
from backend.app.services.printer_providers.base import PrinterProvider

router = APIRouter(prefix="/printer-providers", tags=["printer-providers"])


async def _get_printer(db: AsyncSession, printer_id: int) -> Printer:
    printer = (await db.execute(select(Printer).where(Printer.id == printer_id))).scalar_one_or_none()
    if printer is None:
        raise HTTPException(404, "Printer not found")
    return printer


@router.get("/printers/{printer_id}/capabilities")
async def get_printer_capabilities(
    printer_id: int,
    _: User | None = RequirePermissionIfAuthEnabled(Permission.PRINTERS_READ),
    db: AsyncSession = Depends(get_db),
):
    """What ALVEOLAB Farm can do with this printer, and why the rest is missing."""
    printer = await _get_printer(db, printer_id)
    live = printer_manager.get_capabilities(printer_id)
    capabilities = live or capabilities_for(parse_provider(printer.provider), printer.model)
    return capabilities.to_dict()


@router.get("/printers/{printer_id}/telemetry")
async def get_printer_telemetry(
    printer_id: int,
    _: User | None = RequirePermissionIfAuthEnabled(Permission.PRINTERS_READ),
    db: AsyncSession = Depends(get_db),
):
    """Provider-neutral telemetry snapshot (``NormalizedTelemetry``).

    ``connection`` is ``unknown`` for a printer with no live client — the farm
    does not guess the state of a machine it is not talking to.
    """
    printer = await _get_printer(db, printer_id)
    provider = parse_provider(printer.provider)
    client = printer_manager.get_provider_client(printer_id)
    if client is not None:
        telemetry = client.telemetry()
    else:
        state = printer_manager.get_status(printer_id)
        if state is None or provider is not PrinterProvider.BAMBU:
            return {"provider": provider.value, "connection": "unknown", "job_state": "unknown"}
        telemetry = normalize_bambu_state(state, printer.model)
    return asdict(telemetry)


# --- CANVAS (ALVEOLAB Farm, M5) -------------------------------------------------


@router.get("/elegoo/canvas-filaments")
async def list_canvas_filaments(
    _: User | None = RequirePermissionIfAuthEnabled(Permission.PRINTERS_READ),
):
    """Filaments and brands a CANVAS slot can be set to (ELEGOO's own table)."""
    from backend.app.services.printer_providers.elegoo.canvas import CANVAS_BRANDS, CANVAS_FILAMENTS

    return {"brands": list(CANVAS_BRANDS), "filaments": [asdict(f) for f in CANVAS_FILAMENTS]}


async def _canvas_command(db: AsyncSession, printer_id: int, command: str, *args, **kwargs) -> dict:
    """Run one CANVAS command on a connected ELEGOO printer, mapping its outcome to HTTP.

    The client refuses commands whose capability is not switched on, or that
    the printer's state does not allow; the printer's own answer is passed
    back as it is. No reply is a 504, never a success.
    """
    from backend.app.services.printer_providers import provider_job_client
    from backend.app.services.printer_providers.elegoo.client import (
        COMMAND_ACCEPTED,
        COMMAND_NO_REPLY,
        COMMAND_NOT_SENT,
    )

    await _get_printer(db, printer_id)
    client = provider_job_client(printer_manager, printer_id)
    if client is None:
        raise HTTPException(400, "Printer not connected")
    method = getattr(client, command, None)
    if method is None:
        raise HTTPException(400, "This printer has no CANVAS commands")
    outcome = await asyncio.to_thread(method, *args, **kwargs)
    if outcome.status == COMMAND_ACCEPTED:
        return {"success": True}
    if outcome.status == COMMAND_NOT_SENT:
        raise HTTPException(400, "Printer not connected")
    if outcome.status == COMMAND_NO_REPLY:
        raise HTTPException(
            504,
            {"message": "Command sent, the printer did not answer: check the printer", "code": "no_reply"},
        )
    raise HTTPException(409, {"message": outcome.message or "Refused", "code": outcome.status})


class CanvasSlotUpdate(BaseModel):
    filament_name: str = Field(..., min_length=1, max_length=64)
    color: str = Field(..., pattern=r"^#?[0-9A-Fa-f]{6}$")
    brand: str = Field("Generic", max_length=32)


class CanvasAutoRefill(BaseModel):
    enabled: bool


@router.put("/printers/{printer_id}/canvas/{canvas_id}/slots/{tray_id}")
async def set_canvas_slot(
    printer_id: int,
    canvas_id: int,
    tray_id: int,
    body: CanvasSlotUpdate,
    _: User | None = RequirePermissionIfAuthEnabled(Permission.PRINTERS_CONTROL),
    db: AsyncSession = Depends(get_db),
):
    """Declare the spool in a CANVAS slot (method 2003), like the printer's touchscreen."""
    return await _canvas_command(
        db,
        printer_id,
        "set_canvas_filament",
        canvas_id,
        tray_id,
        filament_name=body.filament_name,
        color=body.color,
        brand=body.brand,
    )


@router.post("/printers/{printer_id}/canvas/{canvas_id}/slots/{tray_id}/load")
async def load_canvas_slot(
    printer_id: int,
    canvas_id: int,
    tray_id: int,
    _: User | None = RequirePermissionIfAuthEnabled(Permission.PRINTERS_CONTROL),
    db: AsyncSession = Depends(get_db),
):
    """Feed a CANVAS slot's filament to the nozzle (method 2001); waits for the printer, up to 5 minutes."""
    return await _canvas_command(db, printer_id, "load_canvas_filament", canvas_id, tray_id)


@router.post("/printers/{printer_id}/canvas/{canvas_id}/slots/{tray_id}/unload")
async def unload_canvas_slot(
    printer_id: int,
    canvas_id: int,
    tray_id: int,
    _: User | None = RequirePermissionIfAuthEnabled(Permission.PRINTERS_CONTROL),
    db: AsyncSession = Depends(get_db),
):
    """Pull a CANVAS slot's filament back from the nozzle (method 2002); waits up to 5 minutes."""
    return await _canvas_command(db, printer_id, "unload_canvas_filament", canvas_id, tray_id)


@router.put("/printers/{printer_id}/canvas/auto-refill")
async def set_canvas_auto_refill(
    printer_id: int,
    body: CanvasAutoRefill,
    _: User | None = RequirePermissionIfAuthEnabled(Permission.PRINTERS_CONTROL),
    db: AsyncSession = Depends(get_db),
):
    """Switch CANVAS auto-refill on or off (method 2004)."""
    return await _canvas_command(db, printer_id, "set_canvas_auto_refill", body.enabled)


class ElegooDiscoveryRequest(BaseModel):
    timeout: float = Field(5.0, ge=0.5, le=30.0)
    # Optional unicast targets (e.g. Docker bridge mode, where broadcast stays
    # inside the container). Empty = broadcast on the LAN.
    targets: list[str] = Field(default_factory=list, max_length=64)

    @field_validator("targets")
    @classmethod
    def _ipv4_only(cls, targets: list[str]) -> list[str]:
        for target in targets:
            ipaddress.IPv4Address(target)  # raises ValueError -> 422
        return targets


@router.post("/elegoo/discover")
async def discover_elegoo_printers(
    body: ElegooDiscoveryRequest,
    _: User | None = RequirePermissionIfAuthEnabled(Permission.DISCOVERY_SCAN),
):
    """Find ELEGOO Centauri Carbon 2 printers on the LAN (UDP 52700, read-only probe)."""
    from backend.app.services.printer_providers.elegoo.discovery import discover_cc2

    found = await discover_cc2(timeout=body.timeout, targets=body.targets or None)
    return [result.to_dict() for result in found]
