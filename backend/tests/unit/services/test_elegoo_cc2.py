"""ELEGOO Centauri Carbon 2 provider (M2): protocol, normalisation, client, discovery.

No test opens a socket to a printer: the MQTT client is replaced by a recorder
and messages are fed from fixtures (backend/tests/_fixtures/elegoo_cc2.py).
"""

import copy
import json
from types import SimpleNamespace

import pytest

from backend.app.services.bambu_mqtt import PrinterState
from backend.app.services.printer_providers import capabilities_for
from backend.app.services.printer_providers.base import Capability, ConnectionStatus, JobState, SlotDataSource
from backend.app.services.printer_providers.elegoo import protocol as p
from backend.app.services.printer_providers.elegoo.capabilities import is_cc2_model
from backend.app.services.printer_providers.elegoo.client import (
    CONNECT_ERROR_AUTH,
    CONNECT_ERROR_REGISTER_TIMEOUT,
    CONNECT_ERROR_TOO_MANY_CLIENTS,
    ElegooCC2Client,
    probe_connection,
)
from backend.app.services.printer_providers.elegoo.normalizer import (
    apply_to_printer_state,
    canvas_connected,
    job_state_from_machine_status,
    normalize_cc2_status,
    parse_canvas_slots,
)
from backend.tests._fixtures import elegoo_cc2 as fx

# --- protocol --------------------------------------------------------------


class TestProtocol:
    def test_topics_follow_sdk_layout(self):
        t = p.CC2Topics("SN1", "1_PC_1234", "1_PC_1234_req")
        assert t.api_response == "elegoo/SN1/1_PC_1234/api_response"
        assert t.api_status == "elegoo/SN1/api_status"
        assert t.register_response == "elegoo/SN1/1_PC_1234_req/register_response"
        assert t.api_request == "elegoo/SN1/1_PC_1234/api_request"
        assert t.api_register == "elegoo/SN1/api_register"

    def test_client_id_is_sdk_format_and_stable(self):
        cid = p.make_client_id("SN1")
        assert cid.startswith("1_PC_") and 1000 <= int(cid[5:]) <= 9999
        assert p.make_client_id("SN1") == cid  # same identity across reconnects/restarts
        assert p.make_request_id(cid) == f"{cid}_req"

    def test_discovery_request(self):
        assert json.loads(p.discovery_request()) == {"id": 0, "method": 7000}

    def test_read_only_methods_only(self):
        assert json.loads(p.build_request(100000, p.METHOD_GET_STATUS)) == {"id": 100000, "method": 1002, "params": {}}
        for method in (p.METHOD_START_PRINT, p.METHOD_PAUSE_PRINT, p.METHOD_STOP_PRINT, 1023, p.METHOD_SET_AUTO_REFILL):
            with pytest.raises(PermissionError):
                p.build_request(1, method)

    def test_parse_discovery_cc2(self):
        result = p.parse_discovery_response(json.dumps(fx.DISCOVERY_RESPONSE), fx.PRINTER_IP)
        assert result is not None
        assert result.serial_number == fx.SERIAL
        assert result.model == "Centauri Carbon 2"
        assert result.access_code_required is False
        assert result.lan_only is True
        assert result.to_dict()["provider"] == "elegoo"

    def test_parse_discovery_token_and_cloud_flags(self):
        data = copy.deepcopy(fx.DISCOVERY_RESPONSE)
        data["result"].update(token_status=1, lan_status=0)
        result = p.parse_discovery_response(json.dumps(data), fx.PRINTER_IP)
        assert result.access_code_required is True
        assert result.lan_only is False

    @pytest.mark.parametrize(
        "payload",
        [json.dumps(fx.CC1_DISCOVERY_RESPONSE), b"not json", json.dumps({"id": 0, "result": {}}), "M99999", "[]"],
    )
    def test_parse_discovery_rejects_non_cc2(self, payload):
        assert p.parse_discovery_response(payload, fx.PRINTER_IP) is None

    def test_register_response(self):
        assert p.parse_register_response({"client_id": "a", "error": "ok"}, "a").accepted
        assert p.parse_register_response({"client_id": "a", "error": "already registered"}, "a").accepted
        refused = p.parse_register_response({"client_id": "a", "error": "too many clients"}, "a")
        assert not refused.accepted and refused.too_many_clients
        assert p.parse_register_response({"client_id": "b", "error": "ok"}, "a") is None

    def test_error_code_description(self):
        assert p.describe_error_code(1009) == "printer busy"
        assert p.describe_error_code(4242) == "error 4242"


