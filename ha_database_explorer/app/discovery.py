"""Environment discovery: Docker socket, filesystem, and hassio preset probes."""

from __future__ import annotations

import asyncio

import aiodocker

from .config import DOCKER_SOCK, PRESET_HOSTS, PRESET_PORTS, SQLITE_PATHS


async def _tcp_probe(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout
        )
        writer.close()
        return True
    except Exception:
        return False


def _engine_from_image(image: str) -> str | None:
    img = image.lower()
    if "mariadb" in img or "mysql" in img:
        return "mysql"
    if "influxdb" in img:
        return "influxdb"
    if "postgres" in img or "timescale" in img:
        return "postgresql"
    return None


async def discover_docker() -> list[dict]:
    if not DOCKER_SOCK.exists():
        return []
    out: list[dict] = []
    try:
        docker = aiodocker.Docker(url=f"unix://{DOCKER_SOCK}")
    except Exception:
        return []
    try:
        containers = await docker.containers.list()
        for c in containers:
            info = await c.show()
            engine = _engine_from_image(info["Image"])
            if not engine:
                continue
            name = (info.get("Name") or "").lstrip("/")
            port = PRESET_PORTS.get(engine, 8086)
            entry = {
                "engine": engine,
                "connection_name": f"{name} (docker)",
                "host": name,  # resolvable on the hassio network
                "port": port,
                "user": "",
                "password": "",
                "database": "homeassistant" if engine != "influxdb" else "homeassistant",
                "detected": "docker",
            }
            out.append(entry)
    except Exception:
        return out
    finally:
        await docker.close()
    return out


async def discover_sqlite() -> list[dict]:
    out: list[dict] = []
    for p in SQLITE_PATHS:
        if p.exists():
            out.append(
                {
                    "engine": "sqlite",
                    "connection_name": f"SQLite {p.name}",
                    "path": str(p),
                    "detected": "filesystem",
                }
            )
    return out


async def discover_presets() -> list[dict]:
    out: list[dict] = []
    for engine, hosts in PRESET_HOSTS.items():
        port = PRESET_PORTS.get(engine, 8086)
        for host in hosts:
            if await _tcp_probe(host, port):
                out.append(
                    {
                        "engine": engine,
                        "connection_name": f"{host} (preset)",
                        "host": host,
                        "port": port,
                        "user": "",
                        "password": "",
                        "database": "homeassistant",
                        "detected": "preset",
                    }
                )
    return out


async def discover_all() -> list[dict]:
    """Combine all discovery sources, de-duplicated by (engine, host/path)."""
    results = await asyncio.gather(
        discover_docker(), discover_sqlite(), discover_presets(), return_exceptions=True
    )
    seen: set[tuple] = set()
    merged: list[dict] = []
    for r in results:
        if isinstance(r, Exception):
            continue
        for entry in r:
            key = (entry["engine"], entry.get("host") or entry.get("path"))
            if key in seen:
                continue
            seen.add(key)
            merged.append(entry)
    return merged
