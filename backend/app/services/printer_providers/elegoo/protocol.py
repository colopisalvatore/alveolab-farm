"""ELEGOO Centauri Carbon 2 (CC2) LAN protocol — wire constants and pure helpers.

Every constant and message shape below is traced to the official ELEGOO SDK
(``elegooofficial/elegoo-link``, Apache-2.0, commit ``ec2d2269``); paths in the
comments are relative to that repository. Facts that only the community
integration (``nict41/HACS-elegoo-integration``) documents are marked
"community". See docs/alveolab-farm/ELEGOO_PROTOCOL_RESEARCH.md.

Nothing here does I/O: building and parsing messages is kept apart from the
MQTT client so it can be tested against fixtures without a printer.

The CC2 is *not* the first-generation Centauri Carbon (SDCP over WebSocket on
port 3030) and not a Moonraker/Klipper machine — the SDK has a separate adapter
for each (``src/lan/core/printer_factory.cpp:17-44``).
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass

# --- Transport ---------------------------------------------------------------

# UDP discovery port (src/lan/adapters/elegoo_cc2_adapters.h:164).
DISCOVERY_PORT = 52700
# MQTT broker runs on the printer, plain TCP, no TLS
# (src/lan/adapters/elegoo_fdm_cc2/elegoo_fdm_cc2_protocol.cpp:20-28).
MQTT_PORT = 1883
# Fixed protocol username (elegoo_fdm_cc2_protocol.cpp:143-190).
MQTT_USERNAME = "elegoo"
# Factory default password, used by the SDK whenever no access code is given
# (printer reports token_status == 0): CC2_DEFAULT_TOKEN in
# elegoo_fdm_cc2_http_transfer.cpp:12 and the fallbacks in
# elegoo_fdm_cc2_protocol.cpp:151-186. A public protocol constant, not a secret.
DEFAULT_ACCESS_CODE = "123456"  # noqa: S105
# MQTT keepalive (src/lan/protocols/mqtt_protocol.cpp:159-173).
MQTT_KEEPALIVE_SECONDS = 60
# Application heartbeat: PING every 10 s, link considered dead after 65 s
# without PONG (elegoo_fdm_cc2_protocol.cpp:324-355).
HEARTBEAT_INTERVAL_SECONDS = 10
HEARTBEAT_TIMEOUT_SECONDS = 65
# Registration reply wait: SDK default 3 s, overridden by the 5 s connection
# timeout (elegoo_fdm_cc2_protocol.cpp:309-312, mqtt_protocol.cpp:279-283).
REGISTER_TIMEOUT_SECONDS = 5
# SDK reconnect loop period (src/lan/protocols/connection_manager_base.cpp:195-238).
RECONNECT_DELAY_SECONDS = 5

# --- Methods -----------------------------------------------------------------
# COMMAND_MAPPING_TABLE, elegoo_fdm_cc2_message_adapter.cpp:1391-1423.
METHOD_GET_ATTRIBUTES = 1001
METHOD_GET_STATUS = 1002
METHOD_START_PRINT = 1020
METHOD_PAUSE_PRINT = 1021
METHOD_STOP_PRINT = 1022
METHOD_SET_AUTO_REFILL = 2004
METHOD_GET_CANVAS_STATUS = 2005
METHOD_ON_STATUS = 6000  # push, delta
METHOD_ON_ATTRIBUTES = 6008  # push
METHOD_DISCOVERY = 7000  # UDP only

# Methods this build may send. Read-only queries only (M2): nothing in this set
# changes what the machine is doing. Job control (1020/1021/1022/1023) is added
# in M4 behind explicit capability flags and a hardware test sign-off.
READ_ONLY_METHODS = frozenset({METHOD_GET_ATTRIBUTES, METHOD_GET_STATUS, METHOD_GET_CANVAS_STATUS})

# --- Status codes ------------------------------------------------------------
# machine_status.status (elegoo_fdm_cc2_message_adapter.cpp:644-957).
STATUS_INITIALIZING = 0
STATUS_IDLE = 1
STATUS_PRINTING = 2
STATUS_FILAMENT_OPERATING = (3, 4)
STATUS_AUTO_LEVELING = 5
STATUS_PID_CALIBRATING = 6
STATUS_RESONANCE_TESTING = 7
STATUS_SELF_CHECKING = 8
STATUS_UPDATING = 9
STATUS_HOMING = 10
STATUS_FILE_TRANSFERRING = 11
STATUS_VIDEO_COMPOSING = 12
STATUS_EXTRUDER_OPERATING = 13
STATUS_EMERGENCY_STOP = 14
STATUS_POWER_LOSS_RECOVERY = 15

MACHINE_STATUS_NAMES = {
    0: "initializing",
    1: "idle",
    2: "printing",
    3: "filament_operating",
    4: "filament_operating",
    5: "auto_leveling",
    6: "pid_calibrating",
    7: "resonance_testing",
    8: "self_checking",
    9: "updating",
    10: "homing",
    11: "file_transferring",
    12: "video_composing",
    13: "extruder_operating",
    14: "emergency_stop",
    15: "power_loss_recovery",
}

# machine_status.sub_status while status == 2 (adapter lines 655-724).
PRINT_SUB_DOWNLOADING = frozenset({1081, 1082, 1086})
PRINT_SUB_EXTRUDER_PREHEATING = frozenset({1045, 1096})
PRINT_SUB_BED_PREHEATING = frozenset({1405, 1906})
PRINT_SUB_HOMING = frozenset({2801, 2802})
PRINT_SUB_AUTO_LEVELING = frozenset({2901, 2902})
PRINT_SUB_PAUSING = 2501
PRINT_SUB_PAUSED = frozenset({2502, 2505})
PRINT_SUB_RESUMING = 2401
PRINT_SUB_RESUMED = 2402
PRINT_SUB_PRINTING = 2075
PRINT_SUB_COMPLETED = 2077
PRINT_SUB_STOPPING = 2503
PRINT_SUB_STOPPED = 2504
PRINT_SUB_NONE = frozenset({0, 1041})

# result.error_code (adapter comment table 1427-1474).
ERROR_CODES = {
    0: "ok",
    109: "filament runout",
    1000: "token validation failed",
    1001: "unknown interface",
    1002: "open folder failed",
    1003: "invalid parameter",
    1004: "file write failed",
    1005: "update token failed",
    1006: "send update to MOS failed",
    1007: "delete failed",
    1008: "empty response",
    1009: "printer busy",
    1010: "not printing",
    1011: "copy failed",
    1012: "task not found",
    1013: "database error",
    1021: "print file not found",
    1026: "missing bed-leveling data",
    9000: "file offset mismatch",
    9001: "open for write failed",
    9002: "write failed",
    9003: "seek failed",
    9004: "MD5 check failed",
    9005: "no cancellation required",
    9006: "cancel failed",
    9007: "upload path missing",
    9008: "MD5 system error",
    9009: "MD5 file read error",
    9999: "unknown error",
}

# Registration replies (elegoo_fdm_cc2_protocol.cpp:257-307).
REGISTER_OK = "ok"
REGISTER_TOO_MANY_CLIENTS = "too many clients"
# community: firmware 02.01.00.00 answers this to a re-registration of a live
# client id; treated as success there (HA cc2/const.py:98-102).
REGISTER_ALREADY_REGISTERED = "already registered"


# --- Topics ------------------------------------------------------------------
# elegoo_fdm_cc2_protocol.cpp:194-237.


@dataclass(frozen=True)
class CC2Topics:
    serial: str
    client_id: str
    request_id: str

    @property
    def api_response(self) -> str:
        return f"elegoo/{self.serial}/{self.client_id}/api_response"

    @property
    def api_status(self) -> str:
        return f"elegoo/{self.serial}/api_status"

    @property
    def register_response(self) -> str:
        return f"elegoo/{self.serial}/{self.request_id}/register_response"

    @property
    def api_request(self) -> str:
        return f"elegoo/{self.serial}/{self.client_id}/api_request"

    @property
    def api_register(self) -> str:
        return f"elegoo/{self.serial}/api_register"

    def subscriptions(self) -> tuple[str, str, str]:
        return (self.api_response, self.api_status, self.register_response)


def make_client_id(serial: str) -> str:
    """Client id in the SDK's ``1_PC_<1000..9999>`` format.

    The SDK draws the number at random per process (elegoo_fdm_cc2_protocol.cpp:13-17).
    Here it is derived from the serial so a restart or reconnect re-uses the
    same identity instead of taking another of the printer's few client slots
    (community: ~4 concurrent clients, CC2_PROTOCOL.md:71).
    """
    digest = int(hashlib.sha256(serial.encode("utf-8")).hexdigest(), 16)
    return f"1_PC_{1000 + digest % 9000}"


def make_request_id(client_id: str) -> str:
    return f"{client_id}_req"  # elegoo_fdm_cc2_protocol.cpp:17


# --- Message builders --------------------------------------------------------


def discovery_request() -> bytes:
    """UDP discovery probe (elegoo_fdm_cc2_discovery_strategy.cpp:10-14)."""
    return json.dumps({"id": 0, "method": METHOD_DISCOVERY}).encode("utf-8")


def register_request(client_id: str, request_id: str) -> str:
    return json.dumps({"client_id": client_id, "request_id": request_id})


def heartbeat_ping() -> str:
    return json.dumps({"type": "PING"})


def build_request(request_id: int, method: int, params: dict | None = None) -> str:
    """``{"id", "method", "params"}`` envelope (adapter lines 25-46).

    Refuses any method outside :data:`READ_ONLY_METHODS`: this build must not
    be able to start, pause or stop a job by accident.
    """
    if method not in READ_ONLY_METHODS:
        raise PermissionError(f"CC2 method {method} is not enabled in this build (read-only)")
    return json.dumps({"id": request_id, "method": method, "params": params or {}})


# --- Parsers -----------------------------------------------------------------


@dataclass(frozen=True)
class CC2DiscoveryResult:
    ip_address: str
    serial_number: str
    name: str | None
    model: str | None
    # token_status == 1: the printer requires its access code (discovery strategy :56-79).
    access_code_required: bool
    # lan_status == 1: LAN-only mode. 0 / missing = cloud mode, which the SDK
    # handles with an app PIN instead (strategy :81-102); community reports
    # LAN control needs LAN-only mode enabled.
    lan_only: bool

    def to_dict(self) -> dict:
        return {
            "ip_address": self.ip_address,
            "serial_number": self.serial_number,
            "name": self.name,
            "model": self.model,
            "access_code_required": self.access_code_required,
            "lan_only": self.lan_only,
            "provider": "elegoo",
        }


def _flag(value) -> bool:
    if isinstance(value, bool):
        return value
    try:
        return int(value) == 1
    except (TypeError, ValueError):
        return False


def parse_discovery_response(payload: bytes | str, ip_address: str) -> CC2DiscoveryResult | None:
    """Parse a reply to :func:`discovery_request`; None if it is not a CC2.

    A CC2 reply has lowercase ``id`` + ``result`` keys; the first-generation
    Centauri Carbon answers with ``Id`` + ``Data`` and is rejected here
    (elegoo_fdm_cc2_discovery_strategy.cpp:26 vs elegoo_fdm_cc_discovery_strategy.cpp:27).
    """
    try:
        data = json.loads(payload)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or "id" not in data or not isinstance(data.get("result"), dict):
        return None
    result = data["result"]
    serial = str(result.get("sn") or "").strip()
    if not serial:
        return None
    return CC2DiscoveryResult(
        ip_address=ip_address,
        serial_number=serial,
        name=result.get("host_name") or None,
        model=result.get("machine_model") or None,
        access_code_required=_flag(result.get("token_status")),
        lan_only=_flag(result.get("lan_status")),
    )


@dataclass(frozen=True)
class RegisterOutcome:
    accepted: bool
    error: str | None
    too_many_clients: bool = False


def parse_register_response(payload: dict, client_id: str) -> RegisterOutcome | None:
    """Interpret ``register_response``; None if it is addressed to another client."""
    if payload.get("client_id") != client_id:
        return None
    error = str(payload.get("error") or "")
    lowered = error.lower()
    if lowered in (REGISTER_OK, REGISTER_ALREADY_REGISTERED):
        return RegisterOutcome(accepted=True, error=None)
    return RegisterOutcome(
        accepted=False,
        error=error or "unknown registration error",
        too_many_clients=REGISTER_TOO_MANY_CLIENTS in lowered,
    )


def describe_error_code(code: int) -> str:
    return ERROR_CODES.get(code, f"error {code}")


# --- Status cache (full status + delta pushes) -------------------------------


def _deep_merge(base: dict, delta: dict) -> dict:
    """Merge *delta* into *base* in place (adapter mergeStatusUpdateJson, 1303-1348).

    Objects merge recursively, scalars and arrays are replaced, and
    ``exception_code`` is replaced as a whole — a cleared fault must disappear.
    """
    for key, value in delta.items():
        if key == "exception_code":
            base[key] = copy.deepcopy(value)
        elif isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = copy.deepcopy(value)
    return base


class CC2StatusCache:
    """Full-status cache fed by 1002 responses and 6000 delta pushes.

    Mirrors the SDK: a delta without a cached full status is dropped (it would
    describe only part of the machine), and after
    :attr:`MAX_GAPS` consecutive non-continuous push ids the caller is told to
    ask for a full status again (adapter lines 549-637, 1277-1292).
    """

    MAX_GAPS = 5

    def __init__(self) -> None:
        self._full: dict | None = None
        self._last_push_id: int | None = None
        self._gaps = 0

    @property
    def status(self) -> dict | None:
        return self._full

    def reset(self) -> None:
        """Forget everything — on (re)connect the SDK starts over (elegoo_fdm_cc2_printer.cpp:54-61)."""
        self._full = None
        self._last_push_id = None
        self._gaps = 0

    def apply_full(self, result: dict) -> dict:
        self._full = copy.deepcopy(result)
        self._gaps = 0
        return self._full

    def apply_delta(self, push_id: int | None, result: dict) -> tuple[dict | None, bool]:
        """Merge a 6000 push. Returns ``(merged_status_or_None, needs_full_refresh)``."""
        if push_id is not None:
            continuous = self._last_push_id is None or push_id == 0 or push_id == self._last_push_id + 1
            self._last_push_id = push_id
            if continuous:
                self._gaps = 0
            else:
                self._gaps += 1
                if self._gaps >= self.MAX_GAPS:
                    self._gaps = 0
                    return None, True
        if self._full is None:
            return None, True
        return _deep_merge(self._full, result), False