class TestStatusCache:
    def test_delta_without_full_status_is_dropped(self):
        cache = p.CC2StatusCache()
        merged, refresh = cache.apply_delta(42, fx.DELTA_STATUS_PUSH["result"])
        assert merged is None and refresh is True

    def test_delta_merges_into_full(self):
        cache = p.CC2StatusCache()
        cache.apply_full(fx.FULL_STATUS_PRINTING["result"])
        merged, refresh = cache.apply_delta(42, fx.DELTA_STATUS_PUSH["result"])
        assert not refresh
        assert merged["machine_status"] == {"status": 2, "sub_status": 2075, "exception_status": [], "progress": 46}
        assert merged["print_status"]["current_layer"] == 230
        assert merged["print_status"]["total_layer"] == 500  # untouched keys survive
        assert merged["extruder"]["temperature"] == 219.5
        assert merged["extruder"]["target"] == 220

    def test_full_status_is_copied(self):
        source = copy.deepcopy(fx.FULL_STATUS_PRINTING["result"])
        cache = p.CC2StatusCache()
        cache.apply_full(source)
        cache.apply_delta(None, {"extruder": {"temperature": 1.0}})
        assert source["extruder"]["temperature"] == 215.0

    def test_exception_code_replaced_not_merged(self):
        cache = p.CC2StatusCache()
        cache.apply_full({"exception": {"exception_code": {"109": {"time": 1}}}})
        merged, _ = cache.apply_delta(None, {"exception": {"exception_code": {}}})
        assert merged["exception"]["exception_code"] == {}

    def test_five_gaps_request_full_refresh(self):
        cache = p.CC2StatusCache()
        cache.apply_full({"machine_status": {"status": 1}})
        cache.apply_delta(10, {})
        outcomes = [cache.apply_delta(push_id, {}) for push_id in (20, 30, 40, 50, 60)]
        assert [refresh for _, refresh in outcomes] == [False, False, False, False, True]
        assert outcomes[-1][0] is None

    def test_continuous_and_zero_ids_reset_gap_count(self):
        cache = p.CC2StatusCache()
        cache.apply_full({})
        for push_id in (10, 20, 30, 31, 40, 0, 50, 60, 70):
            _, refresh = cache.apply_delta(push_id, {})
            assert not refresh


# --- normaliser --------------------------------------------------------------


class TestJobStateMapping:
    @pytest.mark.parametrize(
        ("status", "sub", "expected"),
        [
            (1, 0, JobState.IDLE),
            (2, 2075, JobState.PRINTING),
            (2, 0, JobState.PRINTING),
            (2, 1082, JobState.PREPARING),
            (2, 1405, JobState.PREPARING),
            (2, 2901, JobState.PREPARING),
            (2, 2501, JobState.PAUSING),
            (2, 2502, JobState.PAUSED),
            (2, 2505, JobState.PAUSED),
            (2, 2401, JobState.RESUMING),
            (2, 2402, JobState.PRINTING),
            (2, 2503, JobState.STOPPING),
            (2, 2504, JobState.CANCELLED),
            (2, 2077, JobState.FINISHED),
            (2, 9999, JobState.PRINTING),  # unknown sub-state, machine still says printing
            (5, 2901, JobState.BUSY),
            (10, 2801, JobState.BUSY),
            (14, 0, JobState.FAILED),
            (15, 0, JobState.PAUSED),
            (99, 0, JobState.UNKNOWN),
            (None, None, JobState.UNKNOWN),
        ],
    )
    def test_mapping(self, status, sub, expected):
        assert job_state_from_machine_status(status, sub) is expected


