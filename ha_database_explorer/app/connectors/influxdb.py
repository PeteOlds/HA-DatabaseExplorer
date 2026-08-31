"""InfluxDB 1.8 connector (httpx, async). Read-only; supports blank auth."""

from __future__ import annotations

import asyncio

import httpx

from .base import BaseConnector, DomainMetric, EntityMetric

CONCURRENCY = 8


class InfluxDBConnector(BaseConnector):
    engine = "influxdb"

    def __init__(self, connection_name: str, config: dict) -> None:
        super().__init__(connection_name, config)
        self.host = config.get("host", "a0d7b954-influxdb")
        self.port = int(config.get("port", 8086))
        self.user = config.get("user", "")
        self.password = config.get("password", "")
        self.database = config.get("database", "homeassistant")
        self.base = f"http://{self.host}:{self.port}"

    def _auth(self) -> dict:
        if self.user:
            return {"auth": (self.user, self.password)}
        return {}

    async def test_connection(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10.0) as c:
                r = await c.get(f"{self.base}/ping", **self._auth())
                return r.status_code < 500
        except Exception:
            return False

    async def total_size_mb(self) -> float | None:
        """Query InfluxDB internal stats to calculate disk size for this database."""
        try:
            # Query the _internal database for shard disk usage
            # The shard measurement in _internal tracks diskBytes per shard
            async with httpx.AsyncClient(timeout=30.0) as c:
                params = {
                    "db": "_internal",
                    "q": f'SELECT sum("diskBytes") FROM _internal."autogen"."shard" WHERE "database" = \'{self.database}\' AND time > now() - 1m'
                }
                r = await c.get(f"{self.base}/query", params=params, **self._auth())
                r.raise_for_status()
                data = r.json()
                
            # Parse the result
            for res in data.get("results", []):
                for ser in res.get("series", []):
                    for row in ser.get("values", []):
                        # row format: [time, sum_value]
                        if len(row) >= 2 and row[1] is not None:
                            total_bytes = int(row[1])
                            return total_bytes / (1024 * 1024)  # Convert to MB
            return None
        except Exception:
            # _internal database might not be accessible or query failed
            return None

    async def _query(self, q: str) -> list:
        params = {"db": self.database, "q": q}
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.get(f"{self.base}/query", params=params, **self._auth())
            r.raise_for_status()
            data = r.json()
        out = []
        for res in data.get("results", []):
            for ser in res.get("series", []):
                for row in ser.get("values", []):
                    out.append(row)
        return out

    async def entity_metrics(self) -> list[EntityMetric]:
        try:
            # Get all unique entity_ids from the database
            inventory = await self._query('SHOW TAG VALUES WITH KEY = "entity_id"')
        except Exception:
            return []
        entity_ids = [row[-1] for row in inventory if row]

        sem = asyncio.Semaphore(CONCURRENCY)

        async def _one(entity_id: str) -> EntityMetric | None:
            async with sem:
                try:
                    # Query all measurements filtered by entity_id tag
                    # Use regex to match entity_id in tag value
                    escaped_id = entity_id.replace("\\", "\\\\").replace("'", "\\'")
                    cnt = await self._query(
                        f'SELECT count(value) FROM /.*/ WHERE "entity_id" = \'{escaped_id}\''
                    )
                    fst = await self._query(
                        f'SELECT first(value) FROM /.*/ WHERE "entity_id" = \'{escaped_id}\''
                    )
                except Exception:
                    return None
            record_count = int(cnt[0][1]) if cnt and len(cnt[0]) > 1 else 0
            start = fst[0][0] if fst else None
            if record_count == 0:
                return None
            return EntityMetric(
                entity_id=entity_id,
                record_count=record_count,
                start_date=start,
                updates_per_hour=_rate(record_count, start),
            )

        results = await asyncio.gather(*(_one(e) for e in entity_ids))
        return [m for m in results if m]

    async def domain_metrics(self, entities: list[EntityMetric]) -> list[DomainMetric]:
        return await super().domain_metrics(entities)


def _rate(count: int, start: str | None) -> float:
    if not start:
        return 0.0
    try:
        from datetime import datetime, timezone

        dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
        hours = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
        return round(count / hours, 4) if hours > 0 else 0.0
    except Exception:
        return 0.0
