"""Build one ready-to-slice plate out of plates of existing Bambu/Orca project 3MFs.

A designer's "split" 3MF usually has one plate per colour (body, scarf and hat,
eyes...). Production wants something else: one colour, as many sets as the bed
holds, sometimes from several models at once, sometimes only some objects of a
plate (just the eyes), and sometimes for another printer (an A1 Mini bed). Doing
that by hand in the slicer for every order is what this module replaces.

``build_plate`` takes one or more sources (a project 3MF, a plate number in it,
how many copies of that plate make one "set", optionally which objects to keep),
lays ``sets`` sets out on the bed of the target settings and returns a new
project 3MF with a single plate:

* the geometry is reused, never re-meshed: kept objects keep their mesh files
  and modifiers, every copy is one more build item pointing at them;
* every item is turned about Z to its smallest footprint, then packed in
  shelves inside the printable area, clear of ``bed_exclude_area`` (the P1S
  corner), with a gap that depends on whether the piece is flat or tall;
* ``sets=None`` means "as many as fit";
* ``settings_from`` swaps the whole project settings (printer, process,
  filaments) for another file's, which is how a plate made for a P1S becomes an
  A1 Mini plate; the bed used for packing is the one of the final settings.

The result is meant to be sliced WITHOUT arrange: the layout is already done,
and the slicer's own arrange spreads small pieces over several plates.

The 3MF XML is edited as text, on purpose: the Bambu files carry namespaces and
attributes an XML round-trip would rewrite, and the slicers are picky about them.
"""

from __future__ import annotations

import io
import json
import math
import re
import uuid
import zipfile
from dataclasses import dataclass, field

import numpy as np

MODEL_PATH = "3D/3dmodel.model"
MODEL_RELS_PATH = "3D/_rels/3dmodel.model.rels"
MODEL_SETTINGS_PATH = "Metadata/model_settings.config"
PROJECT_SETTINGS_PATH = "Metadata/project_settings.config"

# Distance kept from the edges of the printable area.
EDGE_MM = 2.0
# Gap between pieces when the caller does not set one. Flat pieces (eyes,
# buttons, scarves) sit close; taller pieces get room for their brim, which is
# what the slicer adds around them when brim is on.
FLAT_HEIGHT_MM = 6.0
GAP_FLAT_MM = 4.0
GAP_TALL_MM = 6.0
GAP_TALL_BRIM_MM = 10.0
# Hard limit on build items in one output, whatever "as many as fit" finds.
MAX_ITEMS = 400
# Z angles tried when looking for the smallest footprint.
ROTATION_STEP_DEG = 3

_SKIP_ENTRIES = re.compile(
    r"^Metadata/(plate_|top_|pick_|filament_sequence\.json$|cut_information\.xml$|slice_info\.config$)"
)


class PlateBuildError(ValueError):
    """The request cannot be turned into a plate (bad plate, nothing fits...)."""


@dataclass
class PlateSource:
    """One plate of one project 3MF, as it goes into each set."""

    data: bytes
    plate: int
    copies: int = 1
    only_objects: list[str] | None = None
    label: str = ""


@dataclass
class Footprint:
    """One build item of a source, ready to be placed."""

    source: int
    object_id: str
    name: str
    rotation: list[float]
    width: float
    depth: float
    height: float
    min_x: float
    min_y: float
    min_z: float
    gap: float = 0.0


@dataclass
class PlateBuildResult:
    content: bytes
    sets: int
    items: int
    bed: tuple[float, float]
    extruders: list[int]
    objects: list[str] = field(default_factory=list)
    max_sets: int | None = None


# ---------------------------------------------------------------- 3MF reading


@dataclass
class _Source:
    zf: zipfile.ZipFile
    model: str
    settings: str
    project: dict
    objects: dict[str, str]  # resource object id -> its <object> block in 3dmodel.model
    items: dict[str, list[str]]  # object id -> build <item> tags, in order
    names: dict[str, str]
    plate_head: str
    instances: list[tuple[str, int]]  # (object id, instance index) on the chosen plate


def _read(zf: zipfile.ZipFile, name: str) -> str:
    try:
        return zf.read(name).decode("utf-8")
    except KeyError as e:
        raise PlateBuildError(f"Not a slicer project 3MF: {name} is missing") from e


def _plates(settings: str) -> list[str]:
    return re.findall(r"<plate>.*?</plate>", settings, re.S)


def _meta(block: str, key: str) -> str | None:
    m = re.search(rf'<metadata key="{re.escape(key)}" value="([^"]*)"', block)
    return m.group(1) if m else None


