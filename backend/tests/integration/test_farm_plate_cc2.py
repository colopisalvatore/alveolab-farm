"""ALVEOLAB Farm: the build plate mounted on a printer, and slicing for the Centauri 2.

- ``printers.bed_type`` is set by hand (PATCH) when the plate is changed or flipped;
- a slice for a printer takes that plate unless the caller names one;
- a slice made for a CC2 is saved as plain ``.gcode`` (the CC2 jams on a 3MF);
- a CC2 G-code sliced for another plate is refused at queue time.
"""

import io
import zipfile

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select

from backend.app.models.library import LibraryFile
from backend.app.services.printer_providers.elegoo.files import check_printable_on_cc2
from backend.tests.integration.test_library_slice_api import (  # noqa: F401 - fixture
    _install_mock_sidecar,
    _wait_for_job,
    slice_test_setup,
)

CC2_COOL = (
    "; HEADER_BLOCK_START\n; HEADER_BLOCK_END\nM140 S35\nG28\n"
    "; curr_bed_type = Cool Plate\n; printer_model = Elegoo Centauri 2\n"
    "; printer_settings_id = Elegoo Centauri 2 0.4 nozzle\n"
    "; filament used [g] = 12.50\n; filament used [mm] = 4100.2\n; layer_height = 0.16\n"
)


def _sliced_cc2(plates: int = 1) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        zf.writestr("3D/3dmodel.model", "<model/>")
        zf.writestr("Metadata/project_settings.config", '{"printer_model": "Elegoo Centauri 2"}')
        for n in range(1, plates + 1):
            zf.writestr(f"Metadata/plate_{n}.gcode", CC2_COOL)
            zf.writestr(f"Metadata/plate_{n}.png", b"\x89PNG plate")
    return out.getvalue()


def _sidecar(content: bytes, captured: dict | None = None, totals: bool = True):
    headers = {"x-print-time-seconds": "3600"}
    if totals:
        headers |= {"x-filament-used-g": "42.5", "x-filament-used-mm": "100"}

    def handler(request: httpx.Request) -> httpx.Response:
        if captured is not None and request.method == "POST":
            captured["body"] = bytes(request.content)
        return httpx.Response(200, content=content, headers=headers)

    _install_mock_sidecar(handler)


def _presets(setup: dict) -> dict:
    return {
        "printer_preset_id": setup["printer_id"],
        "process_preset_id": setup["process_id"],
        "filament_preset_id": setup["filament_id"],
    }


def _slice_url(setup: dict) -> str:
    return f"/api/v1/library/files/{setup['src_file_id']}/slice"


