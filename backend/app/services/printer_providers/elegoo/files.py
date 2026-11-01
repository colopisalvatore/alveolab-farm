"""Which files the farm may send to an ELEGOO Centauri Carbon 2 (M4).

A file goes to the CC2 only when it says, in its own slicer settings, that it
was sliced for a CC2. Anything else is refused, including a file that says
nothing: a 3MF sliced by Bambu Studio carries G-code for a Bambu machine, and
the name of a file proves nothing about who sliced it.

Only plain G-code is printable. A sliced 3MF ("Export plate sliced file" in
Orca), even one made for a CC2, is accepted by the printer over LAN and then
jams it: firmware 02.01.01.04 stays "printing" with sub-status 0, refuses the
stop (9999) and needs a power cycle (HW-M4-03, 31/10/2026). Its target printer
is still read, so the library records who it was sliced for.

Where the slicer writes the target printer:
  * plain G-code from OrcaSlicer / ElegooSlicer / Bambu Studio: comment lines
    ``; printer_model = ...`` and ``; printer_settings_id = ...`` in the
    settings block, which Orca writes at the end of the file;
  * a sliced 3MF: ``Metadata/project_settings.config`` (JSON, keys
    ``printer_model`` / ``printer_settings_id``), else the same comments in the
    plate G-code inside the zip.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

from backend.app.services.printer_providers.elegoo import protocol as p
from backend.app.services.printer_providers.elegoo.capabilities import is_cc2_model

# The settings block can be long (hundreds of KB with Orca); read both ends.
_HEAD_BYTES = 256 * 1024
_TAIL_BYTES = 1024 * 1024
_SETTING_RE = re.compile(rb"^;\s*(printer_model|printer_settings_id|curr_bed_type)\s*=\s*(.+?)\s*$", re.MULTILINE)
_PLATE_GCODE_RE = re.compile(r"^Metadata/plate_\d+\.gcode$")
_SAFE_NAME_RE = re.compile(r"[^\w.\- ()+]")

REASON_NOT_SLICED = "not_sliced"
REASON_WRONG_PRINTER = "sliced_for_another_printer"
REASON_UNKNOWN_TARGET = "target_printer_unknown"
REASON_UNSUPPORTED_TYPE = "unsupported_file_type"
REASON_3MF_JAMS_CC2 = "3mf_not_printable_on_cc2"

THREEMF_REFUSED_MESSAGE = (
    "a 3MF jams the Centauri 2 when sent over the network: "
    "export plain G-code from the slicer (Export G-code) and send that"
)


def refuses_3mf(filename: str | None) -> bool:
    """True for a file name the CC2 must not receive: any 3MF, sliced or not."""
    return bool(filename) and filename.lower().endswith(".3mf")


@dataclass(frozen=True)
class PrintableCheck:
    ok: bool
    sliced_for: str | None = None
    reason: str | None = None
    message: str | None = None
    # Build plate the file was sliced for (``curr_bed_type``), when it says.
    bed_type: str | None = None


def _settings_from_gcode_bytes(head: bytes, tail: bytes) -> dict[str, str]:
    found: dict[str, str] = {}
    for chunk in (head, tail):
        for key, value in _SETTING_RE.findall(chunk):
            found.setdefault(key.decode("ascii"), value.decode("utf-8", errors="replace").strip().strip('"'))
    return found


def _read_ends(handle, size: int) -> tuple[bytes, bytes]:
    head = handle.read(_HEAD_BYTES)
    if size <= _HEAD_BYTES:
        return head, b""
    handle.seek(max(_HEAD_BYTES, size - _TAIL_BYTES))
    return head, handle.read(_TAIL_BYTES)


def _gcode_settings(path: Path) -> dict[str, str]:
    with path.open("rb") as handle:
        return _settings_from_gcode_bytes(*_read_ends(handle, path.stat().st_size))


def _threemf_settings(path: Path) -> tuple[bool, dict[str, str]]:
    """(carries G-code, slicer settings) of a 3MF."""
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        plates = sorted(n for n in names if _PLATE_GCODE_RE.match(n))
        settings: dict[str, str] = {}
        if "Metadata/project_settings.config" in names:
            try:
                data = json.loads(zf.read("Metadata/project_settings.config"))
            except ValueError:
                data = {}
            if isinstance(data, dict):
                for key in ("printer_model", "printer_settings_id"):
                    value = data.get(key)
                    if isinstance(value, list):
                        value = value[0] if value else None
                    if isinstance(value, str) and value.strip():
                        settings[key] = value.strip()
        if plates and not settings:
            info = zf.getinfo(plates[0])
            with zf.open(plates[0]) as handle:
                head = handle.read(_HEAD_BYTES)
                tail = b""
                if info.file_size > _HEAD_BYTES:
                    # Zip members do not seek cheaply: stream to the tail.
                    remaining = info.file_size - _HEAD_BYTES
                    skip = max(0, remaining - _TAIL_BYTES)
                    while skip:
                        skip -= len(handle.read(min(skip, 1024 * 1024)))
                    tail = handle.read()
            settings = _settings_from_gcode_bytes(head, tail)
        return bool(plates), settings


def gcode_sliced_for_cc2(content: bytes) -> bool:
    """True when raw G-code *content* says it was sliced for a Centauri 2."""
    settings = _settings_from_gcode_bytes(content[:_HEAD_BYTES], content[-_TAIL_BYTES:])
    return is_cc2_model(settings.get("printer_model")) or is_cc2_model(settings.get("printer_settings_id"))


def check_printable_on_cc2(path: Path | str) -> PrintableCheck:
    """Whether *path* is a machine file sliced for a Centauri Carbon 2."""
    path = Path(path)
    name = path.name.lower()
    if not name.endswith(p.PRINTABLE_EXTENSIONS):
        return PrintableCheck(False, reason=REASON_UNSUPPORTED_TYPE, message="only .gcode files")
    try:
        if name.endswith(".3mf"):
            carries_gcode, settings = _threemf_settings(path)
            if not carries_gcode:
                return PrintableCheck(
                    False,
                    reason=REASON_NOT_SLICED,
                    message="the 3MF holds no G-code: slice it for the Centauri 2 first",
                )
        else:
            settings = _gcode_settings(path)
    except (OSError, zipfile.BadZipFile) as exc:
        return PrintableCheck(False, reason=REASON_UNSUPPORTED_TYPE, message=f"file not readable: {exc}")
    target = settings.get("printer_model") or settings.get("printer_settings_id")
    if not target:
        return PrintableCheck(
            False,
            reason=REASON_UNKNOWN_TARGET,
            message="the file does not say which printer it was sliced for",
        )
    if not (is_cc2_model(settings.get("printer_model")) or is_cc2_model(settings.get("printer_settings_id"))):
        return PrintableCheck(
            False,
            sliced_for=target,
            reason=REASON_WRONG_PRINTER,
            message=f"sliced for {target}, not for a Centauri 2",
        )
    if name.endswith(".3mf"):
        return PrintableCheck(False, sliced_for=target, reason=REASON_3MF_JAMS_CC2, message=THREEMF_REFUSED_MESSAGE)
    return PrintableCheck(True, sliced_for=target, bed_type=settings.get("curr_bed_type") or None)


def bed_type_mismatch(file_bed_type: str | None, printer_bed_type: str | None) -> str | None:
    """Why a file must not start on this printer's plate, or None when it may.

    Only a known plate on both sides is compared: the bed temperature is in the
    G-code, so a file sliced for another plate prints at the wrong temperature
    (PLA sliced for the Cool Plate at 35 C came off a textured PEI plate).
    """
    if not file_bed_type or not printer_bed_type:
        return None
    if file_bed_type.strip().lower() == printer_bed_type.strip().lower():
        return None
    return (
        f"sliced for the {file_bed_type} but the printer has the {printer_bed_type} mounted "
        "(the bed temperature would be wrong): re-slice for that plate, or update the printer's plate"
    )


def printer_filename(original: str) -> str:
    """Name the file gets on the printer: the original one, made safe.

    Folders are dropped and characters outside letters, digits and ``._-()+``
    and space become ``_``. The extension is kept as it is.
    """
    # Not Path().name: on Windows it would read "a:b.gcode" as drive "a:".
    name = original.replace("\\", "/").rsplit("/", 1)[-1].strip()
    name = _SAFE_NAME_RE.sub("_", name).lstrip(".")
    if len(name) > p.MAX_FILENAME_LENGTH:
        stem, dot, ext = name.rpartition(".")
        keep = p.MAX_FILENAME_LENGTH - len(ext) - 1
        name = f"{stem[:keep]}.{ext}" if dot else name[: p.MAX_FILENAME_LENGTH]
    return p.check_machine_filename(name)


@dataclass(frozen=True)
class SlicedForCC2:
    """The machine file a Centauri 2 can take, out of a sliced 3MF."""

    gcode: bytes
    plate_index: int
    sliced_for: str | None = None
    bed_type: str | None = None
    thumbnail: bytes | None = None
    # Totals from the G-code's own footer, for when the sidecar's headers are missing.
    filament_used_g: float | None = None
    filament_used_mm: float | None = None
    layer_height: float | None = None


class TooManyPlatesForCC2(ValueError):
    """A 3MF sliced for a CC2 with more than one plate: the printer takes one G-code file."""


_TOTAL_RE = re.compile(
    rb"^;\s*(total filament used \[g\]|filament used \[g\]|filament used \[mm\]|layer_height)\s*=\s*([^\r\n]+)$",
    re.MULTILINE,
)


def _footer_number(found: dict[str, str], key: str) -> float | None:
    raw = found.get(key)
    if not raw:
        return None
    try:
        return sum(float(v) for v in raw.replace(";", ",").split(",") if v.strip())
    except ValueError:
        return None


def cc2_gcode_from_sliced_3mf(content: bytes) -> SlicedForCC2 | None:
    """The plate G-code of a sliced 3MF when it was sliced for a Centauri 2, else None.

    The slicer CLI path always produces a ``.gcode.3mf``; the CC2 jams on a 3MF
    sent over the network (HW-M4-03) and prints the plain ``plate_N.gcode``
    inside it. Raises :class:`TooManyPlatesForCC2` for a multi-plate result.

    Runs on every library slice, so a result that names another printer in its
    project settings is let go without decompressing any G-code; a CC2 plate is
    decompressed once.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile:
        return None
    with zf:
        names = zf.namelist()
        plates = sorted(n for n in names if _PLATE_GCODE_RE.match(n))
        if not plates:
            return None
        if "Metadata/project_settings.config" in names:
            try:
                project = json.loads(zf.read("Metadata/project_settings.config"))
            except ValueError:
                project = {}
            if isinstance(project, dict):
                named = [project.get(k) for k in ("printer_model", "printer_settings_id")]
                named = [v[0] if isinstance(v, list) and v else v for v in named]
                named = [v for v in named if isinstance(v, str) and v.strip()]
                if named and not any(is_cc2_model(v) for v in named):
                    return None
        gcode = zf.read(plates[0])
        head, tail = gcode[:_HEAD_BYTES], gcode[-_TAIL_BYTES:]
        settings = _settings_from_gcode_bytes(head, tail)
        if not (is_cc2_model(settings.get("printer_model")) or is_cc2_model(settings.get("printer_settings_id"))):
            return None
        if len(plates) > 1:
            raise TooManyPlatesForCC2(
                f"sliced {len(plates)} plates for the Centauri 2, which takes one G-code file: slice one plate"
            )
        totals: dict[str, str] = {}
        for chunk in (head, tail):
            for key, value in _TOTAL_RE.findall(chunk):
                totals.setdefault(key.decode("ascii"), value.decode("utf-8", errors="replace").strip())
        index = int(re.search(r"plate_(\d+)\.gcode$", plates[0]).group(1))
        picture = f"Metadata/plate_{index}.png"
        layer = _footer_number(totals, "layer_height")
        return SlicedForCC2(
            gcode=gcode,
            plate_index=index,
            sliced_for=settings.get("printer_model") or settings.get("printer_settings_id"),
            bed_type=settings.get("curr_bed_type") or None,
            thumbnail=zf.read(picture) if picture in names else None,
            filament_used_g=_footer_number(totals, "total filament used [g]")
            or _footer_number(totals, "filament used [g]"),
            filament_used_mm=_footer_number(totals, "filament used [mm]"),
            layer_height=layer,
        )
