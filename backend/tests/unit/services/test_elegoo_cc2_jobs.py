"""ELEGOO Centauri Carbon 2, M4: job commands, job tracking and file upload.

No test reaches a printer: MQTT is a recorder that can answer, HTTP is an
``httpx.MockTransport``. The rules under test come from the farm's brief:
never send the same start twice because of a reconnect, never read a
disconnect as the end of a print, an unknown outcome stays unknown.
"""

import copy
import hashlib
import logging
from types import SimpleNamespace

import httpx
import pytest

from backend.app.services.printer_providers.base import Capability, JobState
from backend.app.services.printer_providers.elegoo import protocol as p
from backend.app.services.printer_providers.elegoo.capabilities import (
    ENABLE_ENV,
    elegoo_capabilities,
    enabled_job_capabilities,
)
from backend.app.services.printer_providers.elegoo.client import (
    COMMAND_ACCEPTED,
    COMMAND_NO_REPLY,
    COMMAND_NOT_SENT,
    COMMAND_REFUSED,
    COMMAND_REJECTED,
    ElegooCC2Client,
)
from backend.app.services.printer_providers.elegoo.upload import (
    UPLOAD_ERROR_AUTH,
    UPLOAD_ERROR_BAD_FILE,
    UPLOAD_ERROR_REJECTED,
    UPLOAD_ERROR_UNREACHABLE,
    upload_file,
)
from backend.tests._fixtures import elegoo_cc2 as fx
from backend.tests.unit.services.test_elegoo_cc2 import Clock, FakeMQTT

ALL_JOB = "file_upload,job_start,job_pause_resume,job_stop"

# --- protocol ----------------------------------------------------------------


class TestJobProtocol:
    def test_job_commands_have_their_own_builder(self):
        for method in (p.METHOD_START_PRINT, p.METHOD_PAUSE_PRINT, p.METHOD_STOP_PRINT, p.METHOD_RESUME_PRINT):
            assert p.build_job_request(7, method)
            with pytest.raises(PermissionError):
                p.build_request(7, method)
        for method in (p.METHOD_GET_STATUS, p.METHOD_SET_AUTO_REFILL, 1026, 1043):
            with pytest.raises(PermissionError):
                p.build_job_request(7, method)

    def test_start_params_match_the_sdk(self):
        assert p.start_print_params("benchy.gcode") == {
            "storage_media": "local",
            "filename": "benchy.gcode",
            "config": {
                "delay_video": False,
                "printer_check": False,
                "print_layout": "A",
                "bedlevel_force": False,
                "slot_map": [],
            },
        }
        params = p.start_print_params(
            "x.3mf", plate_layout="B", auto_bed_leveling=True, slot_map=[{"t": 0, "canvas_id": 0, "tray_id": 2}]
        )
        assert params["config"]["print_layout"] == "B"
        assert params["config"]["printer_check"] is True
        assert params["config"]["slot_map"] == [{"t": 0, "canvas_id": 0, "tray_id": 2}]
        with pytest.raises(ValueError):
            p.start_print_params("x.gcode", plate_layout="C")

    @pytest.mark.parametrize("name", ["benchy.gcode", "Svuotatasche KTM 220.3MF", "a-b_c.gcode"])
    def test_machine_file_names_accepted(self, name):
        assert p.check_machine_filename(name) == name

    @pytest.mark.parametrize(
        "name",
        ["", " x.gcode", "../x.gcode", "dir/x.gcode", "dir\\x.gcode", ".x.gcode", "x.stl", "x.gcode\n", "a" * 201],
    )
    def test_machine_file_names_refused(self, name):
        with pytest.raises(ValueError):
            p.check_machine_filename(name)


# --- capabilities --------------------------------------------------------------


