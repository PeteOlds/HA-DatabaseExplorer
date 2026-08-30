"""Internal SQLite cache storing scan results for instant UI rendering."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import aiosqlite

from .config import CACHE_DB

SCHEMA = """
CREATE TABLE IF NOT EXISTS databases (
    id TEXT PRIMARY KEY,
    engine TEXT NOT NULL,
    connection_name TEXT NOT NULL,
    total_size_mb REAL,
    last_scanned TEXT,
    status TEXT NOT NULL,
    scan_duration_s REAL
);
CREATE TABLE IF NOT EXISTS domain_metrics (
    id TEXT PRIMARY KEY,
    db_id TEXT NOT NULL,
    domain TEXT NOT NULL,
    total_records INTEGER NOT NULL,
    estimated_size_mb REAL
);
CREATE TABLE IF NOT EXISTS entity_metrics (
    id TEXT PRIMARY KEY,
    db_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    record_count INTEGER NOT NULL,
    start_date TEXT,
    updates_per_hour REAL
);
CREATE TABLE IF NOT EXISTS overlap_matrix (
    entity_id TEXT PRIMARY KEY,
    present_in TEXT NOT NULL,
    total_redundant_records INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


async def init_cache() -> None:
    async with aiosqlite.connect(CACHE_DB) as db:
        await db.executescript(SCHEMA)
        # Migration: add scan_duration_s column if missing (added in 0.1.6)
        cur = await db.execute("PRAGMA table_info(databases)")
        columns = [row[1] for row in await cur.fetchall()]
        if "scan_duration_s" not in columns:
            await db.execute("ALTER TABLE databases ADD COLUMN scan_duration_s REAL")
        await db.commit()
    # Heal any historical duplicate rows left by the old uuid4-based upsert.
    await _dedupe_cache()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def set_meta(key: str, value: str) -> None:
    async with aiosqlite.connect(CACHE_DB) as db:
        await db.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            (key, value),
        )
        await db.commit()


async def get_meta(key: str) -> str | None:
    async with aiosqlite.connect(CACHE_DB) as db:
        cur = await db.execute("SELECT value FROM meta WHERE key = ?", (key,))
        row = await cur.fetchone()
        return row[0] if row else None