class TestNormalizer:
    def test_full_status(self):
        t = normalize_cc2_status(
            fx.FULL_STATUS_PRINTING["result"],
            attributes=fx.ATTRIBUTES_RESPONSE["result"],
        )
        assert t.connection is ConnectionStatus.ONLINE
        assert t.job_state is JobState.PRINTING
        assert t.raw_state == "printing:2/2075"
        assert t.filename == "benchy.gcode"
        assert t.progress_percent == 45.0
        assert (t.current_layer, t.total_layers) == (225, 500)
        assert (t.elapsed_seconds, t.remaining_seconds) == (3600, 4400)
        assert (t.nozzle.current, t.nozzle.target) == (215.0, 220.0)
        assert (t.bed.current, t.bed.target) == (58.5, 60.0)
        assert (t.chamber.current, t.chamber.target) == (33.0, None)
        assert t.fans_percent == {"part_cooling": 100, "aux": 70, "chamber": 10, "heatbreak": 100, "controller": 100}
        assert t.model == "Centauri Carbon 2"
        assert t.firmware_version == "1.0.5.2"
        assert t.faults == []

    def test_empty_status_is_unknown_not_zero(self):
        t = normalize_cc2_status({})
        assert t.job_state is JobState.UNKNOWN
        assert t.nozzle.current is None and t.bed.current is None
        assert t.progress_percent is None
        assert t.fans_percent == {}

    def test_print_fields_ignored_when_not_printing(self):
        status = copy.deepcopy(fx.FULL_STATUS_PRINTING["result"])
        status["machine_status"]["status"] = 1
        t = normalize_cc2_status(status)
        assert t.job_state is JobState.IDLE
        assert t.filename is None and t.progress_percent is None and t.current_layer is None

    def test_download_substate_hides_progress(self):
        status = copy.deepcopy(fx.FULL_STATUS_PRINTING["result"])
        status["machine_status"]["sub_status"] = 1081
        t = normalize_cc2_status(status)
        assert t.job_state is JobState.PREPARING
        assert t.progress_percent is None and t.remaining_seconds is None

    def test_faults_from_both_sources(self):
        status = {
            "machine_status": {"status": 2, "sub_status": 2502, "exception_status": [109]},
            "exception": {"exception_code": {"109": {"time": 1}, "1026": {"time": 2}}},
        }
        faults = normalize_cc2_status(status).faults
        assert [(f.code, f.message) for f in faults] == [
            ("109", "filament runout"),
            ("1026", "missing bed-leveling data"),
        ]

    def test_emergency_stop_fault(self):
        t = normalize_cc2_status({"machine_status": {"status": 14, "sub_status": 0}})
        assert t.job_state is JobState.FAILED
        assert [f.code for f in t.faults] == ["emergency_stop"]

    def test_garbage_values_do_not_raise(self):
        status = {
            "machine_status": {"status": "2", "sub_status": "x", "progress": "nan"},
            "extruder": {"temperature": "hot", "target": None},
            "fans": {"fan": {"speed": "fast"}, "aux_fan": []},
            "print_status": [],
        }
        t = normalize_cc2_status(status)
        assert t.job_state is JobState.PRINTING
        assert t.progress_percent is None
        assert t.nozzle.current is None
        assert t.fans_percent == {}


class TestCanvas:
    def test_slots_from_real_world_sample(self):
        slots = parse_canvas_slots(fx.CANVAS_STATUS_RESPONSE["result"]["canvas_info"])
        assert [(s.unit_id, s.slot_index, s.material, s.color_hex, s.name, s.loaded, s.active) for s in slots] == [
            (0, 0, "PLA", "2850DF", "PLA", True, False),
            (0, 1, "PLA", "FFFFFF", "PLA Basic", True, False),
            (0, 2, "PLA", "F32FF8", "PLA Silk", True, False),
            (0, 3, "PLA", "000000", "PLA", True, True),
        ]
        # The protocol does not say whether a colour was detected or typed in.
        assert {s.source for s in slots} == {SlotDataSource.UNKNOWN}
        assert {s.vendor for s in slots} == {"ELEGOO"}

    def test_empty_tray_and_placeholders(self):
        info = {
            "canvas_list": [
                {
                    "canvas_id": 0,
                    "tray_list": [
                        {"tray_id": 0, "filament_type": "PLA", "filament_color": "#FF0000", "status": 0},
                        {"tray_id": 1, "filament_type": "?", "filament_color": "—", "status": 1},
                        {"tray_id": 2},
                        {"brand": "no id"},
                    ],
                }
            ]
        }
        slots = parse_canvas_slots(info)
        assert [(s.slot_index, s.loaded, s.material, s.color_hex) for s in slots] == [
            (0, False, None, None),
            (1, True, None, None),
            (2, None, None, None),
        ]

    def test_canvas_detection(self):
        assert canvas_connected({"external_device": {"type": "0303"}}) is True
        assert canvas_connected({"external_device": {"type": "0"}}) is False
        assert canvas_connected({"external_device": {"type": ""}}) is False
        assert canvas_connected({"external_device": {"type": 0}}) is False
        assert canvas_connected({"external_device": {}}) is None
        assert canvas_connected({}) is None


