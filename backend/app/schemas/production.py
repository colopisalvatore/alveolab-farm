"""Schemas for /production (ALVEOLAB Farm production planning)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class RecipeComponent(BaseModel):
    # Short id inside the recipe ("corpo", "sciarpa", "occhi").
    key: str = Field(..., min_length=1, max_length=60)
    label: str | None = Field(default=None, max_length=120)
    # Project 3MF in the library and its plate (1-based) that print this component.
    library_file_id: int | None = None
    plate: int | None = Field(default=None, ge=1, le=200)
    # Only these objects of the plate (as in build-plate); None = the whole plate.
    only_objects: list[str] | None = None
    colour_hex: str = Field(..., max_length=9)
    colour_name: str | None = Field(default=None, max_length=60)
    # Copies of the plate (or objects) one product needs.
    per_unit: int = Field(default=1, ge=1, le=50)

    @field_validator("colour_hex")
    @classmethod
    def _hex(cls, v: str) -> str:
        v = v.strip().lstrip("#").upper()
        if len(v) not in (6, 8) or any(c not in "0123456789ABCDEF" for c in v):
            raise ValueError("colour_hex must be #RRGGBB")
        return "#" + v


class RecipeIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    variant: str = Field(default="", max_length=120)
    aliases: list[str] | None = None
    components: list[RecipeComponent] = Field(default_factory=list, max_length=40)
    notes: str | None = None

    @field_validator("components")
    @classmethod
    def _unique_keys(cls, v: list[RecipeComponent]) -> list[RecipeComponent]:
        keys = [c.key for c in v]
        if len(keys) != len(set(keys)):
            raise ValueError("component keys must be unique in a recipe")
        return v


class RecipeOut(RecipeIn):
    id: int
    created_at: datetime | None = None
    updated_at: datetime | None = None

    class Config:
        from_attributes = True


class StockSet(BaseModel):
    recipe_id: int
    # "" = finished products.
    component_key: str = Field(default="", max_length=60)
    qty: int = Field(..., ge=0)


class StockAdjust(BaseModel):
    recipe_id: int
    component_key: str = Field(default="", max_length=60)
    delta: int


class StockOut(BaseModel):
    recipe_id: int
    component_key: str
    qty: int

    class Config:
        from_attributes = True


class DemandIn(BaseModel):
    # Either the recipe id, or its name (and variant) as the shop or a person says it.
    recipe_id: int | None = None
    recipe: str | None = Field(default=None, max_length=200)
    variant: str | None = Field(default=None, max_length=120)
    qty: int = Field(default=1, ge=0, le=10000)
    source: str = Field(default="manual", max_length=60)
    external_ref: str | None = Field(default=None, max_length=200)
    notes: str | None = None


class DemandOut(BaseModel):
    id: int
    recipe_id: int
    qty: int
    source: str
    external_ref: str | None
    status: str
    notes: str | None
    created_at: datetime | None = None
    closed_at: datetime | None = None

    class Config:
        from_attributes = True


class DemandClose(BaseModel):
    status: Literal["done", "cancelled"] = "done"
    # A done order line takes its products out of the finished stock (they were shipped).
    ship: bool = True


class AssembleIn(BaseModel):
    recipe_id: int
    qty: int = Field(..., ge=1, le=1000)
    # Assemble even when the loose components in stock do not cover it (stock floors at 0).
    force: bool = False


class PlateContent(BaseModel):
    recipe_id: int
    component_key: str = Field(..., max_length=60)
    sets: int = Field(..., ge=1, le=1000)


class PlateIn(BaseModel):
    library_file_id: int | None = None
    printer_id: int | None = None
    queue_item_id: int | None = None
    contents: list[PlateContent] = Field(..., min_length=1, max_length=40)


class PlateOut(BaseModel):
    id: int
    library_file_id: int | None
    printer_id: int | None
    queue_item_id: int | None
    contents: list[dict]
    status: str
    created_at: datetime | None = None
    closed_at: datetime | None = None

    class Config:
        from_attributes = True


class PlateUpdate(BaseModel):
    queue_item_id: int | None = None
    printer_id: int | None = None
    library_file_id: int | None = None


class NeedItem(BaseModel):
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


class ColourNeedOut(BaseModel):
    colour_hex: str
    colour_name: str
    total: int
    items: list[NeedItem]


class RecipeStatusOut(BaseModel):
    recipe_id: int
    recipe: str
    variant: str
    owed: int
    finished: int
    short: int


class NeedsOut(BaseModel):
    recipes: list[RecipeStatusOut]
    colours: list[ColourNeedOut]
