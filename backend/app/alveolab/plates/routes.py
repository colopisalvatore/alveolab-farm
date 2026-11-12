"""Build-plate endpoint: ``POST /api/v1/alveolab/library/build-plate``.

Was ``POST /library/files/build-plate`` on the fork; moved here so ``routes/library.py`` stays upstream.
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.alveolab.plates.schemas import BuildPlateRequest, BuildPlateResponse
from backend.app.core.auth import require_permission_if_auth_enabled
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.library import LibraryFile, LibraryFolder
from backend.app.models.user import User
from backend.app.schemas.library import FileUploadResponse
from backend.app.utils.filename import InvalidFilenameError, validate_print_filename

router = APIRouter(prefix="/alveolab/library", tags=["alveolab"])


@router.post("/build-plate", response_model=BuildPlateResponse)
async def build_plate_file(
    request: BuildPlateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(require_permission_if_auth_enabled(Permission.LIBRARY_UPLOAD)),
):
    """Lay plates of project 3MFs out on one new plate, ready to slice.

    The production step between a designer's split 3MF and the printer: N sets of
    one plate (or of plates of several models, all on one filament), only some
    objects of a plate, or a plate moved to another printer's settings. Pieces
    are turned to their smallest footprint and packed inside the printable area,
    clear of the bed's exclusion zone. The new file is a project 3MF with one
    plate; slice it without arrange, the layout is already done. Sources are
    left untouched.
    """
    from backend.app.alveolab.plates.builder import PlateBuildError, PlateSource, build_plate
    from backend.app.api.routes.library import (
        _ensure_library_file_visible,
        _resolve_source_disk_path,
        is_sliced_file,
        save_3mf_bytes_to_library,
    )

    filename = request.filename.strip()
    if not filename.lower().endswith(".3mf"):
        filename = f"{filename}.3mf"
    try:
        validate_print_filename(filename)
    except InvalidFilenameError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    if request.folder_id is not None:
        folder = (
            await db.execute(select(LibraryFolder).where(LibraryFolder.id == request.folder_id))
        ).scalar_one_or_none()
        if folder is None:
            raise HTTPException(status_code=404, detail="Folder not found")

    can_read_all = current_user is None or current_user.has_permission(Permission.LIBRARY_READ_ALL.value)
    wanted_ids = {item.file_id for item in request.items}
    if request.settings_from_file_id is not None:
        wanted_ids.add(request.settings_from_file_id)
    rows = (await db.execute(LibraryFile.active().where(LibraryFile.id.in_(wanted_ids)))).scalars().all()
    by_id = {row.id: row for row in rows}

    async def read_project(file_id: int) -> tuple[LibraryFile, bytes]:
        lib_file = _ensure_library_file_visible(by_id.get(file_id), current_user, can_read_all)
        lower = lib_file.filename.lower()
        if not lower.endswith(".3mf") or is_sliced_file(lib_file.filename):
            raise HTTPException(status_code=400, detail=f"Not a project 3MF: {lib_file.filename}")
        path = _resolve_source_disk_path(lib_file)
        if path is None or not path.exists():
            raise HTTPException(status_code=404, detail=f"Source file missing on disk: {lib_file.filename}")
        return lib_file, await asyncio.to_thread(path.read_bytes)

    # Gate every file before reading any, so an unknown id answers the same 404
    # whatever else the request names.
    for file_id in wanted_ids:
        _ensure_library_file_visible(by_id.get(file_id), current_user, can_read_all)

    contents: dict[int, tuple[LibraryFile, bytes]] = {}
    for item in request.items:
        if item.file_id not in contents:
            contents[item.file_id] = await read_project(item.file_id)
    sources = [
        PlateSource(
            data=contents[item.file_id][1],
            plate=item.plate,
            copies=item.copies,
            only_objects=item.only_objects,
            label=contents[item.file_id][0].filename,
        )
        for item in request.items
    ]
    settings_from = None
    if request.settings_from_file_id is not None:
        settings_from = (await read_project(request.settings_from_file_id))[1]
    bed_from = None
    if request.bed_preset_id is not None:
        from backend.app.models.local_preset import LocalPreset

        preset = (
            await db.execute(select(LocalPreset).where(LocalPreset.id == request.bed_preset_id))
        ).scalar_one_or_none()
        if preset is None or preset.preset_type != "printer":
            raise HTTPException(status_code=404, detail="Printer preset not found")
        try:
            bed_from = json.loads(preset.setting)
        except (TypeError, ValueError) as e:
            raise HTTPException(status_code=400, detail="The printer preset is not readable") from e
        if not isinstance(bed_from, dict):
            raise HTTPException(status_code=400, detail="The printer preset is not readable")

    try:
        result = await asyncio.to_thread(
            build_plate,
            sources,
            sets=request.sets,
            settings_from=settings_from,
            bed_from=bed_from,
            gap_mm=request.gap_mm,
            extruder=request.extruder,
            filament_colour=request.filament_colour,
            plate_name=request.plate_name,
        )
    except PlateBuildError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    library_file, _ = await save_3mf_bytes_to_library(
        db,
        file_bytes=result.content,
        filename=filename,
        folder_id=request.folder_id,
        source_type="plate_build",
        owner_id=current_user.id if current_user else None,
    )

    return BuildPlateResponse(
        file=FileUploadResponse(
            id=library_file.id,
            filename=library_file.filename,
            file_type=library_file.file_type,
            file_size=library_file.file_size,
            thumbnail_path=library_file.thumbnail_path,
            metadata=library_file.file_metadata,
        ),
        sets=result.sets,
        max_sets=result.max_sets,
        pieces=result.items,
        bed_mm=list(result.bed),
        filaments=result.extruders,
        objects=result.objects,
    )