class TestPlateOnThePrinter:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_set_and_clear_the_mounted_plate(self, async_client: AsyncClient, printer_factory):
        printer = await printer_factory(provider="elegoo", model="Centauri 2")
        assert (await async_client.get(f"/api/v1/printers/{printer.id}")).json()["bed_type"] is None

        resp = await async_client.patch(f"/api/v1/printers/{printer.id}", json={"bed_type": "Cool Plate"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["bed_type"] == "Cool Plate"

        resp = await async_client.patch(f"/api/v1/printers/{printer.id}", json={"bed_type": None})
        assert resp.json()["bed_type"] is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_only_known_plate_names(self, async_client: AsyncClient, printer_factory):
        printer = await printer_factory(provider="elegoo", model="Centauri 2")
        resp = await async_client.patch(f"/api/v1/printers/{printer.id}", json={"bed_type": "textured pei plate"})
        assert resp.json()["bed_type"] == "Textured PEI Plate"
        resp = await async_client.patch(f"/api/v1/printers/{printer.id}", json={"bed_type": "Textured PEI"})
        assert resp.status_code == 422


class TestSliceForTheCentauri:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_cc2_slice_is_saved_as_plain_gcode(self, async_client: AsyncClient, db_session, slice_test_setup):
        _sidecar(_sliced_cc2())
        resp = await async_client.post(_slice_url(slice_test_setup), json=_presets(slice_test_setup))
        final = await _wait_for_job(async_client, resp.json()["job_id"])
        assert final["status"] == "completed", final
        assert final["result"]["name"] == "Cube.gcode"
        assert final["result"]["filament_used_g"] == 42.5

        row = (
            await db_session.execute(select(LibraryFile).where(LibraryFile.id == final["result"]["library_file_id"]))
        ).scalar_one()
        assert row.file_type == "gcode"
        assert row.file_metadata["sliced_for_model"] == "Elegoo Centauri 2"
        assert row.file_metadata["bed_type"] == "Cool Plate"
        assert row.thumbnail_path
        # Stored under a .gcode name: dispatch and the CC2 check go by the name on disk.
        assert row.file_path.endswith(".gcode") and not row.file_path.endswith(".gcode.3mf")
        on_disk_path = slice_test_setup["tmp_path"] / row.file_path
        assert on_disk_path.read_bytes() == CC2_COOL.encode()
        check = check_printable_on_cc2(on_disk_path)
        assert check.ok and check.bed_type == "Cool Plate"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_grams_come_from_the_gcode_when_the_sidecar_does_not_say(
        self, async_client: AsyncClient, db_session, slice_test_setup
    ):
        _sidecar(_sliced_cc2(), totals=False)
        resp = await async_client.post(_slice_url(slice_test_setup), json=_presets(slice_test_setup))
        final = await _wait_for_job(async_client, resp.json()["job_id"])
        assert final["result"]["filament_used_g"] == 12.5
        row = (
            await db_session.execute(select(LibraryFile).where(LibraryFile.id == final["result"]["library_file_id"]))
        ).scalar_one()
        assert row.file_metadata["filament_used_mm"] == 4100.2
        assert row.file_metadata["layer_height"] == 0.16

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_two_cc2_plates_fail_the_job_with_the_reason(self, async_client: AsyncClient, slice_test_setup):
        _sidecar(_sliced_cc2(plates=2))
        resp = await async_client.post(_slice_url(slice_test_setup), json=_presets(slice_test_setup))
        final = await _wait_for_job(async_client, resp.json()["job_id"])
        assert final["status"] == "failed"
        assert "slice one plate" in str(final)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_slice_for_a_printer_takes_its_plate(
        self, async_client: AsyncClient, db_session, slice_test_setup, printer_factory
    ):
        printer = await printer_factory(provider="elegoo", model="Centauri 2")
        printer.bed_type = "Cool Plate"
        await db_session.commit()

        captured: dict = {}
        _sidecar(_sliced_cc2(), captured)
        resp = await async_client.post(
            _slice_url(slice_test_setup), json={**_presets(slice_test_setup), "printer_id": printer.id}
        )
        assert (await _wait_for_job(async_client, resp.json()["job_id"]))["status"] == "completed"
        assert b'"curr_bed_type": "Cool Plate"' in captured["body"]

        # A plate named by the caller wins.
        _sidecar(_sliced_cc2(), captured)
        resp = await async_client.post(
            _slice_url(slice_test_setup),
            json={**_presets(slice_test_setup), "printer_id": printer.id, "bed_type": "Textured PEI Plate"},
        )
        assert (await _wait_for_job(async_client, resp.json()["job_id"]))["status"] == "completed"
        assert b'"curr_bed_type": "Textured PEI Plate"' in captured["body"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_unknown_printer_is_400(self, async_client: AsyncClient, slice_test_setup):
        resp = await async_client.post(
            _slice_url(slice_test_setup), json={**_presets(slice_test_setup), "printer_id": 999999}
        )
        assert resp.status_code == 400


class TestQueueForTheMountedPlate:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_gcode_for_another_plate_is_refused_at_queue_time(
        self, async_client: AsyncClient, db_session, printer_factory, tmp_path
    ):
        path = tmp_path / "orso.gcode"
        path.write_text(CC2_COOL, encoding="utf-8")
        lib = LibraryFile(
            filename="orso.gcode",
            file_path=str(path),
            file_type="gcode",
            file_size=path.stat().st_size,
            file_metadata={"sliced_for_model": "Elegoo Centauri 2"},
        )
        db_session.add(lib)
        centauri = await printer_factory(provider="elegoo", model="Centauri 2")
        centauri.bed_type = "Textured PEI Plate"
        await db_session.commit()
        await db_session.refresh(lib)

        body = {"printer_id": centauri.id, "library_file_id": lib.id, "manual_start": True}
        refused = await async_client.post("/api/v1/queue/", json=body)
        assert refused.status_code == 400
        assert "Textured PEI Plate mounted" in refused.json()["detail"]

        await async_client.patch(f"/api/v1/printers/{centauri.id}", json={"bed_type": "Cool Plate"})
        accepted = await async_client.post("/api/v1/queue/", json=body)
        assert accepted.status_code == 200, accepted.text
