"""ALVEOLAB Farm production planning: recipes, stock, demand, production plates, needs.

Reads need projects:read (API keys: can_read_status); writes need queue:create
(API keys: can_queue), since what they feed is what goes into the queue.

Counts change with single SQL statements (``qty = qty + n``, status moves
guarded by ``WHERE status = ...``): a retry and a click arriving together must
not add a plate's sets twice or lose one of two updates.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import case, select, update
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


async def _stock_keys(db: AsyncSession, recipe_id: int) -> set[str]:
    rows = await db.execute(select(ProductionStock.component_key).where(ProductionStock.recipe_id == recipe_id))
    return {k for (k,) in rows}


async def _check_component(db: AsyncSession, recipe: ProductionRecipe, key: str, *, stock_ok: bool = False) -> None:
    """A key of the recipe; for stock fixes also a key that only an old stock row still has."""
    if not key or key in {c["key"] for c in recipe.components or []}:
        return
    if stock_ok and key in await _stock_keys(db, recipe.id):
        return
    raise HTTPException(400, f"{recipe.name}: no component {key!r}")


async def _ensure_stock_row(db: AsyncSession, recipe_id: int, key: str) -> None:
    """The (recipe, component) stock row exists; a concurrent creator is not an error."""
    exists = (
        await db.execute(
            select(ProductionStock.id).where(
                ProductionStock.recipe_id == recipe_id, ProductionStock.component_key == key
            )
        )
    ).first()
    if exists:
        return
    try:
        async with db.begin_nested():
            db.add(ProductionStock(recipe_id=recipe_id, component_key=key, qty=0))
    except IntegrityError:
        pass  # created meanwhile by another request


async def _bump(db: AsyncSession, recipe_id: int, key: str, delta: int) -> None:
    """qty += delta in one statement, never below 0."""
    await _ensure_stock_row(db, recipe_id, key)
    new = ProductionStock.qty + delta
    await db.execute(
        update(ProductionStock)
        .where(ProductionStock.recipe_id == recipe_id, ProductionStock.component_key == key)
        .values(qty=case((new < 0, 0), else_=new))
    )


async def _stock_out(db: AsyncSession, recipe_id: int, key: str) -> ProductionStock:
    row = (
        await db.execute(
            select(ProductionStock)
            .where(ProductionStock.recipe_id == recipe_id, ProductionStock.component_key == key)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
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


async def _in_use(db: AsyncSession, recipe_id: int) -> tuple[bool, set[str]]:
    """(open demand exists, component keys on prepared plates) for a recipe."""
    demand = (
        await db.execute(
            select(ProductionDemand.id).where(
                ProductionDemand.recipe_id == recipe_id, ProductionDemand.status == "open"
            )
        )
    ).first()
    keys: set[str] = set()
    for (contents,) in await db.execute(select(ProductionPlate.contents).where(ProductionPlate.status == "prepared")):
        keys |= {c["component_key"] for c in contents or [] if int(c["recipe_id"]) == recipe_id}
    return demand is not None, keys


@router.put("/recipes/{recipe_id}", response_model=RecipeOut)
async def replace_recipe(
    recipe_id: int,
    body: RecipeIn,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(WRITE),
):
    recipe = await _recipe(db, recipe_id)
    # A component key that goes away while plates for it are being printed, or
    # loose parts of it are in stock, would leave counts nobody can reach.
    gone = {c["key"] for c in recipe.components or []} - {c.key for c in body.components}
    if gone:
        _, on_plates = await _in_use(db, recipe_id)
        stocked = {
            k
            for (k, q) in await db.execute(
                select(ProductionStock.component_key, ProductionStock.qty).where(ProductionStock.recipe_id == recipe_id)
            )
            if q
        }
        blocked = sorted(gone & (on_plates | stocked))
        if blocked:
            raise HTTPException(
                409, f"{recipe.name}: {', '.join(blocked)} still on prepared plates or in stock: settle them first"
            )
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
    """Refused while something is owed or being printed for it; its stock and old demand go with it
    (explicitly: SQLite here runs without foreign-key enforcement, and reuses the freed id)."""
    recipe = await _recipe(db, recipe_id)
    open_demand, on_plates = await _in_use(db, recipe_id)
    if open_demand or on_plates:
        raise HTTPException(409, f"{recipe.name}: open demand or prepared plates still use this recipe")
    await db.execute(ProductionStock.__table__.delete().where(ProductionStock.recipe_id == recipe_id))
    await db.execute(ProductionDemand.__table__.delete().where(ProductionDemand.recipe_id == recipe_id))
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
    await _check_component(db, await _recipe(db, body.recipe_id), body.component_key, stock_ok=True)
    await _ensure_stock_row(db, body.recipe_id, body.component_key)
    await db.execute(
        update(ProductionStock)
        .where(ProductionStock.recipe_id == body.recipe_id, ProductionStock.component_key == body.component_key)
        .values(qty=body.qty)
    )
    await db.commit()
    return await _stock_out(db, body.recipe_id, body.component_key)


@router.post("/stock/adjust", response_model=StockOut)
async def adjust_stock(
    body: StockAdjust, db: AsyncSession = Depends(get_db), _: User | None = RequirePermissionIfAuthEnabled(WRITE)
):
    await _check_component(db, await _recipe(db, body.recipe_id), body.component_key, stock_ok=True)
    await _bump(db, body.recipe_id, body.component_key, body.delta)
    await db.commit()
    return await _stock_out(db, body.recipe_id, body.component_key)


@router.post("/assemble", response_model=list[StockOut])
async def assemble(
    body: AssembleIn, db: AsyncSession = Depends(get_db), _: User | None = RequirePermissionIfAuthEnabled(WRITE)
):
    """Loose components become finished products: components go down, finished go up."""
    recipe = await _recipe(db, body.recipe_id)
    name = recipe.name
    components = [
        (c["key"], c.get("label") or c["key"], max(1, int(c.get("per_unit") or 1))) for c in recipe.components or []
    ]
    have = dict(
        (
            await db.execute(
                select(ProductionStock.component_key, ProductionStock.qty).where(ProductionStock.recipe_id == recipe.id)
            )
        ).all()
    )
    short = [
        f"{label} ({have.get(key, 0)} of {body.qty * per})"
        for key, label, per in components
        if have.get(key, 0) < body.qty * per
    ]
    if short and not body.force:
        raise HTTPException(400, f"Not enough loose parts for {body.qty} {name}: " + ", ".join(short))
    for key, _label, per in components:
        await _bump(db, recipe.id, key, -body.qty * per)
    await _bump(db, recipe.id, FINISHED, body.qty)
    await db.commit()
    return [await _stock_out(db, recipe.id, FINISHED)] + [
        await _stock_out(db, recipe.id, key) for key, _l, _p in components
    ]


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


async def _demand_by_ref(db: AsyncSession, ref: str) -> ProductionDemand | None:
    return (
        await db.execute(
            select(ProductionDemand)
            .where(ProductionDemand.external_ref == ref)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()


@router.post("/demand", response_model=DemandOut)
async def upsert_demand(
    body: DemandIn, db: AsyncSession = Depends(get_db), _: User | None = RequirePermissionIfAuthEnabled(WRITE)
):
    """Add owed products. Same ``external_ref`` again = update of that open line, not a new one;
    a line already closed is left as it was."""
    if body.recipe_id is not None:
        recipe_id = (await _recipe(db, body.recipe_id)).id
    else:
        recipes = [_recipe_dict(r) for r in (await db.execute(select(ProductionRecipe))).scalars()]
        found = match_recipe(recipes, body.recipe or "", body.variant)
        if found is None:
            raise HTTPException(422, f"No recipe for {body.recipe!r} {body.variant or ''}".strip())
        recipe_id = found["id"]
    row = await _demand_by_ref(db, body.external_ref) if body.external_ref else None
    if row is None:
        row = ProductionDemand(
            external_ref=body.external_ref,
            status="open",
            recipe_id=recipe_id,
            qty=body.qty,
            source=body.source,
            notes=body.notes,
        )
        db.add(row)
        try:
            await db.commit()
        except IntegrityError:
            # The same line sent twice at once: the other request created it.
            await db.rollback()
            row = await _demand_by_ref(db, body.external_ref) if body.external_ref else None
            if row is None:
                raise
        else:
            await db.refresh(row)
            return row
    if row.status == "open":
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
    recipe_id, qty = row.recipe_id, row.qty
    moved = await db.execute(
        update(ProductionDemand)
        .where(ProductionDemand.id == demand_id, ProductionDemand.status == "open")
        .values(status=body.status, closed_at=_now())
    )
    if moved.rowcount and body.status == "done" and body.ship:
        await _bump(db, recipe_id, FINISHED, -qty)
    await db.commit()
    return (
        await db.execute(
            select(ProductionDemand).where(ProductionDemand.id == demand_id).execution_options(populate_existing=True)
        )
    ).scalar_one()


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
        await _check_component(db, await _recipe(db, c.recipe_id), c.component_key)
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


async def _plate(db: AsyncSession, plate_id: int) -> ProductionPlate:
    plate = (
        await db.execute(
            select(ProductionPlate).where(ProductionPlate.id == plate_id).execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if plate is None:
        raise HTTPException(404, "Production plate not found")
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


async def _close_plate(db: AsyncSession, plate_id: int, status: str) -> bool:
    """prepared -> status in one guarded statement: True only for the request that moved it."""
    moved = await db.execute(
        update(ProductionPlate)
        .where(ProductionPlate.id == plate_id, ProductionPlate.status == "prepared")
        .values(status=status, closed_at=_now())
    )
    return bool(moved.rowcount)


@router.post("/plates/{plate_id}/printed", response_model=PlateOut)
async def plate_printed(
    plate_id: int, db: AsyncSession = Depends(get_db), _: User | None = RequirePermissionIfAuthEnabled(WRITE)
):
    """The plate came off the printer good: its sets go into the loose-component stock (once)."""
    plate = await _plate(db, plate_id)
    contents = list(plate.contents or [])
    if await _close_plate(db, plate_id, "printed"):
        existing = {r for (r,) in await db.execute(select(ProductionRecipe.id))}
        for c in contents:
            if int(c["recipe_id"]) in existing:  # a recipe deleted meanwhile has nothing to count into
                await _bump(db, int(c["recipe_id"]), c["component_key"], int(c["sets"]))
    await db.commit()
    return await _plate(db, plate_id)


@router.post("/plates/{plate_id}/failed", response_model=PlateOut)
async def plate_failed(
    plate_id: int, db: AsyncSession = Depends(get_db), _: User | None = RequirePermissionIfAuthEnabled(WRITE)
):
    """Failed or cancelled: its sets are owed again."""
    await _plate(db, plate_id)
    await _close_plate(db, plate_id, "failed")
    await db.commit()
    return await _plate(db, plate_id)


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