def _norm_name(name: str) -> str:
    """'Eye.stl', 'eye' and ' EYE ' are the same object name."""
    name = name.strip().lower()
    return re.sub(r"\.(stl|obj|step|stp|3mf)$", "", name)


def _object_blocks_in_settings(settings: str) -> dict[str, str]:
    return {m.group(1): m.group(0) for m in re.finditer(r'[ \t]*<object id="(\d+)">.*?</object>\n?', settings, re.S)}


def _open_source(src: PlateSource) -> _Source:
    try:
        zf = zipfile.ZipFile(io.BytesIO(src.data))
    except zipfile.BadZipFile as e:
        raise PlateBuildError(f"{src.label or 'source'}: not a 3MF file") from e
    model = _read(zf, MODEL_PATH)
    settings = _read(zf, MODEL_SETTINGS_PATH)
    try:
        project = json.loads(_read(zf, PROJECT_SETTINGS_PATH))
    except json.JSONDecodeError as e:
        raise PlateBuildError(f"{src.label or 'source'}: unreadable project settings") from e

    resources = re.search(r"<resources>(.*)</resources>", model, re.S)
    if not resources:
        raise PlateBuildError(f"{src.label or 'source'}: no resources in the model")
    objects = {
        m.group(1): m.group(0) for m in re.finditer(r'<object id="(\d+)"[^>]*>.*?</object>', resources.group(1), re.S)
    }
    build = re.search(r"<build[^>]*>(.*?)</build>", model, re.S)
    items: dict[str, list[str]] = {}
    for m in re.finditer(r"<item\b[^>]*/>", build.group(1) if build else ""):
        oid = re.search(r'objectid="(\d+)"', m.group(0))
        if oid:
            items.setdefault(oid.group(1), []).append(m.group(0))

    names = {
        oid: (_meta(block, "name") or f"object {oid}") for oid, block in _object_blocks_in_settings(settings).items()
    }

    plates = _plates(settings)
    plate = next((p for p in plates if _meta(p, "plater_id") == str(src.plate)), None)
    if plate is None:
        found = ", ".join(sorted((_meta(p, "plater_id") or "?" for p in plates), key=lambda s: (len(s), s)))
        raise PlateBuildError(f"{src.label or 'source'}: there is no plate {src.plate} (plates: {found or 'none'})")
    on_plate = [
        (m.group(1), int(m.group(2)))
        for m in re.finditer(
            r'<model_instance>\s*<metadata key="object_id" value="(\d+)"/>\s*<metadata key="instance_id" value="(\d+)"/>',
            plate,
        )
    ]
    instances = on_plate
    if src.only_objects:
        wanted = {_norm_name(n) for n in src.only_objects if n.strip()}
        instances = [(o, i) for o, i in on_plate if _norm_name(names.get(o, "")) in wanted]
        if not instances:
            present = ", ".join(sorted({names.get(o, o) for o, _ in on_plate}))
            raise PlateBuildError(
                f"{src.label or 'source'}: plate {src.plate} has none of {', '.join(src.only_objects)} "
                f"(objects: {present})"
            )
    instances = [(o, i) for o, i in instances if o in objects and items.get(o)]
    if not instances:
        raise PlateBuildError(f"{src.label or 'source'}: plate {src.plate} is empty")
    head = re.search(r"<plate>.*?(?=<model_instance>|</plate>)", plate, re.S).group(0)
    return _Source(zf, model, settings, project, objects, items, names, head, instances)


# ------------------------------------------------------------- geometry


def _floats(text: str) -> list[float]:
    return [float(v) for v in text.split()]


def _matrix(transform: str | None) -> tuple[np.ndarray, np.ndarray]:
    """3MF transform "m00 m01 m02 m10 ... m32" -> (3x3, translation), row-vector convention."""
    if not transform:
        return np.eye(3), np.zeros(3)
    v = _floats(transform)
    if len(v) != 12:
        raise PlateBuildError(f"Unexpected transform: {transform}")
    return np.array(v[:9]).reshape(3, 3), np.array(v[9:])


