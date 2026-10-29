"""CC2 status JSON -> provider-neutral telemetry -> Bambuddy ``PrinterState``.

Field names and their meaning follow the SDK parser
(``src/lan/adapters/elegoo_fdm_cc2/elegoo_fdm_cc2_message_adapter.cpp``,
lines 639-1262). Where the SDK and real-firmware observations disagree, the
choice made here is stated next to the field and listed in
docs/alveolab-farm/HARDWARE_TEST_CHECKLIST.md.

Rules:
  * A field the printer did not send is ``None`` — never a made-up 0.
  * The job state is derived only from ``machine_status``; a missing status is
    ``JobState.UNKNOWN``. Losing the link is handled by the client and never
    rewrites the job state.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from backend.app.services.printer_providers.base import (
    ConnectionStatus,
    JobState,
    MultiColorSlot,
    NormalizedTelemetry,
    PrinterFault,
    PrinterProvider,
    SlotDataSource,
    TemperatureReading,
)
from backend.app.services.printer_providers.elegoo import protocol as p

if TYPE_CHECKING:
    from backend.app.services.bambu_mqtt import PrinterState

# Non-print machine activities: the machine is busy, but not with a job.
_BUSY_STATUSES = frozenset(
    {
        p.STATUS_INITIALIZING,
        *p.STATUS_FILAMENT_OPERATING,
        p.STATUS_AUTO_LEVELING,
        p.STATUS_PID_CALIBRATING,
        p.STATUS_RESONANCE_TESTING,
        p.STATUS_SELF_CHECKING,
        p.STATUS_UPDATING,
        p.STATUS_HOMING,
        p.STATUS_FILE_TRANSFERRING,
        p.STATUS_VIDEO_COMPOSING,
        p.STATUS_EXTRUDER_OPERATING,
    }
)

# JobState -> Bambu gcode_state vocabulary, which the dashboard, WebSocket
# payload and smart-plug guard understand. BUSY maps to PREPARE on purpose: it
# is an "active" state there, so nothing cuts power to a printer that is
# homing, calibrating or updating firmware.
_BAMBU_STATE_FOR_JOB = {
    JobState.IDLE: "IDLE",
    JobState.PREPARING: "PREPARE",
    JobState.PRINTING: "RUNNING",
    JobState.PAUSING: "PAUSE",
    JobState.PAUSED: "PAUSE",
    JobState.RESUMING: "RUNNING",
    JobState.STOPPING: "RUNNING",
    JobState.FINISHED: "FINISH",
    JobState.FAILED: "FAILED",
    JobState.CANCELLED: "FAILED",  # Bambu firmware reports a user cancel as FAILED too
    JobState.BUSY: "PREPARE",
    JobState.UNKNOWN: "unknown",
}


def _int(value) -> int | None:
    if isinstance(value, bool):
        return int(value)
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _obj(container: dict, key: str) -> dict:
    value = container.get(key)
    return value if isinstance(value, dict) else {}


def job_state_from_machine_status(status: int | None, sub_status: int | None) -> JobState:
    """Map ``machine_status.status`` / ``sub_status`` to a :class:`JobState`."""
    if status is None:
        return JobState.UNKNOWN
    if status == p.STATUS_IDLE:
        return JobState.IDLE
    if status == p.STATUS_PRINTING:
        if sub_status in p.PRINT_SUB_DOWNLOADING:
            return JobState.PREPARING
        if (
            sub_status in p.PRINT_SUB_EXTRUDER_PREHEATING
            or sub_status in p.PRINT_SUB_BED_PREHEATING
            or sub_status in p.PRINT_SUB_HOMING
            or sub_status in p.PRINT_SUB_AUTO_LEVELING
        ):
            return JobState.PREPARING
        if sub_status == p.PRINT_SUB_PAUSING:
            return JobState.PAUSING
        if sub_status in p.PRINT_SUB_PAUSED:
            return JobState.PAUSED
        if sub_status == p.PRINT_SUB_RESUMING:
            return JobState.RESUMING
        if sub_status == p.PRINT_SUB_STOPPING:
            return JobState.STOPPING
        if sub_status == p.PRINT_SUB_STOPPED:
            return JobState.CANCELLED
        if sub_status == p.PRINT_SUB_COMPLETED:
            return JobState.FINISHED
        # 2075 printing, 2402 resume completed, 0/1041 none, and sub-states the
        # SDK does not know: the machine itself says "printing".
        return JobState.PRINTING
    if status == p.STATUS_EMERGENCY_STOP:
        return JobState.FAILED
    if status == p.STATUS_POWER_LOSS_RECOVERY:
        # A job interrupted by power loss waits for the operator to resume it.
        return JobState.PAUSED
    if status in _BUSY_STATUSES:
        return JobState.BUSY
    return JobState.UNKNOWN


def _fan_percent(value) -> int | None:
    """Fan ``speed`` as 0-100 %.

    The SDK struct calls it a percentage (include/types/printer.h:342) but
    firmware captures in the community docs show PWM values up to 255
    (CC2_PROTOCOL.md:1034-1047). Read as 0-255 PWM; checklist item HW-M2-05.
    """
    raw = _float(value)
    if raw is None:
        return None
    return max(0, min(100, round(raw * 100 / 255)))


def _color_hex(value) -> str | None:
    text = str(value or "").strip().lstrip("#").upper()
    # community: firmware uses "—"-style / "?" placeholders for unset trays (sdcp/models/ams.py:28-41)
    if len(text) < 6 or not all(c in "0123456789ABCDEF" for c in text[:6]):
        return None
    return text[:6]


def _text(value) -> str | None:
    text = str(value or "").strip()
    if not text or text == "?" or set(text) <= {"-", "—"}:
        return None
    return text


def parse_canvas_slots(canvas_info: dict | None) -> list[MultiColorSlot]:
    """CANVAS trays as :class:`MultiColorSlot` (adapter handleCanvasStatus, 1216-1262).

    * ``tray_id`` is kept exactly as sent: SDK docs say 1-based, firmware
      captures show 0-based (checklist HW-M5-01). Echo it, never re-number it.
    * ``status`` 0 = empty in every source; non-zero = a spool is there. What
      1 vs 2 mean is disputed (SDK: pre-loaded/loaded; community: present/active),
      so "active" comes from ``active_canvas_id``/``active_tray_id`` instead.
    * The protocol has no detected-vs-declared flag (no RFID field in either
      source), so ``source`` is UNKNOWN until verified on hardware (HW-M5-02).
    """
    if not isinstance(canvas_info, dict):
        return []
    active_canvas = _int(canvas_info.get("active_canvas_id"))
    active_tray = _int(canvas_info.get("active_tray_id"))
    slots: list[MultiColorSlot] = []
    for canvas in canvas_info.get("canvas_list") or []:
        if not isinstance(canvas, dict):
            continue
        canvas_id = _int(canvas.get("canvas_id"))
        if canvas_id is None:
            continue
        for tray in canvas.get("tray_list") or []:
            if not isinstance(tray, dict):
                continue
            tray_id = _int(tray.get("tray_id"))
            if tray_id is None:
                continue
            status = _int(tray.get("status"))
            loaded = None if status is None else status != 0
            slots.append(
                MultiColorSlot(
                    unit_id=canvas_id,
                    slot_index=tray_id,
                    loaded=loaded,
                    material=_text(tray.get("filament_type")) if loaded is not False else None,
                    color_hex=_color_hex(tray.get("filament_color")) if loaded is not False else None,
                    name=_text(tray.get("filament_name")) if loaded is not False else None,
                    vendor=_text(tray.get("brand")) if loaded is not False else None,
                    source=SlotDataSource.UNKNOWN,
                    active=canvas_id == active_canvas and tray_id == active_tray,
                )
            )
    return slots


def canvas_connected(status: dict) -> bool | None:
    """Whether a CANVAS unit is attached (adapter lines 1126-1142); None if unreported."""
    device = _obj(status, "external_device")
    if "type" not in device:
        return None
    value = device["type"]
    if isinstance(value, str):
        return value.strip() not in ("", "0")
    number = _int(value)
    return None if number is None else number != 0


def _faults(status: dict) -> list[PrinterFault]:
    codes: list[int] = []
    for code in _obj(status, "machine_status").get("exception_status") or []:
        number = _int(code)
        if number is not None and number not in codes:
            codes.append(number)
    for code in _obj(_obj(status, "exception"), "exception_code"):
        number = _int(code)
        if number is not None and number not in codes:
            codes.append(number)
    faults = [PrinterFault(code=str(c), severity="error", message=p.describe_error_code(c)) for c in codes]
    if _int(_obj(status, "machine_status").get("status")) == p.STATUS_EMERGENCY_STOP:
        faults.append(PrinterFault(code="emergency_stop", severity="error", message="emergency stop"))
    return faults


def normalize_cc2_status(
    status: dict | None,
    *,
    attributes: dict | None = None,
    canvas_info: dict | None = None,
    connection: ConnectionStatus = ConnectionStatus.ONLINE,
    model: str | None = None,
) -> NormalizedTelemetry:
    """Build :class:`NormalizedTelemetry` from a merged CC2 status (1002 + 6000 deltas)."""
    status = status if isinstance(status, dict) else {}
    attributes = attributes if isinstance(attributes, dict) else {}
    machine = _obj(status, "machine_status")
    machine_status = _int(machine.get("status"))
    sub_status = _int(machine.get("sub_status"))
    job_state = job_state_from_machine_status(machine_status, sub_status)

    telemetry = NormalizedTelemetry(
        provider=PrinterProvider.ELEGOO,
        connection=connection,
        job_state=job_state,
        raw_state=(
            None
            if machine_status is None
            else f"{p.MACHINE_STATUS_NAMES.get(machine_status, 'unknown')}:{machine_status}/{sub_status}"
        ),
        model=model or attributes.get("machine_model") or None,
        firmware_version=_obj(attributes, "software_version").get("ota_version") or None,
    )

    # print_status is only meaningful while printing; the SDK discards it
    # otherwise (adapter 970-1001), and so do we.
    if machine_status == p.STATUS_PRINTING:
        print_status = _obj(status, "print_status")
        telemetry.filename = _text(print_status.get("filename"))
        telemetry.current_layer = _int(print_status.get("current_layer"))
        telemetry.total_layers = _int(print_status.get("total_layer"))
        if sub_status in p.PRINT_SUB_DOWNLOADING:
            # Downloading a cloud-sliced file: progress/times would describe the
            # download, not the print (adapter 983-991).
            telemetry.progress_percent = None
        else:
            progress = _float(machine.get("progress"))
            telemetry.progress_percent = None if progress is None else max(0.0, min(100.0, progress))
            telemetry.elapsed_seconds = _int(print_status.get("print_duration"))
            telemetry.remaining_seconds = _int(print_status.get("remaining_time_sec"))

    extruder = _obj(status, "extruder")
    telemetry.nozzle = TemperatureReading(_float(extruder.get("temperature")), _float(extruder.get("target")))
    bed = _obj(status, "heater_bed")
    telemetry.bed = TemperatureReading(_float(bed.get("temperature")), _float(bed.get("target")))
    chamber = _obj(status, "ztemperature_sensor")
    telemetry.chamber = TemperatureReading(_float(chamber.get("temperature")), None)

    fans = _obj(status, "fans")
    for wire_name, name in (
        ("fan", "part_cooling"),
        ("aux_fan", "aux"),
        ("box_fan", "chamber"),
        ("heater_fan", "heatbreak"),
        ("controller_fan", "controller"),
    ):
        speed = _fan_percent(_obj(fans, wire_name).get("speed"))
        if speed is not None:
            telemetry.fans_percent[name] = speed

    telemetry.faults = _faults(status)
    telemetry.multicolor_slots = parse_canvas_slots(
        canvas_info if canvas_info is not None else status.get("canvas_info")
    )
    return telemetry


def speed_mode(status: dict | None) -> int | None:
    """``gcode_move_inf.speed_mode``: 0 silent, 1 balanced, 2 sport, 3 ludicrous (printer.h:585-595)."""
    if not isinstance(status, dict):
        return None
    return _int(_obj(status, "gcode_move_inf").get("speed_mode"))


def apply_to_printer_state(
    state: PrinterState,
    telemetry: NormalizedTelemetry,
    *,
    status: dict | None = None,
    attributes: dict | None = None,
    canvas_info: dict | None = None,
) -> None:
    """Write *telemetry* into a Bambuddy :class:`PrinterState` in place.

    Only fields the CC2 reports are written; Bambu-only fields (AMS, HMS,
    stg_cur, nozzle rack, ...) stay at their defaults. The raw ELEGOO payloads
    are kept under ``raw_data["elegoo"]`` — never at the top level, where
    Bambu readers look for ``ams`` / ``vt_tray`` / ``device``.
    """
    state.connected = telemetry.connection is ConnectionStatus.ONLINE
    state.state = _BAMBU_STATE_FOR_JOB[telemetry.job_state]
    state.subtask_name = telemetry.filename
    state.gcode_file = telemetry.filename
    state.current_print = telemetry.filename
    state.progress = telemetry.progress_percent or 0.0
    # PrinterState.remaining_time is in minutes (Bambu mc_remaining_time).
    state.remaining_time = math.ceil(telemetry.remaining_seconds / 60) if telemetry.remaining_seconds else 0
    state.layer_num = telemetry.current_layer or 0
    state.total_layers = telemetry.total_layers or 0

    temps: dict = {}
    for key, reading in (("nozzle", telemetry.nozzle), ("bed", telemetry.bed), ("chamber", telemetry.chamber)):
        if reading.current is not None:
            temps[key] = reading.current
        if reading.target is not None:
            temps[f"{key}_target"] = reading.target
    state.temperatures = temps

    state.cooling_fan_speed = telemetry.fans_percent.get("part_cooling")
    state.big_fan1_speed = telemetry.fans_percent.get("aux")
    state.big_fan2_speed = telemetry.fans_percent.get("chamber")
    state.heatbreak_fan_speed = telemetry.fans_percent.get("heatbreak")

    mode = speed_mode(status)
    if mode is not None and 0 <= mode <= 3:
        state.speed_level = mode + 1  # Bambu: 1 silent .. 4 ludicrous
    led = _obj(status or {}, "led")
    if "status" in led:
        state.chamber_light = _int(led.get("status")) == 1
    if telemetry.firmware_version:
        state.firmware_version = telemetry.firmware_version

    state.raw_data = {
        "elegoo": {
            "status": status or {},
            "attributes": attributes or {},
            "canvas_info": canvas_info if canvas_info is not None else (status or {}).get("canvas_info"),
        }
    }
