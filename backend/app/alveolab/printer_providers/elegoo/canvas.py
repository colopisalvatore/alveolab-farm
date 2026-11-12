"""CANVAS (ELEGOO multicolour unit) commands of the CC2: payloads and checks (M5).

The official SDK maps only 2004 / 2005. The other methods and every payload
below come from ELEGOO's own device page, shipped with Orca Slicer 2.4.3
(``resources/web/elegoolink/lan_service_web/index.html``), which edits a slot,
loads and unloads filament and toggles auto-refill over the same MQTT API:

* 2003 ``SetFilamentInfo``: ``{canvas_id, tray_id, brand, filament_type,
  filament_name, filament_code, filament_color, filament_min_temp,
  filament_max_temp}``; type, code and temperatures come from the catalogue
  below, picked by filament name;
* 2004 ``SetAutoFillFilament``: ``{auto_refill: bool}`` (the community doc's
  ``{"enable": ...}`` is not what ELEGOO's page sends);
* 2001 / 2002 load / unload: ``{canvas_id, tray_id}``, with a 5-minute wait
  for the reply on ELEGOO's page.

When the page allows each action (its ``editBtnDisabled`` / ``feedBtnDisabled``):
editing only with the printer idle; loading idle or paused, from a slot that
holds a recognised filament. Nothing here has been tried on our printer yet
(checklist HW-M5-06..09).
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class CanvasFilament:
    name: str
    type: str
    code: str
    min_temp: int
    max_temp: int
    elegoo: bool  # offered under brand "ELEGOO" on ELEGOO's page
    generic: bool  # offered under brand "Generic"


def _f(name, type_, code, min_temp, max_temp, elegoo=True, generic=True):
    return CanvasFilament(name, type_, code, min_temp, max_temp, elegoo, generic)


# ELEGOO's filament table (``Ud`` in the device page), in its order.
CANVAS_FILAMENTS: tuple[CanvasFilament, ...] = (
    _f("PLA", "PLA", "0x0000", 190, 230),
    _f("PLA+", "PLA", "0x0001", 190, 230),
    _f("PLA PRO", "PLA", "0x0002", 190, 230),
    _f("PLA Silk", "PLA", "0x0003", 190, 230),
    _f("PLA-CF", "PLA", "0x0004", 210, 240),
    _f("PLA Carbon", "PLA", "0x0005", 190, 230, elegoo=False),
    _f("PLA Matte", "PLA", "0x0006", 190, 230),
    _f("PLA Fluo", "PLA", "0x0007", 190, 230, elegoo=False),
    _f("PLA Wood", "PLA", "0x0008", 190, 230),
    _f("PLA Basic", "PLA", "0x0009", 190, 230),
    _f("RAPID PLA+", "PLA", "0x000A", 190, 230),
    _f("PLA Marble", "PLA", "0x000B", 190, 230),
    _f("PLA Galaxy", "PLA", "0x000C", 190, 230),
    _f("PLA Red Copper", "PLA", "0x000D", 190, 230),
    _f("PLA Sparkle", "PLA", "0x000E", 190, 230, elegoo=False),
    _f("PETG", "PETG", "0x0100", 230, 260),
    _f("PETG-CF", "PETG", "0x0101", 240, 270),
    _f("PETG-GF", "PETG", "0x0102", 240, 270),
    _f("PETG PRO", "PETG", "0x0103", 230, 260),
    _f("PETG Translucent", "PETG", "0x0104", 230, 260),
    _f("RAPID PETG", "PETG", "0x0105", 230, 260),
    _f("ABS", "ABS", "0x0200", 240, 280),
    _f("ABS-GF", "ABS", "0x0201", 240, 280, elegoo=False),
    _f("TPU", "TPU", "0x0300", 220, 240, elegoo=False),
    _f("TPU 95A", "TPU", "0x0301", 220, 240),
    _f("RAPID TPU 95A", "TPU", "0x0302", 220, 240),
    _f("PA", "PA", "0x0400", 260, 290, elegoo=False),
    _f("PA-CF", "PA", "0x0401", 260, 300, elegoo=False),
    _f("PAHT-CF", "PA", "0x0402", 280, 320),
    _f("PA6", "PA", "0x0403", 260, 290, elegoo=False),
    _f("PA6-CF", "PA", "0x0404", 270, 310, elegoo=False),
    _f("PA12", "PA", "0x0405", 240, 270, elegoo=False),
    _f("PA12-CF", "PA", "0x0406", 260, 290, elegoo=False),
    _f("CPE", "CPE", "0x0500", 220, 250, elegoo=False),
    _f("PC", "PC", "0x0600", 260, 290),
    _f("PCTG", "PC", "0x0601", 260, 290, elegoo=False),
    _f("PC-FR", "PC", "0x0602", 260, 290),
    _f("PVA", "PVA", "0x0700", 180, 210, elegoo=False),
    _f("ASA", "ASA", "0x0800", 240, 280),
    _f("BVOH", "BVOH", "0x0900", 190, 210, elegoo=False),
    _f("EVA", "EVA", "0x0A00", 180, 220, elegoo=False),
    _f("HIPS", "HIPS", "0x0B00", 220, 250, elegoo=False),
    _f("PP", "PP", "0x0C00", 210, 250, elegoo=False),
    _f("PP-CF", "PP", "0x0C01", 220, 260, elegoo=False),
    _f("PP-GF", "PP", "0x0C02", 230, 250, elegoo=False),
    _f("PPA", "PPA", "0x0D00", 290, 310, elegoo=False),
    _f("PPA-CF", "PPA", "0x0D01", 300, 320, elegoo=False),
    _f("PPA-GF", "PPA", "0x0D02", 290, 310, elegoo=False),
    _f("PPS", "PPS", "0x0E00", 330, 340, elegoo=False),
    _f("PPS-CF", "PPS", "0x0E01", 340, 360, elegoo=False),
)
_BY_NAME = {f.name: f for f in CANVAS_FILAMENTS}

# The two brands ELEGOO's page offers.
CANVAS_BRANDS = ("ELEGOO", "Generic")

# ELEGOO's page waits up to 5 minutes for the reply to a load or unload.
LOAD_UNLOAD_TIMEOUT_SECONDS = 300

_COLOR = re.compile(r"^#?([0-9A-Fa-f]{6})$")


def canvas_filament(name: str) -> CanvasFilament:
    filament = _BY_NAME.get(name)
    if filament is None:
        raise ValueError(f"unknown CANVAS filament {name!r}")
    return filament


def filament_info_params(canvas_id: int, tray_id: int, *, filament_name: str, color: str, brand: str) -> dict:
    """``params`` of a 2003 slot edit, as ELEGOO's page builds them."""
    filament = canvas_filament(filament_name)
    if brand not in CANVAS_BRANDS:
        raise ValueError(f"brand must be one of {', '.join(CANVAS_BRANDS)}")
    if brand == "ELEGOO" and not filament.elegoo:
        raise ValueError(f"{filament.name} is not an ELEGOO filament")
    match = _COLOR.match(color or "")
    if match is None:
        raise ValueError("colour must be #RRGGBB")
    return {
        **tray_params(canvas_id, tray_id),
        "brand": brand,
        "filament_type": filament.type,
        "filament_name": filament.name,
        "filament_code": filament.code,
        "filament_color": f"#{match.group(1).upper()}",
        "filament_min_temp": filament.min_temp,
        "filament_max_temp": filament.max_temp,
    }