def _vertices(zf: zipfile.ZipFile, path: str, object_id: str, cache: dict) -> np.ndarray:
    key = (path, object_id)
    if key not in cache:
        text = cache.get(("file", path))
        if text is None:
            text = _read(zf, path.lstrip("/"))
            cache[("file", path)] = text
        m = re.search(rf'<object id="{object_id}"[^>]*>(.*?)</object>', text, re.S)
        if not m:
            raise PlateBuildError(f"Object {object_id} missing in {path}")
        body = m.group(1)
        pts = re.findall(r'<vertex x="([^"]+)" y="([^"]+)" z="([^"]+)"', body)
        if pts:
            cache[key] = np.array(pts, dtype=float)
        else:
            # An assembly inside the sub-model: follow its components.
            cache[key] = _component_vertices(zf, path, body, cache)
    return cache[key]


def _component_vertices(zf: zipfile.ZipFile, own_path: str, body: str, cache: dict) -> np.ndarray:
    chunks = []
    for c in re.finditer(r"<component\b[^>]*/>", body):
        tag = c.group(0)
        oid = re.search(r'objectid="(\d+)"', tag).group(1)
        path = re.search(r'p:path="([^"]+)"', tag)
        tr = re.search(r'transform="([^"]*)"', tag)
        rot, off = _matrix(tr.group(1) if tr else None)
        pts = _vertices(zf, path.group(1) if path else own_path, oid, cache)
        if len(pts):
            chunks.append(pts @ rot + off)
    return np.vstack(chunks) if chunks else np.zeros((0, 3))


def _object_vertices(src: _Source, object_id: str, cache: dict) -> np.ndarray:
    block = src.objects[object_id]
    inner = re.search(r"<object[^>]*>(.*)</object>", block, re.S).group(1)
    if "<mesh>" in inner:
        pts = re.findall(r'<vertex x="([^"]+)" y="([^"]+)" z="([^"]+)"', inner)
        return np.array(pts, dtype=float)
    return _component_vertices(src.zf, "/" + MODEL_PATH, inner, cache)


def _hull(points: np.ndarray) -> np.ndarray:
    """2D convex hull (monotone chain); a few dozen points instead of the whole mesh."""
    pts = np.unique(np.round(points, 3), axis=0)
    if len(pts) < 3:
        return pts
    pts = pts[np.lexsort((pts[:, 1], pts[:, 0]))]

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: list = []
    for p in pts[::-1]:
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return np.array(lower[:-1] + upper[:-1])


def _rz(deg: float) -> np.ndarray:
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    # Row-vector convention: p' = p @ R turns p by +deg about Z.
    return np.array([[c, s, 0.0], [-s, c, 0.0], [0.0, 0.0, 1.0]])


def _footprint(src_index: int, src: _Source, object_id: str, instance: int, cache: dict) -> Footprint:
    tags = src.items[object_id]
    tag = tags[instance] if instance < len(tags) else tags[0]
    tr = re.search(r'transform="([^"]*)"', tag)
    rot, _ = _matrix(tr.group(1) if tr else None)
    local = _object_vertices(src, object_id, cache)
    if not len(local):
        raise PlateBuildError(f"{src.names.get(object_id, object_id)} has no geometry")
    world = local @ rot
    hull = _hull(world[:, :2])
    best = None
    for deg in range(0, 180, ROTATION_STEP_DEG):
        r = _rz(deg)[:2, :2]
        h = hull @ r
        lo, hi = h.min(axis=0), h.max(axis=0)
        area = float((hi - lo).prod())
        # Keep the designer's orientation unless turning really saves room.
        if best is None or area < best[0] * 0.99:
            best = (area, deg, lo, hi)
    _, deg, lo, hi = best
    final = rot @ _rz(deg)
    return Footprint(
        source=src_index,
        object_id=object_id,
        name=src.names.get(object_id, object_id),
        rotation=final.reshape(-1).tolist(),
        width=float(hi[0] - lo[0]),
        depth=float(hi[1] - lo[1]),
        height=float(world[:, 2].max() - world[:, 2].min()),
        min_x=float(lo[0]),
        min_y=float(lo[1]),
        min_z=float(world[:, 2].min()),
    )


# ---------------------------------------------------------------- bed


def _polygon_box(points: list[str] | None) -> tuple[float, float, float, float] | None:
    if not points:
        return None
    xy = [tuple(float(v) for v in p.split("x")) for p in points if "x" in p]
    if len(xy) < 3:
        return None
    xs, ys = [p[0] for p in xy], [p[1] for p in xy]
    if max(xs) - min(xs) <= 0 or max(ys) - min(ys) <= 0:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def bed_of(project: dict) -> tuple[tuple[float, float, float, float], list[tuple[float, float, float, float]]]:
    """Printable rectangle and exclusion rectangles of a project_settings dict."""
    area = _polygon_box(project.get("printable_area")) or (0.0, 0.0, 256.0, 256.0)
    excluded = []
    raw = project.get("bed_exclude_area") or []
    if isinstance(raw, str):
        raw = [p for p in raw.replace(";", ",").split(",") if p]
    box = _polygon_box(raw)
    if box:
        excluded.append(box)
    return area, excluded


