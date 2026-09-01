"""Application configuration loaded from environment / add-on options."""

from __future__ import annotations

import json
import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("APP_DATA", "/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

CACHE_DB = DATA_DIR / "cache.db"
CONFIG_FILE = DATA_DIR / "app_config.json"
SECRET_KEY_FILE = DATA_DIR / "secret.key"
# HAOS mounts the add-on's options here.
OPTIONS_FILE = DATA_DIR / "options.json"

# Auto-detected / configurable targets.
HASSIO_NETWORK = "172.30.33.0/24"
SQLITE_PATHS = [
    Path("/config/home-assistant_v2.db"),
    DATA_DIR / "home-assistant_v2.db",
]
PRESET_HOSTS = {
    "mariadb": ["core_mariadb", "core-mariadb", "local-mariadb", "local-mariadb", "mariadb"],
    "influxdb": ["a0d7b954-influxdb", "influxdb"],
}
PRESET_PORTS = {"mysql": 3306, "influxdb": 8086, "postgresql": 5432}

CONNECTION_TIMEOUT = float(os.environ.get("CONNECTION_TIMEOUT", "10"))
DOCKER_SOCK = Path(os.environ.get("DOCKER_SOCK", "/var/run/docker.sock"))


def _load_options() -> dict:
    if not OPTIONS_FILE.exists():
        return {}
    try:
        return json.loads(OPTIONS_FILE.read_text())
    except Exception:
        return {}


def _load_config() -> dict:
    if not CONFIG_FILE.exists():
        return {}
    try:
        data = json.loads(CONFIG_FILE.read_text())
        # Migration: if config contains connections array (old format), extract scan_cron and reset
        if isinstance(data, list):
            # Old format: connections were stored here. Migrate to empty config.
            _save_config({})
            return {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_config(config: dict) -> None:
    CONFIG_FILE.write_text(json.dumps(config, indent=2))


_OPTIONS = _load_options()
_CONFIG = _load_config()

# Precedence: config.json (persistent) > options.json (add-on UI) > env > default
DEFAULT_SCAN_CRON = (
    _CONFIG.get("scan_cron")
    or _OPTIONS.get("scan_cron")
    or os.environ.get("SCAN_CRON", "30 3 * * *")
)


def get_scan_cron() -> str:
    """Get current scan cron (reads from config.json for runtime changes)."""
    config = _load_config()
    return config.get("scan_cron") or _OPTIONS.get("scan_cron") or os.environ.get("SCAN_CRON", "30 3 * * *")


def set_scan_cron(cron: str) -> None:
    """Persist scan cron to config.json."""
    config = _load_config()
    config["scan_cron"] = cron
    _save_config(config)


def _load_manual_sizes() -> dict:
    raw = _OPTIONS.get("manual_sizes")
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


MANUAL_SIZES = _load_manual_sizes()
