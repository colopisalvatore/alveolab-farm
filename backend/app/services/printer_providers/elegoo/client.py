"""MQTT client for the ELEGOO Centauri Carbon 2.

Connection sequence, traced to the official SDK (see ``protocol.py``):

  1. MQTT CONNECT to ``tcp://<ip>:1883``, user ``elegoo``, password = access
     code (or the factory default when none is configured).
  2. Subscribe to ``api_response``, ``api_status`` and ``register_response``.
  3. Publish ``{"client_id", "request_id"}`` to ``api_register`` and wait for
     ``register_response`` with ``error == "ok"``.
  4. Ask for attributes (1001), full status (1002) and CANVAS status (2005).
  5. PING every 10 s; 65 s without any message from the printer = link lost.

Threading mirrors ``BambuMQTTClient``: paho runs its own network thread and
reconnects by itself; a small heartbeat thread sends PINGs and watches for
silence. Every state change is pushed through ``on_state_change`` — the
manager hops it onto the asyncio loop.

Safety rules this client enforces:
  * Read-only queries and job commands are built by different functions; a
    job command (M4) is only sent when its capability is enabled, and only
    from the job states where it makes sense.
  * Job commands go out with QoS 0 and are never re-sent: paho re-publishes
    unacknowledged QoS 1 messages after a reconnect, which could start the
    same print twice. A command without reply is reported as such, not
    retried. A start is refused for a job key already sent.
  * A lost link sets ``connected=False`` and leaves the job state as last
    reported: a disconnect is never read as "print finished".
  * Credentials are never logged; the diagnostic log stores payloads only.
  * A rejected password stops the reconnect loop (AUTH_FAILED): retrying the
    same wrong code cannot succeed and only fills the printer's log.
"""

from __future__ import annotations

import copy
import itertools
import json
import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

from backend.app.services.bambu_mqtt import MQTTLogEntry, PrinterState
from backend.app.services.printer_providers.base import (
    ACTIVE_JOB_STATES,
    Capability,
    CapabilitySet,
    ConnectionStatus,
    JobState,
    NormalizedTelemetry,
    PrinterProvider,
)
from backend.app.services.printer_providers.elegoo import canvas as cv, protocol as p
from backend.app.services.printer_providers.elegoo.capabilities import elegoo_capabilities
from backend.app.services.printer_providers.elegoo.normalizer import apply_to_printer_state, normalize_cc2_status

logger = logging.getLogger(__name__)

# CONNACK codes that mean "credentials refused": MQTT 3.1.1 return codes 4/5,
# which paho 2 reports as reason codes 134/135 (community test_auth_detection.py:12-46).
_AUTH_FAILURE_CODES = frozenset({4, 5, 134, 135})

CONNECT_ERROR_AUTH = "auth_failed"
CONNECT_ERROR_TOO_MANY_CLIENTS = "too_many_clients"
CONNECT_ERROR_REGISTER = "register_failed"
CONNECT_ERROR_REGISTER_TIMEOUT = "register_timeout"
CONNECT_ERROR_UNREACHABLE = "unreachable"


# Outcome of a job command.
COMMAND_ACCEPTED = "accepted"  # the printer answered error_code 0
COMMAND_REJECTED = "rejected"  # the printer answered an error code
COMMAND_NO_REPLY = "no_reply"  # sent, no answer in time: outcome unknown, not retried
COMMAND_NOT_SENT = "not_sent"  # not connected, nothing left the farm
COMMAND_REFUSED = "refused"  # refused by the farm before sending

# Job states from which a new print may be started: the machine is idle or
# shows the end of the previous job.
STARTABLE_STATES = frozenset({JobState.IDLE, JobState.FINISHED, JobState.CANCELLED, JobState.FAILED})
# ELEGOO's page allows a CANVAS load / unload when idle or paused.
LOAD_UNLOAD_STATES = STARTABLE_STATES | {JobState.PAUSED}
_TERMINAL_STATES = frozenset({JobState.FINISHED, JobState.CANCELLED, JobState.FAILED})
# Progress from which a print that goes back to idle without a terminal
# sub-status counts as finished. Our CC2 (fw 02.01.01.04, 29/10/2026) went
# from "printing 99%" straight to idle; 2077 was never seen.
FINISHED_PROGRESS_THRESHOLD = 99.0


@dataclass(frozen=True)
class CommandOutcome:
    status: str
    method: int | None = None
    error_code: int | None = None
    message: str | None = None

    @property
    def accepted(self) -> bool:
        return self.status == COMMAND_ACCEPTED

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "method": self.method,
            "error_code": self.error_code,
            "message": self.message,
        }


