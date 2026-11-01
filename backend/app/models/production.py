"""ALVEOLAB Farm: production planning for products made of several printed parts.

A *recipe* says what one finished product is made of: per component (body,
scarf, eyes...), which plate of which library project 3MF prints it, which
objects of that plate, and in which colour. *Demand* is how many finished
products are owed (one row per shop order line, or a stock request). *Stock*
counts finished products (``component_key`` null) and loose printed
components. A *production plate* is a plate prepared for a printer: which
components it prints and how many sets, so its print adds to stock.

From these the Farm answers "what do we still have to print, per colour"
(``services/production.py``).
"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.database import Base


class ProductionRecipe(Base):
    __tablename__ = "production_recipes"
    __table_args__ = (UniqueConstraint("name", "variant", name="uq_production_recipe_name_variant"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    # Product name as the shop and the lab say it ("Fiocco"), and the variant ("Ghiaccio"; "" = the default one).
    name: Mapped[str] = mapped_column(String(120))
    variant: Mapped[str] = mapped_column(String(120), default="", server_default="")
    # Other names that mean this recipe (shop titles, nicknames), matched case-insensitively.
    aliases: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # [{key, label, library_file_id, plate, only_objects, colour_hex, colour_name, per_unit}]
    components: Mapped[list] = mapped_column(JSON, default=list)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class ProductionStock(Base):
    __tablename__ = "production_stock"
    __table_args__ = (UniqueConstraint("recipe_id", "component_key", name="uq_production_stock_item"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    recipe_id: Mapped[int] = mapped_column(ForeignKey("production_recipes.id", ondelete="CASCADE"))
    # "" = finished products; otherwise a component key of the recipe.
    component_key: Mapped[str] = mapped_column(String(60), default="", server_default="")
    qty: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class ProductionDemand(Base):
    __tablename__ = "production_demand"

    id: Mapped[int] = mapped_column(primary_key=True)
    recipe_id: Mapped[int] = mapped_column(ForeignKey("production_recipes.id", ondelete="CASCADE"))
    qty: Mapped[int] = mapped_column(Integer, default=1)
    # Where it comes from: "shopify:deko", "stock", "manual"...
    source: Mapped[str] = mapped_column(String(60), default="manual")
    # Caller's id for the line (e.g. "shopify:deko:#1114:3"), unique: re-sending it updates instead of duplicating.
    external_ref: Mapped[str | None] = mapped_column(String(200), unique=True, nullable=True)
    # open | done | cancelled
    status: Mapped[str] = mapped_column(String(20), default="open")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ProductionPlate(Base):
    __tablename__ = "production_plates"

    id: Mapped[int] = mapped_column(primary_key=True)
    # The printable file (sliced output) of this plate.
    library_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("library_files.id", ondelete="SET NULL"), nullable=True
    )
    printer_id: Mapped[int | None] = mapped_column(ForeignKey("printers.id", ondelete="SET NULL"), nullable=True)
    queue_item_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # [{recipe_id, component_key, sets}]
    contents: Mapped[list] = mapped_column(JSON, default=list)
    # prepared | printed | failed | cancelled
    status: Mapped[str] = mapped_column(String(20), default="prepared")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
