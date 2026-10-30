"""Capability table of ELEGOO printers in ALVEOLAB Farm.

Only what this build implements is listed as supported. Notes say why the rest
is missing, so the UI can explain a disabled control.
"""

from __future__ import annotations

import os

from backend.app.services.printer_providers.base import Capability, CapabilitySet, PrinterProvider

# M2: read-only connection + telemetry over the CC2 MQTT protocol. Job
# control (M4) is added by enabled_job_capabilities().
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
    Capability.JOB_START: "method 1020; switched on only after hardware test HW-M4-04",
    Capability.JOB_PAUSE_RESUME: "1021 pause, 1023 resume (disabled in the official SDK); only after HW-M4-05",
    Capability.JOB_STOP: "method 1022; switched on only after HW-M4-06",
    Capability.FILE_UPLOAD: "HTTP PUT /upload; switched on only after HW-M4-01",
    Capability.FILE_LIST: "not implemented by the official SDK for CC2",
    Capability.MULTICOLOR_SLOT_EDIT: "no slot-edit method in the official SDK; manual mapping in the farm (M5)",
    Capability.CAMERA: "no LAN stream in the official SDK; community reports MJPEG on :8080 (unverified)",
    Capability.QUEUE_DISPATCH: "files sliced for the CC2 only; needs file_upload and job_start",
    Capability.AUTO_ARCHIVE: "archive pipeline reads Bambu 3MF over FTPS; the CC2 exposes no file download",
    Capability.AMS_MANAGEMENT: "Bambu AMS only",
}


# M4 job control is switched on per capability, after its hardware test, by
# listing it in this environment variable (comma-separated capability values,
# e.g. "file_upload,job_start"). Unset = read-only, as in M2/M3. Kept out of
# the database on purpose: enabling a command that moves the machine is a
# deployment decision, not a click in the UI.
ENABLE_ENV = "ALVEOLAB_ELEGOO_ENABLE"
_SWITCHABLE = frozenset(
    {Capability.FILE_UPLOAD, Capability.JOB_START, Capability.JOB_PAUSE_RESUME, Capability.JOB_STOP}
)


def enabled_job_capabilities() -> frozenset[Capability]:
    """Job capabilities switched on in this deployment (see :data:`ENABLE_ENV`)."""
    raw = os.environ.get(ENABLE_ENV, "")
    enabled = set()
    for item in raw.split(","):
        item = item.strip().lower()
        if not item:
            continue
        try:
            capability = Capability(item)
        except ValueError:
            continue
        if capability in _SWITCHABLE:
            enabled.add(capability)
    # The queue can hand a job to the CC2 only when it can both upload and start.
    if {Capability.FILE_UPLOAD, Capability.JOB_START} <= enabled:
        enabled.add(Capability.QUEUE_DISPATCH)
    return frozenset(enabled)


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
        supported=_CC2_SUPPORTED | enabled_job_capabilities(),
        notes=dict(_CC2_NOTES),
    )
