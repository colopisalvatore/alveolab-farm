"""ALVEOLAB Farm provider layer through the API (M1/M2).

Every path that could reach a printer is patched: the ELEGOO probe, the
manager's connect, and UDP discovery. No socket is opened.
"""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from backend.app.models.printer import Printer
from backend.app.services.printer_providers.elegoo.protocol import CC2DiscoveryResult

ELEGOO_PRINTER = {
    "name": "Centauri 2 Combo",
    "serial_number": "cc2Serial0001",
    "ip_address": "192.0.2.50",
    "model": "Centauri Carbon 2",
    "provider": "elegoo",
}


@pytest.fixture
def no_printer_io():
    with (
        patch(
            "backend.app.services.printer_providers.elegoo.client.probe_connection",
            new=AsyncMock(
                return_value={"success": True, "state": "IDLE", "model": "Centauri Carbon 2", "reason": None}
            ),
        ) as elegoo_probe,
        patch(
            "backend.app.services.printer_manager.printer_manager.test_connection",
            new=AsyncMock(return_value={"success": True, "state": "IDLE", "model": "P1S"}),
        ) as bambu_probe,
        patch(
            "backend.app.services.printer_manager.printer_manager.connect_printer",
            new=AsyncMock(return_value=True),
        ) as connect,
    ):
        yield {"elegoo_probe": elegoo_probe, "bambu_probe": bambu_probe, "connect": connect}


class TestCreatePrinter:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_elegoo_without_access_code(self, async_client: AsyncClient, db_session, no_printer_io):
        response = await async_client.post("/api/v1/printers/", json=ELEGOO_PRINTER)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["provider"] == "elegoo"
        # CC2 topics use the serial as reported: no Bambu-style upper-casing.
        assert body["serial_number"] == "cc2Serial0001"
        assert "access_code" not in body
        no_printer_io["elegoo_probe"].assert_awaited_once_with("192.0.2.50", "cc2Serial0001", None)
        no_printer_io["bambu_probe"].assert_not_awaited()
        row = (await db_session.execute(select(Printer).where(Printer.serial_number == "cc2Serial0001"))).scalar_one()
        assert row.provider == "elegoo"
        assert row.access_code == ""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_elegoo_probe_failure_is_refused(self, async_client: AsyncClient, no_printer_io):
        no_printer_io["elegoo_probe"].return_value = {"success": False, "reason": "auth_failed"}
        response = await async_client.post("/api/v1/printers/", json=ELEGOO_PRINTER)
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "printer_connection_failed"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_bambu_still_requires_access_code(self, async_client: AsyncClient, no_printer_io):
        data = {"name": "P1S", "serial_number": "01p00a000000001", "ip_address": "192.0.2.60", "model": "P1S"}
        response = await async_client.post("/api/v1/printers/", json=data)
        assert response.status_code == 422
        data["access_code"] = "12345678"
        response = await async_client.post("/api/v1/printers/", json=data)
        assert response.status_code == 200
        assert response.json()["provider"] == "bambu"
        assert response.json()["serial_number"] == "01P00A000000001"
        no_printer_io["elegoo_probe"].assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_unknown_provider_rejected(self, async_client: AsyncClient, no_printer_io):
        response = await async_client.post("/api/v1/printers/", json={**ELEGOO_PRINTER, "provider": "prusa"})
        assert response.status_code == 422