class TestApplyToPrinterState:
    def test_printer_state_fields(self):
        status = fx.FULL_STATUS_PRINTING["result"]
        state = PrinterState()
        t = normalize_cc2_status(status, attributes=fx.ATTRIBUTES_RESPONSE["result"])
        apply_to_printer_state(state, t, status=status, attributes=fx.ATTRIBUTES_RESPONSE["result"])
        assert state.connected is True
        assert state.state == "RUNNING"
        assert state.subtask_name == "benchy.gcode"
        assert state.progress == 45.0
        assert state.remaining_time == 74  # 4400 s -> minutes, rounded up
        assert (state.layer_num, state.total_layers) == (225, 500)
        assert state.temperatures == {
            "nozzle": 215.0,
            "nozzle_target": 220.0,
            "bed": 58.5,
            "bed_target": 60.0,
            "chamber": 33.0,
        }
        assert state.cooling_fan_speed == 100
        assert state.speed_level == 2  # speed_mode 1 (balanced) -> Bambu "standard"
        assert state.chamber_light is True
        assert state.firmware_version == "1.0.5.2"
        # Raw payload namespaced: Bambu readers look for top-level "ams"/"vt_tray".
        assert set(state.raw_data) == {"elegoo"}
        assert state.hms_errors == []

    @pytest.mark.parametrize(
        ("job", "expected"),
        [
            (JobState.IDLE, "IDLE"),
            (JobState.PAUSED, "PAUSE"),
            (JobState.FINISHED, "FINISH"),
            (JobState.CANCELLED, "FAILED"),
            (JobState.BUSY, "PREPARE"),
            (JobState.UNKNOWN, "unknown"),
        ],
    )
    def test_bambu_state_vocabulary(self, job, expected):
        state = PrinterState()
        t = normalize_cc2_status({})
        t.job_state = job
        apply_to_printer_state(state, t)
        assert state.state == expected


# --- client ------------------------------------------------------------------


class FakeMQTT:
    """Records what the client would send; never touches the network."""

    def __init__(self, client_id):
        self.client_id = client_id
        self.published: list[tuple[str, dict]] = []
        self.subscribed: list[str] = []
        self.credentials = None
        self.connected_to = None
        self.loop_started = False
        self.disconnect_calls = 0
        self.sock = SimpleNamespace(closed=False)
        self.sock.close = lambda: setattr(self.sock, "closed", True)
        self.on_connect = self.on_disconnect = self.on_message = None

    def username_pw_set(self, username, password):
        self.credentials = (username, password)

    def reconnect_delay_set(self, min_delay, max_delay):
        self.reconnect_delay = (min_delay, max_delay)

    def connect_async(self, host, port, keepalive):
        self.connected_to = (host, port, keepalive)

    def loop_start(self):
        self.loop_started = True

    def loop_stop(self):
        self.loop_started = False

    def disconnect(self):
        self.disconnect_calls += 1

    def subscribe(self, topic, qos):
        self.subscribed.append(topic)

    def publish(self, topic, payload, qos, retain):
        self.published.append((topic, json.loads(payload)))
        return SimpleNamespace(rc=0)

    def socket(self):
        return self.sock

    def methods_sent(self):
        return [
            msg.get("method") for topic, msg in self.published if topic.endswith("/api_request") and "method" in msg
        ]


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def harness():
    fakes: list[FakeMQTT] = []
    states: list[PrinterState] = []
    clock = Clock()

    def factory(client_id):
        fake = FakeMQTT(client_id)
        fakes.append(fake)
        return fake

    client = ElegooCC2Client(
        fx.PRINTER_IP,
        fx.SERIAL,
        access_code=None,
        model="Centauri Carbon 2",
        on_state_change=lambda s: states.append(s),
        mqtt_client_factory=factory,
        clock=clock,
        start_heartbeat=False,
    )
    return SimpleNamespace(client=client, fakes=fakes, states=states, clock=clock)


