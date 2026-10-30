"""ELEGOO Centauri Carbon 2, M5: CANVAS commands (slot edit, auto-refill, load / unload).

No test reaches a printer: MQTT is the answering recorder of the M4 tests.
Payloads are the ones ELEGOO's own device page sends (Orca Slicer 2.4.3).
"""

import copy

import pytest

from backend.app.services.printer_providers.base import Capability
from backend.app.services.printer_providers.elegoo import canvas as cv, protocol as p
from backend.app.services.printer_providers.elegoo.capabilities import (
    ENABLE_ENV,
    elegoo_capabilities,
    enabled_job_capabilities,
)
from backend.app.services.printer_providers.elegoo.client import (
    COMMAND_ACCEPTED,
    COMMAND_NO_REPLY,
    COMMAND_REFUSED,
)
from backend.app.services.printer_providers.elegoo.normalizer import normalize_cc2_status
from backend.tests._fixtures import elegoo_cc2 as fx
from backend.tests.unit.services.test_elegoo_cc2_jobs import IDLE, PAUSED, PRINTING, jobs  # noqa: F401

ALL_CANVAS = "multicolor_slot_edit,multicolor_auto_refill,multicolor_load_unload"

# --- payloads --------------------------------------------------------------------


class TestCanvasPayloads:
    def test_canvas_commands_have_their_own_builder(self):
        for method in (2001, 2002, 2003, 2004):
            assert p.build_canvas_request(7, method)
            with pytest.raises(PermissionError):
                p.build_job_request(7, method)
            with pytest.raises(PermissionError):
                p.build_request(7, method)
        for method in (p.METHOD_START_PRINT, p.METHOD_GET_CANVAS_STATUS, 1026, 1043):
            with pytest.raises(PermissionError):
                p.build_canvas_request(7, method)

    def test_slot_edit_like_elegoos_page(self):
        assert cv.filament_info_params(0, 2, filament_name="PLA Matte", color="#2850df", brand="ELEGOO") == {
            "canvas_id": 0,
            "tray_id": 2,
            "brand": "ELEGOO",
            "filament_type": "PLA",
            "filament_name": "PLA Matte",
            "filament_code": "0x0006",
            "filament_color": "#2850DF",
            "filament_min_temp": 190,
            "filament_max_temp": 230,
        }
        assert (
            cv.filament_info_params(0, 0, filament_name="PETG", color="FFFFFF", brand="Generic")["filament_color"]
            == "#FFFFFF"
        )

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"filament_name": "Unobtainium", "color": "#FFFFFF", "brand": "Generic"},
            {"filament_name": "PLA", "color": "white", "brand": "Generic"},
            {"filament_name": "PLA", "color": "#FFF", "brand": "Generic"},
            {"filament_name": "PLA", "color": "#FFFFFF", "brand": "Bambu Lab"},
            # Not offered under ELEGOO on ELEGOO's page.
            {"filament_name": "PLA Carbon", "color": "#FFFFFF", "brand": "ELEGOO"},
        ],
    )
    def test_slot_edit_refuses_what_the_page_cannot_send(self, kwargs):
        with pytest.raises(ValueError):
            cv.filament_info_params(0, 0, **kwargs)

    @pytest.mark.parametrize("ids", [(-1, 0), (0, -1), (True, 0), ("0", 0)])
    def test_tray_ids_are_plain_non_negative_integers(self, ids):
        with pytest.raises(ValueError):
            cv.tray_params(*ids)

    def test_auto_refill_key_is_the_one_elegoos_page_sends(self):
        assert cv.auto_refill_params(True) == {"auto_refill": True}

    def test_catalogue_names_are_unique(self):
        names = [f.name for f in cv.CANVAS_FILAMENTS]
        assert len(names) == len(set(names)) == 50


# --- capabilities and telemetry --------------------------------------------------