@dataclass(frozen=True)
class JobEvent:
    """A print started or ended, as seen while connected.

    ``outcome`` is FINISHED / CANCELLED / FAILED when the machine said so, or
    when ``inferred`` (back to idle after 99%). UNKNOWN when the end was not
    observed (the link dropped during the job): a disconnect never ends a
    job on its own, the next status read does.
    """

    kind: str  # "started" | "ended"
    filename: str | None
    outcome: JobState | None = None
    inferred: bool = False
    progress: float | None = None


class _PendingCommand:
    __slots__ = ("event", "method", "reply")

    def __init__(self, method: int):
        self.method = method
        self.event = threading.Event()
        self.reply: dict | None = None


@dataclass
class _JobRecord:
    filename: str | None
    max_progress: float | None = None
    link_lost: bool = False


def _default_mqtt_factory(client_id: str) -> mqtt.Client:
    return mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=client_id,
        protocol=mqtt.MQTTv311,
        clean_session=True,  # mqtt_protocol.cpp:159-173
    )


class ElegooCC2Client:
    """One CC2 printer. Implements ``PrinterProviderClient``."""

    provider = PrinterProvider.ELEGOO

    STALE_TIMEOUT = p.HEARTBEAT_TIMEOUT_SECONDS
    HEARTBEAT_INTERVAL = p.HEARTBEAT_INTERVAL_SECONDS
    REGISTER_TIMEOUT = p.REGISTER_TIMEOUT_SECONDS
    COMMAND_TIMEOUT = 10.0
    # A resume (1023) sent to firmware 02.01.01.04 got no reply within 10 s
    # although the printer went resuming -> printing about 15 s later
    # (HW-M4-05, 31/10/2026). It waits longer and also accepts the printer's
    # own state change as its answer.
    RESUME_TIMEOUT = 30.0
    CONFIRM_POLL_INTERVAL = 0.25
    LOG_CAPACITY = 500

    def __init__(
        self,
        ip_address: str,
        serial_number: str,
        access_code: str | None = None,
        model: str | None = None,
        on_state_change: Callable[[PrinterState], None] | None = None,
        on_job_event: Callable[[JobEvent], None] | None = None,
        *,
        mqtt_client_factory: Callable[[str], mqtt.Client] | None = None,
        clock: Callable[[], float] = time.monotonic,
        start_heartbeat: bool = True,
    ):
        self.ip_address = ip_address
        self.serial_number = serial_number
        self._access_code = access_code or None
        self.model = model
        self.on_state_change = on_state_change
        self.on_job_event = on_job_event
        self._mqtt_factory = mqtt_client_factory or _default_mqtt_factory
        self._clock = clock
        self._start_heartbeat = start_heartbeat

        self.client_id = p.make_client_id(serial_number)
        self.request_id = p.make_request_id(self.client_id)
        self.topics = p.CC2Topics(serial_number, self.client_id, self.request_id)

        self._lock = threading.RLock()
        self._state = PrinterState()
        self._connection = ConnectionStatus.UNKNOWN
        self._cache = p.CC2StatusCache()
        self._attributes: dict = {}
        self._canvas_info: dict | None = None
        self._telemetry = NormalizedTelemetry(provider=PrinterProvider.ELEGOO, model=model)
        self._request_ids = itertools.count(100000)  # SDK counter start, message_adapter.cpp:44-51
        self._pending: dict[int, _PendingCommand] = {}
        # Job keys whose start was sent by this process; the scheduler keeps
        # its own persisted record, this is the second line.
        self._started_job_keys: dict[str, str] = {}
        self._job: _JobRecord | None = None

        self._client: mqtt.Client | None = None
        self._registered = False
        self._register_sent_at: float | None = None
        self._last_message_at: float | None = None
        self._stopping = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None

        self.last_connect_error: str | None = None
        self.logging_enabled = False
        self._log: deque[MQTTLogEntry] = deque(maxlen=self.LOG_CAPACITY)

    # --- PrinterProviderClient -------------------------------------------------

    @property
    def state(self) -> PrinterState:
        return self._state

    @property
    def capabilities(self) -> CapabilitySet:
        return elegoo_capabilities(self.model or self._attributes.get("machine_model"))

    @property
    def connection(self) -> ConnectionStatus:
        return self._connection

    def telemetry(self) -> NormalizedTelemetry:
        return self._telemetry

    def connect(self) -> None:
        """Start the MQTT session (non-blocking, like ``BambuMQTTClient.connect``)."""
        with self._lock:
            self._stopping.clear()
            self._set_connection(ConnectionStatus.CONNECTING, notify=False)
            client = self._mqtt_factory(self.client_id)
            password = self._access_code or p.DEFAULT_ACCESS_CODE
            client.username_pw_set(p.MQTT_USERNAME, password)
            client.on_connect = self._on_connect
            client.on_disconnect = self._on_disconnect
            client.on_message = self._on_message
            client.reconnect_delay_set(min_delay=p.RECONNECT_DELAY_SECONDS, max_delay=60)
            self._client = client
        logger.info("[ELEGOO %s] connecting to %s:%s", self.serial_number, self.ip_address, p.MQTT_PORT)
        client.connect_async(self.ip_address, p.MQTT_PORT, keepalive=p.MQTT_KEEPALIVE_SECONDS)
        client.loop_start()
        if self._start_heartbeat and (self._heartbeat_thread is None or not self._heartbeat_thread.is_alive()):
            self._heartbeat_thread = threading.Thread(
                target=self._heartbeat_loop, name=f"elegoo-heartbeat-{self.serial_number}", daemon=True
            )
            self._heartbeat_thread.start()

    def disconnect(self, timeout: float = 0) -> None:
        self._stopping.set()
        with self._lock:
            client, self._client = self._client, None
            self._registered = False
        if client is not None:
            try:
                client.disconnect()
                client.loop_stop()
            except Exception as exc:  # teardown must not raise into the manager
                logger.debug("[ELEGOO %s] disconnect error: %s", self.serial_number, exc)
        thread = self._heartbeat_thread
        if thread is not None and thread is not threading.current_thread() and timeout:
            thread.join(timeout)
        self._mark_offline(ConnectionStatus.OFFLINE)

    def check_staleness(self) -> bool:
        """Demote a silent link to offline; returns the connected flag."""
        with self._lock:
            if (
                self._registered
                and self._last_message_at is not None
                and self._clock() - self._last_message_at > self.STALE_TIMEOUT
            ):
                logger.warning(
                    "[ELEGOO %s] no message for %.0fs, link considered lost",
                    self.serial_number,
                    self._clock() - self._last_message_at,
                )
                self._registered = False
                stale = True
            else:
                stale = False
        if stale:
            self._mark_offline(ConnectionStatus.OFFLINE)
            self._drop_socket()
        return self._state.connected

    def _drop_socket(self) -> None:
        """Close the socket so paho's loop notices and reconnects the SAME client.

        Same technique as ``BambuMQTTClient._socket_close_for_reconnect``;
        ``on_connect`` then subscribes and registers again.
        """
        client = self._client
        if client is None:
            return
        try:
            sock = client.socket()
            if sock:
                sock.close()
        except Exception as exc:
            logger.debug("[ELEGOO %s] socket close failed: %s", self.serial_number, exc)

    # --- Diagnostics (same surface as BambuMQTTClient) -------------------------

    def enable_logging(self, enabled: bool = True) -> None:
        self.logging_enabled = enabled

    def get_logs(self) -> list[MQTTLogEntry]:
        return list(self._log)

    def clear_logs(self) -> None:
        self._log.clear()

    def _record(self, topic: str, direction: str, payload) -> None:
        if not self.logging_enabled:
            return
        self._log.append(
            MQTTLogEntry(
                timestamp=datetime.now(timezone.utc).isoformat(),
                topic=topic,
                direction=direction,
                payload=payload if isinstance(payload, dict) else {"raw": str(payload)[:2000]},
            )
        )

    # --- Outbound --------------------------------------------------------------

    def _publish(self, topic: str, payload: str) -> bool:
        client = self._client
        if client is None:
            return False
        try:
            info = client.publish(topic, payload, qos=1, retain=False)  # mqtt_protocol.cpp:79
        except Exception as exc:
            logger.debug("[ELEGOO %s] publish failed: %s", self.serial_number, exc)
            return False
        try:
            self._record(topic, "out", json.loads(payload))
        except ValueError:
            self._record(topic, "out", payload)
        return getattr(info, "rc", mqtt.MQTT_ERR_SUCCESS) == mqtt.MQTT_ERR_SUCCESS

    def request(self, method: int, params: dict | None = None) -> bool:
        """Send a read-only query (1001 / 1002 / 2005). Anything else raises."""
        message = p.build_request(next(self._request_ids), method, params)
        return self._publish(self.topics.api_request, message)

    def request_status_update(self) -> bool:
        return self.request(p.METHOD_GET_STATUS)

    # --- Job commands (M4) -------------------------------------------------------

    def _wait_for_reply(
        self, pending: _PendingCommand, timeout: float, confirm_states: frozenset[JobState] | None
    ) -> bool | None:
        """True on a reply, None when the printer reported one of *confirm_states* first, False on timeout."""
        if not confirm_states:
            return pending.event.wait(timeout)
        deadline = time.monotonic() + timeout
        while True:
            if self._telemetry.job_state in confirm_states:
                # A reply racing the state change still wins: it carries the error code.
                return True if pending.event.is_set() else None
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            if pending.event.wait(min(self.CONFIRM_POLL_INTERVAL, remaining)):
                return True

    def _send_job_command(
        self,
        method: int,
        params: dict | None = None,
        timeout: float | None = None,
        build=p.build_job_request,
        confirm_states: frozenset[JobState] | None = None,
    ) -> CommandOutcome:
        """Publish one job (or, with *build*, CANVAS) command (QoS 0, never re-sent) and wait for its reply.

        With *confirm_states*, the printer reporting one of those job states
        while we wait counts as acceptance: some commands get no reply even
        though the printer carries them out.
        """
        with self._lock:
            client = self._client
            if client is None or not self._registered or self._connection is not ConnectionStatus.ONLINE:
                return CommandOutcome(COMMAND_NOT_SENT, method, message="printer not connected")
            request_id = next(self._request_ids)
            pending = _PendingCommand(method)
            self._pending[request_id] = pending
        try:
            message = build(request_id, method, params)
            try:
                info = client.publish(self.topics.api_request, message, qos=0, retain=False)
            except Exception as exc:
                logger.warning("[ELEGOO %s] command %s not sent: %s", self.serial_number, method, exc)
                return CommandOutcome(COMMAND_NOT_SENT, method, message="publish failed")
            if getattr(info, "rc", mqtt.MQTT_ERR_SUCCESS) != mqtt.MQTT_ERR_SUCCESS:
                return CommandOutcome(COMMAND_NOT_SENT, method, message="publish failed")
            self._record(self.topics.api_request, "out", json.loads(message))
            logger.info("[ELEGOO %s] sent job command %s (id %s)", self.serial_number, method, request_id)
            replied = self._wait_for_reply(
                pending, self.COMMAND_TIMEOUT if timeout is None else timeout, confirm_states
            )
            if replied is None:
                logger.info(
                    "[ELEGOO %s] command %s (id %s) not answered, confirmed by the printer status (%s)",
                    self.serial_number,
                    method,
                    request_id,
                    self._telemetry.job_state.value,
                )
                return CommandOutcome(COMMAND_ACCEPTED, method, message="confirmed by the printer status")
            if not replied:
                logger.warning("[ELEGOO %s] no reply to command %s (id %s)", self.serial_number, method, request_id)
                return CommandOutcome(COMMAND_NO_REPLY, method, message="no reply from the printer")
            code = (pending.reply or {}).get("error_code")
            if code == 0:
                return CommandOutcome(COMMAND_ACCEPTED, method, 0)
            code = code if isinstance(code, int) else None
            message = p.describe_error_code(code) if code is not None else "reply without error_code"
            logger.warning("[ELEGOO %s] command %s rejected: %s", self.serial_number, method, message)
            return CommandOutcome(COMMAND_REJECTED, method, code, message)
        finally:
            with self._lock:
                self._pending.pop(request_id, None)

    def _refuse_unless(
        self, capability: Capability, method: int, allowed: frozenset[JobState]
    ) -> CommandOutcome | None:
        if not self.capabilities.supports(capability):
            return CommandOutcome(
                COMMAND_REFUSED, method, message=f"{capability.value} is not enabled for this printer"
            )
        state = self._telemetry.job_state
        if state not in allowed:
            return CommandOutcome(COMMAND_REFUSED, method, message=f"not possible while the printer is {state.value}")
        return None

    def start_print(
        self,
        filename: str,
        *,
        job_key: str,
        plate_layout: str = "A",
        auto_bed_leveling: bool = False,
        slot_map: list[dict] | None = None,
    ) -> CommandOutcome:
        """Start *filename* (already on the printer). Sent at most once per *job_key*."""
        method = p.METHOD_START_PRINT
        try:
            p.check_machine_filename(filename)
            params = p.start_print_params(
                filename, plate_layout=plate_layout, auto_bed_leveling=auto_bed_leveling, slot_map=slot_map
            )
        except ValueError as exc:
            return CommandOutcome(COMMAND_REFUSED, method, message=str(exc))
        with self._lock:
            refusal = self._refuse_unless(Capability.JOB_START, method, STARTABLE_STATES)
            if refusal is not None:
                return refusal
            if not job_key:
                return CommandOutcome(COMMAND_REFUSED, method, message="missing job key")
            if job_key in self._started_job_keys:
                return CommandOutcome(COMMAND_REFUSED, method, message="start already sent for this job")
            # Recorded before sending: a reply lost to a reconnect must not
            # open the door to a second start of the same job.
            self._started_job_keys[job_key] = filename
        outcome = self._send_job_command(method, params)
        if outcome.status == COMMAND_NOT_SENT:
            with self._lock:
                self._started_job_keys.pop(job_key, None)
        return outcome

    def pause_print(self) -> CommandOutcome:
        method = p.METHOD_PAUSE_PRINT
        refusal = self._refuse_unless(Capability.JOB_PAUSE_RESUME, method, frozenset({JobState.PRINTING}))
        return refusal or self._send_job_command(method)

    def resume_print(self) -> CommandOutcome:
        method = p.METHOD_RESUME_PRINT
        refusal = self._refuse_unless(Capability.JOB_PAUSE_RESUME, method, frozenset({JobState.PAUSED}))
        return refusal or self._send_job_command(
            method,
            timeout=self.RESUME_TIMEOUT,
            confirm_states=frozenset({JobState.RESUMING, JobState.PRINTING}),
        )

    def stop_print(self) -> CommandOutcome:
        method = p.METHOD_STOP_PRINT
        refusal = self._refuse_unless(Capability.JOB_STOP, method, ACTIVE_JOB_STATES)
        return refusal or self._send_job_command(method)

    # --- CANVAS commands (M5) ------------------------------------------------------

    def _send_canvas_command(self, method: int, params: dict, timeout: float | None = None) -> CommandOutcome:
        outcome = self._send_job_command(method, params, timeout, build=p.build_canvas_request)
        if outcome.status == COMMAND_ACCEPTED:
            # The new slot data / auto-refill flag comes back in 2005.
            self.request(p.METHOD_GET_CANVAS_STATUS)
        return outcome

    def _canvas_tray(self, method: int, canvas_id: int, tray_id: int) -> tuple[dict | None, CommandOutcome | None]:
        with self._lock:
            tray = cv.find_tray(self._canvas_info, canvas_id, tray_id)
        if tray is None:
            return None, CommandOutcome(
                COMMAND_REFUSED, method, message=f"CANVAS {canvas_id} slot {tray_id} not reported by the printer"
            )
        return tray, None

    def set_canvas_filament(
        self, canvas_id: int, tray_id: int, *, filament_name: str, color: str, brand: str
    ) -> CommandOutcome:
        """Declare what spool sits in a CANVAS slot (2003). Printer idle only, like ELEGOO's page."""
        method = p.METHOD_SET_FILAMENT_INFO
        try:
            params = cv.filament_info_params(canvas_id, tray_id, filament_name=filament_name, color=color, brand=brand)
        except ValueError as exc:
            return CommandOutcome(COMMAND_REFUSED, method, message=str(exc))
        refusal = self._refuse_unless(Capability.MULTICOLOR_SLOT_EDIT, method, STARTABLE_STATES)
        if refusal is None:
            _, refusal = self._canvas_tray(method, canvas_id, tray_id)
        return refusal or self._send_canvas_command(method, params)

    def set_canvas_auto_refill(self, enabled: bool) -> CommandOutcome:
        """Switch to another slot with the same filament when one runs out (2004)."""
        method = p.METHOD_SET_AUTO_REFILL
        known_states = frozenset(JobState) - {JobState.UNKNOWN}
        refusal = self._refuse_unless(Capability.MULTICOLOR_AUTO_REFILL, method, known_states)
        if refusal is None:
            with self._lock:
                has_canvas = bool((self._canvas_info or {}).get("canvas_list"))
            if not has_canvas:
                refusal = CommandOutcome(COMMAND_REFUSED, method, message="no CANVAS reported by the printer")
        return refusal or self._send_canvas_command(method, cv.auto_refill_params(enabled))

    def load_canvas_filament(self, canvas_id: int, tray_id: int) -> CommandOutcome:
        """Feed the filament of a slot to the nozzle (2001). Idle or paused, recognised spool only."""
        method = p.METHOD_LOAD_FILAMENT
        refusal = self._refuse_unless(Capability.MULTICOLOR_LOAD_UNLOAD, method, LOAD_UNLOAD_STATES)
        if refusal is None:
            tray, refusal = self._canvas_tray(method, canvas_id, tray_id)
            if refusal is None and not cv.tray_recognised(tray):
                refusal = CommandOutcome(
                    COMMAND_REFUSED, method, message="the slot is empty or its filament is not set"
                )
        return refusal or self._send_canvas_command(
            method, cv.tray_params(canvas_id, tray_id), cv.LOAD_UNLOAD_TIMEOUT_SECONDS
        )

    def unload_canvas_filament(self, canvas_id: int, tray_id: int) -> CommandOutcome:
        """Pull the filament of a slot back from the nozzle (2002). Idle or paused."""
        method = p.METHOD_UNLOAD_FILAMENT
        refusal = self._refuse_unless(Capability.MULTICOLOR_LOAD_UNLOAD, method, LOAD_UNLOAD_STATES)
        if refusal is None:
            _, refusal = self._canvas_tray(method, canvas_id, tray_id)
        return refusal or self._send_canvas_command(
            method, cv.tray_params(canvas_id, tray_id), cv.LOAD_UNLOAD_TIMEOUT_SECONDS
        )

    # --- paho callbacks (network thread) ----------------------------------------

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        code = getattr(reason_code, "value", reason_code)
        if code != 0:
            if code in _AUTH_FAILURE_CODES:
                self.last_connect_error = CONNECT_ERROR_AUTH
                logger.warning("[ELEGOO %s] access code rejected by the printer", self.serial_number)
                self._stopping.set()
                # Stop paho's reconnect loop: the same code will be refused again.
                client.disconnect()
                self._mark_offline(ConnectionStatus.AUTH_FAILED)
            else:
                self.last_connect_error = CONNECT_ERROR_UNREACHABLE
                logger.info("[ELEGOO %s] MQTT connect refused (code %s)", self.serial_number, code)
                self._mark_offline(ConnectionStatus.OFFLINE)
            return
        with self._lock:
            self._registered = False
            self._connection = ConnectionStatus.CONNECTING
            self._cache.reset()  # elegoo_fdm_cc2_printer.cpp:54-61
            self._register_sent_at = self._clock()
        for topic in self.topics.subscriptions():
            client.subscribe(topic, qos=1)
        self._publish(self.topics.api_register, p.register_request(self.client_id, self.request_id))

    def _on_disconnect(self, client, userdata, disconnect_flags=None, reason_code=None, properties=None):
        with self._lock:
            self._registered = False
        if self._connection is not ConnectionStatus.AUTH_FAILED:
            self._mark_offline(ConnectionStatus.OFFLINE)

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload)
        except (TypeError, ValueError):
            logger.debug("[ELEGOO %s] non-JSON payload on %s", self.serial_number, msg.topic)
            return
        if not isinstance(payload, dict):
            return
        self.handle_message(msg.topic, payload)

    # --- Message handling (pure enough to drive from tests) ---------------------

    def handle_message(self, topic: str, payload: dict) -> None:
        self._record(topic, "in", payload)
        with self._lock:
            self._last_message_at = self._clock()

        if topic == self.topics.register_response:
            self._handle_register(payload)
            return
        if payload.get("type") == "PONG":
            return
        if not self._registered:
            return

        method = payload.get("method")
        result = payload.get("result")
        if not isinstance(result, dict):
            return
        reply_id = payload.get("id")
        if isinstance(reply_id, int) and topic == self.topics.api_response:
            with self._lock:
                pending = self._pending.get(reply_id)
            if pending is not None:
                pending.reply = result
                pending.event.set()
                return
        if method in p.JOB_METHODS or method in p.CANVAS_METHODS:
            # A late reply to a command we stopped waiting for: logged, so a
            # printer that answers slowly shows up, but it changes nothing.
            logger.info(
                "[ELEGOO %s] late reply to command %s (id %s): error_code %s",
                self.serial_number,
                method,
                reply_id,
                result.get("error_code"),
            )
            return
        error_code = result.get("error_code")
        if isinstance(error_code, int) and error_code != 0 and method in (p.METHOD_GET_STATUS, p.METHOD_GET_ATTRIBUTES):
            logger.warning(
                "[ELEGOO %s] method %s answered error %s (%s)",
                self.serial_number,
                method,
                error_code,
                p.describe_error_code(error_code),
            )
            return

        refresh = False
        refresh_canvas = False
        with self._lock:
            if method == p.METHOD_GET_STATUS:
                self._cache.apply_full(result)
                if isinstance(result.get("canvas_info"), dict):
                    self._canvas_info = copy.deepcopy(result["canvas_info"])
            elif method == p.METHOD_ON_STATUS:
                push_id = payload.get("id") if isinstance(payload.get("id"), int) else None
                merged, refresh = self._cache.apply_delta(push_id, result)
                if merged is None and not refresh:
                    return
                # Our CC2 (fw 02.01.01.04) has no canvas_info in 1002, so the
                # status cache only ever holds CANVAS fragments from pushes.
                # Merge them into the full 2005 picture instead of replacing
                # it (that dropped active_tray_id), then re-read 2005 since the
                # exact shape of these pushes is not confirmed yet.
                canvas_delta = result.get("canvas_info")
                if isinstance(canvas_delta, dict):
                    if isinstance(self._canvas_info, dict):
                        p._deep_merge(self._canvas_info, canvas_delta)
                    else:
                        self._canvas_info = copy.deepcopy(canvas_delta)
                    refresh_canvas = True
            elif method in (p.METHOD_GET_ATTRIBUTES, p.METHOD_ON_ATTRIBUTES):
                self._attributes = dict(result)
            elif method == p.METHOD_GET_CANVAS_STATUS:
                canvas = result.get("canvas_info")
                if isinstance(canvas, dict):
                    self._canvas_info = canvas
            else:
                return
        if refresh:
            self.request_status_update()
        if refresh or refresh_canvas:
            # A lost push may have carried CANVAS changes too: 1002 does not.
            self.request(p.METHOD_GET_CANVAS_STATUS)
        self._refresh_state()

    def _handle_register(self, payload: dict) -> None:
        outcome = p.parse_register_response(payload, self.client_id)
        if outcome is None:
            return
        if not outcome.accepted:
            self.last_connect_error = (
                CONNECT_ERROR_TOO_MANY_CLIENTS if outcome.too_many_clients else CONNECT_ERROR_REGISTER
            )
            logger.warning("[ELEGOO %s] registration refused: %s", self.serial_number, outcome.error)
            with self._lock:
                # Arms the registration timeout in heartbeat_tick(), which drops
                # the socket so paho reconnects and registers again — a client
                # slot may have freed up by then.
                self._register_sent_at = self._clock()
            self._mark_offline(ConnectionStatus.OFFLINE)
            return
        with self._lock:
            self._registered = True
            self._register_sent_at = None
            self.last_connect_error = None
            self._connection = ConnectionStatus.ONLINE
        logger.info("[ELEGOO %s] registered as %s", self.serial_number, self.client_id)
        # SDK asks for status first when it has no cache (elegoo_fdm_cc2_printer.cpp:17-33).
        self.request(p.METHOD_GET_STATUS)
        self.request(p.METHOD_GET_ATTRIBUTES)
        self.request(p.METHOD_GET_CANVAS_STATUS)
        self._refresh_state()

    # --- Heartbeat ---------------------------------------------------------------

    def _heartbeat_loop(self) -> None:
        while not self._stopping.wait(self.HEARTBEAT_INTERVAL):
            self.heartbeat_tick()

    def heartbeat_tick(self) -> None:
        """One heartbeat period: PING, registration timeout, silence check."""
        with self._lock:
            registered = self._registered
            register_sent_at = self._register_sent_at
        if registered:
            self._publish(self.topics.api_request, p.heartbeat_ping())
        elif register_sent_at is not None and self._clock() - register_sent_at > self.REGISTER_TIMEOUT:
            self.last_connect_error = CONNECT_ERROR_REGISTER_TIMEOUT
            logger.warning("[ELEGOO %s] no registration reply", self.serial_number)
            with self._lock:
                self._register_sent_at = None
            # Re-run CONNECT + registration on paho's thread.
            self._drop_socket()
        self.check_staleness()

    # --- State propagation -------------------------------------------------------

    def _set_connection(self, connection: ConnectionStatus, *, notify: bool = True) -> None:
        with self._lock:
            self._connection = connection
        if notify:
            self._refresh_state()

    def _mark_offline(self, connection: ConnectionStatus) -> None:
        with self._lock:
            was_connected = self._state.connected
            self._connection = connection
        self._refresh_state(force_notify=was_connected)

    def _refresh_state(self, force_notify: bool = False) -> None:
        with self._lock:
            status = self._cache.status
            connection = self._connection
            # Keep the last known job picture when the link drops: the cache is
            # only reset by a *new* connection, never by a disconnect.
            telemetry = normalize_cc2_status(
                status,
                attributes=self._attributes,
                canvas_info=self._canvas_info,
                connection=connection,
                model=self.model,
            )
            if status is None:
                # Nothing reported yet on this connection: keep the previous
                # job picture rather than inventing IDLE.
                previous = self._telemetry
                telemetry.job_state = previous.job_state
                telemetry.raw_state = previous.raw_state
                telemetry.filename = previous.filename
                telemetry.progress_percent = previous.progress_percent
            self._telemetry = telemetry
            job_event = self._track_job(telemetry, has_status=status is not None)
            apply_to_printer_state(
                self._state,
                telemetry,
                status=status,
                attributes=self._attributes,
                canvas_info=self._canvas_info,
            )
            state = self._state
        callback = self.on_state_change
        if callback is not None and (force_notify or telemetry.connection is not ConnectionStatus.CONNECTING):
            try:
                callback(state)
            except Exception:
                logger.exception("[ELEGOO %s] state callback failed", self.serial_number)
        if job_event is not None and self.on_job_event is not None:
            try:
                self.on_job_event(job_event)
            except Exception:
                logger.exception("[ELEGOO %s] job event callback failed", self.serial_number)

    def _track_job(self, telemetry: NormalizedTelemetry, *, has_status: bool) -> JobEvent | None:
        """Turn job-state changes into start / end events. Caller holds the lock.

        Only a status read on a live link moves the tracker. A dropped link
        marks the job as partly unobserved and nothing else.
        """
        job = self._job
        if telemetry.connection is not ConnectionStatus.ONLINE or not has_status:
            if job is not None and telemetry.connection in (ConnectionStatus.OFFLINE, ConnectionStatus.AUTH_FAILED):
                job.link_lost = True
            return None
        state = telemetry.job_state
        if job is None:
            if state in ACTIVE_JOB_STATES:
                self._job = _JobRecord(telemetry.filename, telemetry.progress_percent)
                return JobEvent("started", telemetry.filename, progress=telemetry.progress_percent)
            return None
        if telemetry.filename:
            job.filename = telemetry.filename
        if telemetry.progress_percent is not None:
            job.max_progress = max(job.max_progress or 0.0, telemetry.progress_percent)
        if state in _TERMINAL_STATES:
            self._job = None
            return JobEvent("ended", job.filename, state, progress=job.max_progress)
        if state is JobState.IDLE:
            self._job = None
            if job.link_lost:
                outcome, inferred = JobState.UNKNOWN, False
            elif job.max_progress is not None and job.max_progress >= FINISHED_PROGRESS_THRESHOLD:
                outcome, inferred = JobState.FINISHED, True
            else:
                outcome, inferred = JobState.UNKNOWN, False
            return JobEvent("ended", job.filename, outcome, inferred=inferred, progress=job.max_progress)
        return None


