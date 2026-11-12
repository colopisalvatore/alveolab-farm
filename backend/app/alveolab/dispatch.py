"""Queue dispatch to a non-Bambu printer (ALVEOLAB Farm, Centauri module).

Called by ``PrintScheduler._start_print`` in place of the Bambu path (FTPS upload +
``project_file`` command), which does not apply to these printers.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.alveolab import provider_jobs
from backend.app.alveolab.printer_providers.base import Capability
from backend.app.core.websocket import ws_manager
from backend.app.models.archive import PrintArchive
from backend.app.models.print_queue import PrintQueueItem
from backend.app.services.notification_service import notification_service
from backend.app.services.printer_manager import printer_manager

logger = logging.getLogger(__name__)

# Wait for a provider printer to be idle after an upload: 30 x 0.5 s.
PROVIDER_READY_POLLS = 30
PROVIDER_READY_INTERVAL = 0.5

# queue item id -> display name, for failure notifications.
_job_names: dict[int, str] = {}


async def _fail_item(scheduler, db: AsyncSession, item: PrintQueueItem, printer, message: str, reason: str):
    item.status = "failed"
    item.error_message = message
    item.completed_at = datetime.now(timezone.utc)
    await db.commit()
    logger.warning("Queue item %s on printer %s failed: %s", item.id, printer.id, message)
    try:
        await notification_service.on_queue_job_failed(
            job_name=_job_names.get(item.id, f"#{item.id}"),
            printer_id=printer.id,
            printer_name=printer.name,
            reason=message,
            db=db,
        )
    except Exception:
        logger.debug("Queue item %s: failure notification not sent", item.id, exc_info=True)
    try:
        await ws_manager.send_queue_item_failed(
            user_id=item.created_by_id, queue_item_id=item.id, printer_id=printer.id, reason=reason
        )
    except Exception:
        pass
    await scheduler._power_off_if_needed(db, item)


async def start_print(
    scheduler,
    db: AsyncSession,
    item: PrintQueueItem,
    printer,
    archive: PrintArchive | None,
    file_path: Path,
    filename: str | None,
) -> None:
    """Dispatch a queue item to a non-Bambu printer (ELEGOO CC2, M4).

    Order matters and mirrors the Bambu path's safety points:
      1. the printer must declare upload + start, and the file must say it
         was sliced for this printer — a Bambu 3MF is refused here;
      2. upload (harmless: it only stores a file);
      3. CAS pending -> printing with a fresh ``billing_run_id``, committed
         before the command: that id is the job key, so even a crash right
         after the send cannot lead to a second start of this row;
      4. one start command. No retry and no watchdog re-queue: a start
         without reply leaves the row 'printing' and the printer's own
         status decides (``alveolab/provider_jobs.py``, stranded sweep).
    """
    from backend.app.alveolab.printer_providers.elegoo.client import (
        COMMAND_ACCEPTED,
        COMMAND_NO_REPLY,
        STARTABLE_STATES,
    )
    from backend.app.alveolab.printer_providers.elegoo.files import (
        bed_type_mismatch,
        check_printable_on_cc2,
        printer_filename,
    )
    from backend.app.alveolab.printer_providers.elegoo.slots import file_filaments, plan_slot_map
    from backend.app.alveolab.printer_providers.elegoo.upload import upload_file

    _job_names[item.id] = Path(filename or file_path.name).stem
    client = printer_manager.get_provider_client(item.printer_id)
    capabilities = printer_manager.get_capabilities(item.printer_id)
    if (
        client is None
        or capabilities is None
        or not capabilities.supports(Capability.FILE_UPLOAD)
        or not capabilities.supports(Capability.JOB_START)
        or not callable(getattr(client, "start_print", None))
    ):
        await _fail_item(
            scheduler, db, item, printer, "This printer does not accept prints from the farm yet", "not_supported"
        )
        return

    check = await asyncio.to_thread(check_printable_on_cc2, file_path)
    if not check.ok:
        await _fail_item(scheduler, db, item, printer, f"File refused: {check.message}", "incompatible_file")
        return
    # The plate may have been flipped while the item waited in the queue.
    wrong_plate = bed_type_mismatch(check.bed_type, getattr(printer, "bed_type", None))
    if wrong_plate:
        await _fail_item(scheduler, db, item, printer, f"File refused: {wrong_plate}", "wrong_plate")
        return
    try:
        remote_name = printer_filename(filename or file_path.name)
    except ValueError as exc:
        await _fail_item(scheduler, db, item, printer, f"File refused: {exc}", "incompatible_file")
        return

    # Which CANVAS slot feeds each tool. Without it the CC2 reuses the last
    # mapping it was given, not the colour in the file (1/11/2026).
    telemetry = client.telemetry() if callable(getattr(client, "telemetry", None)) else None
    slots = list(getattr(telemetry, "multicolor_slots", None) or [])
    filaments = await asyncio.to_thread(file_filaments, file_path)
    slot_plan = plan_slot_map(filaments, slots)
    if slot_plan.problem:
        await _fail_item(
            scheduler, db, item, printer, f"Filament not loaded: {slot_plan.problem}", "filament_not_loaded"
        )
        return
    logger.info("Queue item %s: CANVAS mapping %s", item.id, "; ".join(slot_plan.picks) or "none (no CANVAS reported)")

    upload = await upload_file(printer.ip_address, file_path, remote_name, printer.access_code or None)
    if not upload.success:
        await _fail_item(
            scheduler,
            db,
            item,
            printer,
            f"Upload to the printer failed: {upload.message} ({upload.reason})",
            "upload_failed",
        )
        return

    await scheduler._propagate_owner_to_printer_manager(db, item)
    now_utc = datetime.now(timezone.utc)
    billing_run_id = str(uuid.uuid4())
    cas = await db.execute(
        update(PrintQueueItem)
        .where(PrintQueueItem.id == item.id)
        .where(PrintQueueItem.status == "pending")
        .values(status="printing", started_at=now_utc, billing_run_id=billing_run_id)
    )
    await db.commit()
    if cas.rowcount == 0:
        logger.info("Queue item %s no longer pending after upload — not starting it", item.id)
        return
    item.status = "printing"
    item.started_at = now_utc
    item.billing_run_id = billing_run_id
    if archive is not None:
        archive.billing_run_id = billing_run_id
        archive.wallet_charge_skipped = False
        await db.commit()

    # The CC2 reports "file transferring" while it stores the upload; the
    # SDK's own example waits before starting. Give it a few seconds to be
    # idle again rather than having the start refused as busy.
    for _ in range(PROVIDER_READY_POLLS):
        telemetry = client.telemetry() if callable(getattr(client, "telemetry", None)) else None
        if telemetry is None or telemetry.job_state in STARTABLE_STATES:
            break
        await asyncio.sleep(PROVIDER_READY_INTERVAL)

    printer_manager.set_awaiting_plate_clear(item.printer_id, False)
    provider_jobs.record_dispatch(item.printer_id, item.id, remote_name)
    pre_status = printer_manager.get_status(item.printer_id)
    outcome = await asyncio.to_thread(
        client.start_print,
        remote_name,
        job_key=billing_run_id,
        auto_bed_leveling=item.bed_levelling == "on",
        slot_map=slot_plan.slot_map,
    )
    if outcome.status in (COMMAND_ACCEPTED, COMMAND_NO_REPLY):
        # The command left the farm: the budget is spent and the printer is
        # held against another dispatch until it reports the new job.
        scheduler._unconfirmed_budget_reservations.discard(item.id)
        scheduler._mark_printer_dispatched(
            item.printer_id,
            getattr(pre_status, "state", None),
            getattr(pre_status, "subtask_id", None),
        )
        if outcome.status == COMMAND_NO_REPLY:
            item.error_message = "Start sent, the printer did not confirm it: check the printer"
            await db.commit()
            logger.warning("Queue item %s: start sent to printer %s without reply", item.id, printer.id)
        else:
            logger.info("Queue item %s: print started on %s (%s)", item.id, printer.name, remote_name)
        return

    provider_jobs.forget_dispatch(item.printer_id)
    await _fail_item(
        scheduler,
        db,
        item,
        printer,
        f"The printer refused the start: {outcome.message}",
        "start_refused",
    )
