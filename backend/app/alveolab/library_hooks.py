"""Hooks of ``routes/library.py`` / ``routes/archives.py`` for printers that take plain G-code (Centauri 2).

All of the logic lives here; the routes only make the calls listed in ALVEOLAB_HOOKS.md.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.alveolab.config import modules_enabled

if TYPE_CHECKING:
    from backend.app.models.library import LibraryFolder
    from backend.app.schemas.slicer import SliceResponse

logger = logging.getLogger(__name__)


def accepts_raw_gcode(content: bytes) -> bool:
    """A raw .gcode upload is how OrcaSlicer / ElegooSlicer hand a job to a Centauri 2.

    Accepted only when the file itself says it was sliced for one; every other raw
    G-code still gets Bambuddy's own message.
    """
    if not modules_enabled():
        return False
    from backend.app.alveolab.printer_providers.elegoo.files import gcode_sliced_for_cc2

    return gcode_sliced_for_cc2(content)


def sliced_for_metadata(file_path: Path) -> dict:
    """``{"sliced_for_model": ...}`` of an uploaded G-code, so the queue's model check keeps it off the wrong machine."""
    if not modules_enabled():
        return {}
    try:
        from backend.app.alveolab.printer_providers.elegoo.files import check_printable_on_cc2

        check = check_printable_on_cc2(file_path)
        if check.sliced_for:
            return {"sliced_for_model": check.sliced_for}
    except Exception as e:
        logger.warning("Failed to read the target printer of %s: %s", file_path.name, e)
    return {}


async def with_printer_bed(db: AsyncSession, request):
    """A slice for a named printer takes the build plate mounted on it, unless the caller picked one."""
    if request.printer_id is None or not modules_enabled():
        return request
    from backend.app.models.printer import Printer

    target = (await db.execute(select(Printer).where(Printer.id == request.printer_id))).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=400, detail="Printer not found")
    if not request.bed_type and target.bed_type:
        request = request.model_copy(update={"bed_type": target.bed_type})
    return request


async def persist_cc2_slice(
    db: AsyncSession,
    *,
    result,
    base_name: str,
    target_folder,
    folder_id: int | None,
    used_embedded_settings: bool,
    extra_metadata: dict | None,
    current_user_id: int | None,
):
    """A Centauri 2 takes plain G-code only (a 3MF jams it over LAN) and the slicer CLI always returns a 3MF.

    When the result was sliced for a CC2, keep just the plate's G-code, with the plate picture, and
    return the ``SliceResponse``; None for any other printer (the caller carries on with the 3MF).
    """
    if not modules_enabled():
        return None
    from backend.app.alveolab.printer_providers.elegoo.files import TooManyPlatesForCC2, cc2_gcode_from_sliced_3mf

    try:
        cc2 = await asyncio.to_thread(cc2_gcode_from_sliced_3mf, result.content)
    except TooManyPlatesForCC2 as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if cc2 is None:
        return None
    return await _persist_cc2_gcode(
        db,
        cc2=cc2,
        base_name=base_name,
        target_folder=target_folder,
        folder_id=folder_id,
        result=result,
        used_embedded_settings=used_embedded_settings,
        extra_metadata=extra_metadata,
        current_user_id=current_user_id,
    )


async def _persist_cc2_gcode(
    db: AsyncSession,
    *,
    cc2,
    base_name: str,
    target_folder: LibraryFolder | None,
    folder_id: int | None,
    result,
    used_embedded_settings: bool,
    extra_metadata: dict | None,
    current_user_id: int | None,
) -> SliceResponse:
    """Save the plain G-code of a slice made for a Centauri 2 as a ``.gcode`` library file.

    Same row as ``slice_and_persist`` writes for a 3MF, with what the queue's
    CC2 gates read: ``sliced_for_model`` and the plate (``bed_type``).
    """
    from backend.app.api.routes.library import (
        _resolve_slice_destination,
        _stored_file_path,
        get_library_thumbnails_dir,
        to_relative_path,
    )
    from backend.app.models.library import LibraryFile
    from backend.app.schemas.slicer import SliceResponse

    out_filename = f"{base_name}.gcode"
    out_path, out_is_external, external_fallback = _resolve_slice_destination(
        target_folder, out_filename, managed_suffix=".gcode"
    )
    if out_is_external:
        out_filename = out_path.name
    if external_fallback:
        logger.warning(
            "Slice output %s stored in managed library instead of external folder %s: %s",
            out_filename,
            target_folder.external_path if target_folder else None,
            external_fallback,
        )
    await asyncio.to_thread(out_path.write_bytes, cc2.gcode)

    thumbnail_relative: str | None = None
    if cc2.thumbnail:
        thumb_path = get_library_thumbnails_dir() / f"{uuid.uuid4().hex}.png"
        await asyncio.to_thread(thumb_path.write_bytes, cc2.thumbnail)
        thumbnail_relative = to_relative_path(thumb_path)

    # Some sidecar builds leave the X-Filament-Used-* headers unset: the
    # G-code's own totals are the fallback, as ThreeMFParser is for a 3MF.
    metadata: dict = {
        "print_time_seconds": result.print_time_seconds,
        "filament_used_g": result.filament_used_g or cc2.filament_used_g or 0.0,
        "filament_used_mm": result.filament_used_mm or cc2.filament_used_mm or 0.0,
        "sliced_for_model": cc2.sliced_for,
    }
    for key, value in (("bed_type", cc2.bed_type), ("layer_height", cc2.layer_height)):
        if value:
            metadata[key] = value
    if used_embedded_settings:
        metadata["used_embedded_settings"] = True
    if external_fallback:
        metadata["external_write_fallback"] = external_fallback
    if extra_metadata:
        metadata.update(extra_metadata)

    new_file = LibraryFile(
        folder_id=folder_id,
        is_external=out_is_external,
        filename=out_filename,
        file_path=_stored_file_path(out_path, out_is_external),
        file_type="gcode",
        file_size=len(cc2.gcode),
        file_hash=hashlib.sha256(cc2.gcode).hexdigest(),
        thumbnail_path=thumbnail_relative,
        file_metadata=metadata,
        source_type="sliced",
        created_by_id=current_user_id,
    )
    db.add(new_file)
    await db.commit()
    return SliceResponse(
        library_file_id=new_file.id,
        name=new_file.filename,
        print_time_seconds=result.print_time_seconds,
        filament_used_g=metadata["filament_used_g"],
        filament_used_mm=metadata["filament_used_mm"],
        used_embedded_settings=used_embedded_settings,
        external_write_fallback=external_fallback,
    )
