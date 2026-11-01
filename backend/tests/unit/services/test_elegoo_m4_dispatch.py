"""Queue dispatch to an ELEGOO CC2 (M4): refuse the wrong file, upload, one start.

The printer client and the HTTP upload are stand-ins; the queue row lives in
the test database so the pending -> printing CAS runs for real.
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.models.print_queue import PrintQueueItem
from backend.app.services import provider_jobs
from backend.app.services.bambu_mqtt import PrinterState
from backend.app.services.print_scheduler import PrintScheduler
from backend.app.services.printer_providers.elegoo.capabilities import ENABLE_ENV, elegoo_capabilities
from backend.app.services.printer_providers.elegoo.client import (
    COMMAND_ACCEPTED,
    COMMAND_NO_REPLY,
    COMMAND_REJECTED,
    CommandOutcome,
)
from backend.app.services.printer_providers.elegoo.upload import UploadResult

CC2_GCODE = "G28\nG1 X1\n; printer_model = Elegoo Centauri Carbon 2\n"
WHITE_GCODE = "M6211 A1 L200 T0\n" + CC2_GCODE + "; filament_colour = #FFFFFF\n; filament_type = PLA\n"


class FakeCC2:
    def __init__(self, outcome: CommandOutcome):
        self.outcome = outcome
        self.starts: list[tuple[str, dict]] = []

    def start_print(self, filename, **kwargs):
        self.starts.append((filename, kwargs))
        return self.outcome


@pytest.fixture
def farm(monkeypatch, db_session, printer_factory, tmp_path):
    monkeypatch.setenv(ENABLE_ENV, "file_upload,job_start")
    provider_jobs._dispatched.clear()
    uploads: list[tuple] = []
    state = SimpleNamespace(client=FakeCC2(CommandOutcome(COMMAND_ACCEPTED, 1020, 0)), upload_ok=True)

    async def fake_upload(ip, path, name, code, **kwargs):
        uploads.append((ip, path, name))
        if state.upload_ok:
            return UploadResult(True, name, 10, "md5", 10)
        return UploadResult(False, name, reason="unreachable", message="ConnectError")

    manager = MagicMock()
    manager.get_provider_client.side_effect = lambda pid: state.client
    manager.get_capabilities.side_effect = lambda pid: elegoo_capabilities("Centauri 2")
    manager.get_status.return_value = PrinterState(connected=True, state="IDLE")

    sched = PrintScheduler()
    sched._power_off_if_needed = AsyncMock()
    sched._propagate_owner_to_printer_manager = AsyncMock()
    with (
        patch("backend.app.services.print_scheduler.printer_manager", manager),
        patch("backend.app.services.printer_providers.elegoo.upload.upload_file", fake_upload),
        patch("backend.app.services.print_scheduler.notification_service.on_queue_job_failed", AsyncMock()),
        patch("backend.app.services.print_scheduler.ws_manager.send_queue_item_failed", AsyncMock()),
    ):

        async def make_item(content: str = CC2_GCODE, filename: str = "benchy.gcode"):
            printer = await printer_factory(name="Centauri 2", model="Centauri 2", provider="elegoo")
            path = tmp_path / filename
            path.write_text(content, encoding="utf-8")
            item = PrintQueueItem(printer_id=printer.id, status="pending", bed_levelling="off")
            db_session.add(item)
            await db_session.commit()
            return printer, item, path

        async def dispatch(printer, item, path):
            await sched._start_print_provider(db_session, item, printer, None, path, path.name)
            await db_session.refresh(item)
            return item

        yield SimpleNamespace(
            sched=sched, manager=manager, state=state, uploads=uploads, make_item=make_item, dispatch=dispatch
        )
    provider_jobs._dispatched.clear()


@pytest.mark.asyncio
class TestProviderDispatch:
    async def test_good_file_is_uploaded_and_started_once(self, farm):
        printer, item, path = await farm.make_item()
        item = await farm.dispatch(printer, item, path)
        assert item.status == "printing"
        assert item.billing_run_id
        assert [u[2] for u in farm.uploads] == ["benchy.gcode"]
        assert farm.state.client.starts == [
            ("benchy.gcode", {"job_key": item.billing_run_id, "auto_bed_leveling": False, "slot_map": []})
        ]
        assert provider_jobs._dispatched[printer.id].printer_filename == "benchy.gcode"
        assert farm.sched._printer_in_dispatch_hold(printer.id) is True

    async def test_start_names_the_canvas_slot_of_the_file_colour(self, farm):
        """1/11/2026: a white T0 file started on the black slot 4 the cubo had used."""
        from backend.app.services.printer_providers.base import JobState, MultiColorSlot

        canvas = [
            MultiColorSlot(0, 0, True, "PLA", "FFFFFF"),
            MultiColorSlot(0, 3, True, "PLA", "000000", active=True),
        ]

        class CanvasCC2(FakeCC2):
            def telemetry(self):
                return SimpleNamespace(job_state=JobState.IDLE, multicolor_slots=canvas)

        farm.state.client = CanvasCC2(CommandOutcome(COMMAND_ACCEPTED, 1020, 0))
        printer, item, path = await farm.make_item(WHITE_GCODE)
        item = await farm.dispatch(printer, item, path)
        assert item.status == "printing"
        assert farm.state.client.starts[0][1]["slot_map"] == [{"t": 0, "canvas_id": 0, "tray_id": 0}]

    async def test_colour_not_in_the_canvas_is_refused_before_the_upload(self, farm):
        from backend.app.services.printer_providers.base import JobState, MultiColorSlot

        class BlackOnlyCC2(FakeCC2):
            def telemetry(self):
                return SimpleNamespace(
                    job_state=JobState.IDLE, multicolor_slots=[MultiColorSlot(0, 3, True, "PLA", "000000")]
                )

        farm.state.client = BlackOnlyCC2(CommandOutcome(COMMAND_ACCEPTED, 1020, 0))
        printer, item, path = await farm.make_item(WHITE_GCODE)
        item = await farm.dispatch(printer, item, path)
        assert item.status == "failed"
        assert item.error_message.startswith("Filament not loaded: no CANVAS slot holds PLA #FFFFFF for T0")
        assert farm.uploads == [] and farm.state.client.starts == []

    async def test_bambu_file_never_leaves_the_farm(self, farm):
        printer, item, path = await farm.make_item("; printer_model = Bambu Lab P1S\nG28\n", "p1s.gcode")
        item = await farm.dispatch(printer, item, path)
        assert item.status == "failed"
        assert "Bambu Lab P1S" in item.error_message
        assert farm.uploads == [] and farm.state.client.starts == []

    async def test_upload_failure_starts_nothing(self, farm):
        farm.state.upload_ok = False
        printer, item, path = await farm.make_item()
        item = await farm.dispatch(printer, item, path)
        assert item.status == "failed"
        assert "Upload to the printer failed" in item.error_message
        assert farm.state.client.starts == []

    async def test_refused_start_fails_the_item(self, farm):
        farm.state.client = FakeCC2(CommandOutcome(COMMAND_REJECTED, 1020, 1009, "printer busy"))
        printer, item, path = await farm.make_item()
        item = await farm.dispatch(printer, item, path)
        assert item.status == "failed"
        assert item.error_message == "The printer refused the start: printer busy"
        assert printer.id not in provider_jobs._dispatched

    async def test_start_without_reply_stays_printing_and_is_not_resent(self, farm):
        farm.state.client = FakeCC2(CommandOutcome(COMMAND_NO_REPLY, 1020))
        printer, item, path = await farm.make_item()
        item = await farm.dispatch(printer, item, path)
        assert item.status == "printing"
        assert "did not confirm" in item.error_message
        assert len(farm.state.client.starts) == 1
        # The row is no longer pending: no later tick can pick it up again.
        await farm.dispatch(printer, item, path)
        assert len(farm.state.client.starts) == 1

    async def test_cancelled_during_upload_is_not_started(self, farm, db_session):
        printer, item, path = await farm.make_item()

        async def cancelling_upload(ip, p, name, code, **kwargs):
            await db_session.execute(
                PrintQueueItem.__table__.update().where(PrintQueueItem.id == item.id).values(status="cancelled")
            )
            await db_session.commit()
            return UploadResult(True, name, 10, "md5", 10)

        with patch("backend.app.services.printer_providers.elegoo.upload.upload_file", cancelling_upload):
            await farm.sched._start_print_provider(db_session, item, printer, None, path, path.name)
        assert farm.state.client.starts == []

    async def test_not_switched_on(self, farm, monkeypatch):
        monkeypatch.setenv(ENABLE_ENV, "file_upload")
        printer, item, path = await farm.make_item()
        item = await farm.dispatch(printer, item, path)
        assert item.status == "failed"
        assert farm.uploads == []


@pytest.mark.asyncio
async def test_stranded_sweep_does_not_call_an_idle_elegoo_a_cancel(db_session, printer_factory, test_engine):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from backend.app.services.printer_providers.base import PrinterProvider

    printer = await printer_factory(name="Centauri 2", model="Centauri 2", provider="elegoo")
    item = PrintQueueItem(printer_id=printer.id, status="printing", started_at=datetime.now(timezone.utc))
    db_session.add(item)
    await db_session.commit()

    manager = MagicMock()
    manager.get_status.return_value = PrinterState(connected=True, state="IDLE")
    manager.get_provider.return_value = PrinterProvider.ELEGOO
    maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    sched = PrintScheduler()
    sched._terminal_since[printer.id] = -1e9  # grace period long over
    with (
        patch("backend.app.services.print_scheduler.printer_manager", manager),
        patch("backend.app.services.print_scheduler.async_session", maker),
    ):
        await sched._close_stranded_printing_items()
    await db_session.refresh(item)
    assert item.status == "failed"
    assert item.error_message == provider_jobs.OUTCOME_NOT_OBSERVED


@pytest.mark.asyncio
async def test_start_waits_for_the_printer_to_finish_storing_the_upload(farm):
    from backend.app.services.printer_providers.base import JobState

    states = iter([JobState.BUSY, JobState.BUSY, JobState.IDLE])
    seen: list[JobState] = []

    class StoringCC2(FakeCC2):
        def telemetry(self):
            state = next(states, JobState.IDLE)
            seen.append(state)
            return SimpleNamespace(job_state=state)

    farm.state.client = StoringCC2(CommandOutcome(COMMAND_ACCEPTED, 1020, 0))
    farm.sched.PROVIDER_READY_INTERVAL = 0
    printer, item, path = await farm.make_item()
    item = await farm.dispatch(printer, item, path)
    assert seen == [JobState.BUSY, JobState.BUSY, JobState.IDLE]
    assert len(farm.state.client.starts) == 1
