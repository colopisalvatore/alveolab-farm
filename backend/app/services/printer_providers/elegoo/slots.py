"""Which CANVAS slot feeds each tool of a G-code (the ``slot_map`` of a 1020 start).

Without a ``slot_map`` the Centauri 2 does not read the colours in the file and
does not use the filament that is loaded: it applies the last mapping it was
given. On 1/11/2026 a white ``T0`` file started on slot 4 (black), the slot the
previous print had used, even after the white had been loaded by hand from the
touchscreen. So the farm always tells the printer which slot to use.

The file says which tools it uses (``T<n>`` lines, and the ``T<n>`` of the
``M6211`` the CC2 start G-code issues) and what each tool should be
(``; filament_type`` / ``; filament_colour``, one value per tool separated by
``;``, in the settings block Orca writes at the end). Each used tool gets the
loaded slot with the same material and the same colour, else the closest
similar colour. No slot fits: the job is refused before the upload, never
printed in the wrong colour.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from backend.app.services.printer_providers.base import MultiColorSlot
from backend.app.services.printer_providers.elegoo.files import _read_ends
from backend.app.utils.color_utils import perceptual_color_distance
from backend.app.utils.filament_types import canonical_filament_type

_SCAN_CHUNK = 4 * 1024 * 1024
_TOOL_RE = re.compile(rb"^(?:T(\d+)|M6211\b[^;\n]*?\bT(\d+))[ \t]*(?:;[^\n]*)?\r?$", re.MULTILINE)
_LIST_RE = re.compile(rb"^;\s*(filament_type|filament_colour)\s*=\s*(.*?)\s*$", re.MULTILINE)
_HEX_RE = re.compile(r"^#?([0-9A-Fa-f]{6})(?:[0-9A-Fa-f]{2})?$")

# Per-channel box of the Bambu matcher (``PrintScheduler._colors_are_similar``):
# a declared colour is only roughly right, so "close" still counts.
SIMILAR_CHANNEL_DELTA = 40
MAX_TOOLS = 64


@dataclass(frozen=True)
class FileFilament:
    t: int
    material: str | None
    color_hex: str | None  # "RRGGBB", upper-case


@dataclass
class SlotPlan:
    slot_map: list[dict] = field(default_factory=list)
    problem: str | None = None
    picks: list[str] = field(default_factory=list)  # one line per tool, for the log


def _hex(value: str) -> str | None:
    match = _HEX_RE.match(value.strip().strip('"'))
    return match.group(1).upper() if match else None


def _used_tools(path: Path) -> list[int]:
    tools: set[int] = set()
    with path.open("rb") as handle:
        rest = b""
        while True:
            chunk = handle.read(_SCAN_CHUNK)
            if not chunk:
                data, rest = rest, b""
            else:
                data = rest + chunk
                cut = data.rfind(b"\n")
                if cut < 0:
                    rest = data
                    continue
                data, rest = data[: cut + 1], data[cut + 1 :]
            for plain, m6211 in _TOOL_RE.findall(data):
                tool = int(plain or m6211)
                if tool < MAX_TOOLS:  # T255/T1000-style specials are not filaments
                    tools.add(tool)
            if not chunk:
                break
    return sorted(tools) or [0]


def file_filaments(path: Path | str) -> list[FileFilament]:
    """The tools a plain G-code uses, with the material and colour it asks for each."""
    path = Path(path)
    with path.open("rb") as handle:
        head, tail = _read_ends(handle, path.stat().st_size)
    lists: dict[str, list[str]] = {}
    for chunk in (head, tail):
        for key, value in _LIST_RE.findall(chunk):
            lists.setdefault(key.decode("ascii"), value.decode("utf-8", errors="replace").split(";"))
    types = [v.strip().strip('"') or None for v in lists.get("filament_type", [])]
    colours = [_hex(v) for v in lists.get("filament_colour", [])]
    return [
        FileFilament(
            t=t,
            material=types[t] if t < len(types) else None,
            color_hex=colours[t] if t < len(colours) else None,
        )
        for t in _used_tools(path)
    ]


def _similar(a: str, b: str) -> bool:
    return all(abs(int(a[i : i + 2], 16) - int(b[i : i + 2], 16)) <= SIMILAR_CHANNEL_DELTA for i in (0, 2, 4))


def _describe(material: str | None, color_hex: str | None) -> str:
    return f"{material or '?'} #{color_hex or '??????'}"


def plan_slot_map(filaments: list[FileFilament], slots: list[MultiColorSlot]) -> SlotPlan:
    """``slot_map`` for *filaments* on the loaded CANVAS *slots*, or the reason there is none.

    No CANVAS reported at all: empty map, the printer feeds from its own spool
    holder. A tool the file does not describe, or one no loaded slot fits,
    makes ``problem`` say which and what is loaded.
    """
    loaded = [s for s in slots if s.loaded and s.material and s.color_hex]
    plan = SlotPlan()
    if not slots:
        return plan
    taken: set[tuple[int, int]] = set()
    for filament in filaments:
        if not filament.material or not filament.color_hex:
            plan.problem = (
                f"the file does not say which filament T{filament.t} uses: "
                "export it again from the slicer with Export G-code"
            )
            return plan
        wanted_type = canonical_filament_type(filament.material.upper())
        candidates = [
            s
            for s in loaded
            if (s.unit_id, s.slot_index) not in taken
            and canonical_filament_type(s.material.upper()) == wanted_type
            and _similar(s.color_hex, filament.color_hex)
        ]
        if not candidates:
            on_canvas = (
                ", ".join(f"slot {s.slot_index + 1} {_describe(s.material, s.color_hex)}" for s in loaded) or "nothing"
            )
            plan.problem = (
                f"no CANVAS slot holds {_describe(filament.material, filament.color_hex)} for T{filament.t} "
                f"(loaded: {on_canvas}): load it, or set the slot's filament on the card"
            )
            return plan
        # Exact colour first, then the closest one; slot order breaks ties.
        best = min(
            candidates,
            key=lambda s: (
                s.color_hex != filament.color_hex,
                perceptual_color_distance(s.color_hex, filament.color_hex) or 0.0,
                s.unit_id,
                s.slot_index,
            ),
        )
        taken.add((best.unit_id, best.slot_index))
        plan.slot_map.append({"t": filament.t, "canvas_id": best.unit_id, "tray_id": best.slot_index})
        plan.picks.append(
            f"T{filament.t} {_describe(filament.material, filament.color_hex)} -> slot {best.slot_index + 1} "
            f"{_describe(best.material, best.color_hex)}"
        )
    return plan
