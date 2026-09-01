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
    upsert_database,
)
from .config import DEFAULT_SCAN_CRON, get_scan_cron, set_scan_cron
from .connectors import build_connector
from .crypto import safe_config_dump
from .discovery import discover_all
from .scan import JOBS, run_scan, subscribe, unsubscribe
from .store import add_connection, load_connections, remove_connection, update_connection
from .ha_config import parse_purge_keep_days, parse_purge_keep_days_from_storage
from .ha_config_write import write_purge_keep_days


# Global scheduler reference for runtime rescheduling
scheduler: AsyncIOScheduler | None = None


def _suppress_dns_noise(loop, context) -> None:
    """Swallow benign DNS-resolution noise (e.g. probing hassio preset hosts that
    are not present, or httpx/anyio DNS races) so the supervisor log stays clean.
    Any other unexpected exception still goes to the default handler."""
    import socket

    exc = context.get("exception")
    if isinstance(exc, socket.gaierror):
        return
    loop.default_exception_handler(context)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global scheduler
    asyncio.get_event_loop().set_exception_handler(_suppress_dns_noise)
    await init_cache()
    # Zero-config bootstrap: if no databases are configured yet, auto-discover and
    # persist whatever the environment exposes so the add-on works with no user input.
    # Runs as a background task because the add-on container's DNS may not be ready at
    # first boot; it retries for a few minutes instead of blocking startup.
    if not load_connections():
        asyncio.create_task(_bootstrap_discover())
    else:
        # Connections exist but the scan-results cache may be empty (e.g. after a
        # restart). Kick off one scan so the UI has something to show.
        if not await get_databases():
            _spawn_scan(uuid.uuid4().hex)
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        lambda: _spawn_scan(uuid.uuid4().hex),
        CronTrigger.from_crontab(get_scan_cron()),
        id="deep_scan",
        replace_existing=True,
    )
    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(title="HA Database Explorer", version="0.2.2", lifespan=lifespan)


@app.get("/api/health")
async def health():
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}


@app.get("/api/config/scan-cron")
async def get_scan_cron_endpoint():
    cron = get_scan_cron()
    # Calculate next run time for display
    next_run = None
    try:
        trigger = CronTrigger.from_crontab(cron)
        next_dt = trigger.get_next_fire_time(None, datetime.now(timezone.utc))
        if next_dt:
            next_run = next_dt.isoformat()
    except Exception:
        pass
    return {"cron": cron, "next_run": next_run}


class ScanCronRequest(BaseModel):
    cron: str


@app.post("/api/config/scan-cron")
async def set_scan_cron_endpoint(req: ScanCronRequest):
    global scheduler
    # Validate cron expression
    try:
        CronTrigger.from_crontab(req.cron)
    except Exception as e:
        raise HTTPException(400, f"Invalid cron expression: {e}")
    # Persist
    set_scan_cron(req.cron)
    # Reschedule job
    if scheduler:
        scheduler.reschedule_job(
            "deep_scan",
            trigger=CronTrigger.from_crontab(req.cron),
        )
    # Calculate next run
    next_run = None
    try:
        trigger = CronTrigger.from_crontab(req.cron)
        next_dt = trigger.get_next_fire_time(None, datetime.now(timezone.utc))
        if next_dt:
            next_run = next_dt.isoformat()
    except Exception:
        pass
    return {"cron": req.cron, "next_run": next_run, "saved": True}


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
            # Populate the scan-results table so the UI shows the discovered DBs.
            _spawn_scan(uuid.uuid4().hex)
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
    # Return the configured connections, merged with their latest scan status so the
    # UI shows databases immediately even before a scan has populated the cache.
    conns = load_connections()
    rows = await get_databases()
    latest: dict[str, dict] = {}
    for r in rows:
        name = r.get("connection_name")
        if name not in latest or (r.get("last_scanned") or "") >= (
            latest[name].get("last_scanned") or ""
        ):
            latest[name] = r
    out = []
    for c in conns:
        name = c.get("connection_name")
        cached = latest.get(name, {})
        out.append(
            {
                "id": cached.get("id"),
                "engine": c.get("engine"),
                "connection_name": name,
                # Expose connection fields so the Setup tab can pre-fill the edit form
                # without a second round-trip (password is intentionally omitted).
                "host": c.get("host"),
                "port": c.get("port"),
                "database": c.get("database"),
                "user": c.get("user"),
                "path": c.get("path"),
                "status": cached.get("status"),
                "total_size_mb": cached.get("total_size_mb"),
                "last_scanned": cached.get("last_scanned"),
                "scan_duration_s": cached.get("scan_duration_s"),
                "detected": c.get("detected"),
                "retention_days": cached.get("retention_days"),
                "influxdb_rp_json": cached.get("influxdb_rp_json"),
            }
        )
    return out


