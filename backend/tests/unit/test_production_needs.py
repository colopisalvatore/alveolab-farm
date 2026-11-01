"""What still has to be printed, per colour (services/production.py)."""

from backend.app.services.production import compute_needs, match_recipe, norm

FIOCCO = {
    "id": 1,
    "name": "Fiocco",
    "variant": "",
    "aliases": ["Pupazzo di neve"],
    "components": [
        {
            "key": "corpo",
            "label": "Corpo",
            "library_file_id": 10,
            "plate": 1,
            "colour_hex": "#FFFFFF",
            "colour_name": "Bianco",
        },
        {
            "key": "sciarpa",
            "label": "Sciarpa e cappello",
            "library_file_id": 10,
            "plate": 2,
            "colour_hex": "#C12E1F",
            "colour_name": "Rosso",
        },
        {
            "key": "occhi",
            "library_file_id": 10,
            "plate": 3,
            "only_objects": ["Eye"],
            "colour_hex": "#000000",
            "colour_name": "Nero",
            "per_unit": 2,
        },
    ],
}
FIOCCO_ICE = {
    "id": 2,
    "name": "Fiocco",
    "variant": "Ghiaccio",
    "aliases": [],
    "components": [
        {"key": "corpo", "library_file_id": 10, "plate": 1, "colour_hex": "FFFFFF", "colour_name": "Bianco"},
        {"key": "sciarpa", "library_file_id": 10, "plate": 2, "colour_hex": "#A5D8FF", "colour_name": "Ice Blue"},
    ],
}
FRAC = {
    "id": 3,
    "name": "Frac",
    "variant": "",
    "aliases": [],
    "components": [
        {"key": "corpo", "library_file_id": 20, "plate": 2, "colour_hex": "#FFFFFF", "colour_name": "Bianco"}
    ],
}
RECIPES = [FIOCCO, FIOCCO_ICE, FRAC]


def test_names_compare_without_case_accents_or_extra_spaces():
    assert norm("  Nevio  Pelúche ") == "nevio peluche"


def test_match_by_name_variant_alias_and_shop_title():
    assert match_recipe(RECIPES, "fiocco")["id"] == 1
    assert match_recipe(RECIPES, "Fiocco", "Ghiaccio")["id"] == 2
    assert match_recipe(RECIPES, "pupazzo di neve")["id"] == 1
    assert match_recipe(RECIPES, "Fiocco - Ghiaccio")["id"] == 2
    assert match_recipe(RECIPES, "Decorazione a maglia Fiocco", "Classica")["id"] == 1
    assert match_recipe(RECIPES, "Cometa") is None
    assert match_recipe(RECIPES, "") is None


def test_needs_per_colour_after_stock_and_prepared_plates():
    stock = [
        {"recipe_id": 1, "component_key": "", "qty": 2},  # 2 finished Fiocco
        {"recipe_id": 1, "component_key": "corpo", "qty": 3},  # 3 loose bodies
        {"recipe_id": 3, "component_key": "corpo", "qty": 1},
    ]
    demand = [{"recipe_id": 1, "qty": 10}, {"recipe_id": 2, "qty": 3}, {"recipe_id": 3, "qty": 4}]
    plates = [{"contents": [{"recipe_id": 1, "component_key": "sciarpa", "sets": 5}]}]
    statuses, colours = compute_needs(RECIPES, stock, demand, plates)

    by_id = {s.recipe_id: s for s in statuses}
    assert (by_id[1].owed, by_id[1].finished, by_id[1].short) == (10, 2, 8)
    assert by_id[2].short == 3

    totals = {c.colour_name: c.total for c in colours}
    # White: Fiocco 8-3=5, Fiocco Ghiaccio 3, Frac 4-1=3.
    assert totals["Bianco"] == 11
    assert totals["Rosso"] == 3  # 8 - 5 on a prepared plate
    assert totals["Nero"] == 16  # eyes: 8 products x 2
    assert totals["Ice Blue"] == 3
    assert [c.colour_name for c in colours][0] == "Nero"  # most sets first
    white = next(c for c in colours if c.colour_name == "Bianco")
    assert [(i.recipe, i.variant, i.need) for i in white.items] == [
        ("Fiocco", "", 5),
        ("Fiocco", "Ghiaccio", 3),
        ("Frac", "", 3),
    ]
    eyes = next(c for c in colours if c.colour_name == "Nero").items[0]
    assert (eyes.library_file_id, eyes.plate, eyes.only_objects) == (10, 3, ["Eye"])


def test_nothing_owed_nothing_to_print():
    statuses, colours = compute_needs(
        RECIPES, [{"recipe_id": 1, "component_key": "", "qty": 5}], [{"recipe_id": 1, "qty": 5}], []
    )
    assert colours == []
    assert {s.recipe_id: s.short for s in statuses}[1] == 0
