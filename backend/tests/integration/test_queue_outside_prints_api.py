"""``GET /queue/outside-prints``: finished prints no queue row owns.

A farm prints from the slicer as much as from the queue. The queue history
only listed queue rows, so most of what the printers did never showed up.
"""

from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient

from backend.app.models.print_queue import PrintQueueItem


@pytest.mark.asyncio
async def test_lists_only_finished_prints_without_a_queue_row(
    async_client: AsyncClient, db_session, printer_factory, archive_factory
):
    printer = await printer_factory(name="P1S-2")
    now = datetime.now(timezone.utc)
    from_slicer = await archive_factory(
        printer.id, print_name="Snowman", status="completed", completed_at=now - timedelta(hours=1)
    )
    stopped = await archive_factory(printer.id, print_name="Reindeer", status="aborted", completed_at=now)
    from_queue = await archive_factory(printer.id, print_name="Queued", status="completed")
    await archive_factory(printer.id, print_name="Live", status="printing")
    await archive_factory(printer.id, print_name="Gone", status="completed", deleted_at=now)
    db_session.add(PrintQueueItem(printer_id=printer.id, archive_id=from_queue.id, status="completed"))
    await db_session.commit()

    response = await async_client.get("/api/v1/queue/outside-prints")

    assert response.status_code == 200
    rows = response.json()
    assert [r["archive_id"] for r in rows] == [stopped.id, from_slicer.id]
    assert rows[0]["status"] == "aborted"
    assert rows[1]["name"] == "Snowman"
    assert rows[1]["printer_name"] == "P1S-2"


@pytest.mark.asyncio
async def test_filters_by_printer(async_client: AsyncClient, printer_factory, archive_factory):
    first = await printer_factory(name="A")
    second = await printer_factory(name="B")
    await archive_factory(first.id, print_name="On A")
    on_b = await archive_factory(second.id, print_name="On B")

    response = await async_client.get(f"/api/v1/queue/outside-prints?printer_id={second.id}")

    assert [r["archive_id"] for r in response.json()] == [on_b.id]
