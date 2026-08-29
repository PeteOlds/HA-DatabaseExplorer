"""Application configuration loaded from environment / add-on options."""

from __future__ import annotations

import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("APP_DATA", "/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

CACHE_DB = DATA_DIR / "cache.db"
CONFIG_FILE = DATA_DIR / "config.json"
SECRET_KEY_FILE = DATA_DIR / "secret.key"

# Auto-detected / configurable targets.
HASSIO_NETWORK = "172.30.33.0/24"
SQLITE_PATHS = [
    Path("/config/home-assistant_v2.db"),
    DATA_DIR / "home-assistant_v2.db",
]
PRESET_HOSTS = {
    "mariadb": ["core-mariadb", "local-mariadb", "mariadb"],
    "influxdb": ["a0d7b954-influxdb", "influxdb"],
}
PRESET_PORTS = {"mysql": 3306, "influxdb": 8086, "postgresql": 5432}

DEFAULT_SCAN_CRON = os.environ.get("SCAN_CRON", "30 3 * * *")
CONNECTION_TIMEOUT = float(os.environ.get("CONNECTION_TIMEOUT", "10"))

DOCKER_SOCK = Path(os.environ.get("DOCKER_SOCK", "/var/run/docker.sock"))