def _db_id(engine: str, connection_name: str) -> str:
    """Deterministic id so repeated scans update the same row instead of appending."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{engine}:{connection_name}"))


async def upsert_database(
    engine: str,
    connection_name: str,
    total_size_mb: float | None,
    status: str,
    scan_duration_s: float | None = None,
) -> str:
    db_id = _db_id(engine, connection_name)
    async with aiosqlite.connect(CACHE_DB) as db:
        # Drop any stale rows for this (engine, name) so there is exactly one.
        cur = await db.execute(
            "SELECT id FROM databases WHERE engine = ? AND connection_name = ? AND id <> ?",
            (engine, connection_name, db_id),
        )
        old_ids = [row[0] for row in await cur.fetchall()]
        await db.execute(
            "DELETE FROM databases WHERE engine = ? AND connection_name = ?",
            (engine, connection_name),
        )
        await db.execute(
            "INSERT INTO databases "
            "(id, engine, connection_name, total_size_mb, last_scanned, status, scan_duration_s) "
            "VALUES (?,?,?,?,?,?,?)",
            (db_id, engine, connection_name, total_size_mb, _now(), status, scan_duration_s),
        )
        for oid in old_ids:
            await db.execute("DELETE FROM domain_metrics WHERE db_id = ?", (oid,))
            await db.execute("DELETE FROM entity_metrics WHERE db_id = ?", (oid,))
        await db.commit()
    return db_id


async def _dedupe_cache() -> None:
    """Keep only the latest row per (engine, connection_name) and purge orphaned metrics."""
    async with aiosqlite.connect(CACHE_DB) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT id, engine, connection_name, last_scanned FROM databases")
        rows = await cur.fetchall()
        keep: dict[tuple, dict] = {}
        for r in rows:
            key = (r["engine"], r["connection_name"])
            if key not in keep or (r["last_scanned"] or "") >= (keep[key]["last_scanned"] or ""):
                keep[key] = r
        keep_ids = {r["id"] for r in keep.values()}
        for r in rows:
            if r["id"] not in keep_ids:
                await db.execute("DELETE FROM databases WHERE id = ?", (r["id"],))
                await db.execute("DELETE FROM domain_metrics WHERE db_id = ?", (r["id"],))
                await db.execute("DELETE FROM entity_metrics WHERE db_id = ?", (r["id"],))
        await db.commit()


async def replace_domain_metrics(db_id: str, rows: list[dict]) -> None:
    async with aiosqlite.connect(CACHE_DB) as db:
        await db.execute("DELETE FROM domain_metrics WHERE db_id = ?", (db_id,))
        for r in rows:
            params = (
                str(uuid.uuid4()),
                db_id,
                r["domain"],
                r["total_records"],
                r.get("estimated_size_mb"),
            )
            await db.execute(
                "INSERT INTO domain_metrics (id, db_id, domain, total_records, estimated_size_mb) "
                "VALUES (?,?,?,?,?)",
                params,
            )
        await db.commit()


async def replace_entity_metrics(db_id: str, rows: list[dict]) -> None:
    async with aiosqlite.connect(CACHE_DB) as db:
        await db.execute("DELETE FROM entity_metrics WHERE db_id = ?", (db_id,))
        for r in rows:
            await db.execute(
                "INSERT INTO entity_metrics "
                "(id, db_id, entity_id, record_count, start_date, updates_per_hour) "
                "VALUES (?,?,?,?,?,?)",
                (
                    str(uuid.uuid4()),
                    db_id,
                    r["entity_id"],
                    r["record_count"],
                    r.get("start_date"),
                    r.get("updates_per_hour"),
                ),
            )
        await db.commit()


async def replace_overlap(rows: list[dict]) -> None:
    async with aiosqlite.connect(CACHE_DB) as db:
        await db.execute("DELETE FROM overlap_matrix")
        for r in rows:
            params = (
                r["entity_id"],
                json.dumps(r["present_in"]),
                r["total_redundant_records"],
            )
            await db.execute(
                "INSERT OR REPLACE INTO overlap_matrix "
                "(entity_id, present_in, total_redundant_records) VALUES (?,?,?)",
                params,
            )
        await db.commit()


async def get_databases() -> list[dict]:
    async with aiosqlite.connect(CACHE_DB) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM databases")
        rows = [dict(row) for row in await cur.fetchall()]
    # Defensive de-dupe: keep the latest row per (engine, connection_name).
    seen: dict[tuple, dict] = {}
    out: list[dict] = []
    for r in sorted(rows, key=lambda x: x.get("last_scanned") or "", reverse=True):
        key = (r["engine"], r["connection_name"])
        if key not in seen:
            seen[key] = True
            out.append(r)
    out.sort(key=lambda x: x["connection_name"])
    return out


async def get_domain_metrics(db_id: str | None = None) -> list[dict]:
    async with aiosqlite.connect(CACHE_DB) as db:
        db.row_factory = aiosqlite.Row
        if db_id:
            cur = await db.execute("SELECT * FROM domain_metrics WHERE db_id = ?", (db_id,))
        else:
            cur = await db.execute("SELECT * FROM domain_metrics")
        return [dict(row) for row in await cur.fetchall()]


async def get_entity_metrics(
    domain: str | None = None,
    db_id: str | None = None,
    sort: str = "record_count",
    order: str = "desc",
) -> list[dict]:
    async with aiosqlite.connect(CACHE_DB) as db:
        db.row_factory = aiosqlite.Row
        sql = "SELECT * FROM entity_metrics WHERE 1=1"
        params: list = []
        if domain:
            sql += " AND domain = ?"
            params.append(domain)
        if db_id:
            sql += " AND db_id = ?"
            params.append(db_id)
        if sort in {"record_count", "updates_per_hour", "entity_id", "start_date"}:
            sql += f" ORDER BY {sort} {'DESC' if order == 'desc' else 'ASC'}"
        cur = await db.execute(sql, params)
        return [dict(row) for row in await cur.fetchall()]


async def get_overlap() -> list[dict]:
    async with aiosqlite.connect(CACHE_DB) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM overlap_matrix")
        rows = [dict(row) for row in await cur.fetchall()]
    for r in rows:
        r["present_in"] = json.loads(r["present_in"])
    return rows


async def all_entity_index() -> dict[str, set[str]]:
    """entity_id -> set of db_ids, used to build the overlap matrix."""
    out: dict[str, set[str]] = {}
    async with aiosqlite.connect(CACHE_DB) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT db_id, entity_id FROM entity_metrics")
        for row in await cur.fetchall():
            out.setdefault(row["entity_id"], set()).add(row["db_id"])
    return out
