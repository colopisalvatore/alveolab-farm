"""Integration tests for POST /library/files/build-plate (project 3MF plates -> one packed plate)."""

import io
import json
import re
import zipfile

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from backend.app.models.library import LibraryFile
from backend.app.models.local_preset import LocalPreset
from backend.tests._fixtures.plate_3mf import snowman_like


async def _upload(client: AsyncClient, name: str, content: bytes) -> int:
    resp = await client.post(
        "/api/v1/library/files",
        files={"file": (name, content, "application/octet-stream")},
        params={"generate_stl_thumbnails": "false"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


class TestBuildPlate:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_builds_a_new_library_file_with_the_sets(self, async_client: AsyncClient, db_session):
        src = await _upload(async_client, "Snowman.3mf", snowman_like())

        resp = await async_client.post(
            "/api/v1/library/files/build-plate",
            json={"items": [{"file_id": src, "plate": 2}], "sets": 5, "filename": "Eyes x5"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["file"]["filename"] == "Eyes x5.3mf"
        assert body["sets"] == 5 and body["pieces"] == 10
        assert body["max_sets"] >= 5
        assert body["bed_mm"] == [256.0, 256.0]
        assert body["filaments"] == [3]
        assert body["objects"] == ["Eye.stl"]

        row = (await db_session.execute(select(LibraryFile).where(LibraryFile.id == body["file"]["id"]))).scalar_one()
        assert row.source_type == "plate_build"

        content = (await async_client.get(f"/api/v1/library/files/{body['file']['id']}/download")).content
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            model = zf.read("3D/3dmodel.model").decode()
        assert len(re.findall(r"<item objectid=", model)) == 10
        # The source is untouched.
        assert (await async_client.get(f"/api/v1/library/files/{src}")).status_code == 200

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_settings_from_another_file(self, async_client: AsyncClient):
        src = await _upload(async_client, "Snowman.3mf", snowman_like())
        mini = await _upload(async_client, "Mini.3mf", snowman_like(bed=180, exclude=False, tag="_mini"))

        resp = await async_client.post(
            "/api/v1/library/files/build-plate",
            json={"items": [{"file_id": src, "plate": 1}], "settings_from_file_id": mini, "filename": "Body mini"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["bed_mm"] == [180.0, 180.0]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_builder_errors_are_400_with_the_reason(self, async_client: AsyncClient):
        src = await _upload(async_client, "Snowman.3mf", snowman_like())

        resp = await async_client.post(
            "/api/v1/library/files/build-plate",
            json={"items": [{"file_id": src, "plate": 9}], "filename": "x"},
        )
        assert resp.status_code == 400
        assert "no plate 9" in resp.json()["detail"]

        resp = await async_client.post(
            "/api/v1/library/files/build-plate",
            json={"items": [{"file_id": src, "plate": 1}], "sets": 400, "filename": "x"},
        )
        assert resp.status_code == 400
        assert "at most" in resp.json()["detail"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_unknown_file_is_404(self, async_client: AsyncClient):
        resp = await async_client.post(
            "/api/v1/library/files/build-plate",
            json={"items": [{"file_id": 999999, "plate": 1}], "filename": "x"},
        )
        assert resp.status_code == 404


class TestBuildPlateForAPrinterPreset:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_bed_of_a_local_printer_preset(self, async_client: AsyncClient, db_session):
        src = await _upload(async_client, "Big.3mf", snowman_like(bed=330))
        printer = LocalPreset(
            name="Elegoo Centauri 2 0.4 nozzle",
            preset_type="printer",
            source="orcaslicer",
            setting=json.dumps({"printable_area": ["0x0", "200x0", "200x200", "0x200"], "bed_exclude_area": []}),
        )
        process = LocalPreset(name="proc", preset_type="process", source="orcaslicer", setting="{}")
        db_session.add_all([printer, process])
        await db_session.commit()

        resp = await async_client.post(
            "/api/v1/library/files/build-plate",
            json={"items": [{"file_id": src, "plate": 1}], "bed_preset_id": printer.id, "filename": "for cc2"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["bed_mm"] == [200.0, 200.0]

        for bad in (process.id, 999999):
            resp = await async_client.post(
                "/api/v1/library/files/build-plate",
                json={"items": [{"file_id": src, "plate": 1}], "bed_preset_id": bad, "filename": "x"},
            )
            assert resp.status_code == 404