class TestJobCapabilities:
    def test_read_only_by_default(self, monkeypatch):
        monkeypatch.delenv(ENABLE_ENV, raising=False)
        caps = elegoo_capabilities("Centauri 2")
        for capability in (
            Capability.FILE_UPLOAD,
            Capability.JOB_START,
            Capability.JOB_PAUSE_RESUME,
            Capability.JOB_STOP,
            Capability.QUEUE_DISPATCH,
        ):
            assert not caps.supports(capability)

    def test_switched_on_one_by_one(self, monkeypatch):
        monkeypatch.setenv(ENABLE_ENV, "file_upload")
        assert enabled_job_capabilities() == {Capability.FILE_UPLOAD}
        monkeypatch.setenv(ENABLE_ENV, " file_upload , JOB_START ")
        assert enabled_job_capabilities() == {Capability.FILE_UPLOAD, Capability.JOB_START, Capability.QUEUE_DISPATCH}

    def test_only_job_capabilities_can_be_switched(self, monkeypatch):
        monkeypatch.setenv(ENABLE_ENV, "camera,ams_management,queue_dispatch,nonsense")
        assert enabled_job_capabilities() == frozenset()

    def test_other_elegoo_models_stay_empty(self, monkeypatch):
        monkeypatch.setenv(ENABLE_ENV, ALL_JOB)
        assert elegoo_capabilities("Neptune 4").supported == frozenset()


# --- client: commands ------------------------------------------------------------


class AnsweringMQTT(FakeMQTT):
    """FakeMQTT that records QoS and can answer job commands like the printer."""

    def __init__(self, client_id):
        super().__init__(client_id)
        self.qos: list[tuple[int | None, int]] = []
        self.answer = None  # callable(request) -> result dict | None
        self.client = None

    def publish(self, topic, payload, qos, retain):
        info = super().publish(topic, payload, qos, retain)
        request = self.published[-1][1]
        self.qos.append((request.get("method"), qos))
        if self.answer is not None and request.get("method") in p.JOB_METHODS:
            result = self.answer(request)
            if result is not None:
                self.client.handle_message(
                    self.client.topics.api_response,
                    {"id": request["id"], "method": request["method"], "result": result},
                )
        return info


def _status(status: int, sub: int, *, progress: float = 0, filename: str = "") -> dict:
    message = copy.deepcopy(fx.REAL_FULL_STATUS_IDLE)
    message["result"]["machine_status"].update(status=status, sub_status=sub, progress=progress)
    message["result"]["print_status"]["filename"] = filename
    return message


IDLE = (1, 2802)
PRINTING = (2, 2075)
PAUSED = (2, 2502)


@pytest.fixture
def jobs(monkeypatch):
    monkeypatch.setenv(ENABLE_ENV, ALL_JOB)
    fakes: list[AnsweringMQTT] = []
    events = []

    def factory(client_id):
        fake = AnsweringMQTT(client_id)
        fakes.append(fake)
        return fake

    client = ElegooCC2Client(
        fx.PRINTER_IP,
        fx.SERIAL,
        model="Centauri 2",
        on_job_event=events.append,
        mqtt_client_factory=factory,
        clock=Clock(),
        start_heartbeat=False,
    )
    client.COMMAND_TIMEOUT = 0.05

    def connect():
        client.connect()
        fake = fakes[-1]
        fake.client = client
        client._on_connect(fake, None, None, SimpleNamespace(value=0))
        client.handle_message(client.topics.register_response, {"client_id": client.client_id, **fx.REGISTER_OK})
        return fake

    def feed(state, **kwargs):
        client.handle_message(client.topics.api_response, _status(*state, **kwargs))

    return SimpleNamespace(client=client, fakes=fakes, events=events, connect=connect, feed=feed)


def _sent(fake, method):
    return [m for m in fake.methods_sent() if m == method]


