"""Finished prints no queue row owns (started from the slicer or the touchscreen).

``GET /api/v1/alveolab/queue/outside-prints``. The live ones (still printing) come from the
printer status in the browser; see ``frontend/src/alveolab/outside-queue``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import require_ownership_permission
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.archive import PrintArchive
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.printer import Printer
from backend.app.models.user import User
from backend.app.schemas.print_queue import UTCDatetime

router = APIRouter(prefix="/alveolab/queue", tags=["alveolab"])


class OutsideQueuePrintResponse(BaseModel):
    """A finished print that no queue row owns: started from the slicer, the
    touchscreen or the archive's reprint button. Read from its archive."""

    archive_id: int
    printer_id: int | None = None
    printer_name: str | None = None
    name: str
    # The archive's own status: completed, failed, cancelled or aborted.
    status: str
    started_at: UTCDatetime = None
    completed_at: UTCDatetime = None
    created_at: UTCDatetime = None
    print_time_seconds: int | None = None
    filament_used_grams: float | None = None
    filament_type: str | None = None
    filament_color: str | None = None
    failure_reason: str | None = None
    has_thumbnail: bool = False


# Archive statuses a print can end with. "printing" ones are live and come
# from the printer status instead.
OUTSIDE_QUEUE_END_STATUSES = ("completed", "failed", "cancelled", "aborted")


@router.get("/outside-prints", response_model=list[OutsideQueuePrintResponse])
async def list_outside_queue_prints(
    printer_id: int | None = Query(None, description="Filter by printer"),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    auth_result: tuple[User | None, bool] = Depends(
        require_ownership_permission(
            Permission.ARCHIVES_READ_ALL,
            Permission.ARCHIVES_READ_OWN,
        )
    ),
):
    """Finished prints that no queue row refers to, newest first.

    A farm prints from the slicer as much as from the queue; without these the
    queue history only shows part of what the printers did. A queue print
    reuses its row's archive, so "no row points at the archive" is what marks
    a print as started outside the queue.
    """
    user, can_read_all = auth_result
    owned_by_queue = select(PrintQueueItem.id).where(PrintQueueItem.archive_id == PrintArchive.id).exists()
    query = (
        select(PrintArchive, Printer.name)
        .outerjoin(Printer, Printer.id == PrintArchive.printer_id)
        .where(
            PrintArchive.deleted_at.is_(None),
            PrintArchive.status.in_(OUTSIDE_QUEUE_END_STATUSES),
            ~owned_by_queue,
        )
        .order_by(func.coalesce(PrintArchive.completed_at, PrintArchive.created_at).desc(), PrintArchive.id.desc())
        .limit(limit)
    )
    if printer_id is not None:
        query = query.where(PrintArchive.printer_id == printer_id)
    if user is not None and not can_read_all:
        query = query.where(PrintArchive.created_by_id == user.id)
    rows = (await db.execute(query)).all()
    return [
        OutsideQueuePrintResponse(
            archive_id=archive.id,
            printer_id=archive.printer_id,
            printer_name=printer_name,
            name=archive.print_name or archive.filename,
            status=archive.status,
            started_at=archive.started_at,
            completed_at=archive.completed_at,
            created_at=archive.created_at,
            print_time_seconds=archive.print_time_seconds,
            filament_used_grams=archive.filament_used_grams,
            filament_type=archive.filament_type,
            filament_color=archive.filament_color,
            failure_reason=archive.failure_reason,
            has_thumbnail=bool(archive.thumbnail_path),
        )
        for archive, printer_name in rows
    ]
