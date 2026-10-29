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
