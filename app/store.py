"""Persistent connection store (encrypted credentials on disk)."""

from __future__ import annotations

import json

from .config import CONFIG_FILE
from .crypto import deserialize_config, serialize_config


def load_connections() -> list[dict]:
    if not CONFIG_FILE.exists():
        return []
    try:
        raw = json.loads(CONFIG_FILE.read_text())
    except Exception:
        return []
    return [deserialize_config(r) for r in raw]


def save_connections(connections: list[dict]) -> None:
    raw = [serialize_config(c) for c in connections]
    CONFIG_FILE.write_text(json.dumps(raw, indent=2))


def add_connection(conn: dict) -> list[dict]:
    conns = load_connections()
    conns.append(conn)
    save_connections(conns)
    return conns


def remove_connection(name: str) -> list[dict]:
    conns = [c for c in load_connections() if c.get("connection_name") != name]
    save_connections(conns)
    return conns
