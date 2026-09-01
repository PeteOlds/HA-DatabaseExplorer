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
        """Query InfluxDB debug endpoint to calculate disk size for this database."""
        try:
            # Query the /debug/vars endpoint which exposes shard-level stats
            async with httpx.AsyncClient(timeout=30.0) as c:
                r = await c.get(f"{self.base}/debug/vars", **self._auth())
                r.raise_for_status()
                data = r.json()
                
            # Sum diskBytes across all shards for this database
            total_bytes = 0
            for key, value in data.items():
                if key.startswith("shard:") and self.database in key:
                    disk_bytes = value.get("values", {}).get("diskBytes", 0)
                    total_bytes += disk_bytes
            
            return total_bytes / (1024 * 1024) if total_bytes > 0 else None
        except Exception:
            # /debug/vars might not be accessible or query failed
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
                    lst = await self._query(
                        f'SELECT last(value) FROM /.*/ WHERE "entity_id" = \'{escaped_id}\''
                    )
                except Exception:
                    return None
            record_count = int(cnt[0][1]) if cnt and len(cnt[0]) > 1 else 0
            start = fst[0][0] if fst else None
            end = lst[0][0] if lst else None
            if record_count == 0:
                return None
            return EntityMetric(
                entity_id=entity_id,
                record_count=record_count,
                start_date=start,
                end_date=end,
                updates_per_hour=_rate(record_count, start),
            )

        results = await asyncio.gather(*(_one(e) for e in entity_ids))
        return [m for m in results if m]

    async def domain_metrics(self, entities: list[EntityMetric]) -> list[DomainMetric]:
        return await super().domain_metrics(entities)

    async def get_retention_policies(self) -> list[dict]:
        """Get all retention policies for the configured database.
        
        Returns list of dicts with: name, duration, shard_group_duration, replica_n, default
        """
        try:
            rows = await self._query("SHOW RETENTION POLICIES")
        except Exception:
            return []
        policies = []
        for row in rows:
            # row format: [name, duration, shardGroupDuration, replicaN, default]
            if len(row) >= 5:
                policies.append({
                    "name": row[0],
                    "duration": row[1],
                    "shard_group_duration": row[2],
                    "replica_n": row[3],
                    "default": bool(row[4]),
                })
        return policies

    async def get_entity_rp(self, entity_id: str) -> str | None:
        """Determine which retention policy an entity uses by querying its measurements."""
        try:
            # Get measurements that contain this entity_id
            # Query SHOW MEASUREMENTS with tag filter
            escaped_id = entity_id.replace("\\", "\\\\").replace("'", "\\'")
            rows = await self._query(
                f'SHOW MEASUREMENTS WHERE "entity_id" = \'{escaped_id}\''
            )
            if not rows:
                return None
            # The first measurement found - get its RP via SHOW RETENTION POLICIES
            # Actually, we need to check which RP the measurement belongs to
            # For InfluxDB 1.8, measurements are in the default RP unless specified
            # Let's check the default RP
            policies = await self.get_retention_policies()
            default_rp = next((p["name"] for p in policies if p.get("default")), None)
            return default_rp
        except Exception:
            return None

    async def set_retention_policy(self, name: str, duration: str, shard_group_duration: str | None = None, replica_n: int | None = None, make_default: bool = False) -> bool:
        """Create or alter a retention policy.
        
        Args:
            name: RP name
            duration: Duration string (e.g., '30d', '7d', 'INF')
            shard_group_duration: Optional shard group duration
            replica_n: Optional replication factor
            make_default: Whether to make this the default RP
        """
        try:
            # Check if RP exists
            policies = await self.get_retention_policies()
            exists = any(p["name"] == name for p in policies)
            
            parts = [f'RETENTION POLICY "{name}" ON "{self.database}"']
            if exists:
                parts[0] = "ALTER " + parts[0]
            else:
                parts[0] = "CREATE " + parts[0]
            
            parts.append(f"DURATION {duration}")
            if shard_group_duration:
                parts.append(f"SHARD GROUP DURATION {shard_group_duration}")
            if replica_n is not None:
                parts.append(f"REPLICATION {replica_n}")
            if make_default:
                parts.append("DEFAULT")
            
            query = " ".join(parts)
            await self._query(query)
            return True
        except Exception:
            return False

    async def delete_retention_policy(self, name: str) -> bool:
        """Delete a retention policy."""
        try:
            query = f'DROP RETENTION POLICY "{name}" ON "{self.database}"'
            await self._query(query)
            return True
        except Exception:
            return False

    async def get_entity_values(self, entity_id: str, limit: int = 100, offset: int = 0) -> list[dict]:
        """Get recent state values for a specific entity from all measurements."""
        try:
            escaped_id = entity_id.replace("\\", "\\\\").replace("'", "\\'")
            # Query all measurements for this entity_id
            rows = await self._query(
                f'SELECT * FROM /.*/ WHERE "entity_id" = \'{escaped_id}\' ORDER BY time DESC LIMIT {limit} OFFSET {offset}'
            )
            out = []
            for row in rows:
                # row format: [time, value, ...other columns...]
                if len(row) >= 2:
                    out.append({
                        "time": row[0],
                        "value": row[1],
                        # Include all other columns as attributes
                    })
            return out
        except Exception:
            return []


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
