"""Bambu Lab side of the provider layer.

Nothing here talks to a printer. ``BambuMQTTClient`` keeps doing all of that,
exactly as upstream Bambuddy does; this module only *describes* a Bambu printer
in the provider vocabulary:

  * :func:`bambu_capabilities` — the capability set, derived from the same
    model helpers the rest of the app already uses (chamber sensor, ...).
  * :func:`normalize_bambu_state` — a :class:`NormalizedTelemetry` view of a
    live :class:`PrinterState`, for code that wants to treat every brand alike.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from backend.app.services.printer_providers.base import (
    Capability,
    CapabilitySet,
    ConnectionStatus,
    JobState,
    MultiColorSlot,
    NormalizedTelemetry,
    PrinterFault,
    PrinterProvider,
    SlotDataSource,
    TemperatureReading,
)

if TYPE_CHECKING:
    from backend.app.services.bambu_mqtt import PrinterState

# Everything upstream Bambuddy does for every Bambu model. Model-dependent
# extras are added in bambu_capabilities().
_BAMBU_BASE_CAPABILITIES = frozenset(
    {
        Capability.LAN_DISCOVERY,
        Capability.MANUAL_REGISTRATION,
        Capability.AUTO_RECONNECT,
        Capability.STATUS,
        Capability.TEMPERATURES,
        Capability.PRINT_PROGRESS,
        Capability.LAYER_PROGRESS,
        Capability.FAN_SPEEDS,
        Capability.PRINT_SPEED,
        Capability.ERROR_REPORTING,
        Capability.DIAGNOSTIC_LOG,
        Capability.JOB_START,
        Capability.JOB_PAUSE_RESUME,
        Capability.JOB_STOP,
        Capability.FILE_UPLOAD,
        Capability.FILE_LIST,
        Capability.MULTICOLOR_STATUS,
        Capability.MULTICOLOR_SLOT_EDIT,
        Capability.CAMERA,
        Capability.QUEUE_DISPATCH,
        Capability.AUTO_ARCHIVE,
        Capability.AMS_MANAGEMENT,
    }
)

# Bambu gcode_state -> provider-neutral job state. Values come from
# BambuMQTTClient (print.gcode_state) and its own "unknown" default.
_BAMBU_JOB_STATES: dict[str, JobState] = {
    "IDLE": JobState.IDLE,
    "PREPARE": JobState.PREPARING,
    "SLICING": JobState.PREPARING,
    "RUNNING": JobState.PRINTING,
    "PAUSE": JobState.PAUSED,
    "FINISH": JobState.FINISHED,
    "FAILED": JobState.FAILED,
}


# HMSError.severity is Bambu's alert level: 1 error, 2 warning, 3 notification.
_HMS_SEVERITY = {1: "error", 2: "warning", 3: "info"}


def bambu_capabilities(model: str | None) -> CapabilitySet:
    """Capability set of a Bambu printer of *model*."""
    # Imported lazily: printer_manager imports this package.
    from backend.app.services.printer_manager import supports_chamber_temp

    supported = set(_BAMBU_BASE_CAPABILITIES)
    notes: dict[Capability, str] = {}
    if supports_chamber_temp(model):
        supported.add(Capability.CHAMBER_TEMPERATURE)
    else:
        notes[Capability.CHAMBER_TEMPERATURE] = "model has no chamber temperature sensor"
    return CapabilitySet(
        provider=PrinterProvider.BAMBU,
        model=model,
        supported=frozenset(supported),
        notes=notes,
    )


def bambu_job_state(gcode_state: str | None) -> JobState:
    if not gcode_state:
        return JobState.UNKNOWN
    return _BAMBU_JOB_STATES.get(gcode_state.upper(), JobState.UNKNOWN)


def _reading(temps: dict, key: str) -> TemperatureReading:
    def _num(value) -> float | None:
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    return TemperatureReading(current=_num(temps.get(key)), target=_num(temps.get(f"{key}_target")))


def _ams_slots(raw_data: dict) -> list[MultiColorSlot]:
    slots: list[MultiColorSlot] = []
    for unit in raw_data.get("ams") or []:
        if not isinstance(unit, dict):
            continue
        try:
            unit_id = int(unit.get("id", 0))
        except (TypeError, ValueError):
            continue
        for tray in unit.get("tray") or []:
            if not isinstance(tray, dict):
                continue
            try:
                slot_index = int(tray.get("id", 0))
            except (TypeError, ValueError):
                continue
            material = tray.get("tray_type") or None
            color = (tray.get("tray_color") or "")[:6].upper() or None
            # A tag UID other than all-zeros means the AMS read the spool's RFID.
            tag = str(tray.get("tag_uid") or "")
            detected = bool(tag) and set(tag) != {"0"}
            slots.append(
                MultiColorSlot(
                    unit_id=unit_id,
                    slot_index=slot_index,
                    loaded=bool(material) if "tray_type" in tray else None,
                    material=material,
                    color_hex=color,
                    name=tray.get("tray_sub_brands") or None,
                    source=SlotDataSource.DETECTED if detected else SlotDataSource.DECLARED,
                )
            )
    return slots


def normalize_bambu_state(state: PrinterState, model: str | None = None) -> NormalizedTelemetry:
    """Provider-neutral view of a Bambu :class:`PrinterState`.

    Pure: reads the state, never mutates it. A disconnected client keeps its
    last job state in ``PrinterState.state`` but the connection is reported as
    OFFLINE, so callers can tell "was printing, link lost" from "finished".
    """
    temps = state.temperatures or {}
    fans = {
        name: value
        for name, value in (
            ("part_cooling", state.cooling_fan_speed),
            ("aux", state.big_fan1_speed),
            ("chamber", state.big_fan2_speed),
            ("heatbreak", state.heatbreak_fan_speed),
        )
        if value is not None
    }
    faults = [
        PrinterFault(
            code=str(getattr(err, "code", err)),
            severity=_HMS_SEVERITY.get(getattr(err, "severity", 1), "error"),
            message=getattr(err, "description", None),
        )
        for err in (state.hms_errors or [])
    ]
    remaining_minutes = state.remaining_time
    return NormalizedTelemetry(
        provider=PrinterProvider.BAMBU,
        connection=ConnectionStatus.ONLINE if state.connected else ConnectionStatus.OFFLINE,
        job_state=bambu_job_state(state.state),
        raw_state=state.state,
        filename=state.subtask_name or state.gcode_file or None,
        progress_percent=float(state.progress) if state.progress is not None else None,
        current_layer=state.layer_num,
        total_layers=state.total_layers,
        # Bambu reports mc_remaining_time in minutes; PrinterState keeps it as-is.
        remaining_seconds=int(remaining_minutes) * 60 if remaining_minutes is not None else None,
        nozzle=_reading(temps, "nozzle"),
        bed=_reading(temps, "bed"),
        chamber=_reading(temps, "chamber"),
        fans_percent=fans,
        faults=faults,
        multicolor_slots=_ams_slots(state.raw_data or {}),
        firmware_version=state.firmware_version,
        model=model,
    )
