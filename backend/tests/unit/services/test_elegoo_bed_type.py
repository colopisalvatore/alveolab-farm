"""ALVEOLAB Farm, Centauri 2: the build plate a G-code was sliced for, and the
plain G-code out of a 3MF sliced for a CC2.

1/11/2026: PLA sliced for the Cool Plate (35 C) came off a textured PEI plate.
"""

import io
import zipfile

import pytest

from backend.app.services.printer_providers.elegoo.files import (
    TooManyPlatesForCC2,
    bed_type_mismatch,
    cc2_gcode_from_sliced_3mf,
    check_printable_on_cc2,
)

CC2_COOL = (
    "; HEADER_BLOCK_START\n; HEADER_BLOCK_END\nM140 S35\nG28\n"
    "; CONFIG_BLOCK_START\n; curr_bed_type = Cool Plate\n"
    "; printer_model = Elegoo Centauri 2\n; printer_settings_id = Elegoo Centauri 2 0.4 nozzle\n"
    "; CONFIG_BLOCK_END\n"
)
P1S = "G28\n; curr_bed_type = Textured PEI Plate\n; printer_model = Bambu Lab P1S\n"


def _sliced(plates: dict[int, str], png: bool = True) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        zf.writestr("3D/3dmodel.model", "<model/>")
        for n, text in plates.items():
            zf.writestr(f"Metadata/plate_{n}.gcode", text)
            if png:
                zf.writestr(f"Metadata/plate_{n}.png", b"\x89PNG plate")
    return out.getvalue()


def test_the_check_reads_the_plate_of_the_file(tmp_path):
    path = tmp_path / "a.gcode"
    path.write_text(CC2_COOL, encoding="utf-8")
    check = check_printable_on_cc2(path)
    assert check.ok and check.bed_type == "Cool Plate"


def test_a_file_that_names_no_plate_has_none(tmp_path):
    path = tmp_path / "a.gcode"
    path.write_text(CC2_COOL.replace("; curr_bed_type = Cool Plate\n", ""), encoding="utf-8")
    assert check_printable_on_cc2(path).bed_type is None


@pytest.mark.parametrize(
    ("file_bed", "printer_bed", "refused"),
    [
        ("Cool Plate", "Textured PEI Plate", True),
        ("Cool Plate", "cool plate ", False),
        ("Cool Plate", None, False),
        (None, "Textured PEI Plate", False),
    ],
)
def test_only_a_known_different_plate_is_refused(file_bed, printer_bed, refused):
    message = bed_type_mismatch(file_bed, printer_bed)
    assert bool(message) is refused
    if refused:
        assert "Cool Plate" in message and "Textured PEI Plate" in message


def test_cc2_gcode_comes_out_of_the_sliced_3mf_with_its_picture():
    got = cc2_gcode_from_sliced_3mf(_sliced({2: CC2_COOL}))
    assert got is not None
    assert got.gcode == CC2_COOL.encode()
    assert got.plate_index == 2
    assert got.sliced_for == "Elegoo Centauri 2"
    assert got.bed_type == "Cool Plate"
    assert got.thumbnail == b"\x89PNG plate"


def test_bambu_slices_and_project_files_are_left_alone():
    assert cc2_gcode_from_sliced_3mf(_sliced({1: P1S})) is None
    assert cc2_gcode_from_sliced_3mf(_sliced({})) is None
    assert cc2_gcode_from_sliced_3mf(b"not a zip") is None


def test_more_than_one_cc2_plate_is_refused():
    with pytest.raises(TooManyPlatesForCC2, match="2 plates"):
        cc2_gcode_from_sliced_3mf(_sliced({1: CC2_COOL, 2: CC2_COOL}))


def test_no_picture_is_fine():
    assert cc2_gcode_from_sliced_3mf(_sliced({1: CC2_COOL}, png=False)).thumbnail is None
