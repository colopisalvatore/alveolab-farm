"""Print start / end on non-Bambu printers (ALVEOLAB Farm, M4).

Bambu printers feed the archive and queue through ``main.on_print_start`` /
``main.on_print_complete``, which fetch the 3MF over FTPS. A provider printer
(ELEGOO CC2) reports :class:`JobEvent` instead, and this module applies it:

  * the queue item the farm dispatched to that printer is closed with the
    outcome the printer reported (finished / cancelled / failed), or as failed
    with an explicit "outcome not observed" message when the end was missed;
  * its archive gets the same status;
  * the printer waits for the plate to be cleared before the next job, like a
    Bambu after a print.

Prints the farm did not dispatch (started from the slicer or the touchscreen)
are left alone: the CC2 offers no way to fetch their file, so there is nothing
to archive, and inventing a record would be worse than having none.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select

from backend.app.core.database import async_session
from backend.app.models.archive import PrintArchive
from backend.app.models.print_queue import PrintQueueItem
from backend.app.services.printer_manager import printer_manager
from backend.app.services.printer_providers.base import JobState

logger = logging.getLogger(__name__)

OUTCOME_NOT_OBSERVED = (
    "The farm did not see how this print ended (the connection dropped or the printer "
    "went idle before finishing): check the part on the plate"
)

_QUEUE_STATUS = {
    JobState.FINISHED: "completed",
    JobState.CANCELLED: "cancelled",
    JobState.FAILED: "failed",
}


@dataclass(frozen=True)
class ProviderDispatch:
    queue_item_id: int
    printer_filename: str


# printer_id -> the job the farm last started on it. In memory only: after a
# restart the queue row (status 'printing' + its archive) is the record.
_dispatched: dict[int, ProviderDispatch] = {}


def record_dispatch(printer_id: int, queue_item_id: int, printer_filename: str) -> None:
    _dispatched[printer_id] = ProviderDispatch(queue_item_id, printer_filename)


def forget_dispatch(printer_id: int) -> None:
    _dispatched.pop(printer_id, None)


async def _printing_item(db, printer_id: int) -> PrintQueueItem | None:
    result = await db.execute(
        select(PrintQueueItem)
        .where(PrintQueueItem.printer_id == printer_id)
        .where(PrintQueueItem.status == "printing")
        .order_by(PrintQueueItem.started_at.desc())
    )
    return result.scalars().first()


async def _belongs(db, printer_id: int, item: PrintQueueItem, filename: str | None) -> bool:
    """True when the printer's job is the one the farm dispatched for *item*."""
    if not filename:
        return True  # the printer did not name its job: nothing contradicts the queue
    dispatch = _dispatched.get(printer_id)
    if dispatch is not None and dispatch.queue_item_id == item.id:
        return dispatch.printer_filename == filename
    if item.archive_id:
        from backend.app.services.printer_providers.elegoo.files import printer_filename

        archive = await db.get(PrintArchive, item.archive_id)
        if archive is not None and archive.filename:
            try:
                return printer_filename(archive.filename) == filename
            except ValueError:
                return False
    return False


async def _close_archive_of_stopped_item(db, printer_id: int, event) -> None:
    dispatch = _dispatched.get(printer_id)
    if dispatch is None or (event.filename and event.filename != dispatch.printer_filename):
        logger.info("Printer %s ended %r outside the farm queue", printer_id, event.filename)
        return
    item = await db.get(PrintQueueItem, dispatch.queue_item_id)
    forget_dispatch(printer_id)
    if item is None or not item.archive_id:
        return
    archive = await db.get(PrintArchive, item.archive_id)
    if archive is not None and archive.status not in ("completed", "failed", "cancelled"):
        archive.status = item.status if item.status in ("cancelled", "failed", "completed") else "cancelled"
        archive.completed_at = datetime.now(timezone.utc)
        if archive.status == "cancelled":
            archive.failure_reason = "userCancelled"
        await db.commit()


async def on_provider_job_event(printer_id: int, event) -> None:
    """Apply a ``JobEvent`` from a provider client. Never raises into the loop."""
    try:
        if event.kind == "started":
            await _on_started(printer_id, event)
        elif event.kind == "ended":
            await _on_ended(printer_id, event)
    except Exception:
        logger.exception("Provider job event on printer %s failed", printer_id)


async def _on_started(printer_id: int, event) -> None:
    async with async_session() as db:
        item = await _printing_item(db, printer_id)
        if item is None or not await _belongs(db, printer_id, item, event.filename):
            logger.info("Printer %s started %r outside the farm queue", printer_id, event.filename)
            return
        if item.archive_id:
            archive = await db.get(PrintArchive, item.archive_id)
            if archive is not None and archive.status != "printing":
                archive.status = "printing"
                archive.started_at = archive.started_at or datetime.now(timezone.utc)
                await db.commit()
        logger.info("Printer %s started queue item %s (%s)", printer_id, item.id, event.filename)


async def _on_ended(printer_id: int, event) -> None:
    # Whatever the outcome, something may be on the plate.
    printer_manager.set_awaiting_plate_clear(printer_id, True)
    async with async_session() as db:
        item = await _printing_item(db, printer_id)
        if item is None:
            # Stopped from the queue: the row is already 'cancelled', only its
            # archive is still waiting for the printer to confirm the end.
            await _close_archive_of_stopped_item(db, printer_id, event)
            return
        if not await _belongs(db, printer_id, item, event.filename):
            logger.info(
                "Printer %s ended %r (%s) outside the farm queue",
                printer_id,
                event.filename,
                getattr(event.outcome, "value", event.outcome),
            )
            return
        now = datetime.now(timezone.utc)
        status = _QUEUE_STATUS.get(event.outcome)
        item.completed_at = now
        if status is None:
            item.status = "failed"
            item.error_message = OUTCOME_NOT_OBSERVED
        else:
            item.status = status
            if status == "completed" and event.inferred:
                logger.info(
                    "Queue item %s: printer %s went idle after %.0f%%, counted as finished",
                    item.id,
                    printer_id,
                    event.progress or 0,
                )
        if item.archive_id:
            archive = await db.get(PrintArchive, item.archive_id)
            if archive is not None:
                archive.status = item.status
                archive.completed_at = now
                if status is None:
                    archive.failure_reason = "noStatusUpdate"
                elif status == "cancelled":
                    archive.failure_reason = "userCancelled"
        await db.commit()
        forget_dispatch(printer_id)
        logger.info(
            "Queue item %s on printer %s closed as %s (printer said %s%s)",
            item.id,
            printer_id,
            item.status,
            getattr(event.outcome, "value", event.outcome),
            ", inferred" if event.inferred else "",
        )
        try:
            from backend.app.services.print_batch import refresh_batch_status_for_item

            await refresh_batch_status_for_item(db, item.id)
            await db.commit()
        except Exception as exc:
            logger.warning("Batch refresh for queue item %s failed: %s", item.id, exc)