@app.get("/api/databases/{name}/retention")
async def get_retention(name: str):
    """Return the retention info for a specific configured database.
    
    For HA Recorder: returns purge_keep_days
    For InfluxDB: returns retention policies list
    """
    conns = load_connections()
    matching = [c for c in conns if c.get("connection_name") == name]
    if not matching:
        raise HTTPException(status_code=404, detail="database not found")
    conn = matching[0]
    engine = conn.get("engine")
    # Check cache first via get_databases
    rows = await get_databases()
    cached = next((r for r in rows if r.get("connection_name") == name), None)
    
    if engine == "influxdb":
        if cached and cached.get("influxdb_rp_json") is not None:
            import json
            return {"influxdb_rp": json.loads(cached["influxdb_rp_json"]), "source": "cache"}
        return {"influxdb_rp": [], "source": "not_set"}
    
    # HA Recorder (SQLite/MySQL/PostgreSQL)
    if cached and cached.get("retention_days") is not None:
        return {"retention_days": cached["retention_days"], "source": "cache"}
    # Fall back to reading from HA config
    days = parse_purge_keep_days_from_storage()
    if days is not None:
        return {"retention_days": days, "source": "storage"}
    days = parse_purge_keep_days()
    if days is not None:
        return {"retention_days": days, "source": "yaml"}
    return {"retention_days": None, "source": "not_set"}


@app.post("/api/databases/{name}/retention")
async def set_retention(name: str, req: dict):
    """Set retention for a specific database.
    
    HA Recorder (SQLite/MySQL/PostgreSQL):
        Body: { "retention_days": 30 }   or   { "retention_days": null } to clear
    
    InfluxDB:
        Body: { "influxdb_rp": { "action": "create|alter|delete", "name": "autogen", "duration": "30d", "make_default": true } }
    """
    # Find the connection by name
    conns = load_connections()
    matching = [c for c in conns if c.get("connection_name") == name]
    if not matching:
        raise HTTPException(status_code=404, detail="database not found")
    conn = matching[0]
    engine = conn.get("engine")
    
    if engine == "influxdb":
        rp_req = req.get("influxdb_rp")
        if not rp_req:
            raise HTTPException(status_code=400, detail="influxdb_rp required for InfluxDB")
        
        action = rp_req.get("action", "create")
        rp_name = rp_req.get("name")
        if not rp_name:
            raise HTTPException(status_code=400, detail="RP name required")
        
        connector = build_connector(engine, name, conn)
        
        if action == "delete":
            ok = await connector.delete_retention_policy(rp_name)
            if not ok:
                raise HTTPException(status_code=500, detail="failed to delete retention policy")
        else:
            duration = rp_req.get("duration")
            if not duration:
                raise HTTPException(status_code=400, detail="duration required for create/alter")
            
            # Validate duration format: INF or N[d|h|w]
            import re
            if duration != "INF" and not re.match(r'^\d+[dhw]$', duration):
                raise HTTPException(status_code=400, detail="duration must be 'INF' or format like '30d', '7d', '24h', '4w'")
            
            shard_group_duration = rp_req.get("shard_group_duration")
            replica_n = rp_req.get("replica_n")
            make_default = rp_req.get("make_default", False)
            
            ok = await connector.set_retention_policy(
                rp_name, duration, shard_group_duration, replica_n, make_default
            )
            if not ok:
                raise HTTPException(status_code=500, detail="failed to create/alter retention policy")
        
        # Refresh cache with new RP list
        try:
            rp_policies = await connector.get_retention_policies()
            import json
            await upsert_database(
                conn["engine"],
                conn["connection_name"],
                conn.get("total_size_mb"),
                "connected",
                influxdb_rp_json=json.dumps(rp_policies),
            )
        except Exception:
            pass
        
        return {"influxdb_rp": rp_policies if "rp_policies" in locals() else [], "saved": True, "action": action}
    
    # HA Recorder (SQLite/MySQL/PostgreSQL)
    days = req.get("retention_days")
    # Write back to HA config via ruamel.yaml round-trip
    ok = write_purge_keep_days(name, days)
    if not ok:
        raise HTTPException(status_code=500, detail="failed to write HA config")
    # Update cache
    await upsert_database(
        conn["engine"],
        conn["connection_name"],
        conn.get("total_size_mb"),
        "connected",
        retention_days=days,
    )
    return {"retention_days": days, "saved": True, "source": conn.get("detected")}


