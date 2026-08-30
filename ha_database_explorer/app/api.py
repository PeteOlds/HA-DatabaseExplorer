"""FastAPI REST + WebSocket API."""

from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, HTTPException, WebSocket
from pydantic import BaseModel

from .cache import (
    get_databases,
    get_domain_metrics,
    get_entity_metrics,
    get_overlap,
    init_cache,
)
from .config import DEFAULT_SCAN_CRON
from .connectors import build_connector
from .crypto import safe_config_dump
from .discovery import discover_all
from .scan import JOBS, run_scan, subscribe, unsubscribe
from .store import add_connection, load_connections, remove_connection


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_cache()
    # Zero-config bootstrap: if no databases are configured yet, auto-discover and
    # persist whatever the environment exposes so the add-on works with no user input.
    # Runs as a background task because the add-on container's DNS may not be ready at
    # first boot; it retries for a few minutes instead of blocking startup.
    if not load_connections():
        asyncio.create_task(_bootstrap_discover())
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        lambda: _spawn_scan(uuid.uuid4().hex),
        CronTrigger.from_crontab(DEFAULT_SCAN_CRON),
        id="deep_scan",
        replace_existing=True,
    )
    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(title="HA Database Explorer", version="0.1.0", lifespan=lifespan)


@app.get("/api/health")
async def health():
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}


def _spawn_scan(job_id: str) -> None:
    """Run a scan as a detached task, swallowing any exception so it never leaks
    as an unretrieved Future warning."""

    async def _run() -> None:
        try:
            await run_scan(job_id)
        except Exception:
            pass

    task = asyncio.create_task(_run())
    task.add_done_callback(lambda t: t.exception())


async def _bootstrap_discover(max_attempts: int = 90, interval: float = 2.0) -> None:
    """Best-effort auto-discovery that keeps retrying after startup so it survives
    the add-on container's DNS not being ready at first boot."""
    for _ in range(max_attempts):
        try:
            found = await discover_all()
        except Exception:
            found = []
        if found:
            for entry in found:
                try:
                    add_connection(entry)
                except Exception:
                    pass
            return
        await asyncio.sleep(interval)


class DBConfig(BaseModel):
    engine: str
    connection_name: str
    host: str | None = None
    port: int | None = None
    user: str | None = None
    password: str | None = None
    database: str | None = None
    path: str | None = None
    manual_size_mb: float | None = None


@app.get("/api/databases")
async def list_databases():
    return await get_databases()


@app.post("/api/databases")
async def save_database(cfg: DBConfig):
    conn = cfg.model_dump(exclude_none=True)
    add_connection(conn)
    return {"saved": True, "config": safe_config_dump(conn)}


@app.delete("/api/databases/{name}")
async def delete_database(name: str):
    remove_connection(name)
    return {"deleted": name}


@app.post("/api/databases/discover")
async def discover():
    return await discover_all()


@app.post("/api/databases/test-connection")
async def test_connection(cfg: DBConfig):
    try:
        cfg_dict = cfg.model_dump(exclude_none=True)
        connector = build_connector(cfg.engine, cfg.connection_name, cfg_dict)
        ok = await connector.test_connection()
    except Exception as exc:
        return {"connected": False, "error": str(exc)}
    return {"connected": ok}


@app.post("/api/scan/trigger")
async def trigger_scan():
    job_id = str(uuid.uuid4())
    _spawn_scan(job_id)
    return {"job_id": job_id}


@app.get("/api/scan/status/{job_id}")
async def scan_status(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="unknown job")
    return job


@app.websocket("/api/scan/ws")
async def scan_ws(ws: WebSocket):
    await ws.accept()
    subscribe(ws)
    try:
        for job_id, job in JOBS.items():
            await ws.send_text(json.dumps({"job_id": job_id, **job}))
        while True:
            await ws.receive_text()  # keep-alive; broadcasts pushed via scan._broadcast
    except Exception:
        pass
    finally:
        unsubscribe(ws)


@app.get("/api/metrics/global")
async def metrics_global():
    dbs = await get_databases()
    domains = await get_domain_metrics()
    overlap = await get_overlap()
    total_mb = sum(d["total_size_mb"] or 0 for d in dbs)
    total_records = sum(dm["total_records"] for dm in domains)
    by_engine = {}
    for d in dbs:
        by_engine[d["engine"]] = by_engine.get(d["engine"], 0) + (d["total_size_mb"] or 0)
    return {
        "total_size_mb": round(total_mb, 2),
        "total_records": total_records,
        "by_engine_mb": {k: round(v, 2) for k, v in by_engine.items()},
        "database_count": len(dbs),
        "overlap_entity_count": len(overlap),
    }


