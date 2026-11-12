"""What Bambuddy asks the Centauri / provider module at its hook points.

Each function answers "not a provider printer" (None / False) when the printer is
a Bambu, so the caller carries on with the upstream path unchanged.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import HTTPException

from backend.app.alveolab.config import modules_enabled
from backend.app.alveolab.printer_providers import provider_job_client
from backend.app.alveolab.printer_providers.base import Capability, PrinterProvider
from backend.app.services.printer_manager import printer_manager


def is_provider_printer(printer_id: int) -> bool:
    """True for a connected non-Bambu printer."""
    provider = printer_manager.get_provider(printer_id)
    return isinstance(provider, PrinterProvider) and provider is not PrinterProvider.BAMBU


def cannot_take_queue_jobs(printer_id: int) -> bool:
    """True for a connected printer whose provider has no queue dispatch.

    Unknown capabilities (not connected) answer False and are left to the connection checks.
    """
    capabilities = printer_manager.get_capabilities(printer_id)
    return capabilities is not None and not capabilities.supports(Capability.QUEUE_DISPATCH)


async def provider_job_command(printer_id: int, action: str) -> dict | None:
    """Pause / resume / stop on a non-Bambu printer.

    None when the printer is not a connected provider printer (Bambu path follows).
    The provider client refuses commands whose capability is off or that make no
    sense in the current job state; the printer's own answer is passed back.
    """
    from backend.app.alveolab.printer_providers.elegoo.client import (
        COMMAND_ACCEPTED,
        COMMAND_NO_REPLY,
        COMMAND_NOT_SENT,
    )

    client = provider_job_client(printer_manager, printer_id)
    if client is None:
        return None
    command = getattr(client, f"{action}_print", None)
    if command is None:
        raise HTTPException(400, "This printer does not support this command")
    outcome = await asyncio.to_thread(command)
    if outcome.status == COMMAND_ACCEPTED:
        return {"success": True, "message": f"Print {action} accepted by the printer"}
    if outcome.status == COMMAND_NOT_SENT:
        raise HTTPException(400, "Printer not connected")
    if outcome.status == COMMAND_NO_REPLY:
        raise HTTPException(
            504,
            {"message": "Command sent, the printer did not answer: check the printer", "code": "no_reply"},
        )
    raise HTTPException(409, {"message": outcome.message or "Refused", "code": outcome.status})


async def stop_print(printer_id: int) -> dict | None:
    """Stop on a provider printer, marked as stopped by the user. None for a Bambu."""
    result = await provider_job_command(printer_id, "stop")
    if result is not None:
        from backend.app.main import mark_printer_stopped_by_user

        mark_printer_stopped_by_user(printer_id)
    return result


async def probe_new_printer(printer_data) -> dict | None:
    """Connection test for a printer being added; None when it is a Bambu."""
    if printer_data.provider != "elegoo" or not modules_enabled():
        return None
    from backend.app.alveolab.printer_providers.elegoo.client import probe_connection

    return await probe_connection(
        printer_data.ip_address,
        printer_data.serial_number,
        printer_data.access_code or None,
    )


async def validate_queue_item(printer, queued_name: str | None, source_path: Path | None) -> None:
    """Refuse at queue time a file the printer cannot print (HTTPException 400).

    Does nothing for a Bambu printer. ``source_path`` is the file on disk when known.
    """
    if printer is None or printer.provider != "elegoo":
        return
    from backend.app.alveolab.printer_providers.elegoo.files import (
        THREEMF_REFUSED_MESSAGE,
        bed_type_mismatch,
        check_printable_on_cc2,
        refuses_3mf,
    )

    # A 3MF jams the Centauri 2 over LAN (HW-M4-03).
    if refuses_3mf(queued_name):
        raise HTTPException(400, f"{queued_name}: {THREEMF_REFUSED_MESSAGE}")
    # Sliced for another build plate than the one mounted: refused now, with the
    # reason, rather than failed at start (the dispatcher checks again).
    if printer.bed_type and source_path is not None and source_path.exists():
        check = await asyncio.to_thread(check_printable_on_cc2, source_path)
        wrong_plate = bed_type_mismatch(check.bed_type, printer.bed_type)
        if wrong_plate:
            raise HTTPException(400, f"{queued_name}: {wrong_plate}")
