"""Request/response of the build-plate endpoint."""

from pydantic import BaseModel, Field

from backend.app.schemas.library import FileUploadResponse


class BuildPlateItem(BaseModel):
    """One plate of a library project 3MF that goes into every set."""

    file_id: int
    # Plate number in the source file, as the slicer shows it (1-based).
    plate: int = Field(default=1, ge=1, le=200)
    # How many copies of this plate make one set.
    copies: int = Field(default=1, ge=1, le=50)
    # Keep only these objects of the plate (names as listed by /files/{id}/plates,
    # extension optional, case-insensitive). None = the whole plate.
    only_objects: list[str] | None = Field(default=None, max_length=50)


class BuildPlateRequest(BaseModel):
    """Lay plates of existing project 3MFs out on one new plate, ready to slice without arrange."""

    items: list[BuildPlateItem] = Field(..., min_length=1, max_length=20)
    # Number of sets on the bed; None = as many as fit.
    sets: int | None = Field(default=None, ge=1, le=400)
    # Take printer/process/filament settings (and so the bed) from another project 3MF.
    settings_from_file_id: int | None = None
    # Lay out for the bed of this printer preset (local preset id), e.g. the one the
    # plate will be sliced with: a designer file made for a bigger printer would
    # otherwise land off the target's bed.
    bed_preset_id: int | None = None
    # Gap between pieces in mm; None = 4 for flat pieces, 6 or 10 (with brim) for taller ones.
    gap_mm: float | None = Field(default=None, ge=0, le=50)
    # Print every object with this filament (1-based), e.g. to put pieces of two models on one colour.
    extruder: int | None = Field(default=None, ge=1, le=16)
    # Colour of the filament the plate uses, "#RRGGBB"; needs a single-filament plate.
    filament_colour: str | None = Field(default=None, max_length=9)
    plate_name: str | None = Field(default=None, max_length=100)
    # Name of the new library file; ``.3mf`` is appended when missing.
    filename: str = Field(..., min_length=1, max_length=255)
    folder_id: int | None = None


class BuildPlateResponse(BaseModel):
    file: FileUploadResponse
    sets: int
    # Most sets that fit with these settings (same as ``sets`` when the request left it empty).
    max_sets: int | None = None
    pieces: int
    bed_mm: list[float]
    filaments: list[int]
    objects: list[str]
