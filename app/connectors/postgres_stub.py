"""PostgreSQL / TimescaleDB connector — deferred (first build targets SQLite/MySQL/InfluxDB)."""

from __future__ import annotations

from .base import BaseConnector


class PostgresConnector(BaseConnector):
    engine = "postgresql"

    def __init__(self, connection_name: str, config: dict) -> None:
        super().__init__(connection_name, config)
        raise NotImplementedError(
            "PostgreSQL/TimescaleDB support is deferred. Supported engines: "
            "sqlite, mysql (MariaDB), influxdb."
        )
