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
    status TEXT NOT NULL
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
"""


async def init_cache() -> None:
    async with aiosqlite.connect(CACHE_DB) as db:
        await db.executescript(SCHEMA)
        await db.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def upsert_database(
    engine: str,
    connection_name: str,
    total_size_mb: float | None,
    status: str,
) -> str:
    db_id = str(uuid.uuid4())
    async with aiosqlite.connect(CACHE_DB) as db:
        await db.execute(
            "INSERT OR REPLACE INTO databases "
            "(id, engine, connection_name, total_size_mb, last_scanned, status) "
            "VALUES (?,?,?,?,?,?)",
            (db_id, engine, connection_name, total_size_mb, _now(), status),
        )
        await db.commit()
    return db_id


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
        cur = await db.execute("SELECT * FROM databases ORDER BY connection_name")
        return [dict(row) for row in await cur.fetchall()]


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
