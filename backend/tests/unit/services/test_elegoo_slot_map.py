"""CANVAS ``slot_map`` of a Centauri 2 start, from the G-code and the loaded slots.

1/11/2026: without a ``slot_map`` a white ``T0`` file started on slot 4
(black), the slot the previous print had used.
"""

from backend.app.services.printer_providers.base import MultiColorSlot
from backend.app.services.printer_providers.elegoo.slots import FileFilament, file_filaments, plan_slot_map

ORCA_TAIL_WHITE = "; filament_colour = #FFFFFF\n; filament_type = PLA\n; printer_model = Elegoo Centauri Carbon 2\n"


def _slot(index, material, color, loaded=True, unit=0):
    return MultiColorSlot(unit_id=unit, slot_index=index, loaded=loaded, material=material, color_hex=color)


# The CANVAS of 1/11/2026.
CANVAS = [
    _slot(0, "PLA", "FFFFFF"),
    _slot(1, "PLA", "F72221"),
    _slot(2, "PLA", "2850DF"),
    _slot(3, "PLA", "000000"),
]


class TestFileFilaments:
    def test_single_colour_orca_file(self, tmp_path):
        path = tmp_path / "babbo.gcode"
        path.write_text("; filament: 1\nM6211 A1 L200 T0 Q220 R220 S220\nG1 X1\n" + ORCA_TAIL_WHITE)
        assert file_filaments(path) == [FileFilament(0, "PLA", "FFFFFF")]

    def test_only_used_tools_out_of_the_declared_ones(self, tmp_path):
        """The cubo declares five filaments and prints with T0 only."""
        path = tmp_path / "cubo.gcode"
        path.write_text(
            "M6211 A1 L200 T0 Q220 R220 S220\nT0\nG1 X1\n"
            "; filament_colour = #000000;#FFFFFF;#008000;#0000FF;#FFFF00\n"
            "; filament_type = PLA;PLA;PLA;PLA;PLA\n"
        )
        assert file_filaments(path) == [FileFilament(0, "PLA", "000000")]

    def test_two_colours(self, tmp_path):
        path = tmp_path / "two.gcode"
        path.write_text(
            "M6211 A1 L200 T0\nG1 X1\nT1 ; change\nG1 X2\nT0\n"
            '; filament_colour = "#FFFFFF";"#F72221FF"\n'
            "; filament_type = PLA;PETG\n"
        )
        assert file_filaments(path) == [FileFilament(0, "PLA", "FFFFFF"), FileFilament(1, "PETG", "F72221")]

    def test_tool_changes_across_read_chunks(self, tmp_path, monkeypatch):
        from backend.app.services.printer_providers.elegoo import slots

        monkeypatch.setattr(slots, "_SCAN_CHUNK", 7)
        path = tmp_path / "chunks.gcode"
        path.write_text(
            "G1 X1\nT2\nG1 X22222\nT1\n; filament_type = A;B;C\n; filament_colour = #111111;#222222;#333333\n"
        )
        assert [f.t for f in file_filaments(path)] == [1, 2]

    def test_special_tools_are_not_filaments(self, tmp_path):
        path = tmp_path / "special.gcode"
        path.write_text("T255\nT1000\nG1 X1\n" + ORCA_TAIL_WHITE)
        assert [f.t for f in file_filaments(path)] == [0]

    def test_no_tool_command_means_t0(self, tmp_path):
        path = tmp_path / "plain.gcode"
        path.write_text("G28\nG1 X1\n" + ORCA_TAIL_WHITE)
        assert [f.t for f in file_filaments(path)] == [0]


class TestPlanSlotMap:
    def test_white_goes_to_the_white_slot_not_the_last_used(self):
        plan = plan_slot_map([FileFilament(0, "PLA", "FFFFFF")], CANVAS)
        assert plan.problem is None
        assert plan.slot_map == [{"t": 0, "canvas_id": 0, "tray_id": 0}]
        assert plan.picks == ["T0 PLA #FFFFFF -> slot 1 PLA #FFFFFF"]

    def test_closest_similar_colour(self):
        slots = [_slot(0, "PLA", "1A1A1A"), _slot(1, "PLA", "0A0A0A")]
        plan = plan_slot_map([FileFilament(0, "PLA", "000000")], slots)
        assert plan.slot_map == [{"t": 0, "canvas_id": 0, "tray_id": 1}]

    def test_exact_colour_beats_slot_order(self):
        slots = [_slot(0, "PLA", "F0F0F0"), _slot(2, "PLA", "FFFFFF")]
        plan = plan_slot_map([FileFilament(0, "PLA", "FFFFFF")], slots)
        assert plan.slot_map == [{"t": 0, "canvas_id": 0, "tray_id": 2}]

    def test_two_tools_two_slots(self):
        plan = plan_slot_map([FileFilament(0, "PLA", "000000"), FileFilament(1, "PLA", "F72221")], CANVAS)
        assert plan.slot_map == [
            {"t": 0, "canvas_id": 0, "tray_id": 3},
            {"t": 1, "canvas_id": 0, "tray_id": 1},
        ]

    def test_one_slot_never_feeds_two_tools(self):
        plan = plan_slot_map(
            [FileFilament(0, "PLA", "FFFFFF"), FileFilament(1, "PLA", "FFFFFF")], [_slot(0, "PLA", "FFFFFF")]
        )
        assert plan.problem and "T1" in plan.problem

    def test_missing_colour_is_refused_with_what_is_loaded(self):
        plan = plan_slot_map([FileFilament(0, "PLA", "00FF00")], CANVAS)
        assert plan.slot_map == []
        assert "PLA #00FF00 for T0" in plan.problem
        assert "slot 1 PLA #FFFFFF" in plan.problem

    def test_wrong_material_is_refused(self):
        plan = plan_slot_map([FileFilament(0, "PETG", "FFFFFF")], CANVAS)
        assert plan.problem and "PETG #FFFFFF" in plan.problem

    def test_empty_slots_are_ignored(self):
        slots = [_slot(0, None, None, loaded=False), _slot(1, "PLA", "FFFFFF")]
        plan = plan_slot_map([FileFilament(0, "PLA", "FFFFFF")], slots)
        assert plan.slot_map == [{"t": 0, "canvas_id": 0, "tray_id": 1}]

    def test_file_without_filament_data_is_refused(self):
        plan = plan_slot_map([FileFilament(0, None, None)], CANVAS)
        assert plan.problem and "does not say" in plan.problem

    def test_no_canvas_means_no_map(self):
        plan = plan_slot_map([FileFilament(0, None, None)], [])
        assert plan.problem is None and plan.slot_map == []
