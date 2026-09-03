"""SQLite recorder connector (Home Assistant default recorder DB)."""

from __future__ import annotations

from pathlib import Path

import aiosqlite

from .base import BaseConnector, DomainMetric, EntityMetric

DEFAULT_PATH = "/config/home-assistant_v2.db"


class SQLiteConnector(BaseConnector):
    engine = "sqlite"

    def __init__(self, connection_name: str, config: dict) -> None:
        super().__init__(connection_name, config)
        self.path = Path(config.get("path", DEFAULT_PATH))

    async def test_connection(self) -> bool:
        if self.path == ":memory:":
            try:
                async with aiosqlite.connect(self.path) as db:
                    await db.execute("SELECT 1 FROM states_meta LIMIT 1")
                return True
            except Exception:
                return False
        if not self.path.exists():
            return False
        try:
            async with aiosqlite.connect(self.path) as db:
                await db.execute("SELECT 1 FROM states_meta LIMIT 1")
            return True
        except Exception:
            return False

    async def total_size_mb(self) -> float | None:
        if not self.path.exists():
            return None
        size = self.path.stat().st_size / 1_000_000
        self._total_size = size
        return size

    async def entity_metrics(self) -> list[EntityMetric]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """
                SELECT sm.entity_id AS entity_id,
                       COUNT(s.rowid) AS record_count,
                       MIN(COALESCE(NULLIF(s.last_updated, ''), datetime(s.last_updated_ts, 'unixepoch'))) AS start_date,
                       MAX(COALESCE(NULLIF(s.last_updated, ''), datetime(s.last_updated_ts, 'unixepoch'))) AS end_date
                FROM states s
                JOIN states_meta sm ON sm.metadata_id = s.metadata_id
                GROUP BY sm.metadata_id
                """
            )
            rows = await cur.fetchall()
        out: list[EntityMetric] = []
        for r in rows:
            start = r["start_date"]
            end = r["end_date"]
            uph = _rate(r["record_count"], start)
            out.append(
                EntityMetric(
                    entity_id=r["entity_id"],
                    record_count=r["record_count"],
                    start_date=start,
                    end_date=end,
                    updates_per_hour=uph,
                )
            )
        return out

    async def domain_metrics(self, entities: list[EntityMetric]) -> list[DomainMetric]:
        return await super().domain_metrics(entities)

    async def get_entity_values(self, entity_id: str, limit: int = 100, offset: int = 0) -> list[dict]:
        """Get recent state values for a specific entity."""
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """
                SELECT s.state, s.attributes, s.last_updated, s.last_updated_ts
                FROM states s
                JOIN states_meta sm ON sm.metadata_id = s.metadata_id
                WHERE sm.entity_id = ?
                ORDER BY s.last_updated_ts DESC
                LIMIT ? OFFSET ?
                """,
                (entity_id, limit, offset),
            )
            rows = await cur.fetchall()
        out = []
        for r in rows:
            out.append({
                "state": r["state"],
                "attributes": r["attributes"],
                "last_updated": r["last_updated"],
                "last_updated_ts": r["last_updated_ts"],
            })
        return out


def _rate(count: int, start: str | None) -> float:
    if not start:
        return 0.0
    try:
        from datetime import datetime, timezone

        dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
        # Ensure dt is timezone-aware (assume UTC if naive)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        hours = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
        return round(count / hours, 4) if hours > 0 else 0.0
    except Exception:
        return 0.0