def tray_params(canvas_id: int, tray_id: int) -> dict:
    """``{canvas_id, tray_id}``: both 0-based on the CC2 (observed 29/10/2026)."""
    for value in (canvas_id, tray_id):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError("canvas_id and tray_id must be integers >= 0")
    return {"canvas_id": canvas_id, "tray_id": tray_id}


def auto_refill_params(enabled: bool) -> dict:
    return {"auto_refill": bool(enabled)}


def find_tray(canvas_info: dict | None, canvas_id: int, tray_id: int) -> dict | None:
    """The tray *canvas_id* / *tray_id* of a connected CANVAS, as last reported, or None."""
    if not isinstance(canvas_info, dict):
        return None
    for canvas in canvas_info.get("canvas_list") or []:
        if not isinstance(canvas, dict) or canvas.get("canvas_id") != canvas_id:
            continue
        if canvas.get("connected") not in (None, 1, True):
            return None
        for tray in canvas.get("tray_list") or []:
            if isinstance(tray, dict) and tray.get("tray_id") == tray_id:
                return tray
    return None


def tray_recognised(tray: dict) -> bool:
    """Spool present with type and colour known: what ELEGOO's page needs before a load."""
    return (
        tray.get("status") not in (None, 0)
        and bool(str(tray.get("filament_type") or "").strip())
        and bool(str(tray.get("filament_color") or "").strip())
    )
