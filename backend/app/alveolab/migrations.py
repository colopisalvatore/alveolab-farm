"""Schema changes of the ALVEOLAB modules, called from ``database.run_migrations``."""

from sqlalchemy.ext.asyncio import AsyncConnection


async def run(conn: AsyncConnection, safe_execute) -> None:
    """``safe_execute`` is Bambuddy's ``_safe_execute`` (ignores "column exists")."""
    # Protocol family of a printer. Existing rows are all Bambu: the DEFAULT backfills them.
    await safe_execute(conn, "ALTER TABLE printers ADD COLUMN provider VARCHAR(20) DEFAULT 'bambu' NOT NULL")
    # Build plate mounted on the printer (nullable, no backfill).
    await safe_execute(conn, "ALTER TABLE printers ADD COLUMN bed_type VARCHAR(40)")