class TestJobCommands:
    def test_refused_when_not_switched_on(self, jobs, monkeypatch):
        fake = jobs.connect()
        jobs.feed(PRINTING, progress=10)
        monkeypatch.setenv(ENABLE_ENV, "file_upload")
        outcome = jobs.client.pause_print()
        assert outcome.status == COMMAND_REFUSED
        assert "job_pause_resume" in outcome.message
        assert _sent(fake, p.METHOD_PAUSE_PRINT) == []

    def test_pause_accepted_with_qos0(self, jobs):
        fake = jobs.connect()
        fake.answer = lambda request: {"error_code": 0}
        jobs.feed(PRINTING, progress=10)
        outcome = jobs.client.pause_print()
        assert outcome.status == COMMAND_ACCEPTED
        assert (p.METHOD_PAUSE_PRINT, 0) in fake.qos
        sent = [m for t, m in fake.published if m.get("method") == p.METHOD_PAUSE_PRINT]
        assert sent[0]["params"] == {}

    def test_printer_error_is_reported(self, jobs):
        fake = jobs.connect()
        fake.answer = lambda request: {"error_code": 1010}
        jobs.feed(PRINTING, progress=10)
        outcome = jobs.client.stop_print()
        assert outcome.status == COMMAND_REJECTED
        assert outcome.error_code == 1010
        assert outcome.message == "not printing"

    def test_state_guards(self, jobs):
        fake = jobs.connect()
        jobs.feed(IDLE)
        assert jobs.client.pause_print().status == COMMAND_REFUSED
        assert jobs.client.resume_print().status == COMMAND_REFUSED
        assert jobs.client.stop_print().status == COMMAND_REFUSED
        jobs.feed(PAUSED, progress=40)
        assert jobs.client.pause_print().status == COMMAND_REFUSED
        fake.answer = lambda request: {"error_code": 0}
        assert jobs.client.resume_print().status == COMMAND_ACCEPTED
        assert _sent(fake, p.METHOD_RESUME_PRINT) == [p.METHOD_RESUME_PRINT]

    def test_nothing_sent_while_offline(self, jobs):
        fake = jobs.connect()
        jobs.feed(PRINTING, progress=10)
        jobs.client._on_disconnect(fake, None)
        assert jobs.client.stop_print().status == COMMAND_NOT_SENT
        assert _sent(fake, p.METHOD_STOP_PRINT) == []

    def test_no_reply_is_not_retried_after_reconnect(self, jobs):
        fake = jobs.connect()
        jobs.feed(PRINTING, progress=10)
        outcome = jobs.client.pause_print()
        assert outcome.status == COMMAND_NO_REPLY
        # The link drops and comes back: nothing is re-sent on our side, and
        # QoS 0 means paho has nothing queued to re-publish either.
        jobs.client._on_disconnect(fake, None)
        jobs.client._on_connect(fake, None, None, SimpleNamespace(value=0))
        assert _sent(fake, p.METHOD_PAUSE_PRINT) == [p.METHOD_PAUSE_PRINT]
        assert all(qos == 0 for method, qos in fake.qos if method in p.JOB_METHODS)

    def test_late_reply_is_ignored(self, jobs):
        fake = jobs.connect()
        jobs.feed(PRINTING, progress=10)
        jobs.client.pause_print()
        request_id = [m for t, m in fake.published if m.get("method") == p.METHOD_PAUSE_PRINT][0]["id"]
        jobs.client.handle_message(
            jobs.client.topics.api_response, {"id": request_id, "method": 1021, "result": {"error_code": 0}}
        )
        assert jobs.client.telemetry().job_state is JobState.PRINTING


