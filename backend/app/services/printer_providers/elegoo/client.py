"""MQTT client for the ELEGOO Centauri Carbon 2 (read-only, M2).

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
  * Only :data:`protocol.READ_ONLY_METHODS` can be sent — no job commands.
  * A lost link sets ``connected=False`` and leaves the job state as last
    reported: a disconnect is never read as "print finished".
  * Credentials are never logged; the diagnostic log stores payloads only.
  * A rejected password stops the reconnect loop (AUTH_FAILED): retrying the
    same wrong code cannot succeed and only fills the printer's log.
"""

from __future__ import annotations

import itertools
import json
import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

from backend.app.services.bambu_mqtt import MQTTLogEntry, PrinterState
from backend.app.services.printer_providers.base import (
    CapabilitySet,
    ConnectionStatus,
    NormalizedTelemetry,
    PrinterProvider,
)
from backend.app.services.printer_providers.elegoo import protocol as p
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
    LOG_CAPACITY = 500

    def __init__(
        self,
        ip_address: str,
        serial_number: str,
        access_code: str | None = None,
        model: str | None = None,
        on_state_change: Callable[[PrinterState], None] | None = None,
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
        with self._lock:
            if method == p.METHOD_GET_STATUS:
                self._cache.apply_full(result)
            elif method == p.METHOD_ON_STATUS:
                push_id = payload.get("id") if isinstance(payload.get("id"), int) else None
                merged, refresh = self._cache.apply_delta(push_id, result)
                if merged is None and not refresh:
                    return
            elif method in (p.METHOD_GET_ATTRIBUTES, p.METHOD_ON_ATTRIBUTES):
                self._attributes = dict(result)
            elif method == p.METHOD_GET_CANVAS_STATUS:
                canvas = result.get("canvas_info")
                if isinstance(canvas, dict):
                    self._canvas_info = canvas
            else:
                return
            if isinstance(self._cache.status, dict) and isinstance(self._cache.status.get("canvas_info"), dict):
                self._canvas_info = self._cache.status["canvas_info"]
        if refresh:
            self.request_status_update()
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