def _connect_and_register(h):
    h.client.connect()
    fake = h.fakes[-1]
    h.client._on_connect(fake, None, None, SimpleNamespace(value=0))
    h.client.handle_message(h.client.topics.register_response, {"client_id": h.client.client_id, **fx.REGISTER_OK})
    return fake


class TestClient:
    def test_connect_uses_sdk_transport_and_default_code(self, harness):
        harness.client.connect()
        fake = harness.fakes[0]
        assert fake.connected_to == (fx.PRINTER_IP, 1883, 60)
        assert fake.credentials == ("elegoo", p.DEFAULT_ACCESS_CODE)
        assert fake.loop_started
        assert harness.client.connection is ConnectionStatus.CONNECTING
        assert harness.client.state.connected is False

    def test_access_code_used_when_set(self):
        fakes = []
        client = ElegooCC2Client(
            fx.PRINTER_IP,
            fx.SERIAL,
            access_code="987654",
            mqtt_client_factory=lambda cid: fakes.append(FakeMQTT(cid)) or fakes[-1],
            start_heartbeat=False,
        )
        client.connect()
        assert fakes[0].credentials == ("elegoo", "987654")

    def test_on_connect_subscribes_and_registers(self, harness):
        harness.client.connect()
        fake = harness.fakes[0]
        harness.client._on_connect(fake, None, None, SimpleNamespace(value=0))
        assert fake.subscribed == list(harness.client.topics.subscriptions())
        topic, msg = fake.published[-1]
        assert topic == f"elegoo/{fx.SERIAL}/api_register"
        assert msg == {"client_id": harness.client.client_id, "request_id": harness.client.request_id}
        assert fake.methods_sent() == []  # nothing before registration

    def test_registration_brings_printer_online_and_queries_read_only(self, harness):
        fake = _connect_and_register(harness)
        assert harness.client.connection is ConnectionStatus.ONLINE
        assert harness.client.state.connected is True
        assert fake.methods_sent() == [1002, 1001, 2005]
        assert set(fake.methods_sent()) <= p.READ_ONLY_METHODS
        assert harness.states and harness.states[-1].connected

    def test_register_reply_for_other_client_ignored(self, harness):
        harness.client.connect()
        fake = harness.fakes[0]
        harness.client._on_connect(fake, None, None, SimpleNamespace(value=0))
        harness.client.handle_message(
            harness.client.topics.register_response, {"client_id": "1_PC_0000", "error": "ok"}
        )
        assert harness.client.connection is ConnectionStatus.CONNECTING

    def test_too_many_clients(self, harness):
        harness.client.connect()
        fake = harness.fakes[0]
        harness.client._on_connect(fake, None, None, SimpleNamespace(value=0))
        harness.client.handle_message(
            harness.client.topics.register_response, {"client_id": harness.client.client_id, **fx.REGISTER_TOO_MANY}
        )
        assert harness.client.last_connect_error == CONNECT_ERROR_TOO_MANY_CLIENTS
        assert harness.client.connection is ConnectionStatus.OFFLINE
        # Registration is retried after the timeout by dropping the socket.
        harness.clock.now += p.REGISTER_TIMEOUT_SECONDS + 1
        harness.client.heartbeat_tick()
        assert fake.sock.closed

    def test_registration_timeout_drops_socket(self, harness):
        harness.client.connect()
        fake = harness.fakes[0]
        harness.client._on_connect(fake, None, None, SimpleNamespace(value=0))
        harness.clock.now += p.REGISTER_TIMEOUT_SECONDS + 1
        harness.client.heartbeat_tick()
        assert harness.client.last_connect_error == CONNECT_ERROR_REGISTER_TIMEOUT
        assert fake.sock.closed

    @pytest.mark.parametrize("code", [4, 5, 134, 135])
    def test_auth_failure_stops_reconnecting(self, harness, code):
        harness.client.connect()
        fake = harness.fakes[0]
        harness.client._on_connect(fake, None, None, SimpleNamespace(value=code))
        assert harness.client.connection is ConnectionStatus.AUTH_FAILED
        assert harness.client.last_connect_error == CONNECT_ERROR_AUTH
        assert fake.disconnect_calls == 1
        harness.client._on_disconnect(fake, None)
        assert harness.client.connection is ConnectionStatus.AUTH_FAILED

    def test_status_flow_full_then_delta(self, harness):
        _connect_and_register(harness)
        c = harness.client
        c.handle_message(c.topics.api_response, {**fx.FULL_STATUS_PRINTING, "method": 1002})
        assert c.state.state == "RUNNING"
        assert c.state.progress == 45.0
        c.handle_message(c.topics.api_status, fx.DELTA_STATUS_PUSH)
        assert c.state.progress == 46.0
        assert c.state.layer_num == 230
        assert c.state.temperatures["nozzle"] == 219.5
        assert c.telemetry().job_state is JobState.PRINTING

    def test_attributes_and_canvas(self, harness):
        _connect_and_register(harness)
        c = harness.client
        c.handle_message(c.topics.api_response, fx.ATTRIBUTES_RESPONSE)
        c.handle_message(c.topics.api_response, fx.CANVAS_STATUS_RESPONSE)
        assert c.state.firmware_version == "1.0.5.2"
        assert c.telemetry().job_state is JobState.UNKNOWN  # no status yet: not invented
        c.handle_message(c.topics.api_response, {**fx.FULL_STATUS_PRINTING, "method": 1002})
        assert c.state.firmware_version == "1.0.5.2"
        assert len(c.telemetry().multicolor_slots) == 4
        assert c.state.raw_data["elegoo"]["canvas_info"]["active_tray_id"] == 3

    def test_delta_before_full_status_requests_full(self, harness):
        fake = _connect_and_register(harness)
        before = len(fake.methods_sent())
        harness.client.handle_message(harness.client.topics.api_status, fx.DELTA_STATUS_PUSH)
        assert fake.methods_sent()[before:] == [1002]
        assert harness.client.telemetry().job_state is JobState.UNKNOWN

    def test_error_response_does_not_overwrite_status(self, harness):
        _connect_and_register(harness)
        c = harness.client
        c.handle_message(c.topics.api_response, {**fx.FULL_STATUS_PRINTING, "method": 1002})
        c.handle_message(c.topics.api_response, {"id": 7, "method": 1002, "result": {"error_code": 1009}})
        assert c.state.state == "RUNNING"

    def test_messages_ignored_before_registration(self, harness):
        harness.client.connect()
        harness.client.handle_message(harness.client.topics.api_status, fx.FULL_STATUS_PRINTING)
        assert harness.client.state.state == "unknown"

    def test_disconnect_does_not_finish_the_print(self, harness):
        """A lost link is OFFLINE with the job state as last reported — never FINISH."""
        fake = _connect_and_register(harness)
        c = harness.client
        c.handle_message(c.topics.api_response, {**fx.FULL_STATUS_PRINTING, "method": 1002})
        c._on_disconnect(fake, None)
        assert c.state.connected is False
        assert c.connection is ConnectionStatus.OFFLINE
        assert c.state.state == "RUNNING"
        assert c.telemetry().job_state is JobState.PRINTING
        assert harness.states[-1].connected is False

    def test_reconnect_resets_cache_and_reregisters(self, harness):
        fake = _connect_and_register(harness)
        c = harness.client
        c.handle_message(c.topics.api_response, {**fx.FULL_STATUS_PRINTING, "method": 1002})
        c._on_disconnect(fake, None)
        c._on_connect(fake, None, None, SimpleNamespace(value=0))
        assert c.connection is ConnectionStatus.CONNECTING
        # job picture kept until the printer reports again
        assert c.state.state == "RUNNING"
        assert [t for t, _ in fake.published].count(c.topics.api_register) == 2
        c.handle_message(c.topics.register_response, {"client_id": c.client_id, "error": "ok"})
        idle = copy.deepcopy(fx.FULL_STATUS_PRINTING)
        idle["result"]["machine_status"] = {"status": 1, "sub_status": 0, "progress": 0}
        c.handle_message(c.topics.api_response, {**idle, "method": 1002})
        assert c.state.state == "IDLE"

    def test_stale_link_goes_offline_and_drops_socket(self, harness):
        fake = _connect_and_register(harness)
        c = harness.client
        c.handle_message(c.topics.api_response, {**fx.FULL_STATUS_PRINTING, "method": 1002})
        harness.clock.now += p.HEARTBEAT_TIMEOUT_SECONDS - 1
        assert c.check_staleness() is True
        harness.clock.now += 2
        assert c.check_staleness() is False
        assert fake.sock.closed
        assert c.state.state == "RUNNING"

    def test_heartbeat_sends_ping_when_registered(self, harness):
        fake = _connect_and_register(harness)
        harness.client.heartbeat_tick()
        assert fake.published[-1] == (harness.client.topics.api_request, {"type": "PING"})

    def test_pong_counts_as_traffic(self, harness):
        _connect_and_register(harness)
        c = harness.client
        harness.clock.now += 60
        c.handle_message(c.topics.api_response, {"type": "PONG"})
        harness.clock.now += 30
        assert c.check_staleness() is True

    def test_request_refuses_job_commands(self, harness):
        _connect_and_register(harness)
        with pytest.raises(PermissionError):
            harness.client.request(p.METHOD_START_PRINT, {"filename": "x.gcode"})

    def test_diagnostic_log_has_no_credentials(self, harness):
        harness.client.enable_logging(True)
        fake = _connect_and_register(harness)
        harness.client.handle_message(harness.client.topics.api_response, fx.ATTRIBUTES_RESPONSE)
        dump = json.dumps([entry.payload for entry in harness.client.get_logs()])
        assert p.DEFAULT_ACCESS_CODE not in dump
        assert fake.credentials[1] not in dump
        assert {e.direction for e in harness.client.get_logs()} == {"in", "out"}
        harness.client.clear_logs()
        assert harness.client.get_logs() == []

    def test_disconnect_tears_down(self, harness):
        fake = _connect_and_register(harness)
        harness.client.disconnect()
        assert fake.disconnect_calls == 1 and not fake.loop_started
        assert harness.client.state.connected is False

    def test_capabilities(self, harness):
        caps = harness.client.capabilities
        assert caps.supports(Capability.TEMPERATURES)
        assert caps.supports(Capability.MULTICOLOR_STATUS)
        for missing in (
            Capability.JOB_START,
            Capability.JOB_PAUSE_RESUME,
            Capability.JOB_STOP,
            Capability.FILE_UPLOAD,
            Capability.QUEUE_DISPATCH,
            Capability.AUTO_ARCHIVE,
            Capability.AMS_MANAGEMENT,
        ):
            assert not caps.supports(missing), missing
            assert caps.notes[missing]