class TestProviderEndpoints:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_existing_rows_default_to_bambu(self, async_client: AsyncClient, printer_factory):
        printer = await printer_factory(model="P1S")
        assert printer.provider == "bambu"
        response = await async_client.get(f"/api/v1/printer-providers/printers/{printer.id}/capabilities")
        assert response.status_code == 200
        body = response.json()
        assert body["provider"] == "bambu"
        assert "queue_dispatch" in body["supported"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_elegoo_capabilities_offline(self, async_client: AsyncClient, printer_factory):
        printer = await printer_factory(provider="elegoo", model="Centauri Carbon 2", access_code="")
        response = await async_client.get(f"/api/v1/printer-providers/printers/{printer.id}/capabilities")
        body = response.json()
        assert body["provider"] == "elegoo"
        assert "temperatures" in body["supported"]
        assert "queue_dispatch" not in body["supported"]
        assert "job_start" in body["notes"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_telemetry_unknown_when_not_connected(self, async_client: AsyncClient, printer_factory):
        printer = await printer_factory(provider="elegoo", model="Centauri Carbon 2", access_code="")
        response = await async_client.get(f"/api/v1/printer-providers/printers/{printer.id}/telemetry")
        assert response.json() == {"provider": "elegoo", "connection": "unknown", "job_state": "unknown"}

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_capabilities_404(self, async_client: AsyncClient):
        response = await async_client.get("/api/v1/printer-providers/printers/999999/capabilities")
        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_discovery(self, async_client: AsyncClient):
        found = [
            CC2DiscoveryResult(
                ip_address="192.0.2.50",
                serial_number="CC2SERIAL0001",
                name="Centauri Carbon 2",
                model="Centauri Carbon 2",
                access_code_required=False,
                lan_only=True,
            )
        ]
        with patch(
            "backend.app.services.printer_providers.elegoo.discovery.discover_cc2", new=AsyncMock(return_value=found)
        ) as discover:
            response = await async_client.post(
                "/api/v1/printer-providers/elegoo/discover", json={"timeout": 1, "targets": ["192.0.2.50"]}
            )
        assert response.status_code == 200
        assert response.json()[0]["serial_number"] == "CC2SERIAL0001"
        discover.assert_awaited_once_with(timeout=1.0, targets=["192.0.2.50"])

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_discovery_rejects_non_ipv4_targets(self, async_client: AsyncClient):
        response = await async_client.post(
            "/api/v1/printer-providers/elegoo/discover", json={"targets": ["printer.example.com"]}
        )
        assert response.status_code == 422


# --- M4: job commands through the API ---------------------------------------------


class _FakeJobClient:
    """Stands in for a connected ElegooCC2Client; records the commands asked."""

    def __init__(self, outcome):
        self.outcome = outcome
        self.calls: list[str] = []

    def pause_print(self):
        self.calls.append("pause")
        return self.outcome

    def resume_print(self):
        self.calls.append("resume")
        return self.outcome

    def stop_print(self):
        self.calls.append("stop")
        return self.outcome

    def set_canvas_filament(self, canvas_id, tray_id, **kwargs):
        self.calls.append(("slot", canvas_id, tray_id, kwargs))
        return self.outcome

    def set_canvas_auto_refill(self, enabled):
        self.calls.append(("auto_refill", enabled))
        return self.outcome

    def load_canvas_filament(self, canvas_id, tray_id):
        self.calls.append(("load", canvas_id, tray_id))
        return self.outcome

    def unload_canvas_filament(self, canvas_id, tray_id):
        self.calls.append(("unload", canvas_id, tray_id))
        return self.outcome


@pytest.fixture
def provider_client():
    from backend.app.services.printer_manager import printer_manager
    from backend.app.services.printer_providers.base import PrinterProvider

    installed: list[int] = []

    def install(printer_id: int, outcome):
        client = _FakeJobClient(outcome)
        printer_manager._provider_clients[printer_id] = client
        printer_manager._providers[printer_id] = PrinterProvider.ELEGOO
        installed.append(printer_id)
        return client

    yield install
    for printer_id in installed:
        printer_manager._provider_clients.pop(printer_id, None)
        printer_manager._providers.pop(printer_id, None)


class TestProviderJobCommands:
    @pytest.mark.asyncio
    @pytest.mark.integration
    @pytest.mark.parametrize("action", ["pause", "resume", "stop"])
    async def test_accepted(self, async_client: AsyncClient, printer_factory, provider_client, action):
        from backend.app.services.printer_providers.elegoo.client import COMMAND_ACCEPTED, CommandOutcome

        printer = await printer_factory(provider="elegoo", model="Centauri 2", access_code="")
        client = provider_client(printer.id, CommandOutcome(COMMAND_ACCEPTED, 1021, 0))
        response = await async_client.post(f"/api/v1/printers/{printer.id}/print/{action}")
        assert response.status_code == 200
        assert client.calls == [action]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_no_reply_is_not_a_success(self, async_client: AsyncClient, printer_factory, provider_client):
        from backend.app.services.printer_providers.elegoo.client import COMMAND_NO_REPLY, CommandOutcome

        printer = await printer_factory(provider="elegoo", model="Centauri 2", access_code="")
        provider_client(printer.id, CommandOutcome(COMMAND_NO_REPLY, 1022))
        response = await async_client.post(f"/api/v1/printers/{printer.id}/print/stop")
        assert response.status_code == 504
        assert response.json()["detail"]["code"] == "no_reply"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_refusal_carries_the_reason(self, async_client: AsyncClient, printer_factory, provider_client):
        from backend.app.services.printer_providers.elegoo.client import COMMAND_REFUSED, CommandOutcome

        printer = await printer_factory(provider="elegoo", model="Centauri 2", access_code="")
        provider_client(
            printer.id,
            CommandOutcome(COMMAND_REFUSED, 1021, message="job_pause_resume is not enabled for this printer"),
        )
        response = await async_client.post(f"/api/v1/printers/{printer.id}/print/pause")
        assert response.status_code == 409
        assert "not enabled" in response.json()["detail"]["message"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_queue_stop_keeps_the_row_when_the_printer_does_not_confirm(
        self, async_client: AsyncClient, printer_factory, provider_client, db_session
    ):
        from backend.app.models.print_queue import PrintQueueItem
        from backend.app.services.printer_providers.elegoo.client import COMMAND_NOT_SENT, CommandOutcome

        printer = await printer_factory(provider="elegoo", model="Centauri 2", access_code="")
        item = PrintQueueItem(printer_id=printer.id, status="printing")
        db_session.add(item)
        await db_session.commit()
        provider_client(printer.id, CommandOutcome(COMMAND_NOT_SENT, 1022, message="printer not connected"))
        response = await async_client.post(f"/api/v1/queue/{item.id}/stop")
        assert response.status_code == 400
        await db_session.refresh(item)
        assert item.status == "printing"


class TestCanvasCommands:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_catalogue(self, async_client: AsyncClient):
        response = await async_client.get("/api/v1/printer-providers/elegoo/canvas-filaments")
        assert response.status_code == 200
        body = response.json()
        assert body["brands"] == ["ELEGOO", "Generic"]
        assert {"name": "PLA Matte", "type": "PLA", "code": "0x0006"}.items() <= body["filaments"][6].items()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_each_command_reaches_the_client(self, async_client: AsyncClient, printer_factory, provider_client):
        from backend.app.services.printer_providers.elegoo.client import COMMAND_ACCEPTED, CommandOutcome

        printer = await printer_factory(provider="elegoo", model="Centauri 2", access_code="")
        client = provider_client(printer.id, CommandOutcome(COMMAND_ACCEPTED, 2003, 0))
        base = f"/api/v1/printer-providers/printers/{printer.id}/canvas"
        responses = [
            await async_client.put(
                f"{base}/0/slots/2", json={"filament_name": "PETG", "color": "#000000", "brand": "Generic"}
            ),
            await async_client.put(f"{base}/auto-refill", json={"enabled": True}),
            await async_client.post(f"{base}/0/slots/2/load"),
            await async_client.post(f"{base}/0/slots/2/unload"),
        ]
        assert [r.status_code for r in responses] == [200, 200, 200, 200]
        assert client.calls == [
            ("slot", 0, 2, {"filament_name": "PETG", "color": "#000000", "brand": "Generic"}),
            ("auto_refill", True),
            ("load", 0, 2),
            ("unload", 0, 2),
        ]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_refusal_and_no_reply(self, async_client: AsyncClient, printer_factory, provider_client):
        from backend.app.services.printer_providers.elegoo.client import (
            COMMAND_NO_REPLY,
            COMMAND_REFUSED,
            CommandOutcome,
        )

        printer = await printer_factory(provider="elegoo", model="Centauri 2", access_code="")
        base = f"/api/v1/printer-providers/printers/{printer.id}/canvas"
        provider_client(
            printer.id, CommandOutcome(COMMAND_REFUSED, 2001, message="not possible while the printer is printing")
        )
        response = await async_client.post(f"{base}/0/slots/0/load")
        assert response.status_code == 409
        assert "printing" in response.json()["detail"]["message"]
        provider_client(printer.id, CommandOutcome(COMMAND_NO_REPLY, 2002))
        response = await async_client.post(f"{base}/0/slots/0/unload")
        assert response.status_code == 504
        assert response.json()["detail"]["code"] == "no_reply"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_bad_colour_never_reaches_the_printer(
        self, async_client: AsyncClient, printer_factory, provider_client
    ):
        from backend.app.services.printer_providers.elegoo.client import COMMAND_ACCEPTED, CommandOutcome

        printer = await printer_factory(provider="elegoo", model="Centauri 2", access_code="")
        client = provider_client(printer.id, CommandOutcome(COMMAND_ACCEPTED, 2003, 0))
        response = await async_client.put(
            f"/api/v1/printer-providers/printers/{printer.id}/canvas/0/slots/0",
            json={"filament_name": "PLA", "color": "red"},
        )
        assert response.status_code == 422
        assert client.calls == []

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_not_connected(self, async_client: AsyncClient, printer_factory):
        printer = await printer_factory(provider="elegoo", model="Centauri 2", access_code="")
        response = await async_client.put(
            f"/api/v1/printer-providers/printers/{printer.id}/canvas/auto-refill", json={"enabled": False}
        )
        assert response.status_code == 400