class TestStartPrint:
    def test_start_sent_once_per_job_key(self, jobs):
        fake = jobs.connect()
        jobs.feed(IDLE)
        outcome = jobs.client.start_print("benchy.gcode", job_key="queue-12")
        assert outcome.status == COMMAND_NO_REPLY
        # Reply lost, link bounced, printer still idle: the same job is refused.
        jobs.client._on_disconnect(fake, None)
        jobs.client._on_connect(fake, None, None, SimpleNamespace(value=0))
        jobs.client.handle_message(
            jobs.client.topics.register_response, {"client_id": jobs.client.client_id, **fx.REGISTER_OK}
        )
        jobs.feed(IDLE)
        again = jobs.client.start_print("benchy.gcode", job_key="queue-12")
        assert again.status == COMMAND_REFUSED
        assert again.message == "start already sent for this job"
        assert _sent(fake, p.METHOD_START_PRINT) == [p.METHOD_START_PRINT]

    def test_start_params_on_the_wire(self, jobs):
        fake = jobs.connect()
        fake.answer = lambda request: {"error_code": 0}
        jobs.feed(IDLE)
        outcome = jobs.client.start_print("benchy.gcode", job_key="k1", plate_layout="B")
        assert outcome.accepted
        request = [m for t, m in fake.published if m.get("method") == p.METHOD_START_PRINT][0]
        assert request["params"] == p.start_print_params("benchy.gcode", plate_layout="B")
        assert (p.METHOD_START_PRINT, 0) in fake.qos

    def test_not_started_on_a_busy_or_unknown_printer(self, jobs):
        fake = jobs.connect()
        assert jobs.client.start_print("a.gcode", job_key="k1").status == COMMAND_REFUSED  # nothing read yet
        jobs.feed(PRINTING, progress=5)
        assert jobs.client.start_print("a.gcode", job_key="k1").status == COMMAND_REFUSED
        assert _sent(fake, p.METHOD_START_PRINT) == []

    def test_not_sent_frees_the_key(self, jobs):
        fake = jobs.connect()
        jobs.feed(IDLE)
        fake.publish_rc = 4
        original = fake.publish
        fake.publish = lambda *a, **k: SimpleNamespace(rc=4)
        assert jobs.client.start_print("a.gcode", job_key="k1").status == COMMAND_NOT_SENT
        fake.publish = original
        fake.answer = lambda request: {"error_code": 0}
        assert jobs.client.start_print("a.gcode", job_key="k1").status == COMMAND_ACCEPTED

    def test_bambu_file_refused(self, jobs):
        jobs.connect()
        jobs.feed(IDLE)
        outcome = jobs.client.start_print("model.stl", job_key="k1")
        assert outcome.status == COMMAND_REFUSED


# --- client: job tracking ------------------------------------------------------------


class TestJobTracking:
    def test_back_to_idle_after_99_percent_is_a_finish(self, jobs):
        jobs.connect()
        jobs.feed(IDLE)
        jobs.feed(PRINTING, progress=1, filename="benchy.gcode")
        jobs.feed(PRINTING, progress=99, filename="benchy.gcode")
        jobs.feed(IDLE)
        kinds = [(e.kind, e.outcome, e.inferred) for e in jobs.events]
        assert kinds == [("started", None, False), ("ended", JobState.FINISHED, True)]
        assert jobs.events[-1].filename == "benchy.gcode"

    def test_reported_cancel_is_a_cancel(self, jobs):
        jobs.connect()
        jobs.feed(PRINTING, progress=30, filename="x.gcode")
        jobs.feed((2, p.PRINT_SUB_STOPPED), progress=30, filename="x.gcode")
        jobs.feed(IDLE)
        assert [(e.kind, e.outcome, e.inferred) for e in jobs.events] == [
            ("started", None, False),
            ("ended", JobState.CANCELLED, False),
        ]

    def test_disconnect_never_ends_a_job(self, jobs):
        fake = jobs.connect()
        jobs.feed(PRINTING, progress=50, filename="x.gcode")
        jobs.client._on_disconnect(fake, None)
        assert [e.kind for e in jobs.events] == ["started"]
        assert jobs.client.telemetry().job_state is JobState.PRINTING

    def test_end_missed_while_offline_is_unknown(self, jobs):
        fake = jobs.connect()
        jobs.feed(PRINTING, progress=99.5, filename="x.gcode")
        jobs.client._on_disconnect(fake, None)
        jobs.client._on_connect(fake, None, None, SimpleNamespace(value=0))
        jobs.client.handle_message(
            jobs.client.topics.register_response, {"client_id": jobs.client.client_id, **fx.REGISTER_OK}
        )
        jobs.feed(IDLE)
        assert [(e.kind, e.outcome, e.inferred) for e in jobs.events] == [
            ("started", None, False),
            ("ended", JobState.UNKNOWN, False),
        ]

    def test_idle_halfway_is_unknown(self, jobs):
        jobs.connect()
        jobs.feed(PRINTING, progress=40, filename="x.gcode")
        jobs.feed(IDLE)
        assert jobs.events[-1].outcome is JobState.UNKNOWN

    def test_print_running_at_connect_is_reported_as_started(self, jobs):
        jobs.connect()
        jobs.feed(PRINTING, progress=60, filename="from-orca.gcode")
        assert jobs.events[0].kind == "started"
        assert jobs.events[0].filename == "from-orca.gcode"