class TestRealHardwareCapture:
    """Messages captured on our own Centauri 2 (fw 02.01.01.04), see fixtures."""

    def test_discovery(self):
        result = p.parse_discovery_response(json.dumps(fx.REAL_DISCOVERY_RESPONSE), fx.PRINTER_IP)
        assert result.model == "Centauri 2"
        assert result.access_code_required is True
        assert result.lan_only is True
        assert is_cc2_model(result.model)

    def test_idle_status(self):
        t = normalize_cc2_status(
            fx.REAL_FULL_STATUS_IDLE["result"],
            attributes=fx.REAL_ATTRIBUTES_RESPONSE["result"],
            canvas_info=fx.REAL_CANVAS_STATUS_RESPONSE["result"]["canvas_info"],
            connection=ConnectionStatus.ONLINE,
            model="Centauri 2",
        )
        assert t.job_state is JobState.IDLE
        assert (t.nozzle.current, t.bed.current) == (39.0, 21.0)
        assert t.chamber.current is None  # no ztemperature_sensor on this firmware
        assert t.fans_percent["controller"] == 100  # 255.0 PWM
        assert t.firmware_version == "02.01.01.04"
        assert t.faults == []
        assert [s.slot_index for s in t.multicolor_slots] == [0, 1, 2, 3]
        assert [s.loaded for s in t.multicolor_slots] == [True, False, False, False]
        assert all(s.material is None and s.color_hex is None for s in t.multicolor_slots)

    def test_chamber_not_advertised(self):
        caps = capabilities_for("elegoo", "Centauri 2")
        assert not caps.supports(Capability.CHAMBER_TEMPERATURE)
        assert caps.notes[Capability.CHAMBER_TEMPERATURE]

    def test_consecutive_push_ids_need_no_refresh(self):
        cache = p.CC2StatusCache()
        cache.apply_full(fx.REAL_FULL_STATUS_IDLE["result"])
        for push in fx.REAL_DELTA_PUSHES:
            merged, needs_refresh = cache.apply_delta(push["id"], push["result"])
            assert merged is not None and not needs_refresh
        assert cache.status["heater_bed"]["temperature"] == 20


