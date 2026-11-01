"""ALVEOLAB Farm production planning: recipes, stock, demand, production plates, needs.

Reads need projects:read (API keys: can_read_status); writes need queue:create
(API keys: can_queue), since what they feed is what goes into the queue.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import RequirePermissionIfAuthEnabled
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.production import ProductionDemand, ProductionPlate, ProductionRecipe, ProductionStock
from backend.app.models.user import User
from backend.app.schemas.production import (
    AssembleIn,
    DemandClose,
    DemandIn,
    DemandOut,
    NeedsOut,
    PlateIn,
    PlateOut,
    PlateUpdate,
    RecipeIn,
    RecipeOut,
    StockAdjust,
    StockOut,
    StockSet,
)
from backend.app.services.production import FINISHED, compute_needs, match_recipe

router = APIRouter(prefix="/production", tags=["Production"])

READ = Permission.PROJECTS_READ
WRITE = Permission.QUEUE_CREATE


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _recipe_dict(r: ProductionRecipe) -> dict:
    return {
        "id": r.id,
        "name": r.name,
        "variant": r.variant or "",
        "aliases": r.aliases or [],
        "components": r.components or [],
    }


async def _recipe(db: AsyncSession, recipe_id: int) -> ProductionRecipe:
    recipe = (await db.execute(select(ProductionRecipe).where(ProductionRecipe.id == recipe_id))).scalar_one_or_none()
    if recipe is None:
        raise HTTPException(404, "Recipe not found")
    return recipe


def _check_component(recipe: ProductionRecipe, key: str) -> None:
    if key and key not in {c["key"] for c in recipe.components or []}:
        raise HTTPException(400, f"{recipe.name}: no component {key!r}")


async def _stock_row(db: AsyncSession, recipe_id: int, key: str) -> ProductionStock:
    row = (
        await db.execute(
            select(ProductionStock).where(ProductionStock.recipe_id == recipe_id, ProductionStock.component_key == key)
        )
    ).scalar_one_or_none()
    if row is None:
        row = ProductionStock(recipe_id=recipe_id, component_key=key, qty=0)
        db.add(row)
    return row


# ------------------------------------------------------------------ recipes


@router.get("/recipes", response_model=list[RecipeOut])
async def list_recipes(db: AsyncSession = Depends(get_db), _: User | None = RequirePermissionIfAuthEnabled(READ)):
    rows = (
        await db.execute(select(ProductionRecipe).order_by(ProductionRecipe.name, ProductionRecipe.variant))
    ).scalars()
    return list(rows)


@router.post("/recipes", response_model=RecipeOut)
async def create_recipe(
    body: RecipeIn, db: AsyncSession = Depends(get_db), _: User | None = RequirePermissionIfAuthEnabled(WRITE)
):
    recipe = ProductionRecipe(**body.model_dump(mode="json"))
    db.add(recipe)
    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(409, f"A recipe {body.name} {body.variant} already exists".strip()) from e
    await db.refresh(recipe)
    return recipe


@router.put("/recipes/{recipe_id}", response_model=RecipeOut)
async def replace_recipe(
    recipe_id: int,
    body: RecipeIn,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(WRITE),
):
    recipe = await _recipe(db, recipe_id)
    for k, v in body.model_dump(mode="json").items():
        setattr(recipe, k, v)
    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(409, f"A recipe {body.name} {body.variant} already exists".strip()) from e
    await db.refresh(recipe)
    return recipe


@router.delete("/recipes/{recipe_id}")
async def delete_recipe(
    recipe_id: int, db: AsyncSession = Depends(get_db), _: User | None = RequirePermissionIfAuthEnabled(WRITE)
):
    recipe = await _recipe(db, recipe_id)
    await db.delete(recipe)
    await db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ stock


@router.get("/stock", response_model=list[StockOut])
async def list_stock(db: AsyncSession = Depends(get_db), _: User | None = RequirePermissionIfAuthEnabled(READ)):
    return list((await db.execute(select(ProductionStock).where(ProductionStock.qty != 0))).scalars())


@router.put("/stock", response_model=StockOut)
async def set_stock(
    body: StockSet, db: AsyncSession = Depends(get_db), _: User | None = RequirePermissionIfAuthEnabled(WRITE)
):
    """Set a count after a stock take (finished products: component_key "")."""
    _check_component(await _recipe(db, body.recipe_id), body.component_key)
    row = await _stock_row(db, body.recipe_id, body.component_key)
    row.qty = body.qty
    await db.commit()
    await db.refresh(row)
    return row


@router.post("/stock/adjust", response_model=StockOut)
async def adjust_stock(
    body: StockAdjust, db: AsyncSession = Depends(get_db), _: User | None = RequirePermissionIfAuthEnabled(WRITE)
):
    _check_component(await _recipe(db, body.recipe_id), body.component_key)
    row = await _stock_row(db, body.recipe_id, body.component_key)
    row.qty = max(0, (row.qty or 0) + body.delta)
    await db.commit()
    await db.refresh(row)
    return row


@router.post("/assemble", response_model=list[StockOut])
async def assemble(
    body: AssembleIn, db: AsyncSession = Depends(get_db), _: User | None = RequirePermissionIfAuthEnabled(WRITE)
):
    """Loose components become finished products: components go down, finished go up."""
    recipe = await _recipe(db, body.recipe_id)
    rows = []
    short = []
    for comp in recipe.components or []:
        row = await _stock_row(db, recipe.id, comp["key"])
        use = body.qty * max(1, int(comp.get("per_unit") or 1))
        if (row.qty or 0) < use:
            short.append(f"{comp.get('label') or comp['key']} ({row.qty or 0} of {use})")
        rows.append((row, use))
    if short and not body.force:
        # Message first: a rollback expires the loaded rows.
        message = f"Not enough loose parts for {body.qty} {recipe.name}: " + ", ".join(short)
        await db.rollback()
        raise HTTPException(400, message)
    for row, use in rows:
        row.qty = max(0, (row.qty or 0) - use)
    finished = await _stock_row(db, recipe.id, FINISHED)
    finished.qty = (finished.qty or 0) + body.qty
    await db.commit()
    return [finished] + [r for r, _ in rows]


# ------------------------------------------------------------------ demand


@router.get("/demand", response_model=list[DemandOut])
async def list_demand(
    status: str | None = "open",
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(READ),
):
    q = select(ProductionDemand).order_by(ProductionDemand.created_at, ProductionDemand.id)
    if status:
        q = q.where(ProductionDemand.status == status)
    return list((await db.execute(q)).scalars())


@router.post("/demand", response_model=DemandOut)
async def upsert_demand(
    body: DemandIn, db: AsyncSession = Depends(get_db), _: User | None = RequirePermissionIfAuthEnabled(WRITE)
):
    """Add owed products. Same ``external_ref`` again = update of that line, not a new one."""
    if body.recipe_id is not None:
        recipe_id = (await _recipe(db, body.recipe_id)).id
    else:
        recipes = [_recipe_dict(r) for r in (await db.execute(select(ProductionRecipe))).scalars()]
        found = match_recipe(recipes, body.recipe or "", body.variant)
        if found is None:
            raise HTTPException(422, f"No recipe for {body.recipe!r} {body.variant or ''}".strip())
        recipe_id = found["id"]
    row = None
    if body.external_ref:
        row = (
            await db.execute(select(ProductionDemand).where(ProductionDemand.external_ref == body.external_ref))
        ).scalar_one_or_none()
    if row is None:
        row = ProductionDemand(external_ref=body.external_ref, status="open")
        db.add(row)
    row.recipe_id, row.qty, row.source, row.notes = recipe_id, body.qty, body.source, body.notes
    await db.commit()
    await db.refresh(row)
    return row


@router.post("/demand/{demand_id}/close", response_model=DemandOut)
async def close_demand(
    demand_id: int,
    body: DemandClose,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(WRITE),
):
    row = (await db.execute(select(ProductionDemand).where(ProductionDemand.id == demand_id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "Demand not found")
    if row.status == "open":
        row.status, row.closed_at = body.status, _now()
        if body.status == "done" and body.ship:
            finished = await _stock_row(db, row.recipe_id, FINISHED)
            finished.qty = max(0, (finished.qty or 0) - row.qty)
        await db.commit()
        await db.refresh(row)
    return row


# ------------------------------------------------------------------ plates


@router.get("/plates", response_model=list[PlateOut])
async def list_plates(
    status: str | None = "prepared",
    library_file_id: int | None = None,
    queue_item_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(READ),
):
    q = select(ProductionPlate).order_by(ProductionPlate.id)
    if status:
        q = q.where(ProductionPlate.status == status)
    if library_file_id is not None:
        q = q.where(ProductionPlate.library_file_id == library_file_id)
    if queue_item_id is not None:
        q = q.where(ProductionPlate.queue_item_id == queue_item_id)
    return list((await db.execute(q)).scalars())


@router.post("/plates", response_model=PlateOut)
async def register_plate(
    body: PlateIn, db: AsyncSession = Depends(get_db), _: User | None = RequirePermissionIfAuthEnabled(WRITE)
):
    """A plate prepared for a printer and what it prints: its sets count as coming until printed or failed."""
    for c in body.contents:
        _check_component(await _recipe(db, c.recipe_id), c.component_key)
    plate = ProductionPlate(
        library_file_id=body.library_file_id,
        printer_id=body.printer_id,
        queue_item_id=body.queue_item_id,
        contents=[c.model_dump() for c in body.contents],
        status="prepared",
    )
    db.add(plate)
    await db.commit()
    await db.refresh(plate)
    return plate


@router.patch("/plates/{plate_id}", response_model=PlateOut)
async def update_plate(
    plate_id: int,
    body: PlateUpdate,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(WRITE),
):
    plate = await _plate(db, plate_id)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(plate, k, v)
    await db.commit()
    await db.refresh(plate)
    return plate


async def _plate(db: AsyncSession, plate_id: int) -> ProductionPlate:
    plate = (await db.execute(select(ProductionPlate).where(ProductionPlate.id == plate_id))).scalar_one_or_none()
    if plate is None:
        raise HTTPException(404, "Production plate not found")
    return plate


@router.post("/plates/{plate_id}/printed", response_model=PlateOut)
async def plate_printed(
    plate_id: int, db: AsyncSession = Depends(get_db), _: User | None = RequirePermissionIfAuthEnabled(WRITE)
):
    """The plate came off the printer good: its sets go into the loose-component stock (once)."""
    plate = await _plate(db, plate_id)
    if plate.status == "prepared":
        for c in plate.contents or []:
            row = await _stock_row(db, int(c["recipe_id"]), c["component_key"])
            row.qty = (row.qty or 0) + int(c["sets"])
        plate.status, plate.closed_at = "printed", _now()
        await db.commit()
        await db.refresh(plate)
    return plate


@router.post("/plates/{plate_id}/failed", response_model=PlateOut)
async def plate_failed(
    plate_id: int, db: AsyncSession = Depends(get_db), _: User | None = RequirePermissionIfAuthEnabled(WRITE)
):
    """Failed or cancelled: its sets are owed again."""
    plate = await _plate(db, plate_id)
    if plate.status == "prepared":
        plate.status, plate.closed_at = "failed", _now()
        await db.commit()
        await db.refresh(plate)
    return plate


# ------------------------------------------------------------------ needs


@router.get("/needs", response_model=NeedsOut)
async def needs(db: AsyncSession = Depends(get_db), _: User | None = RequirePermissionIfAuthEnabled(READ)):
    """What still has to be printed, per colour, after finished and loose stock and prepared plates."""
    recipes = [_recipe_dict(r) for r in (await db.execute(select(ProductionRecipe))).scalars()]
    stock = [
        {"recipe_id": s.recipe_id, "component_key": s.component_key, "qty": s.qty}
        for s in (await db.execute(select(ProductionStock))).scalars()
    ]
    demand = [
        {"recipe_id": d.recipe_id, "qty": d.qty}
        for d in (await db.execute(select(ProductionDemand).where(ProductionDemand.status == "open"))).scalars()
    ]
    plates = [
        {"contents": p.contents}
        for p in (await db.execute(select(ProductionPlate).where(ProductionPlate.status == "prepared"))).scalars()
    ]
    statuses, colours = compute_needs(recipes, stock, demand, plates)
    return NeedsOut(
        recipes=[s.__dict__ for s in statuses],
        colours=[{**c.__dict__, "items": [i.__dict__ for i in c.items]} for c in colours],
    )
