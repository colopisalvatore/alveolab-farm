"""Printer-provider contracts (ALVEOLAB Farm multibrand layer).

Bambuddy was built for one family of machines: every connected printer is a
:class:`~backend.app.services.bambu_mqtt.BambuMQTTClient`, and ~100 call sites
read its :class:`~backend.app.services.bambu_mqtt.PrinterState` directly. This
module adds the provider-neutral vocabulary a second family (ELEGOO) needs
*without* rewriting those call sites:

  * :class:`PrinterProvider` — which protocol family a printer row speaks.
  * :class:`Capability` / :class:`CapabilitySet` — what a given provider + model
    can actually do. Nothing in the farm may assume a feature exists; it asks.
  * :class:`ConnectionStatus` / :class:`JobState` — explicit states, including
    ``UNKNOWN``, so silence is never mistaken for an answer (a disconnect is
    *not* a finished print).
  * :class:`NormalizedTelemetry` and friends — the typed snapshot every provider
    can produce, regardless of how its wire protocol spells things.
  * :class:`PrinterProviderClient` — the minimal runtime contract a non-Bambu
    client must fulfil to be held by ``PrinterManager``. Optional abilities
    (job control, upload, multicolour) are separate protocols, checked with
    ``isinstance`` *and* gated by the capability set.

The Bambu path is intentionally untouched: ``BambuMQTTClient`` is not forced to
implement these protocols. Its telemetry is normalised from ``PrinterState`` by
:mod:`backend.app.services.printer_providers.bambu`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from backend.app.core.compat import StrEnum

if TYPE_CHECKING:
    from backend.app.services.bambu_mqtt import PrinterState


class PrinterProvider(StrEnum):
    """Protocol family of a printer row (``printers.provider``)."""

    BAMBU = "bambu"
    ELEGOO = "elegoo"


DEFAULT_PROVIDER = PrinterProvider.BAMBU


def parse_provider(value: str | None) -> PrinterProvider:
    """Map a stored ``printers.provider`` value to the enum.

    ``None`` / empty means a row that predates the column — every such row is a
    Bambu printer, so that is the answer (as is a non-string, which only a
    duck-typed stand-in for the ORM row can produce). An unrecognised *string*
    is an error, not a silent fallback to Bambu: connecting an unknown machine
    with the Bambu client would send it Bambu commands.
    """
    if isinstance(value, PrinterProvider):
        return value
    if not isinstance(value, str) or not value.strip():
        return DEFAULT_PROVIDER
    try:
        return PrinterProvider(value.strip().lower())
    except ValueError as exc:
        raise ValueError(f"Unknown printer provider {value!r}") from exc


class Capability(StrEnum):
    """One thing a printer can do *through ALVEOLAB Farm*.

    Capabilities describe what the farm can rely on, not the machine's
    marketing sheet: a feature the hardware has but the provider does not
    implement yet is absent.
    """

    # Connection / discovery
    LAN_DISCOVERY = "lan_discovery"
    MANUAL_REGISTRATION = "manual_registration"
    AUTO_RECONNECT = "auto_reconnect"
    # Read-only telemetry
    STATUS = "status"
    TEMPERATURES = "temperatures"
    CHAMBER_TEMPERATURE = "chamber_temperature"
    PRINT_PROGRESS = "print_progress"
    LAYER_PROGRESS = "layer_progress"
    FAN_SPEEDS = "fan_speeds"
    PRINT_SPEED = "print_speed"
    ERROR_REPORTING = "error_reporting"
    DIAGNOSTIC_LOG = "diagnostic_log"
    # Job control (commands that change what the machine is doing)
    JOB_START = "job_start"
    JOB_PAUSE_RESUME = "job_pause_resume"
    JOB_STOP = "job_stop"
    FILE_UPLOAD = "file_upload"
    FILE_LIST = "file_list"
    # Multicolour / filament
    MULTICOLOR_STATUS = "multicolor_status"
    MULTICOLOR_SLOT_EDIT = "multicolor_slot_edit"
    # Media
    CAMERA = "camera"
    # Bambuddy pipelines that are wired to the Bambu protocol today. Kept as
    # capabilities so the queue / archive can refuse a non-Bambu printer
    # explicitly instead of failing deep inside an FTP upload.
    QUEUE_DISPATCH = "queue_dispatch"
    AUTO_ARCHIVE = "auto_archive"
    AMS_MANAGEMENT = "ams_management"


@dataclass(frozen=True)
class CapabilitySet:
    """What one printer supports, plus why something is missing.

    ``notes`` carries a human-readable reason per capability (supported or not)
    — e.g. "not implemented yet (M4)" or "not exposed by firmware" — so the UI
    can explain a greyed-out control instead of hiding it silently.
    """

    provider: PrinterProvider
    model: str | None
    supported: frozenset[Capability]
    notes: dict[Capability, str] = field(default_factory=dict)

    def supports(self, capability: Capability) -> bool:
        return capability in self.supported

    def to_dict(self) -> dict:
        return {
            "provider": self.provider.value,
            "model": self.model,
            "supported": sorted(c.value for c in self.supported),
            "notes": {c.value: note for c, note in sorted(self.notes.items())},
        }


class ConnectionStatus(StrEnum):
    """Link state between the farm and a printer."""

    CONNECTING = "connecting"
    ONLINE = "online"
    OFFLINE = "offline"
    # The printer answered but refused us (wrong access code / pairing). Kept
    # apart from OFFLINE: retrying will not fix it, a human has to.
    AUTH_FAILED = "auth_failed"
    UNKNOWN = "unknown"


class JobState(StrEnum):
    """Provider-neutral print-job state.

    ``UNKNOWN`` is a real value, not an error: it is what a printer is in until
    it has *told* us otherwise, and what it stays in when the link drops. A
    disconnect must never be read as FINISHED or FAILED.
    """

    IDLE = "idle"
    PREPARING = "preparing"
    PRINTING = "printing"
    PAUSING = "pausing"
    PAUSED = "paused"
    RESUMING = "resuming"
    STOPPING = "stopping"
    FINISHED = "finished"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BUSY = "busy"  # machine busy with a non-print task (homing, calibration, filament ops...)
    UNKNOWN = "unknown"


ACTIVE_JOB_STATES = frozenset(
    {JobState.PREPARING, JobState.PRINTING, JobState.PAUSING, JobState.PAUSED, JobState.RESUMING, JobState.STOPPING}
)


class SlotDataSource(StrEnum):
    """Where a multicolour slot's material/colour came from.

    The distinction matters for job safety: a colour the machine *detected*
    (RFID, sensor) can be trusted to match a job; one the operator *declared*
    on the touchscreen or in the slicer is only as good as the operator.
    """

    DETECTED = "detected"
    DECLARED = "declared"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class TemperatureReading:
    current: float | None = None
    target: float | None = None


@dataclass(frozen=True)
class MultiColorSlot:
    """One filament slot of a multicolour unit (Bambu AMS tray, ELEGOO CANVAS slot)."""

    unit_id: int
    slot_index: int
    loaded: bool | None = None  # None = the printer does not say
    material: str | None = None
    color_hex: str | None = None  # "RRGGBB", upper-case, no leading '#'
    name: str | None = None
    vendor: str | None = None
    source: SlotDataSource = SlotDataSource.UNKNOWN
    active: bool = False


@dataclass(frozen=True)
class PrinterFault:
    """An error or warning reported by the printer."""

    code: str
    severity: str = "error"  # "error" | "warning" | "info"
    message: str | None = None


@dataclass
class NormalizedTelemetry:
    """Provider-neutral snapshot of one printer.

    Every numeric field is ``None`` when the printer has not reported it —
    never a made-up zero. Consumers render "—" for ``None``.
    """

    provider: PrinterProvider
    connection: ConnectionStatus = ConnectionStatus.UNKNOWN
    job_state: JobState = JobState.UNKNOWN
    raw_state: str | None = None  # provider-native state value, for diagnostics
    filename: str | None = None
    progress_percent: float | None = None
    current_layer: int | None = None
    total_layers: int | None = None
    elapsed_seconds: int | None = None
    remaining_seconds: int | None = None
    nozzle: TemperatureReading = field(default_factory=TemperatureReading)
    bed: TemperatureReading = field(default_factory=TemperatureReading)
    chamber: TemperatureReading = field(default_factory=TemperatureReading)
    fans_percent: dict[str, int] = field(default_factory=dict)
    speed_percent: int | None = None
    faults: list[PrinterFault] = field(default_factory=list)
    multicolor_slots: list[MultiColorSlot] = field(default_factory=list)
    firmware_version: str | None = None
    model: str | None = None
    updated_at: float = field(default_factory=time.time)

    @property
    def job_active(self) -> bool:
        return self.job_state in ACTIVE_JOB_STATES


@runtime_checkable
class PrinterProviderClient(Protocol):
    """Runtime contract for a non-Bambu printer client held by ``PrinterManager``.

    ``state`` is a :class:`PrinterState` on purpose: it is the shape the
    dashboard, WebSocket broadcast and status API already consume, so a provider
    that fills it in shows up everywhere for free. A provider only fills in the
    fields its protocol really reports and leaves the rest at defaults.
    """

    provider: PrinterProvider

    @property
    def state(self) -> PrinterState: ...

    @property
    def capabilities(self) -> CapabilitySet: ...

    def connect(self) -> None: ...

    def disconnect(self, timeout: float = 0) -> None: ...

    def check_staleness(self) -> bool:
        """Return the current connected flag, demoting a silent link first."""
        ...

    def telemetry(self) -> NormalizedTelemetry: ...


@runtime_checkable
class SupportsJobControl(Protocol):
    """Optional: pause / resume / stop of the running job."""

    def pause_print(self) -> bool: ...

    def resume_print(self) -> bool: ...

    def stop_print(self) -> bool: ...
