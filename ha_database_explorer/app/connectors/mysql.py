"""MariaDB / MySQL recorder connector (aiomysql, READ UNCOMMITTED)."""

from __future__ import annotations

import aiomysql

from ..config import CONNECTION_TIMEOUT
from .base import BaseConnector, EntityMetric


class MySQLConnector(BaseConnector):
    engine = "mysql"

    def __init__(self, connection_name: str, config: dict) -> None:
        super().__init__(connection_name, config)
        self.host = config.get("host", "core-mariadb")
        self.port = int(config.get("port", 3306))
        self.user = config.get("user", "homeassistant")
        self.password = config.get("password", "")
        self.db = config.get("database", "homeassistant")

    def _pool_args(self) -> dict:
        return {
            "host": self.host,
            "port": self.port,
            "user": self.user,
            "password": self.password,
            "db": self.db,
            "connect_timeout": CONNECTION_TIMEOUT,
        }

    async def test_connection(self) -> bool:
        try:
            pool = await aiomysql.create_pool(**self._pool_args())
        except Exception:
            return False
        try:
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT 1 FROM states_meta LIMIT 1")
            return True
        except Exception:
            return False
        finally:
            pool.close()
            await pool.wait_closed()

    async def total_size_mb(self) -> float | None:
        try:
            pool = await aiomysql.create_pool(**self._pool_args())
        except Exception:
            return None
        try:
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        "SELECT SUM(DATA_LENGTH + INDEX_LENGTH) "
                        "FROM information_schema.TABLES WHERE TABLE_SCHEMA = %s",
                        (self.db,),
                    )
                    (size,) = await cur.fetchone()
            self._total_size = float((size or 0) / 1_000_000)
            return self._total_size
        except Exception:
            return None
        finally:
            pool.close()
            await pool.wait_closed()

    async def entity_metrics(self) -> list[EntityMetric]:
        pool = await aiomysql.create_pool(**self._pool_args())
        try:
            async with pool.acquire() as conn:
                async with conn.cursor(aiomysql.DictCursor) as cur:
                    await cur.execute("SET SESSION TRANSACTION ISOLATION LEVEL READ UNCOMMITTED")
                    await cur.execute(
                        """
                        SELECT sm.entity_id AS entity_id,
                               COUNT(*) AS record_count,
                               MIN(COALESCE(NULLIF(s.last_updated, ''), FROM_UNIXTIME(s.last_updated_ts))) AS start_date,
                               MAX(COALESCE(NULLIF(s.last_updated, ''), FROM_UNIXTIME(s.last_updated_ts))) AS end_date
                        FROM states s
                        JOIN states_meta sm ON sm.metadata_id = s.metadata_id
                        GROUP BY sm.metadata_id
                        """
                    )
                    rows = await cur.fetchall()
            out: list[EntityMetric] = []
            for r in rows:
                out.append(
                    EntityMetric(
                        entity_id=r["entity_id"],
                        record_count=r["record_count"],
                        start_date=_iso(r["start_date"]),
                        end_date=_iso(r["end_date"]),
                        updates_per_hour=_rate(r["record_count"], r["start_date"]),
                    )
                )
            return out
        finally:
            pool.close()
            await pool.wait_closed()

    async def get_entity_values(self, entity_id: str, limit: int = 100, offset: int = 0) -> list[dict]:
        """Get recent state values for a specific entity."""
        pool = await aiomysql.create_pool(**self._pool_args())
        try:
            async with pool.acquire() as conn:
                async with conn.cursor(aiomysql.DictCursor) as cur:
                    await cur.execute("SET SESSION TRANSACTION ISOLATION LEVEL READ UNCOMMITTED")
                    await cur.execute(
                        """
                        SELECT s.state, s.attributes, s.last_updated, s.last_updated_ts
                        FROM states s
                        JOIN states_meta sm ON sm.metadata_id = s.metadata_id
                        WHERE sm.entity_id = %s
                        ORDER BY s.last_updated_ts DESC
                        LIMIT %s OFFSET %s
                        """,
                        (entity_id, limit, offset),
                    )
                    rows = await cur.fetchall()
            out = []
            for r in rows:
                out.append({
                    "state": r["state"],
                    "attributes": r["attributes"],
                    "last_updated": _iso(r["last_updated"]),
                    "last_updated_ts": r["last_updated_ts"],
                })
            return out
        finally:
            pool.close()
            await pool.wait_closed()


def _iso(val) -> str | None:
    if val is None:
        return None
    if hasattr(val, "isoformat"):
        return val.isoformat()
    return str(val)


def _rate(count: int, start) -> float:
    if not start:
        return 0.0
    try:
        from datetime import datetime, timezone

        if hasattr(start, "isoformat"):
            dt = start
        else:
            dt = datetime.fromisoformat(str(start))
        # Ensure dt is timezone-aware (assume UTC if naive)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        hours = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
        return round(count / hours, 4) if hours > 0 else 0.0
    except Exception:
        return 0.0
