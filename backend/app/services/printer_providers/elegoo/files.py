"""Which files the farm may send to an ELEGOO Centauri Carbon 2 (M4).

A file goes to the CC2 only when it says, in its own slicer settings, that it
was sliced for a CC2. Anything else is refused, including a file that says
nothing: a 3MF sliced by Bambu Studio carries G-code for a Bambu machine, and
the name of a file proves nothing about who sliced it.

Where the slicer writes the target printer:
  * plain G-code from OrcaSlicer / ElegooSlicer / Bambu Studio: comment lines
    ``; printer_model = ...`` and ``; printer_settings_id = ...`` in the
    settings block, which Orca writes at the end of the file;
  * a sliced 3MF: ``Metadata/project_settings.config`` (JSON, keys
    ``printer_model`` / ``printer_settings_id``), else the same comments in the
    plate G-code inside the zip.
"""

from __future__ import annotations

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
_SETTING_RE = re.compile(rb"^;\s*(printer_model|printer_settings_id)\s*=\s*(.+?)\s*$", re.MULTILINE)
_PLATE_GCODE_RE = re.compile(r"^Metadata/plate_\d+\.gcode$")
_SAFE_NAME_RE = re.compile(r"[^\w.\- ()+]")

REASON_NOT_SLICED = "not_sliced"
REASON_WRONG_PRINTER = "sliced_for_another_printer"
REASON_UNKNOWN_TARGET = "target_printer_unknown"
REASON_UNSUPPORTED_TYPE = "unsupported_file_type"


@dataclass(frozen=True)
class PrintableCheck:
    ok: bool
    sliced_for: str | None = None
    reason: str | None = None
    message: str | None = None


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
        return PrintableCheck(False, reason=REASON_UNSUPPORTED_TYPE, message="only .gcode or sliced .3mf files")
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
    return PrintableCheck(True, sliced_for=target)


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
