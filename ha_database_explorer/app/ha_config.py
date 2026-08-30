"""Discover DBs referenced from the live Home Assistant config.

Reads (when the add-on's ``homeassistant_config`` map is present):
- ``/config/configuration.yaml`` — ``recorder: db_url`` (the canonical recorder target)
- ``/config/.storage/core.config_entries`` — any ``recorder`` entries (future-proof)

All findings are surfaced as ``Save`` suggestions in the Setup tab — they are
never auto-saved. ``engine`` is inferred from the URL scheme.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from pathlib import Path

HASS_CONFIG = Path("/config/configuration.yaml")
STORAGE_CONFIG_ENTRIES = Path("/config/.storage/core.config_entries")


def _engine_from_scheme(scheme: str) -> str | None:
    s = scheme.lower()
    if s in ("mysql", "mariadb"):
        return "mysql"
    if s == "postgresql":
        return "postgresql"
    if s in ("sqlite", "sqlite3"):
        return "sqlite"
    return None


def _parse_db_url(raw: str) -> dict | None:
    """Parse a recorder ``db_url`` into a connection dict, or ``None``."""
    if not raw or not isinstance(raw, str):
        return None
    raw = raw.strip()
    if not raw:
        return None
    # ``sqlite:///`` URLs have an empty netloc; handle via string split first.
    if raw.startswith("sqlite"):
        # e.g. sqlite:////config/home-assistant_v2.db  or  sqlite:///:memory:
        # Use the path component as ``path``.
        try:
            parsed = urllib.parse.urlparse(raw)
            path = parsed.path or ""
            # ``sqlite:////config/...`` -> path ``//config/...`` then urlparse keeps leading slashes
            # Normalise: strip leading ``/`` single but keep absolute.
            if path.startswith("//"):
                path = path[1:]
            if path in ("", "/:memory:", ":memory:"):
                path = ":memory:"
            return {
                "engine": "sqlite",
                "connection_name": "HA recorder (configuration.yaml)",
                "path": path,
                "detected": "ha_config",
            }
        except Exception:
            return None
    try:
        parsed = urllib.parse.urlparse(raw)
        engine = _engine_from_scheme(parsed.scheme)
        if not engine:
            return None
        # ``parsed`` may still have query string e.g. ?charset=utf8
        user = urllib.parse.unquote(parsed.username or "")
        password = urllib.parse.unquote(parsed.password or "")
        host = parsed.hostname or ""
        port = parsed.port
        database = (parsed.path or "").lstrip("/") or "homeassistant"
        # Strip query-string remnants already handled by urlparse (path excludes ?)
        # For ``mysql://host/db?charset=utf8`` -> database ``db``
        return {
            "engine": engine,
            "connection_name": f"HA recorder ({host or database})",
            "host": host,
            "port": port,
            "user": user,
            "password": password,
            "database": database,
            "detected": "ha_config",
        }
    except Exception:
        return None


def _discover_from_yaml() -> list[dict]:
    if not HASS_CONFIG.exists():
        return []
    try:
        text = HASS_CONFIG.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []
    out: list[dict] = []
    # Lightweight extraction: look for ``recorder:`` block then ``db_url:``.
    # Full YAML parse would need PyYAML; regex avoids an extra dependency and
    # is resilient to HA's ``!include`` / ``!secret`` tags which break strict parsers.
    for m in re.finditer(r"db_url\s*:\s*(.+)", text):
        raw = m.group(1).strip().strip("'\"")
        # Strip trailing YAML comment
        raw = re.split(r"\s+#", raw, maxsplit=1)[0].strip().strip("'\"")
        entry = _parse_db_url(raw)
        if entry:
            out.append(entry)
    return out


def _discover_from_storage() -> list[dict]:
    if not STORAGE_CONFIG_ENTRIES.exists():
        return []
    try:
        data = json.loads(STORAGE_CONFIG_ENTRIES.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return []
    out: list[dict] = []
    for entry in data.get("data", {}).get("entries", []):
        if entry.get("domain") != "recorder":
            continue
        opts = entry.get("options") or {}
        raw = opts.get("db_url") or (entry.get("data") or {}).get("db_url") or ""
        parsed = _parse_db_url(raw)
        if parsed:
            # Prefer storage-sourced name
            parsed["connection_name"] = f"HA recorder ({parsed.get('host') or parsed.get('path') or 'storage'})"
            out.append(parsed)
    return out


async def discover_ha_config() -> list[dict]:
    seen: set[tuple] = set()
    merged: list[dict] = []
    for src in (_discover_from_yaml(), _discover_from_storage()):
        key = (src.get("engine"), src.get("host") or src.get("path"), src.get("database"))
        if key in seen:
            continue
        seen.add(key)
        merged.append(src)
    return merged