def _brim_on(project: dict, settings_block: str) -> bool:
    own = _meta(settings_block, "brim_type")
    kind = own or project.get("brim_type") or "no_brim"
    if isinstance(kind, list):
        kind = kind[0] if kind else "no_brim"
    return kind != "no_brim"


# ---------------------------------------------------------------- packing


def pack(
    pieces: list[Footprint],
    area: tuple[float, float, float, float],
    excluded: list[tuple[float, float, float, float]],
) -> list[tuple[float, float]] | None:
    """Shelf packing; returns the lower-left corner of each piece's footprint, or None if one does not fit.

    Pieces go in rows, deepest first. Two neighbours are kept apart by the larger
    of their gaps; an exclusion zone is stepped over like a piece already there.
    """
    x0, y0, x1, y1 = area[0] + EDGE_MM, area[1] + EDGE_MM, area[2] - EDGE_MM, area[3] - EDGE_MM
    order = sorted(range(len(pieces)), key=lambda i: (-pieces[i].depth, -pieces[i].width))
    out: list[tuple[float, float] | None] = [None] * len(pieces)
    row_y, row_top, row_gap = y0, y0, 0.0
    cur_x: float | None = None  # right edge of the last thing in the row
    prev_gap = 0.0
    for i in order:
        p = pieces[i]
        while True:
            left = x0 if cur_x is None else cur_x + max(prev_gap, p.gap)
            if left + p.width > x1:
                if cur_x is None:
                    return None  # wider than the bed
                row_y, cur_x, prev_gap, row_gap = row_top + max(row_gap, p.gap), None, 0.0, 0.0
                continue
            if row_y + p.depth > y1:
                return None
            hit = next(
                (
                    ex
                    for ex in excluded
                    if left < ex[2] + EDGE_MM
                    and left + p.width > ex[0] - EDGE_MM
                    and row_y < ex[3] + EDGE_MM
                    and row_y + p.depth > ex[1] - EDGE_MM
                ),
                None,
            )
            if hit is None:
                break
            cur_x, prev_gap = hit[2], EDGE_MM
        out[i] = (left, row_y)
        cur_x, prev_gap = left + p.width, p.gap
        row_top = max(row_top, row_y + p.depth)
        row_gap = max(row_gap, p.gap)
    return out  # type: ignore[return-value]


# ---------------------------------------------------------------- writing


def _item_uuid(n: int) -> str:
    return "%08x-b1ec-4553-aec9-835e5b724bb4" % (0x10000 + n)


def _fmt(v: float) -> str:
    return repr(round(v, 9))


