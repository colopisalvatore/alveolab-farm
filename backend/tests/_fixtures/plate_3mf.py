"""Small Bambu-style project 3MFs for the plate builder tests.

Built the way Bambu Studio writes them: top-level objects in 3D/3dmodel.model
are assemblies whose meshes live in 3D/Objects/*.model (production extension),
plates and per-object settings live in Metadata/model_settings.config, and the
printer/bed in Metadata/project_settings.config.
"""

import io
import json
import math
import zipfile

NS = (
    'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
    'xmlns:BambuStudio="http://schemas.bambulab.com/package/2021" '
    'xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06" requiredextensions="p"'
)


def box_mesh_model(object_id: int, sx: float, sy: float, sz: float) -> str:
    """A sub-model file with one box mesh centred on the origin in X/Y, from z=0 up."""
    xs, ys = (-sx / 2, sx / 2), (-sy / 2, sy / 2)
    verts = [(x, y, z) for z in (0.0, sz) for y in ys for x in xs]
    tris = [
        (0, 2, 1),
        (1, 2, 3),
        (4, 5, 6),
        (5, 7, 6),
        (0, 1, 4),
        (1, 5, 4),
        (2, 6, 3),
        (3, 6, 7),
        (0, 4, 2),
        (2, 4, 6),
        (1, 3, 5),
        (3, 7, 5),
    ]
    v = "".join(f'<vertex x="{x}" y="{y}" z="{z}"/>' for x, y, z in verts)
    t = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in tris)
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>\n<model unit="millimeter" {NS}>\n <resources>\n'
        f'  <object id="{object_id}" p:UUID="0000{object_id:04x}-81cb-4c03-9d28-80fed5dfa1dc" type="model">'
        f"<mesh><vertices>{v}</vertices><triangles>{t}</triangles></mesh></object>\n"
        " </resources>\n <build/>\n</model>\n"
    )


def rz_transform(deg: float, tx: float, ty: float, tz: float = 0.0) -> str:
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return f"{c} {s} 0 {-s} {c} 0 0 0 1 {tx} {ty} {tz}"


def make_project_3mf(
    parts: list[dict],
    plates: list[list[tuple[int, int]]],
    *,
    bed: int = 256,
    exclude: bool = True,
    brim: str = "auto_brim",
    filaments: int = 4,
    printer: str = "Bambu Lab P1S 0.4 nozzle",
    tag: str = "",
) -> bytes:
    """parts: [{id, name, size: (x, y, z), extruder, items: [transform, ...]}];
    plates: per plate, the (object id, instance index) pairs on it."""
    resources, items, settings_objects, rels, files = [], [], [], [], {}
    n = 0
    for part in parts:
        oid, mesh_id = part["id"], part["id"] - 1
        path = f"3D/Objects/object_{oid}{tag}.model"
        files[path] = box_mesh_model(mesh_id, *part["size"])
        resources.append(
            f'<object id="{oid}" p:UUID="0000{oid:04x}-61cb-4c03-9d28-80fed5dfa1dc" type="model"><components>'
            f'<component p:path="/{path}" objectid="{mesh_id}" p:UUID="0001{oid:04x}-b206-40ff-9872-83e8017abed1" '
            'transform="1 0 0 0 1 0 0 0 1 0 0 0"/></components></object>'
        )
        for tr in part["items"]:
            n += 1
            items.append(
                f'<item objectid="{oid}" p:UUID="{n:08x}-b1ec-4553-aec9-835e5b724bb4" transform="{tr}" printable="1"/>'
            )
        settings_objects.append(
            f'  <object id="{oid}">\n    <metadata key="name" value="{part["name"]}"/>\n'
            f'    <metadata key="extruder" value="{part.get("extruder", 1)}"/>\n'
            f'    <part id="{mesh_id}" subtype="normal_part">\n      <metadata key="name" value="{part["name"]}"/>\n'
            f'      <metadata key="extruder" value="{part.get("extruder", 1)}"/>\n    </part>\n  </object>\n'
        )
        rels.append(
            f' <Relationship Target="/{path}" Id="rel-{oid}" '
            'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>'
        )
    plate_xml = []
    for pi, inst in enumerate(plates, start=1):
        body = "".join(
            "    <model_instance>\n"
            f'      <metadata key="object_id" value="{o}"/>\n'
            f'      <metadata key="instance_id" value="{i}"/>\n'
            f'      <metadata key="identify_id" value="{100 * pi + k}"/>\n'
            "    </model_instance>\n"
            for k, (o, i) in enumerate(inst)
        )
        plate_xml.append(
            f'  <plate>\n    <metadata key="plater_id" value="{pi}"/>\n    <metadata key="plater_name" value="Plate {pi}"/>\n'
            f'    <metadata key="locked" value="false"/>\n    <metadata key="filament_maps" value="{" ".join(["1"] * filaments)}"/>\n'
            f'    <metadata key="thumbnail_file" value="Metadata/plate_{pi}.png"/>\n{body}  </plate>\n'
        )
    model = (
        f'<?xml version="1.0" encoding="UTF-8"?>\n<model unit="millimeter" xml:lang="en-US" {NS}>\n'
        ' <metadata name="Application">BambuStudio-02.08.02.61</metadata>\n <resources>\n  '
        + "\n  ".join(resources)
        + '\n </resources>\n <build p:UUID="2c7c17d8-22b5-4d84-8835-1976022ea369">\n  '
        + "\n  ".join(items)
        + "\n </build>\n</model>\n"
    )
    project = {
        "printable_area": ["0x0", f"{bed}x0", f"{bed}x{bed}", f"0x{bed}"],
        "bed_exclude_area": ["0x0", "18x0", "18x28", "0x28"] if exclude else [],
        "brim_type": brim,
        "brim_width": "5",
        "filament_colour": ["#FFFFFF", "#C12E1F", "#000000", "#FF9016"][:filaments],
        "printer_settings_id": printer,
    }
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?>\n<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
            '<Default Extension="png" ContentType="image/png"/></Types>',
        )
        z.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8"?>\n<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            ' <Relationship Target="/3D/3dmodel.model" Id="rel-1" '
            'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>',
        )
        z.writestr("3D/3dmodel.model", model)
        z.writestr(
            "3D/_rels/3dmodel.model.rels",
            '<?xml version="1.0" encoding="UTF-8"?>\n<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
            + "\n".join(rels)
            + "\n</Relationships>\n",
        )
        for path, text in files.items():
            z.writestr(path, text)
        for pi in range(1, len(plates) + 1):
            z.writestr(f"Metadata/plate_{pi}.png", b"\x89PNG fake")
        z.writestr("Metadata/project_settings.config", json.dumps(project, indent=4))
        z.writestr(
            "Metadata/model_settings.config",
            '<?xml version="1.0" encoding="UTF-8"?>\n<config>\n'
            + "".join(settings_objects)
            + "".join(plate_xml)
            + "</config>\n",
        )
    return out.getvalue()


def snowman_like(**kw) -> bytes:
    """Plate 1: a 40x30x20 body turned 45 degrees; plate 2: two flat 12x12x2 eyes."""
    return make_project_3mf(
        [
            {"id": 2, "name": "Body.stl", "size": (40, 30, 20), "extruder": 1, "items": [rz_transform(45, 120, 120)]},
            {
                "id": 4,
                "name": "Eye.stl",
                "size": (12, 12, 2),
                "extruder": 3,
                "items": [rz_transform(0, 400, 100), rz_transform(0, 420, 100)],
            },
        ],
        [[(2, 0)], [(4, 0), (4, 1)]],
        **kw,
    )
