"""Persistent connection store (encrypted credentials on disk)."""

from __future__ import annotations

import json

from .config import CONFIG_FILE
from .crypto import deserialize_config, serialize_config


def _key(c: dict) -> tuple:
    return (c.get("engine"), c.get("connection_name"))


def load_connections() -> list[dict]:
    if not CONFIG_FILE.exists():
        return []
    try:
        raw = json.loads(CONFIG_FILE.read_text())
    except Exception:
        return []
    conns = [deserialize_config(r) for r in raw]
    # De-duplicate by (engine, connection_name) so duplicates can't accumulate.
    seen: dict[tuple, bool] = {}
    out: list[dict] = []
    for c in conns:
        k = _key(c)
        if k not in seen:
            seen[k] = True
            out.append(c)
    return out


def save_connections(connections: list[dict]) -> None:
    raw = [serialize_config(c) for c in connections]
    CONFIG_FILE.write_text(json.dumps(raw, indent=2))


def add_connection(conn: dict) -> list[dict]:
    conns = load_connections()
    k = _key(conn)
    if any(_key(c) == k for c in conns):
        return conns
    conns.append(conn)
    save_connections(conns)
    return conns


def remove_connection(name: str) -> list[dict]:
    conns = [c for c in load_connections() if c.get("connection_name") != name]
    save_connections(conns)
    return conns


def update_connection(name: str, fields: dict) -> list[dict]:
    """Merge ``fields`` into the stored connection matching ``name``.

    ``connection_name`` and ``engine`` are immutable — changing them would
    orphan scan-cache rows and create duplicate keys, so they are ignored
    even if supplied. An empty/omitted password is preserved so editing
    other fields never wipes existing credentials."""
    conns = load_connections()
    for c in conns:
        if c.get("connection_name") == name:
            for k, v in fields.items():
                if k in ("connection_name", "engine"):
                    continue
                if k == "password" and (v is None or v == ""):
                    continue
                c[k] = v
            break
    save_connections(conns)
    return conns
