"""Round-trip write-back of purge_keep_days to HA configuration.yaml.

Uses ruamel.yaml (typ='rt') so comments, !include, !secret tags are preserved
when modifying the recorder.purge_keep_days key.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import ruamel.yaml

HASS_CONFIG = Path("/config/configuration.yaml")
BACKUP_CONFIG = Path("/config/.storage/backup/configuration.yaml.backup")


def _load_yaml_rt() -> dict | None:
    """Load configuration.yaml with ruamel.yaml round-trip.

    Returns the parsed dict, or None if the file doesn't exist or can't be parsed.
    """
    if not HASS_CONFIG.exists():
        return None
    try:
        yaml = ruamel.yaml.YAML(typ="rt")
        with HASS_CONFIG.open("r", encoding="utf-8") as f:
            data = yaml.load(f)
        return data
    except Exception:
        return None


def _save_yaml_rt(data: dict) -> bool:
    """Save configuration.yaml with ruamel.yaml round-trip, preserving formatting.

    Creates a backup before overwriting.
    """
    try:
        # Create backup of existing config
        if HASS_CONFIG.exists():
            BACKUP_CONFIG.parent.mkdir(parents=True, exist_ok=True)
            BACKUP_CONFIG.write_text(HASS_CONFIG.read_text(encoding="utf-8"), encoding="utf-8")

        yaml = ruamel.yaml.YAML(typ="rt")
        with HASS_CONFIG.open("w", encoding="utf-8") as f:
            yaml.dump(data, f)
        return True
    except Exception as exc:
        # Attempt restore from backup on failure
        try:
            if BACKUP_CONFIG.exists():
                HASS_CONFIG.write_text(
                    BACKUP_CONFIG.read_text(encoding="utf-8"), encoding="utf-8"
                )
        except Exception:
            pass
        return False


def write_purge_keep_days(connection_name: str, days: int | None) -> bool:
    """Write ``purge_keep_days`` to HA configuration.yaml via ruamel.yaml round-trip.

    - ``days`` is an integer (1..365) → sets ``recorder.purge_keep_days: X``
    - ``days`` is ``None`` → removes the ``purge_keep_days`` key entirely

    Returns ``True`` on success, ``False`` on failure (restores backup on error).
    """
    data = _load_yaml_rt()
    if data is None:
        return False

    # Ensure recorder block exists
    if "recorder" not in data:
        data["recorder"] = {}

    if days is None:
        # Remove the key entirely
        data["recorder"].pop("purge_keep_days", None)
    else:
        # Validate range
        if not isinstance(days, int) or days < 1 or days > 365:
            return False
        data["recorder"]["purge_keep_days"] = days

    return _save_yaml_rt(data)