class TestCanvasCapabilities:
    def test_off_by_default(self, monkeypatch):
        monkeypatch.delenv(ENABLE_ENV, raising=False)
        caps = elegoo_capabilities("Centauri 2")
        for capability in (
            Capability.MULTICOLOR_SLOT_EDIT,
            Capability.MULTICOLOR_AUTO_REFILL,
            Capability.MULTICOLOR_LOAD_UNLOAD,
        ):
            assert not caps.supports(capability)
            assert caps.notes[capability]

    def test_switched_on_one_by_one(self, monkeypatch):
        monkeypatch.setenv(ENABLE_ENV, "multicolor_auto_refill")
        assert enabled_job_capabilities() == {Capability.MULTICOLOR_AUTO_REFILL}

    def test_auto_refill_flag_reaches_the_telemetry(self):
        canvas = copy.deepcopy(fx.REAL_CANVAS_STATUS_RESPONSE["result"]["canvas_info"])
        status = fx.REAL_FULL_STATUS_IDLE["result"]
        assert normalize_cc2_status(status, canvas_info=canvas).multicolor_auto_refill is False
        canvas["auto_refill"] = True
        assert normalize_cc2_status(status, canvas_info=canvas).multicolor_auto_refill is True
        del canvas["auto_refill"]
        assert normalize_cc2_status(status, canvas_info=canvas).multicolor_auto_refill is None


# --- client --------------------------------------------------------------------------


def _canvas(*, tray0: dict | None = None) -> dict:
    message = copy.deepcopy(fx.REAL_CANVAS_STATUS_RESPONSE)
    if tray0 is not None:
        message["result"]["canvas_info"]["canvas_list"][0]["tray_list"][0].update(tray0)
    return message


PLA_WHITE = {"status": 1, "filament_type": "PLA", "filament_name": "PLA", "filament_color": "#FFFFFF"}


@pytest.fixture
def canvas(jobs, monkeypatch):  # noqa: F811
    monkeypatch.setenv(ENABLE_ENV, ALL_CANVAS)
    fake = jobs.connect()

    def feed_canvas(**kwargs):
        jobs.client.handle_message(jobs.client.topics.api_response, _canvas(**kwargs))

    jobs.fake = fake
    jobs.feed_canvas = feed_canvas
    return jobs


def _requests(fake, method):
    return [m for t, m in fake.published if m.get("method") == method]


