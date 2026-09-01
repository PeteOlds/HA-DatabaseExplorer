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

import ruamel.yaml

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


def _extract_purge_keep_days_from_yaml(text: str) -> int | None:
    """Extract purge_keep_days from HA config YAML text using ruamel.yaml round-trip.

    Returns the integer value, or None if not found/unparseable.
    ruamel.yaml preserves !include / !secret tags; if they prevent parsing we fall back to None.
    """
    try:
        yaml = ruamel.yaml.YAML(typ="rt")
        data = yaml.load(text)
        if data and "recorder" in data:
            purge = data["recorder"].get("purge_keep_days")
            if isinstance(purge, int):
                return purge
        return None
    except Exception:
        return None


def parse_purge_keep_days() -> int | None:
    """Read purge_keep_days from /config/configuration.yaml using ruamel.yaml round-trip.

    Returns integer days, or None if not set / unparseable.
    """
    HASS_CONFIG = Path("/config/configuration.yaml")
    if not HASS_CONFIG.exists():
        return None
    try:
        text = HASS_CONFIG.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return None
    return _extract_purge_keep_days_from_yaml(text)


def parse_purge_keep_days_from_storage() -> int | None:
    """Read purge_keep_days from /config/.storage/core.config_entries.

    Returns integer days, or None if not set.
    """
    STORAGE_CONFIG_ENTRIES = Path("/config/.storage/core.config_entries")
    if not STORAGE_CONFIG_ENTRIES.exists():
        return None
    try:
        data = json.loads(STORAGE_CONFIG_ENTRIES.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return None
    for entry in data.get("data", {}).get("entries", []):
        if entry.get("domain") != "recorder":
            continue
        opts = entry.get("options") or {}
        val = opts.get("purge_keep_days")
        if val is not None:
            return int(val)
        # some entries store under data sub-key
        ddata = entry.get("data") or {}
        val = ddata.get("purge_keep_days")
        if val is not None:
            return int(val)
    return None


async def discover_ha_config() -> list[dict]:
    seen: set[tuple] = set()
    merged: list[dict] = []
    for src in (_discover_from_yaml(), _discover_from_storage()):
        # enrichment: add purge_keep_days from HA config
        purge = await parse_purge_keep_days() if src.get("detected") == "ha_config" else None
        if purge is None:
            purge = parse_purge_keep_days_from_storage()
        if "purge_keep_days" not in src or purge is not None:
            src["purge_keep_days"] = purge
        key = (src.get("engine"), src.get("host") or src.get("path"), src.get("database"))
        if key in seen:
            continue
        seen.add(key)
        merged.append(src)
    return merged
