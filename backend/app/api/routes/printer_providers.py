"""ALVEOLAB Farm provider-layer API: capabilities, normalised telemetry, ELEGOO discovery.

Kept in its own router (instead of ``printers.py``) so the upstream Bambuddy
routes stay untouched and merges from upstream remain cheap.
"""

from __future__ import annotations

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
