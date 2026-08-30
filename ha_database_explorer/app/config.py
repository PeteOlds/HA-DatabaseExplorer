"""Application configuration loaded from environment / add-on options."""

from __future__ import annotations

import json
import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("APP_DATA", "/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

CACHE_DB = DATA_DIR / "cache.db"
CONFIG_FILE = DATA_DIR / "config.json"
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


_OPTIONS = _load_options()
DEFAULT_SCAN_CRON = _OPTIONS.get("scan_cron") or os.environ.get("SCAN_CRON", "30 3 * * *")


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
