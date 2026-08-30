"""Scan orchestrator: runs deep scans, caches results, tracks job progress."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from .cache import (
    all_entity_index,
    get_entity_metrics,
    replace_domain_metrics,
    replace_entity_metrics,
    replace_overlap,
    upsert_database,
)
from .config import MANUAL_SIZES
from .connectors import build_connector
from .store import load_connections

JOBS: dict[str, dict] = {}
_SUBSCRIBERS: set = set()


def subscribe(ws) -> None:
    _SUBSCRIBERS.add(ws)


def unsubscribe(ws) -> None:
    _SUBSCRIBERS.discard(ws)


async def _broadcast(job_id: str) -> None:
    msg = json.dumps({"job_id": job_id, **JOBS[job_id]})
    for ws in list(_SUBSCRIBERS):
        try:
            await ws.send_text(msg)
        except Exception:
            _SUBSCRIBERS.discard(ws)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def run_scan(job_id: str) -> None:
    start_ts = time.monotonic()
    JOBS[job_id] = {"status": "running", "percent": 0, "message": "starting", "started": _now()}
    await _broadcast(job_id)
    connections = load_connections()
    total = max(len(connections), 1)
    try:
        for i, conn in enumerate(connections, start=1):
            name = conn.get("connection_name", conn.get("engine"))
            JOBS[job_id]["message"] = f"scanning {name}"
            await _broadcast(job_id)
            try:
                connector = build_connector(conn["engine"], name, conn)
                ok = await connector.test_connection()
                if not ok:
                    await upsert_database(conn["engine"], name, None, "auth_failed")
                    continue
                size = conn.get("manual_size_mb")
                if size is None:
                    size = await connector.total_size_mb()
                if size is None:
                    size = MANUAL_SIZES.get(name)
                entities = await connector.entity_metrics()
                domains = await connector.domain_metrics(entities)
                db_id = await upsert_database(conn["engine"], name, size, "connected")
                await replace_entity_metrics(db_id, [e.__dict__ for e in entities])
                await replace_domain_metrics(
                    db_id, [d.__dict__ for d in domains]
                )
            except Exception as exc:  # one bad DB must not abort the whole scan
                await upsert_database(conn.get("engine", "unknown"), name, None, "error")
                JOBS[job_id]["message"] = f"{name}: {exc}"
            JOBS[job_id]["percent"] = int(100 * i / total)
            await _broadcast(job_id)

        await _build_overlap()
        JOBS[job_id].update(
            status="complete",
            percent=100,
            message="done",
            finished=_now(),
            duration_s=round(time.monotonic() - start_ts, 1),
        )
    except Exception as exc:
        JOBS[job_id].update(
            status="failed",
            message=str(exc),
            finished=_now(),
            duration_s=round(time.monotonic() - start_ts, 1),
        )
    finally:
        await _broadcast(job_id)


async def _build_overlap() -> None:
    index = await all_entity_index()
    rows = []
    for entity_id, db_ids in index.items():
        if len(db_ids) < 2:
            continue
        present = sorted(db_ids)
        # Sum record counts across every db except the one holding the fewest (the "primary").
        counts = {
            d: (await _entity_count(entity_id, d)) for d in present
        }
        primary = min(present, key=lambda d: counts.get(d, 0))
        redundant = sum(counts[d] for d in present if d != primary)
        rows.append(
            {
                "entity_id": entity_id,
                "present_in": present,
                "total_redundant_records": redundant,
            }
        )
    await replace_overlap(rows)


async def _entity_count(entity_id: str, db_id: str) -> int:
    rows = await get_entity_metrics(db_id=db_id)
    for r in rows:
        if r["entity_id"] == entity_id:
            return r["record_count"]
    return 0
