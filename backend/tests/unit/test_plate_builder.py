"""Unit tests for building one ready-to-slice plate out of project 3MF plates."""

import io
import json
import re
import zipfile

import numpy as np
import pytest

from backend.app.services.plate_builder import (
    EDGE_MM,
    GAP_FLAT_MM,
    PlateBuildError,
    PlateSource,
    build_plate,
)
from backend.tests._fixtures.plate_3mf import make_project_3mf, rz_transform, snowman_like


def _read(data: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return {name: z.read(name) for name in z.namelist()}


def _boxes(data: bytes) -> list[tuple[float, float, float, float, float, str]]:
    """World footprint (x0, y0, x1, y1, z0) of every build item, read back independently."""
    files = _read(data)
    model = files["3D/3dmodel.model"].decode()
    out = []
    for item in re.finditer(r'<item objectid="(\d+)"[^>]*transform="([^"]*)"', model):
        oid, tr = item.group(1), [float(v) for v in item.group(2).split()]
        obj = re.search(rf'<object id="{oid}"[^>]*>(.*?)</object>', model, re.S).group(1)
        comp = re.search(r'p:path="/([^"]+)" objectid="(\d+)"', obj)
        sub = files[comp.group(1)].decode()
        mesh = re.search(rf'<object id="{comp.group(2)}"[^>]*>(.*?)</object>', sub, re.S).group(1)
        pts = np.array(re.findall(r'<vertex x="([^"]+)" y="([^"]+)" z="([^"]+)"', mesh), dtype=float)
        world = pts @ np.array(tr[:9]).reshape(3, 3) + np.array(tr[9:])
        lo, hi = world.min(axis=0), world.max(axis=0)
        out.append((lo[0], lo[1], hi[0], hi[1], lo[2], oid))
    return out


def _overlap(a, b, gap=0.0) -> bool:
    return a[0] < b[2] + gap and b[0] < a[2] + gap and a[1] < b[3] + gap and b[1] < a[3] + gap


def _plates(data: bytes) -> list[str]:
    return re.findall(r"<plate>.*?</plate>", _read(data)["Metadata/model_settings.config"].decode(), re.S)


def test_fills_the_bed_with_whole_sets_inside_the_printable_area_and_off_the_exclusion_zone():
    result = build_plate([PlateSource(snowman_like(), 2)])

    assert result.sets == result.max_sets and result.sets > 50
    assert result.items == 2 * result.sets
    assert result.bed == (256.0, 256.0)
    boxes = _boxes(result.content)
    assert len(boxes) == result.items
    for b in boxes:
        assert b[0] >= EDGE_MM - 1e-6 and b[2] <= 256 - EDGE_MM + 1e-6
        assert b[1] >= EDGE_MM - 1e-6 and b[3] <= 256 - EDGE_MM + 1e-6
        assert b[4] == pytest.approx(0.0, abs=1e-6)  # on the bed
        assert not _overlap(b, (0, 0, 18, 28), gap=EDGE_MM - 1e-6)
    for i, a in enumerate(boxes):
        for b in boxes[i + 1 :]:
            assert not _overlap(a, b, gap=GAP_FLAT_MM - 1e-6)


def test_one_plate_with_one_instance_per_piece_and_old_plates_gone():
    result = build_plate([PlateSource(snowman_like(), 2)], sets=3)
    files = _read(result.content)

    plates = _plates(result.content)
    assert len(plates) == 1
    assert 'key="plater_id" value="1"' in plates[0]
    assert re.findall(r'key="instance_id" value="(\d+)"', plates[0]) == [str(i) for i in range(6)]
    assert "thumbnail_file" not in plates[0]
    assert not any(n.startswith("Metadata/plate_") for n in files)
    # The body (plate 1) is gone with its mesh file and relationship.
    model = files["3D/3dmodel.model"].decode()
    assert re.findall(r'<object id="(\d+)"', model) == ["4"]
    assert "3D/Objects/object_2.model" not in files
    assert "object_2.model" not in files["3D/_rels/3dmodel.model.rels"].decode()
    assert re.findall(r'<item objectid="(\d+)"', model) == ["4"] * 6


def test_turns_a_rotated_piece_to_its_smallest_footprint():
    result = build_plate([PlateSource(snowman_like(), 1)], sets=1)
    (box,) = _boxes(result.content)
    w, d = box[2] - box[0], box[3] - box[1]
    assert sorted([round(w, 3), round(d, 3)]) == [30.0, 40.0]


def test_too_many_sets_says_how_many_fit():
    with pytest.raises(PlateBuildError, match=r"at most \d+"):
        build_plate([PlateSource(snowman_like(), 1)], sets=500)


def test_settings_from_another_printer_use_its_bed():
    p1s = build_plate([PlateSource(snowman_like(), 1)])
    mini_settings = snowman_like(bed=180, exclude=False, printer="Bambu Lab A1 mini 0.4 nozzle")
    mini = build_plate([PlateSource(snowman_like(), 1)], settings_from=mini_settings)

    assert mini.bed == (180.0, 180.0)
    assert mini.sets < p1s.sets
    project = json.loads(_read(mini.content)["Metadata/project_settings.config"])
    assert project["printer_settings_id"] == "Bambu Lab A1 mini 0.4 nozzle"
    for b in _boxes(mini.content):
        assert b[2] <= 180 - EDGE_MM + 1e-6 and b[3] <= 180 - EDGE_MM + 1e-6


def test_only_objects_keeps_the_named_pieces_and_names_the_others_when_none_match():
    two = make_project_3mf(
        [
            {"id": 2, "name": "Hat.stl", "size": (30, 30, 15), "items": [rz_transform(0, 50, 50)]},
            {"id": 4, "name": "Eye.stl", "size": (10, 10, 2), "items": [rz_transform(0, 90, 50)]},
        ],
        [[(2, 0), (4, 0)]],
    )
    result = build_plate([PlateSource(two, 1, only_objects=["eye"])], sets=2)
    assert result.objects == ["Eye.stl"]
    assert re.findall(r'<item objectid="(\d+)"', _read(result.content)["3D/3dmodel.model"].decode()) == ["4", "4"]

    with pytest.raises(PlateBuildError, match=r"Eye\.stl, Hat\.stl"):
        build_plate([PlateSource(two, 1, only_objects=["Nose"])])


def test_missing_plate_lists_the_plates():
    with pytest.raises(PlateBuildError, match=r"no plate 7 \(plates: 1, 2\)"):
        build_plate([PlateSource(snowman_like(), 7)])


def test_two_models_on_one_filament_keep_their_own_meshes():
    other = make_project_3mf(
        [{"id": 2, "name": "Scarf.stl", "size": (25, 10, 3), "extruder": 2, "items": [rz_transform(0, 60, 60)]}],
        [[(2, 0)]],
        tag="_b",
    )
    result = build_plate(
        [PlateSource(snowman_like(), 2), PlateSource(other, 1, copies=2)],
        sets=2,
        extruder=2,
        filament_colour="#a5d8ff",
    )
    files = _read(result.content)
    model = files["3D/3dmodel.model"].decode()
    ids = re.findall(r'<object id="(\d+)"', model)
    assert len(ids) == len(set(ids)) == 2
    items = re.findall(r'<item objectid="(\d+)"', model)
    assert len(items) == result.items == 2 * (2 + 2)
    # The second model's mesh file came along under its own name, with its relationship.
    paths = re.findall(r'p:path="/([^"]+)"', model)
    assert all(p in files for p in paths)
    rels = files["3D/_rels/3dmodel.model.rels"].decode()
    assert all(p in rels for p in paths)
    assert result.extruders == [2]
    assert set(re.findall(r'key="extruder" value="(\d+)"', files["Metadata/model_settings.config"].decode())) == {"2"}
    colours = json.loads(files["Metadata/project_settings.config"])["filament_colour"]
    assert colours[1] == "#A5D8FF"
    assert result.objects == ["Eye.stl", "Scarf.stl"]


def test_colour_needs_a_single_filament():
    mixed = make_project_3mf(
        [
            {"id": 2, "name": "A.stl", "size": (10, 10, 2), "extruder": 1, "items": [rz_transform(0, 30, 30)]},
            {"id": 4, "name": "B.stl", "size": (10, 10, 2), "extruder": 2, "items": [rz_transform(0, 60, 30)]},
        ],
        [[(2, 0), (4, 0)]],
    )
    with pytest.raises(PlateBuildError, match="one filament"):
        build_plate([PlateSource(mixed, 1)], sets=1, filament_colour="#FF0000")


def test_gap_override_and_tall_pieces_get_room_for_the_brim():
    tall = build_plate([PlateSource(snowman_like(), 1)])
    tight = build_plate([PlateSource(snowman_like(), 1)], gap_mm=1)
    no_brim = build_plate([PlateSource(snowman_like(brim="no_brim"), 1)])
    assert tight.sets > no_brim.sets >= tall.sets


def test_not_a_project_file():
    with pytest.raises(PlateBuildError, match="not a 3MF"):
        build_plate([PlateSource(b"plain text", 1)])


# ---- review fixes


def test_a_wide_piece_beside_the_exclusion_zone_goes_above_it_instead_of_looping():
    from backend.app.services.plate_builder import Footprint, pack

    wide = Footprint(0, "1", "wide", [1, 0, 0, 0, 1, 0, 0, 0, 1], 240, 20, 5, 0, 0, 0, gap=4)
    spots = pack([wide], (0, 0, 256, 256), [(0, 0, 18, 28)])
    assert spots == [(EDGE_MM, 28 + EDGE_MM)]
    # Too deep to fit even above the zone: a clean "no", not a hang.
    assert (
        pack([Footprint(0, "1", "big", [1] * 9, 240, 240, 5, 0, 0, 0, gap=4)], (0, 0, 256, 256), [(0, 0, 18, 28)])
        is None
    )


def test_modifiers_and_negative_volumes_do_not_lift_or_move_the_piece():
    src = make_project_3mf(
        [
            {
                "id": 2,
                "name": "Base.stl",
                "size": (20, 20, 10),
                "items": [rz_transform(0, 100, 100)],
                # A negative volume reaching 3 mm below the part, and a wide modifier.
                "volumes": [((10, 10, 6), "negative_part", (0, 0, -3)), ((80, 80, 4), "modifier_part", (0, 0, 2))],
            }
        ],
        [[(2, 0)]],
    )
    result = build_plate([PlateSource(src, 1)], sets=1)
    model = _read(result.content)["3D/3dmodel.model"].decode()
    tr = [float(v) for v in re.search(r'<item [^>]*transform="([^"]*)"', model).group(1).split()]
    assert tr[11] == pytest.approx(0.0, abs=1e-6)  # the printed part sits on the bed
    # Footprint from the 20x20 part, not the 80x80 modifier: many fit.
    assert result.max_sets > 50


def test_a_sunk_object_stays_sunk():
    src = make_project_3mf(
        [{"id": 2, "name": "Sunk.stl", "size": (20, 20, 10), "items": [rz_transform(0, 100, 100, -2)]}], [[(2, 0)]]
    )
    result = build_plate([PlateSource(src, 1)], sets=1)
    model = _read(result.content)["3D/3dmodel.model"].decode()
    tr = [float(v) for v in re.search(r'<item [^>]*transform="([^"]*)"', model).group(1).split()]
    assert tr[11] == pytest.approx(-2.0)


def test_a_floating_object_is_put_down():
    src = make_project_3mf(
        [{"id": 2, "name": "Up.stl", "size": (20, 20, 10), "items": [rz_transform(0, 100, 100, 7)]}], [[(2, 0)]]
    )
    (box,) = _boxes(build_plate([PlateSource(src, 1)], sets=1).content)
    assert box[4] == pytest.approx(0.0, abs=1e-6)


def test_a_filament_the_target_settings_do_not_have_is_a_clear_error():
    one_filament = snowman_like(bed=180, exclude=False, filaments=1)
    with pytest.raises(PlateBuildError, match="use filament 3 but the settings have 1"):
        build_plate([PlateSource(snowman_like(), 2)], sets=1, settings_from=one_filament)
    with pytest.raises(PlateBuildError, match="use filament 3 but the settings have 1"):
        build_plate([PlateSource(snowman_like(), 2)], sets=1, settings_from=one_filament, filament_colour="#00FF00")
    ok = build_plate([PlateSource(snowman_like(), 2)], sets=1, settings_from=one_filament, extruder=1)
    assert ok.extruders == [1]


def test_too_many_pieces_in_one_set(monkeypatch):
    from backend.app.services import plate_builder

    monkeypatch.setattr(plate_builder, "MAX_ITEMS", 3)
    with pytest.raises(PlateBuildError, match="One set has 4 pieces"):
        build_plate([PlateSource(snowman_like(), 2, copies=2)], sets=1)
    monkeypatch.setattr(plate_builder, "MAX_ITEMS", 4)
    with pytest.raises(PlateBuildError, match="more than 4 pieces on one plate: at most 2"):
        build_plate([PlateSource(snowman_like(), 2)], sets=3)


def test_unpacked_size_is_capped(monkeypatch):
    from backend.app.services import plate_builder

    monkeypatch.setattr(plate_builder, "MAX_SOURCE_BYTES", 1000)
    with pytest.raises(PlateBuildError, match="once unpacked"):
        build_plate([PlateSource(snowman_like(), 1)])
    monkeypatch.setattr(plate_builder, "MAX_SOURCE_BYTES", 10**9)
    monkeypatch.setattr(plate_builder, "MAX_TOTAL_BYTES", 1000)
    with pytest.raises(PlateBuildError, match="sources are"):
        build_plate([PlateSource(snowman_like(), 1)])


def test_brim_gap_follows_the_settings_that_will_be_sliced():
    with_brim = build_plate([PlateSource(snowman_like(), 1)])
    no_brim_settings = snowman_like(brim="no_brim")
    without = build_plate([PlateSource(snowman_like(), 1)], settings_from=no_brim_settings)
    assert without.sets > with_brim.sets


def test_merged_mesh_files_in_subfolders_keep_their_path():
    other = make_project_3mf(
        [{"id": 2, "name": "Scarf.stl", "size": (25, 10, 3), "items": [rz_transform(0, 60, 60)]}],
        [[(2, 0)]],
        folder="sub/",
    )
    result = build_plate([PlateSource(snowman_like(), 2), PlateSource(other, 1)], sets=1, extruder=1)
    files = _read(result.content)
    paths = re.findall(r'p:path="/([^"]+)"', files["3D/3dmodel.model"].decode())
    assert any(p.startswith("3D/Objects/s1_sub/") for p in paths)
    assert all(p in files for p in paths)


def test_relationships_are_filtered_whatever_the_attribute_order():
    src = make_project_3mf(
        [
            {"id": 2, "name": "Keep.stl", "size": (10, 10, 2), "items": [rz_transform(0, 50, 50)]},
            {
                "id": 4,
                "name": "Drop.stl",
                "size": (10, 10, 2),
                "items": [rz_transform(0, 80, 50)],
                "volumes": [((5, 5, 1), "modifier_part", (0, 0, 0))],
            },
        ],
        [[(2, 0)], [(4, 0)]],
    )
    files = _read(build_plate([PlateSource(src, 1)], sets=1).content)
    rels = files["3D/_rels/3dmodel.model.rels"].decode()
    assert "object_4" not in rels  # its Id-first relationship went too
    assert not any(n.startswith("3D/Objects/object_4") for n in files)


def test_dropped_thumbnails_leave_no_dangling_references():
    files = _read(build_plate([PlateSource(snowman_like(), 1)], sets=1).content)
    assert "plate_1.png" not in files["_rels/.rels"].decode()
    assert "Thumbnail_Middle" not in files["3D/3dmodel.model"].decode()
    assert "3D/3dmodel.model" in files["_rels/.rels"].decode()


def test_plate_name_is_added_when_the_source_plate_has_none():
    src = snowman_like(plate_names=False)
    (plate,) = _plates(build_plate([PlateSource(src, 1)], sets=1, plate_name="Bianco x4").content)
    assert 'key="plater_name" value="Bianco x4"' in plate


def test_bed_from_a_printer_profile_replaces_the_designers_bed():
    big = snowman_like(bed=330)
    cc2 = {
        "printable_area": ["0x0", "256x0", "256x256", "0x256"],
        "bed_exclude_area": ["246x0", "256x0", "256x20", "246x20"],
    }
    result = build_plate([PlateSource(big, 1)], bed_from=cc2)
    assert result.bed == (256.0, 256.0)
    project = json.loads(_read(result.content)["Metadata/project_settings.config"])
    assert project["printable_area"] == cc2["printable_area"]
    assert project["bed_exclude_area"] == cc2["bed_exclude_area"]
    for b in _boxes(result.content):
        assert b[2] <= 256 - EDGE_MM + 1e-6 and b[3] <= 256 - EDGE_MM + 1e-6
        assert not _overlap(b, (246, 0, 256, 20), gap=EDGE_MM - 1e-6)
    assert result.sets < build_plate([PlateSource(big, 1)]).sets

    with pytest.raises(PlateBuildError, match="no printable_area"):
        build_plate([PlateSource(big, 1)], bed_from={"name": "x"})