class TestCanvasCommands:
    def test_slot_edit_sent_with_qos0_and_canvas_reread(self, canvas):
        canvas.fake.answer = lambda request: {"error_code": 0}
        canvas.feed(IDLE)
        canvas.feed_canvas()
        reads_before = len(_requests(canvas.fake, p.METHOD_GET_CANVAS_STATUS))
        outcome = canvas.client.set_canvas_filament(0, 1, filament_name="PETG", color="#000000", brand="Generic")
        assert outcome.status == COMMAND_ACCEPTED
        [request] = _requests(canvas.fake, p.METHOD_SET_FILAMENT_INFO)
        assert request["params"] == cv.filament_info_params(
            0, 1, filament_name="PETG", color="#000000", brand="Generic"
        )
        assert (p.METHOD_SET_FILAMENT_INFO, 0) in canvas.fake.qos
        assert len(_requests(canvas.fake, p.METHOD_GET_CANVAS_STATUS)) == reads_before + 1

    def test_slot_edit_only_when_idle(self, canvas):
        canvas.feed(PRINTING, progress=10)
        canvas.feed_canvas()
        outcome = canvas.client.set_canvas_filament(0, 0, filament_name="PLA", color="#FFFFFF", brand="Generic")
        assert outcome.status == COMMAND_REFUSED
        assert "printing" in outcome.message
        assert _requests(canvas.fake, p.METHOD_SET_FILAMENT_INFO) == []

    def test_unknown_slot_is_refused(self, canvas):
        canvas.feed(IDLE)
        canvas.feed_canvas()
        outcome = canvas.client.set_canvas_filament(1, 0, filament_name="PLA", color="#FFFFFF", brand="Generic")
        assert outcome.status == COMMAND_REFUSED
        assert "not reported" in outcome.message
        assert _requests(canvas.fake, p.METHOD_SET_FILAMENT_INFO) == []

    def test_not_switched_on(self, canvas, monkeypatch):
        monkeypatch.setenv(ENABLE_ENV, "multicolor_auto_refill")
        canvas.feed(IDLE)
        canvas.feed_canvas()
        outcome = canvas.client.set_canvas_filament(0, 0, filament_name="PLA", color="#FFFFFF", brand="Generic")
        assert outcome.status == COMMAND_REFUSED
        assert "multicolor_slot_edit" in outcome.message
        assert canvas.client.load_canvas_filament(0, 0).status == COMMAND_REFUSED
        assert _requests(canvas.fake, p.METHOD_SET_FILAMENT_INFO) == []
        assert _requests(canvas.fake, p.METHOD_LOAD_FILAMENT) == []

    def test_auto_refill_also_while_printing(self, canvas):
        canvas.fake.answer = lambda request: {"error_code": 0}
        canvas.feed(PRINTING, progress=30)
        canvas.feed_canvas()
        assert canvas.client.set_canvas_auto_refill(True).status == COMMAND_ACCEPTED
        [request] = _requests(canvas.fake, p.METHOD_SET_AUTO_REFILL)
        assert request["params"] == {"auto_refill": True}

    def test_auto_refill_needs_a_known_state_and_a_canvas(self, canvas):
        assert canvas.client.set_canvas_auto_refill(True).status == COMMAND_REFUSED  # nothing read yet
        canvas.feed(IDLE)
        outcome = canvas.client.set_canvas_auto_refill(True)
        assert outcome.status == COMMAND_REFUSED
        assert "no CANVAS" in outcome.message
        assert _requests(canvas.fake, p.METHOD_SET_AUTO_REFILL) == []

    def test_load_needs_a_recognised_spool(self, canvas):
        canvas.feed(IDLE)
        canvas.feed_canvas()  # real capture: slot 0 present but with no filament data, slot 1 empty
        for tray in (0, 1):
            outcome = canvas.client.load_canvas_filament(0, tray)
            assert outcome.status == COMMAND_REFUSED
            assert "not set" in outcome.message
        assert _requests(canvas.fake, p.METHOD_LOAD_FILAMENT) == []

    def test_load_idle_or_paused_with_the_long_wait(self, canvas, monkeypatch):
        waits = []
        send = canvas.client._send_job_command

        def spy(method, params=None, timeout=None, build=p.build_job_request):
            waits.append(timeout)
            return send(method, params, 0.05, build)

        monkeypatch.setattr(canvas.client, "_send_job_command", spy)
        canvas.fake.answer = lambda request: {"error_code": 0}
        canvas.feed(PAUSED, progress=40)
        canvas.feed_canvas(tray0=PLA_WHITE)
        assert canvas.client.load_canvas_filament(0, 0).status == COMMAND_ACCEPTED
        assert waits == [cv.LOAD_UNLOAD_TIMEOUT_SECONDS]
        [request] = _requests(canvas.fake, p.METHOD_LOAD_FILAMENT)
        assert request["params"] == {"canvas_id": 0, "tray_id": 0}

    def test_no_load_or_unload_while_printing(self, canvas):
        canvas.feed(PRINTING, progress=10)
        canvas.feed_canvas(tray0=PLA_WHITE)
        assert canvas.client.load_canvas_filament(0, 0).status == COMMAND_REFUSED
        assert canvas.client.unload_canvas_filament(0, 0).status == COMMAND_REFUSED
        assert _requests(canvas.fake, p.METHOD_LOAD_FILAMENT) == []
        assert _requests(canvas.fake, p.METHOD_UNLOAD_FILAMENT) == []

    def test_unload_without_reply_is_not_a_success_and_not_resent(self, canvas, monkeypatch):
        monkeypatch.setattr(cv, "LOAD_UNLOAD_TIMEOUT_SECONDS", 0.05)
        canvas.feed(IDLE)
        canvas.feed_canvas(tray0=PLA_WHITE)
        assert canvas.client.unload_canvas_filament(0, 0).status == COMMAND_NO_REPLY
        assert len(_requests(canvas.fake, p.METHOD_UNLOAD_FILAMENT)) == 1
        assert all(qos == 0 for method, qos in canvas.fake.qos if method in p.CANVAS_METHODS)

    def test_late_reply_is_ignored(self, canvas):
        canvas.feed(IDLE)
        canvas.feed_canvas()
        canvas.client.set_canvas_auto_refill(True)
        [request] = _requests(canvas.fake, p.METHOD_SET_AUTO_REFILL)
        canvas.client.handle_message(
            canvas.client.topics.api_response, {"id": request["id"], "method": 2004, "result": {"error_code": 0}}
        )
        assert canvas.client.telemetry().multicolor_auto_refill is False