class TestCapabilityModels:
    def test_cc2_model_detection(self):
        assert is_cc2_model("Centauri Carbon 2")
        assert is_cc2_model("ELEGOO Centauri 2 Combo")
        assert not is_cc2_model("Centauri Carbon")
        assert not is_cc2_model("Neptune 4 Pro")

    def test_non_cc2_elegoo_gets_nothing(self):
        """Gen-1 Centauri Carbon (SDCP) and Neptune (Moonraker) must not be driven as a CC2."""
        assert capabilities_for("elegoo", "Centauri Carbon").supported == frozenset()
        assert capabilities_for("elegoo", "Neptune 4").supported == frozenset()
        assert capabilities_for("elegoo", None).supports(Capability.STATUS)


@pytest.mark.asyncio
async def test_probe_connection_success():
    def factory(client_id):
        fake = FakeMQTT(client_id)

        def deliver(topic_suffix, payload):
            topic = next(t for t in fake.subscribed if t.endswith(topic_suffix))
            fake.on_message(fake, None, SimpleNamespace(topic=topic, payload=json.dumps(payload).encode()))

        def connect_async(host, port, keepalive):
            # Simulated broker: accept, then answer registration and attributes.
            fake.on_connect(fake, None, None, SimpleNamespace(value=0))
            deliver("register_response", {"client_id": client_id, "error": "ok"})
            deliver("api_response", fx.ATTRIBUTES_RESPONSE)

        fake.connect_async = connect_async
        return fake

    result = await probe_connection(fx.PRINTER_IP, fx.SERIAL, None, timeout=1.0, mqtt_client_factory=factory)
    assert result == {"success": True, "state": "unknown", "model": "Centauri Carbon 2", "reason": None}


@pytest.mark.asyncio
async def test_probe_connection_timeout_reports_unreachable():
    result = await probe_connection(
        fx.PRINTER_IP, fx.SERIAL, "000000", timeout=0.5, mqtt_client_factory=lambda cid: FakeMQTT(cid)
    )
    assert result["success"] is False
    assert result["reason"] == "unreachable"


@pytest.mark.asyncio
async def test_probe_connection_reports_register_timeout(monkeypatch):
    """Real CC2 with LAN Only Mode off: CONNACK ok, api_register never answered."""
    monkeypatch.setattr(ElegooCC2Client, "REGISTER_TIMEOUT", 0.1)

    def factory(client_id):
        fake = FakeMQTT(client_id)
        fake.connect_async = lambda host, port, keepalive: fake.on_connect(fake, None, None, SimpleNamespace(value=0))
        return fake

    result = await probe_connection(fx.PRINTER_IP, fx.SERIAL, None, timeout=2.0, mqtt_client_factory=factory)
    assert result["success"] is False
    assert result["reason"] == "register_timeout"
