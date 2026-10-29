"""The print queue never dispatches to a printer whose provider cannot take queue jobs.

The dispatch path uploads over FTPS and starts with a Bambu ``project_file``
MQTT command; an ELEGOO printer (M2: read-only) must stay out of it even when
connected and idle.
"""

from unittest.mock import MagicMock, patch

from backend.app.services.bambu_mqtt import PrinterState
from backend.app.services.print_scheduler import PrintScheduler
from backend.app.services.printer_manager import PrinterManager
from backend.app.services.printer_providers import capabilities_for
from backend.app.services.printer_providers.base import PrinterProvider


class _IdleElegoo:
    provider = PrinterProvider.ELEGOO
    state = PrinterState(connected=True, state="IDLE")
    capabilities = capabilities_for("elegoo", "Centauri Carbon 2")

    def check_staleness(self):
        return True


def _manager_with(elegoo_id: int, bambu_id: int) -> PrinterManager:
    manager = PrinterManager()
    manager._provider_clients[elegoo_id] = _IdleElegoo()
    manager._providers[elegoo_id] = PrinterProvider.ELEGOO
    bambu = MagicMock()
    bambu.state = PrinterState(connected=True, state="IDLE")
    bambu.check_staleness.return_value = True
    manager._clients[bambu_id] = bambu
    manager._providers[bambu_id] = PrinterProvider.BAMBU
    manager._models[bambu_id] = "P1S"
    return manager


def test_idle_elegoo_is_not_idle_for_the_queue():
    manager = _manager_with(elegoo_id=7, bambu_id=1)
    with patch("backend.app.services.print_scheduler.printer_manager", manager):
        sched = PrintScheduler()
        assert sched._cannot_take_queue_jobs(7) is True
        assert sched._is_printer_idle(7, require_plate_clear=False) is False


def test_idle_bambu_is_unchanged():
    manager = _manager_with(elegoo_id=7, bambu_id=1)
    with patch("backend.app.services.print_scheduler.printer_manager", manager):
        sched = PrintScheduler()
        assert sched._cannot_take_queue_jobs(1) is False
        assert sched._is_printer_idle(1, require_plate_clear=False) is True


def test_unknown_printer_is_left_to_connection_checks():
    manager = PrinterManager()
    with patch("backend.app.services.print_scheduler.printer_manager", manager):
        assert PrintScheduler._cannot_take_queue_jobs(42) is False
