"""Connector base interface shared by all database backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class EntityMetric:
    entity_id: str
    record_count: int
    start_date: str | None
    end_date: str | None
    updates_per_hour: float


@dataclass
class DomainMetric:
    domain: str
    total_records: int
    estimated_size_mb: float | None


class BaseConnector(ABC):
    engine: str = "unknown"

    def __init__(self, connection_name: str, config: dict) -> None:
        self.connection_name = connection_name
        self.config = config

    @abstractmethod
    async def test_connection(self) -> bool:
        ...

    @abstractmethod
    async def total_size_mb(self) -> float | None:
        """Disk footprint. Return None when not obtainable via this backend's API."""

    @abstractmethod
    async def entity_metrics(self) -> list[EntityMetric]:
        ...

    async def domain_metrics(self, entities: list[EntityMetric]) -> list[DomainMetric]:
        """Default aggregation: group entities by HA domain and sum records."""
        by_domain: dict[str, list[EntityMetric]] = {}
        for e in entities:
            domain = e.entity_id.split(".", 1)[0] if "." in e.entity_id else "other"
            by_domain.setdefault(domain, []).append(e)
        total = sum(e.record_count for e in entities) or 1
        out: list[DomainMetric] = []
        for domain, items in sorted(by_domain.items()):
            recs = sum(i.record_count for i in items)
            # Approximate per-domain size as a share of total file/index size.
            size = self._total_size
            est = (size * recs / total) if size else None
            out.append(DomainMetric(domain=domain, total_records=recs, estimated_size_mb=est))
        return out

    _total_size: float | None = None