@app.get("/api/metrics/entities")
async def metrics_entities(
    domain: str | None = None,
    db_id: str | None = None,
    sort: str = "record_count",
    order: str = "desc",
):
    return await get_entity_metrics(domain=domain, db_id=db_id, sort=sort, order=order)


@app.get("/api/metrics/overlap")
async def metrics_overlap():
    return await get_overlap()


class ExclusionRequest(BaseModel):
    entity_ids: list[str]


@app.post("/api/tools/generate-exclusions")
async def generate_exclusions(req: ExclusionRequest):
    yaml_lines = ["recorder:", "  exclude:", "    entities:"]
    for eid in req.entity_ids:
        yaml_lines.append(f"      - {eid}")
    return {"yaml": "\n".join(yaml_lines)}


PRIVILEGED_USERS = {"root", "homeassistant", "admin", "postgres", "mysql", "administrator"}


def is_privileged_user(engine: str, user: str | None) -> bool:
    """A blank user is fine for InfluxDB (no auth); otherwise flag superuser write accounts."""
    if engine == "influxdb" and not user:
        return False
    return bool(user) and user.lower() in PRIVILEGED_USERS


@app.get("/api/connections")
async def list_connections():
    out = []
    for c in load_connections():
        red = safe_config_dump(c)
        red["privileged_user"] = is_privileged_user(c.get("engine", ""), c.get("user"))
        out.append(red)
    return out


class RetentionRequest(BaseModel):
    retention_days: int
    entity_ids: list[str] | None = None
    db_id: str | None = None


@app.post("/api/tools/retention-advice")
async def retention_advice(req: RetentionRequest):
    from datetime import datetime, timedelta, timezone

    entities = await get_entity_metrics(db_id=req.db_id)
    if req.entity_ids:
        wanted = set(req.entity_ids)
        entities = [e for e in entities if e["entity_id"] in wanted]
    domains = await get_domain_metrics()
    domain_meta = {
        d["domain"]: (d["total_records"], d.get("estimated_size_mb")) for d in domains
    }
    cutoff = datetime.now(timezone.utc) - timedelta(days=req.retention_days)
    results = []
    total_freed_mb = 0.0
    for e in entities:
        domain = e["entity_id"].split(".", 1)[0]
        recs = e["record_count"]
        total, est_mb = domain_meta.get(domain, (None, None))
        per_record_mb = (est_mb / total) if (est_mb and total) else None
        start = e["start_date"]
        if start:
            try:
                dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
            except Exception:
                dt = None
        else:
            dt = None
        if dt is None or dt >= cutoff:
            retained = recs
        else:
            rate_day = (e.get("updates_per_hour") or 0) * 24
            retained = int(min(recs, rate_day * req.retention_days))
        freed_recs = max(0, recs - retained)
        freed_mb = round(per_record_mb * freed_recs, 4) if per_record_mb else None
        if freed_mb:
            total_freed_mb += freed_mb
        results.append(
            {
                "entity_id": e["entity_id"],
                "current_records": recs,
                "retained_records": retained,
                "freed_records": freed_recs,
                "freed_mb": freed_mb,
            }
        )
    return {
        "retention_days": req.retention_days,
        "total_freed_mb": round(total_freed_mb, 4),
        "entities": results,
    }


@app.get("/api/tools/read-only-guidance")
async def read_only_guidance(engine: str = "mysql"):
    snippets = {
        "mysql": (
            "CREATE USER 'ha_reader'@'%' IDENTIFIED BY 'strong_password';\n"
            "GRANT SELECT, PROCESS ON *.* TO 'ha_reader'@'%';\n"
            "FLUSH PRIVILEGES;\n"
            "-- Then point HA Database Explorer at host with user 'ha_reader'."
        ),
        "influxdb": (
            "# InfluxDB 1.8 has no per-user DB grants; isolate via a separate\n"
            "# organisation/user and keep the explorer on read-only queries.\n"
            "# For auth-enabled setups create a dedicated user with READ on the DB:\n"
            "CREATE USER 'ha_reader' WITH PASSWORD 'strong_password'\n"
            "GRANT READ ON 'homeassistant' TO 'ha_reader'"
        ),
        "sqlite": (
            "# SQLite has no user accounts. Restrict the file instead:\n"
            "chmod 644 /config/home-assistant_v2.db\n"
            "# and run the explorer as an unprivileged user that can only read it."
        ),
    }
    return {
        "engine": engine,
        "sql": snippets.get(engine, snippets["mysql"]),
        "note": "Use a dedicated read-only account instead of root/homeassistant write creds.",
    }