def build_plate(
    sources: list[PlateSource],
    *,
    sets: int | None = None,
    settings_from: bytes | None = None,
    gap_mm: float | None = None,
    extruder: int | None = None,
    filament_colour: str | None = None,
    plate_name: str | None = None,
) -> PlateBuildResult:
    """Lay ``sets`` sets of the given plates out on one plate (None = as many as fit)."""
    if not sources:
        raise PlateBuildError("No source plate given")
    if sets is not None and sets < 1:
        raise PlateBuildError("sets must be at least 1")
    opened = [_open_source(s) for s in sources]

    project = opened[0].project
    if settings_from is not None:
        try:
            with zipfile.ZipFile(io.BytesIO(settings_from)) as zf:
                project = json.loads(zf.read(PROJECT_SETTINGS_PATH).decode("utf-8"))
        except (zipfile.BadZipFile, KeyError, json.JSONDecodeError) as e:
            raise PlateBuildError("The settings file is not a slicer project 3MF") from e
    project = json.loads(json.dumps(project))  # private copy, edited below
    area, excluded = bed_of(project)

    # One footprint per (source, plate instance); the same object placed twice on
    # the source plate is two pieces of a set.
    cache: dict = {}
    per_set: list[Footprint] = []
    for si, (spec, src) in enumerate(zip(sources, opened, strict=True)):
        cache = {}
        blocks = _object_blocks_in_settings(src.settings)
        for oid, inst in src.instances:
            fp = _footprint(si, src, oid, inst, cache)
            if gap_mm is not None:
                fp.gap = float(gap_mm)
            elif fp.height <= FLAT_HEIGHT_MM:
                fp.gap = GAP_FLAT_MM
            else:
                fp.gap = GAP_TALL_BRIM_MM if _brim_on(src.project, blocks.get(oid, "")) else GAP_TALL_MM
            for _ in range(max(1, spec.copies)):
                per_set.append(fp)
    if not per_set:
        raise PlateBuildError("Nothing to place")

    def layout(n: int):
        return pack(per_set * n, area, excluded)

    bed_size = (area[2] - area[0], area[3] - area[1])
    if layout(1) is None:
        big = max(per_set, key=lambda p: p.width * p.depth)
        raise PlateBuildError(
            f"One set does not fit on a {bed_size[0]:.0f}x{bed_size[1]:.0f} bed "
            f"(largest piece: {big.name}, {big.width:.0f}x{big.depth:.0f} mm)"
        )
    max_sets = 1
    while (max_sets + 1) * len(per_set) <= MAX_ITEMS and layout(max_sets + 1) is not None:
        max_sets += 1
    if sets is None:
        sets = max_sets
    elif sets > max_sets:
        raise PlateBuildError(
            f"{sets} sets do not fit on a {bed_size[0]:.0f}x{bed_size[1]:.0f} bed: at most {max_sets}"
        )
    pieces = per_set * sets
    spots = layout(sets)
    assert spots is not None

    # ---- merge the sources into the first one's archive
    base = opened[0]
    model = base.model
    settings = base.settings
    rels = base.zf.read(MODEL_RELS_PATH).decode("utf-8") if MODEL_RELS_PATH in base.zf.namelist() else None
    extra_files: dict[str, bytes] = {}
    id_map: list[dict[str, str]] = [{} for _ in opened]
    kept_resource_blocks: list[str] = []
    kept_settings_blocks: list[str] = []
    used_paths: set[str] = set()

    for si, src in enumerate(opened):
        wanted = sorted({oid for oid, _ in src.instances}, key=int)
        sblocks = _object_blocks_in_settings(src.settings)
        prefix = f"s{si}_" if si else ""
        offset = 0 if si == 0 else 100000 * si
        for oid in wanted:
            new_id = str(int(oid) + offset)
            id_map[si][oid] = new_id
            block = src.objects[oid]
            if si:
                block = re.sub(r'<object id="\d+"', f'<object id="{new_id}"', block, count=1)
                block = re.sub(
                    r'p:path="/3D/Objects/([^"]+)"',
                    lambda m, pre=prefix: f'p:path="/3D/Objects/{pre}{m.group(1)}"',
                    block,
                )
                block = re.sub(r'p:UUID="[^"]*"', lambda _m: f'p:UUID="{uuid.uuid4()}"', block)
            used_paths.update(p.lstrip("/") for p in re.findall(r'p:path="([^"]+)"', block))
            kept_resource_blocks.append(block)
            sb = sblocks.get(oid)
            if sb is not None:
                if si:
                    sb = re.sub(r'<object id="\d+"', f'<object id="{new_id}"', sb, count=1)
                kept_settings_blocks.append(sb if sb.endswith("\n") else sb + "\n")
        if si:
            for info in src.zf.infolist():
                if info.filename.startswith("3D/Objects/"):
                    extra_files[f"3D/Objects/{prefix}{info.filename.split('/')[-1]}"] = src.zf.read(info.filename)

    # Resources: only the kept objects (other sources' objects included).
    model = re.sub(
        r"(<resources>)(.*)(</resources>)",
        lambda m: m.group(1) + "\n  " + "\n  ".join(kept_resource_blocks) + "\n " + m.group(3),
        model,
        count=1,
        flags=re.S,
    )

    # Build: one item per piece, turned and placed.
    new_items = []
    instance_count: dict[str, int] = {}
    plate_instances = []
    for n, (fp, spot) in enumerate(zip(pieces, spots, strict=True), start=1):
        new_oid = id_map[fp.source][fp.object_id]
        rot = fp.rotation
        tx, ty, tz = spot[0] - fp.min_x, spot[1] - fp.min_y, -fp.min_z
        transform = " ".join(_fmt(v) for v in rot) + f" {tx:.4f} {ty:.4f} {tz:.4f}"
        new_items.append(f'<item objectid="{new_oid}" p:UUID="{_item_uuid(n)}" transform="{transform}" printable="1"/>')
        k = instance_count.get(new_oid, 0)
        instance_count[new_oid] = k + 1
        plate_instances.append(
            "    <model_instance>\n"
            f'      <metadata key="object_id" value="{new_oid}"/>\n'
            f'      <metadata key="instance_id" value="{k}"/>\n'
            f'      <metadata key="identify_id" value="{1000 + n}"/>\n'
            "    </model_instance>"
        )
    model = re.sub(
        r"(<build[^>]*>)(.*?)(</build>)",
        lambda m: m.group(1) + "\n  " + "\n  ".join(new_items) + "\n " + m.group(3),
        model,
        count=1,
        flags=re.S,
    )

    # model_settings: kept objects, one plate.
    filament_count = len(project.get("filament_colour") or []) or 1
    head = base.plate_head
    head = re.sub(r'(key="plater_id" value=")\d+', r"\g<1>1", head)
    for key in ("thumbnail_file", "thumbnail_no_light_file", "top_file", "pick_file"):
        head = re.sub(rf'\s*<metadata key="{key}" value="[^"]*"/>', "", head)
    if plate_name is not None:
        safe = plate_name.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")
        head = re.sub(r'(key="plater_name" value=")[^"]*', lambda m: m.group(1) + safe, head)
    head = re.sub(r'(key="filament_maps" value=")[^"]*', r"\g<1>" + " ".join(["1"] * filament_count), head)
    new_plate = head.rstrip() + "\n" + "\n".join(plate_instances) + "\n  </plate>\n"

    body = re.sub(r'[ \t]*<object id="\d+">.*?</object>\n?', "", settings, flags=re.S)
    body = re.sub(r"[ \t]*<plate>.*?</plate>\n?", "", body, flags=re.S)
    body = re.sub(r"[ \t]*<assemble>.*?</assemble>\n?", "", body, flags=re.S)
    objects_text = "".join(b if b.startswith("  ") else "  " + b for b in kept_settings_blocks)
    body = body.replace("</config>", objects_text + "  " + new_plate.lstrip() + "</config>")

    if extruder is not None:
        if not 1 <= extruder <= filament_count:
            raise PlateBuildError(f"Filament {extruder} does not exist: the settings have {filament_count}")
        body = re.sub(r'(<metadata key="extruder" value=")\d+', rf"\g<1>{extruder}", body)
    used = sorted({int(v) for v in re.findall(r'<metadata key="extruder" value="(\d+)"', body)} or {1})

    if filament_colour is not None:
        if not re.fullmatch(r"#?[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?", filament_colour):
            raise PlateBuildError(f"Not a colour: {filament_colour}")
        if len(used) != 1:
            raise PlateBuildError("A colour can be set only when the plate uses one filament (set extruder too)")
        colours = list(project.get("filament_colour") or ["#FFFFFF"] * filament_count)
        colours[used[0] - 1] = "#" + filament_colour.lstrip("#").upper()
        project["filament_colour"] = colours

    # Relationships: drop the mesh files nothing points at any more, add the merged ones.
    if rels is not None:

        def keep_rel(m: re.Match) -> str:
            target = m.group(1).lstrip("/")
            return m.group(0) if target in used_paths or not target.startswith("3D/Objects/") else ""

        rels = re.sub(r'\s*<Relationship Target="([^"]+)"[^>]*/>', keep_rel, rels)
        added = "".join(
            f'\n <Relationship Target="/{name}" Id="rel-{name.split("/")[-1].replace(".", "-")}" '
            'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>'
            for name in extra_files
            if name in used_paths
        )
        rels = rels.replace("</Relationships>", added + "\n</Relationships>")

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zo:
        for info in base.zf.infolist():
            name = info.filename
            if _SKIP_ENTRIES.match(name):
                continue
            if name.startswith("3D/Objects/") and name not in used_paths:
                continue
            if name == MODEL_PATH:
                data = model.encode("utf-8")
            elif name == MODEL_SETTINGS_PATH:
                data = body.encode("utf-8")
            elif name == PROJECT_SETTINGS_PATH:
                data = json.dumps(project, indent=4).encode("utf-8")
            elif name == MODEL_RELS_PATH and rels is not None:
                data = rels.encode("utf-8")
            else:
                data = base.zf.read(name)
            zo.writestr(info, data)
        for name, data in extra_files.items():
            if name in used_paths:
                zo.writestr(name, data)

    names = []
    for fp in per_set:
        if fp.name not in names:
            names.append(fp.name)
    return PlateBuildResult(
        content=out.getvalue(),
        sets=sets,
        items=len(pieces),
        bed=bed_size,
        extruders=used,
        objects=names,
        max_sets=max_sets,
    )
