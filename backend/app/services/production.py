"""ALVEOLAB Farm: what still has to be printed, per colour.

For each recipe the open demand, less the finished products in stock, is the
number of products short. Each component then needs ``short * per_unit`` sets,
less the loose components in stock and the sets already on plates prepared but
not printed yet. Components of every recipe are grouped by colour, because a
plate prints one colour: that is the list a person (or Jarvis) picks plates from.

Pure functions over plain dicts, so the rules are testable without a database.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

FINISHED = ""  # component_key of finished products in stock


def norm(text: str | None) -> str:
    """Lower case, no accents, single spaces: how names are compared."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text).strip().lower()


DEFAULT_VARIANTS = {"", "classica", "classico", "standard", "default", "default title"}


def match_recipe(recipes: list[dict], name: str, variant: str | None = None) -> dict | None:
    """The recipe a name means, or None: better to tell "no recipe" than to book an order on the
    wrong product. Accepted: the name or an alias, with the variant given apart (a default variant
    such as "Classica" means the recipe without variant), or "name variant" written together.

    ``recipes`` items need ``id``, ``name``, ``variant``, ``aliases``.
    """
    want, want_variant = norm(name), norm(variant)
    if want_variant in DEFAULT_VARIANTS:
        want_variant = ""
    if not want:
        return None

    def names(r: dict) -> list[str]:
        return [norm(r["name"])] + [norm(a) for a in (r.get("aliases") or [])]

    for r in recipes:
        if want in names(r) and norm(r.get("variant")) == want_variant:
            return r
    if want_variant:
        return None
    for r in recipes:
        v = norm(r.get("variant"))
        if v and any(want == f"{n} {v}" for n in names(r)):
            return r
    return None


@dataclass
class ComponentNeed:
    recipe_id: int
    recipe: str
    variant: str
    component_key: str
    label: str
    need: int
    library_file_id: int | None
    plate: int | None
    only_objects: list[str] | None
    colour_hex: str
    colour_name: str


@dataclass
class ColourNeed:
    colour_hex: str
    colour_name: str
    total: int = 0
    items: list[ComponentNeed] = field(default_factory=list)


@dataclass
class RecipeStatus:
    recipe_id: int
    recipe: str
    variant: str
    owed: int
    finished: int
    short: int


def compute_needs(
    recipes: list[dict], stock: list[dict], demand: list[dict], plates: list[dict]
) -> tuple[list[RecipeStatus], list[ColourNeed]]:
    """(per recipe status, per colour needs sorted by most sets first).

    ``stock``: {recipe_id, component_key, qty}; ``demand``: open rows {recipe_id, qty};
    ``plates``: prepared, not yet printed rows {contents: [{recipe_id, component_key, sets}]}.
    """
    have: dict[tuple[int, str], int] = {}
    for s in stock:
        key = (s["recipe_id"], s.get("component_key") or FINISHED)
        have[key] = have.get(key, 0) + int(s.get("qty") or 0)
    on_plates: dict[tuple[int, str], int] = {}
    for p in plates:
        for c in p.get("contents") or []:
            key = (int(c["recipe_id"]), c.get("component_key") or FINISHED)
            on_plates[key] = on_plates.get(key, 0) + int(c.get("sets") or 0)
    owed: dict[int, int] = {}
    for d in demand:
        owed[d["recipe_id"]] = owed.get(d["recipe_id"], 0) + int(d.get("qty") or 0)

    statuses: list[RecipeStatus] = []
    colours: dict[str, ColourNeed] = {}
    for r in recipes:
        rid = r["id"]
        finished = max(0, have.get((rid, FINISHED), 0))
        short = max(0, owed.get(rid, 0) - finished)
        statuses.append(RecipeStatus(rid, r["name"], r.get("variant") or "", owed.get(rid, 0), finished, short))
        if not short:
            continue
        for comp in r.get("components") or []:
            key = comp["key"]
            want = short * max(1, int(comp.get("per_unit") or 1))
            need = want - max(0, have.get((rid, key), 0)) - on_plates.get((rid, key), 0)
            if need <= 0:
                continue
            hex_ = "#" + str(comp.get("colour_hex") or "").lstrip("#").upper()
            bucket = colours.setdefault(hex_, ColourNeed(hex_, comp.get("colour_name") or hex_))
            bucket.total += need
            bucket.items.append(
                ComponentNeed(
                    recipe_id=rid,
                    recipe=r["name"],
                    variant=r.get("variant") or "",
                    component_key=key,
                    label=comp.get("label") or key,
                    need=need,
                    library_file_id=comp.get("library_file_id"),
                    plate=comp.get("plate"),
                    only_objects=comp.get("only_objects"),
                    colour_hex=hex_,
                    colour_name=bucket.colour_name,
                )
            )
    ordered = sorted(colours.values(), key=lambda c: (-c.total, c.colour_name))
    for c in ordered:
        c.items.sort(key=lambda i: (-i.need, i.recipe, i.component_key))
    return statuses, ordered