# --- upload -----------------------------------------------------------------------


@pytest.fixture
def gcode(tmp_path):
    path = tmp_path / "benchy.gcode"
    path.write_bytes(b"G28\nG1 X10\nG1 Y10\n")
    return path


def _transport(reply):
    requests: list[httpx.Request] = []

    def handler(request):
        requests.append(request)
        return reply(request, len(requests))

    return httpx.MockTransport(handler), requests


@pytest.mark.asyncio
class TestUpload:
    async def test_chunks_carry_range_md5_and_token(self, gcode):
        transport, requests = _transport(lambda r, n: httpx.Response(200, json={"error_code": 0}))
        progress = []
        result = await upload_file(
            fx.PRINTER_IP,
            gcode,
            "benchy.gcode",
            None,
            chunk_size=8,
            transport=transport,
            on_progress=lambda a, b: progress.append((a, b)),
        )
        data = gcode.read_bytes()
        assert result.success and result.uploaded_bytes == len(data) == 18
        assert result.md5 == hashlib.md5(data).hexdigest()
        assert [r.headers["Content-Range"] for r in requests] == ["bytes 0-7/18", "bytes 8-15/18", "bytes 16-17/18"]
        assert b"".join(r.content for r in requests) == data
        for r in requests:
            assert r.method == "PUT"
            assert r.url == httpx.URL(f"http://{fx.PRINTER_IP}:80/upload")
            assert r.headers["X-File-MD5"] == result.md5
            assert r.headers["X-File-Name"] == "benchy.gcode"
            assert r.headers["X-Token"] == p.DEFAULT_ACCESS_CODE
        assert progress[-1] == (18, 18)

    async def test_stops_at_the_first_refused_chunk(self, gcode):
        def reply(request, n):
            return httpx.Response(200, json={"error_code": 0 if n == 1 else 9004})

        transport, requests = _transport(reply)
        result = await upload_file(
            fx.PRINTER_IP, gcode, "benchy.gcode", "secret-code", chunk_size=8, transport=transport
        )
        assert not result.success
        assert result.reason == UPLOAD_ERROR_REJECTED
        assert result.message == "MD5 check failed"
        assert result.uploaded_bytes == 8
        assert len(requests) == 2

    @pytest.mark.parametrize(
        "response",
        [httpx.Response(401), httpx.Response(200, json={"error_code": 1000})],
    )
    async def test_refused_access_code(self, gcode, response):
        transport, _ = _transport(lambda r, n: response)
        result = await upload_file(fx.PRINTER_IP, gcode, "benchy.gcode", "wrong", transport=transport)
        assert result.reason == UPLOAD_ERROR_AUTH

    async def test_unreachable(self, gcode):
        def reply(request, n):
            raise httpx.ConnectError("refused", request=request)

        transport, _ = _transport(reply)
        result = await upload_file(fx.PRINTER_IP, gcode, "benchy.gcode", None, transport=transport)
        assert result.reason == UPLOAD_ERROR_UNREACHABLE

    async def test_bad_name_sends_nothing(self, gcode):
        transport, requests = _transport(lambda r, n: httpx.Response(200, json={"error_code": 0}))
        result = await upload_file(fx.PRINTER_IP, gcode, "../benchy.gcode", None, transport=transport)
        assert result.reason == UPLOAD_ERROR_BAD_FILE
        assert requests == []

    async def test_access_code_never_logged(self, gcode, caplog):
        transport, _ = _transport(lambda r, n: httpx.Response(200, json={"error_code": 1000}))
        with caplog.at_level(logging.DEBUG):
            await upload_file(fx.PRINTER_IP, gcode, "benchy.gcode", "s3cr3t-code", transport=transport)
        assert "s3cr3t-code" not in caplog.text