@app.post("/api/databases/{name}/retention/refresh")
async def refresh_retention(name: str):
    """Re-read purge_keep_days from HA config and update cache.

    Useful when the user edited configuration.yaml outside the add-on.
    """
    conns = load_connections()
    matching = [c for c in conns if c.get("connection_name") == name]
    if not matching:
        raise HTTPException(status_code=404, detail="database not found")
    conn = matching[0]
    # Re-detect from both sources
    days_yaml = parse_purge_keep_days()
    days_storage = parse_purge_keep_days_from_storage()
    # Prefer storage if available, otherwise yaml
    days = days_storage if days_storage is not None else days_yaml
    await upsert_database(
        conn["engine"],
        conn["connection_name"],
        conn.get("total_size_mb"),
        "connected",
        retention_days=days,
    )
    return {"retention_days": days, "refreshed": True, "source": conn.get("detected") or "unknown"}


@app.post("/api/databases")
async def save_database(cfg: DBConfig):
    conn = cfg.model_dump(exclude_none=True)
    add_connection(conn)
    # Test the connection and update status immediately
    test_result = None
    try:
        connector = build_connector(cfg.engine, cfg.connection_name, conn)
        test_result = await connector.test_connection()
        status = "connected" if test_result else "auth_failed"
        await upsert_database(cfg.engine, cfg.connection_name, None, status)
    except Exception:
        await upsert_database(cfg.engine, cfg.connection_name, None, "error")
    return {"saved": True, "config": safe_config_dump(conn)}


@app.delete("/api/databases/{name}")
async def delete_database(name: str):
    remove_connection(name)
    return {"deleted": name}


@app.put("/api/databases/{name}")
async def update_database(name: str, cfg: DBConfig):
    conn = cfg.model_dump(exclude_none=True)
    update_connection(name, conn)
    # Test the updated connection and update status immediately
    try:
        connector = build_connector(cfg.engine, name, conn)
        ok = await connector.test_connection()
        status = "connected" if ok else "auth_failed"
        await upsert_database(cfg.engine, name, None, status)
    except Exception:
        await upsert_database(cfg.engine, name, None, "error")
    return {"updated": True, "config": safe_config_dump(conn)}


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


@app.get("/api/scan/last")
async def scan_last():
    from .cache import get_meta

    raw = await get_meta("last_scan")
    if raw:
        try:
            return json.loads(raw)
        except Exception:
            pass
    if not JOBS:
        return None
    return max(
        JOBS.values(),
        key=lambda j: j.get("finished") or j.get("started") or "",
    )


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
    conns = load_connections()
    dbs = await get_databases()
    db_ids = {d["id"] for d in dbs}
    domains = [dm for dm in await get_domain_metrics() if dm["db_id"] in db_ids]
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
        "database_count": len(conns),
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


@app.get("/api/entities/{db_id}/{entity_id}/values")
async def get_entity_values(db_id: str, entity_id: str, limit: int = 100, offset: int = 0):
    """Get recent state values for a specific entity from a specific database."""
    # Find the connection by db_id
    rows = await get_databases()
    cached = next((r for r in rows if r.get("id") == db_id), None)
    if not cached:
        raise HTTPException(status_code=404, detail="database not found")
    
    conn_name = cached.get("connection_name")
    conns = load_connections()
    matching = [c for c in conns if c.get("connection_name") == conn_name]
    if not matching:
        raise HTTPException(status_code=404, detail="database connection not found")
    conn = matching[0]
    
    # Build connector and query
    connector = build_connector(conn["engine"], conn_name, conn)
    if not hasattr(connector, "get_entity_values"):
        raise HTTPException(status_code=400, detail=f"Entity values not supported for {conn['engine']}")
    
    try:
        values = await connector.get_entity_values(entity_id, limit=limit, offset=offset)
        return {"entity_id": entity_id, "db_id": db_id, "values": values, "limit": limit, "offset": offset}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Query failed: {e}")


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