async def probe_connection(
    ip_address: str,
    serial_number: str,
    access_code: str | None,
    *,
    timeout: float = 8.0,
    mqtt_client_factory: Callable[[str], mqtt.Client] | None = None,
) -> dict:
    """Connect + register once, then tear down. Same result shape as
    ``PrinterManager.test_connection`` so the add-printer route can treat both
    providers alike. Sends only registration and read-only queries.
    """
    import asyncio

    client = ElegooCC2Client(
        ip_address,
        serial_number,
        access_code,
        mqtt_client_factory=mqtt_client_factory,
        start_heartbeat=False,
    )
    try:
        client.connect()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if client.state.connected or client.connection is ConnectionStatus.AUTH_FAILED:
                break
            if client.last_connect_error in (CONNECT_ERROR_TOO_MANY_CLIENTS, CONNECT_ERROR_REGISTER):
                break
            # The heartbeat thread is off here, so check the registration
            # timeout ourselves. Seen on a real CC2 with LAN Only Mode off: the
            # broker accepts CONNECT but the firmware never answers api_register.
            with client._lock:
                register_sent_at = client._register_sent_at
            if register_sent_at is not None and client._clock() - register_sent_at > client.REGISTER_TIMEOUT:
                client.last_connect_error = CONNECT_ERROR_REGISTER_TIMEOUT
                break
            await asyncio.sleep(0.2)
        attributes = client._attributes
        return {
            "success": client.state.connected,
            "state": client.state.state if client.state.connected else None,
            "model": attributes.get("machine_model"),
            "reason": None if client.state.connected else (client.last_connect_error or CONNECT_ERROR_UNREACHABLE),
        }
    finally:
        await asyncio.to_thread(client.disconnect)
