"""Connector factory."""

from __future__ import annotations

from .base import BaseConnector
from .influxdb import InfluxDBConnector
from .mysql import MySQLConnector
from .postgres_stub import PostgresConnector
from .sqlite import SQLiteConnector

_FACTORIES = {
    "sqlite": SQLiteConnector,
    "mysql": MySQLConnector,
    "mariadb": MySQLConnector,
    "influxdb": InfluxDBConnector,
    "postgresql": PostgresConnector,
    "timescaledb": PostgresConnector,
}


def build_connector(engine: str, connection_name: str, config: dict) -> BaseConnector:
    factory = _FACTORIES.get(engine.lower())
    if not factory:
        raise ValueError(f"Unsupported engine: {engine}")
    return factory(connection_name, config)
