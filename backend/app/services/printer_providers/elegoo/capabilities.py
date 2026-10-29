"""Capability table of ELEGOO printers in ALVEOLAB Farm.

Only what this build implements is listed as supported. Notes say why the rest
is missing, so the UI can explain a disabled control.
"""

from __future__ import annotations

from backend.app.services.printer_providers.base import Capability, CapabilitySet, PrinterProvider

# M2: read-only connection + telemetry over the CC2 MQTT protocol.
_CC2_SUPPORTED = frozenset(
    {
        Capability.LAN_DISCOVERY,
        Capability.MANUAL_REGISTRATION,
        Capability.AUTO_RECONNECT,
        Capability.STATUS,
        Capability.TEMPERATURES,
        Capability.PRINT_PROGRESS,
        Capability.LAYER_PROGRESS,
        Capability.FAN_SPEEDS,
        Capability.ERROR_REPORTING,
        Capability.DIAGNOSTIC_LOG,
        Capability.MULTICOLOR_STATUS,
    }
)

_CC2_NOTES = {
    Capability.FAN_SPEEDS: "read-only; 0-255 PWM scale assumed until verified on hardware (HW-M2-05)",
    Capability.MULTICOLOR_STATUS: "CANVAS trays as reported by the printer; detected vs declared not exposed by the protocol",
    Capability.CHAMBER_TEMPERATURE: (
        "not reported by our CC2 (firmware 02.01.01.04 sends no ztemperature_sensor); "
        "read into telemetry if a firmware starts sending it"
    ),
    Capability.PRINT_SPEED: "speed mode is read into the status, percentage is not reported",
    Capability.JOB_START: "planned for M4 (method 1020), needs hardware sign-off",
    Capability.JOB_PAUSE_RESUME: "planned for M4 (1021 pause; resume 1023 is disabled in the official SDK)",
    Capability.JOB_STOP: "planned for M4 (method 1022)",
    Capability.FILE_UPLOAD: "planned for M4 (HTTP PUT /upload)",
    Capability.FILE_LIST: "not implemented by the official SDK for CC2",
    Capability.MULTICOLOR_SLOT_EDIT: "no slot-edit method in the official SDK; manual mapping in the farm (M5)",
    Capability.CAMERA: "no LAN stream in the official SDK; community reports MJPEG on :8080 (unverified)",
    Capability.QUEUE_DISPATCH: "the queue dispatches Bambu 3MF over FTPS; ELEGOO dispatch is M4",
    Capability.AUTO_ARCHIVE: "archive pipeline reads 3MF over FTPS; ELEGOO job archive is M4",
    Capability.AMS_MANAGEMENT: "Bambu AMS only",
}


def is_cc2_model(model: str | None) -> bool:
    """SDK rule: a model containing "Centauri Carbon 2" or "Centauri 2" is a CC2 (include/types/printer.h:79-89)."""
    if not model:
        return False
    return "centauri carbon 2" in model.lower() or "centauri 2" in model.lower()


def elegoo_capabilities(model: str | None) -> CapabilitySet:
    """Capabilities of an ELEGOO printer of *model*.

    Only the CC2 protocol is implemented. Any other ELEGOO model gets an empty
    set: it would need a different adapter (gen-1 Centauri Carbon speaks SDCP
    over WebSocket, Neptune 4 speaks Moonraker) and must not be driven as a CC2.
    """
    if model and not is_cc2_model(model):
        return CapabilitySet(
            provider=PrinterProvider.ELEGOO,
            model=model,
            supported=frozenset(),
            notes={Capability.STATUS: "only the Centauri Carbon 2 protocol is implemented"},
        )
    return CapabilitySet(
        provider=PrinterProvider.ELEGOO,
        model=model,
        supported=_CC2_SUPPORTED,
        notes=dict(_CC2_NOTES),
    )
