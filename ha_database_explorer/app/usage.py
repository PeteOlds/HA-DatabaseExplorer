"""Entity usage scanner: where is each entity referenced?

Read-only. Walks HA config surfaces under HA_CONFIG_DIR (default
``/homeassistant`` — the supervisor mount inside the add-on container) and
counts, per entity per object type, how many times it is referenced.

Surfaces: automations, scripts, scenes, Lovelace dashboards (storage JSON
+ YAML mode), templates, command_line/sql sensors, helpers (input_*),
energy config, python_scripts, consumer configs (frigate, go2rtc,
known_devices), recorder excludes, device-registry bridge, statistics_meta
(long-term statistics), InfluxDB recorded-there (from scan cache).

Verdicts are conservative: any reference — even in a disabled automation —
counts as USED. Only absolute-zero references yield "unused". Orphan state
(in DB but gone from live HA) comes from the live-states check.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .cache import canonical_entity_id

HA_CONFIG_DIR = Path(os.environ.get("HA_CONFIG_DIR", "/homeassistant"))

ENTITY_RE = re.compile(r"[a-z0-9_]+\.[a-z0-9_]+")
TEMPLATE_RES = [
    re.compile(r"""states\(\s*['"]([a-z0-9_]+\.[a-z0-9_]+)['"]"""),
    re.compile(r"""is_state(?:_attr)?\(\s*['"]([a-z0-9_]+\.[a-z0-9_]+)['"]"""),
    re.compile(r"""state_attr\(\s*['"]([a-z0-9_]+\.[a-z0-9_]+)['"]"""),
    re.compile(r"""expand\(([^)]*)\)"""),
]
DEVICE_RE = re.compile(r"\b[0-9a-f]{32}\b")


@dataclass
class Doc:
    """One searchable unit: object type, human location, searchable text."""

    object_type: str
    where: str
    text: str


@dataclass
class SurfaceStatus:
    name: str
    status: str  # ok | empty | missing | skipped | pending-access
    detail: str = ""


def _ruamel():
    try:
        from ruamel.yaml import YAML

        return YAML()
    except Exception:
        return None


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None


def _collect_yaml_objects(
    path: Path, object_type: str, name_keys: tuple[str, ...] = ("alias", "id", "name")
) -> tuple[list[Doc], str]:
    """Parse a YAML list file into per-object docs, with line numbers.

    Falls back to a single whole-file doc when structured parsing fails
    (e.g. custom ``!include``/``!secret`` tags).
    """
    text = _read(path)
    if text is None:
        return [], "missing"
    if not text.strip():
        return [], "empty"
    loader = _ruamel()
    if loader is None:
        return [Doc(object_type, path.name, text)], "ok-text-fallback"
    try:
        data = loader.load(text)
    except Exception:
        return [Doc(object_type, path.name, text)], "ok-text-fallback"
    docs: list[Doc] = []
    if isinstance(data, dict):
        # e.g. scripts.yaml keyed by script id
        items = list(data.items())
        for key, val in items:
            line = _lc_line(data, key)
            docs.append(Doc(object_type, _where(path.name, str(key), line), _dump(val)))
    elif isinstance(data, list):
        for i, item in enumerate(data):
            name = None
            if isinstance(item, dict):
                for k in name_keys:
                    if item.get(k):
                        name = str(item.get(k))
                        break
            line = _lc_line(data, i)
            docs.append(Doc(object_type, _where(path.name, name or f"#{i + 1}", line), _dump(item)))
    else:
        docs.append(Doc(object_type, path.name, text))
    return docs, "ok"


def _lc_line(node, key) -> int | None:
    try:
        return node.lc.line(key) + 1
    except Exception:
        return None


def _where(*parts: str) -> str:
    base = " › ".join(p for p in parts[:2] if p)
    line = parts[2] if len(parts) > 2 and parts[2] else None
    return f"{base}:L{line}" if line else base


def _dump(node) -> str:
    try:
        import json as _json

        return _json.dumps(node, default=str)
    except Exception:
        return str(node)


def _walk_json(node, path: str, out: list[tuple[str, str]]) -> None:
    """Collect (path, text) leaf docs from dashboard JSON card trees."""
    if isinstance(node, dict):
        # A Lovelace card: record the whole card as one doc for precise drill-down.
        if "type" in node and ("cards" in node or "entities" in node or "entity" in node):
            out.append((path, _dump(node)))
        for k, v in node.items():
            _walk_json(v, f"{path} › {k}", out)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _walk_json(v, f"{path} › #{i + 1}" if "card" in path.lower() else path, out)


def _collect_dashboards(root: Path) -> tuple[list[Doc], list[SurfaceStatus]]:
    docs: list[Doc] = []
    statuses: list[SurfaceStatus] = []
    storage = root / ".storage"
    if not storage.is_dir():
        return docs, [SurfaceStatus("dashboards", "missing", "no .storage dir")]
    files = sorted(
        p
        for p in storage.glob("lovelace*")
        if p.is_file() and ".bak" not in p.name and ".log" not in p.name
    )
    # storage lovelace files have no extension; JSON parsing decides admissibility
    for path in files:
        text = _read(path)
        if text is None:
            continue
        try:
            data = json.loads(text)
        except Exception:
            continue
        title = str(data.get("title", path.name))
        leaves: list[tuple[str, str]] = []
        _walk_json(data.get("views", data), title, leaves)
        if not leaves:
            leaves.append((title, text))
        for where, blob in leaves:
            docs.append(Doc("dashboard", where, blob))
        statuses.append(SurfaceStatus("dashboards", "ok", f"{path.name}: {len(leaves)} cards"))
    # YAML-mode dashboards
    for path in [root / "ollama_lovelace.yaml", *sorted(root.glob("dwains-dashboard/**/*.yaml"))]:
        if path.is_file():
            text = _read(path)
            if text:
                docs.append(Doc("dashboard", path.name, text))
                statuses.append(SurfaceStatus("dashboards", "ok", path.name))
    if not statuses:
        statuses.append(SurfaceStatus("dashboards", "empty", "no dashboards found"))
    return docs, statuses


def _collect_config_sections(root: Path) -> tuple[list[Doc], set[str], set[str]]:
    """Template/command_line/sql/helper docs + recorder exclude sets."""
    docs: list[Doc] = []
    excluded_ids: set[str] = set()
    excluded_globs: set[str] = set()
    cfg = root / "configuration.yaml"
    text = _read(cfg)
    if text is None:
        return docs, excluded_ids, excluded_globs
    loader = _ruamel()
    data = None
    if loader is not None:
        try:
            data = loader.load(text)
        except Exception:
            data = None
    if isinstance(data, dict):
        # template sensors
        tpl = data.get("template", [])
        if isinstance(tpl, dict):
            tpl = [tpl]
        for group in tpl or []:
            if not isinstance(group, dict):
                continue
            for sensor in group.get("sensor", []) or []:
                if isinstance(sensor, dict) and sensor.get("name"):
                    docs.append(Doc("template", f"template › {sensor.get('name')}", _dump(sensor)))
        # command_line + sql sensors
        for section in ("command_line", "sql"):
            items = data.get(section, [])
            if isinstance(items, dict):
                items = [items]
            for item in items or []:
                if isinstance(item, dict):
                    docs.append(
                        Doc(
                            "sensor_config",
                            f"{section} › {item.get('name', '?')}",
                            _dump(item),
                        )
                    )
        # input_* helpers are entities; their definitions rarely reference others,
        # but scan them anyway for cross-references (e.g. initial templates)
        for section in ("input_text", "input_button", "input_select", "input_boolean", "input_number", "input_datetime"):
            items = data.get(section, {})
            if isinstance(items, dict):
                for key, val in items.items():
                    docs.append(Doc("helper", f"{section} › {key}", _dump(val)))
        # recorder excludes
        rec = data.get("recorder", {}) or {}
        exc = rec.get("exclude", {}) or {}
        for eid in exc.get("entity_ids", []) or []:
            excluded_ids.add(str(eid))
        for g in exc.get("entity_globs", []) or []:
            excluded_globs.add(str(g))
    else:
        docs.append(Doc("config", "configuration.yaml", text))
    # TemplateSensors.yaml include
    tpl_path = root / "TemplateSensors.yaml"
    tpl_text = _read(tpl_path)
    if tpl_text:
        docs.append(Doc("template", "TemplateSensors.yaml", tpl_text))
    return docs, excluded_ids, excluded_globs


def _collect_misc(root: Path) -> list[Doc]:
    docs: list[Doc] = []
    # python_scripts (absent dir is fine — consumer of entity_ids when present)
    py_dir = root / "python_scripts"
    if py_dir.is_dir():
        for path in sorted(py_dir.glob("*.py")):
            text = _read(path)
            if text:
                docs.append(Doc("python_script", f"python_scripts/{path.name}", text))
    # energy config
    energy = root / ".storage" / "energy"
    if energy.is_file():
        text = _read(energy)
        if text:
            docs.append(Doc("energy", "energy config", text))
    # consumer configs: entity-ish references (cameras, devices)
    for name in ("frigate.yaml", "known_devices.yaml"):
        path = root / name
        text = _read(path) if path.is_file() else None
        if text:
            docs.append(Doc("consumer", name, text))
    go2rtc = sorted((root / "go2rtc-1.9.9").glob("*.yaml")) if (root / "go2rtc-1.9.9").is_dir() else []
    for path in go2rtc:
        text = _read(path)
        if text:
            docs.append(Doc("consumer", f"go2rtc/{path.name}", text))
    return docs


def _device_map(root: Path) -> dict[str, tuple[str, list[str]]]:
    """device_id -> (device name, member entity_ids) from registries."""
    out: dict[str, tuple[str, list[str]]] = {}
    try:
        ent_reg = json.loads((root / ".storage" / "core.entity_registry").read_text())
        dev_reg = json.loads((root / ".storage" / "core.device_registry").read_text())
    except Exception:
        return out
    dev_names = {d.get("id"): (d.get("name_by_user") or d.get("name") or d.get("id")) for d in dev_reg.get("data", {}).get("devices", []) if isinstance(d, dict)}
    members: dict[str, list[str]] = {}
    for e in ent_reg.get("data", {}).get("entities", []) or []:
        if not isinstance(e, dict):
            continue
        did = e.get("device_id")
        eid = e.get("entity_id")
        if did and eid:
            members.setdefault(did, []).append(eid)
    for did, eids in members.items():
        out[did] = (dev_names.get(did, did), eids)
    return out


def _build_matcher(entity_ids: list[str]) -> re.Pattern:
    alts = sorted({re.escape(e) for e in entity_ids if "." in e}, key=len, reverse=True)
    if not alts:
        return re.compile(r"(?!)")
    return re.compile(r"(?<![A-Za-z0-9_.])(?:" + "|".join(alts) + r")(?![A-Za-z0-9_])")


def run_usage_scan(
    known_entity_ids: list[str],
    live_entity_ids: set[str] | None = None,
    recorded_by_entity: dict[str, list[str]] | None = None,
    has_lts: set[str] | None = None,
    root: Path | None = None,
) -> dict:
    """Scan config surfaces; return per-entity refs + verdicts + surface statuses."""
    root = root or HA_CONFIG_DIR
    recorded_by_entity = recorded_by_entity or {}
    has_lts = has_lts or set()
    docs: list[Doc] = []
    statuses: list[SurfaceStatus] = []
    excluded_ids: set[str] = set()
    excluded_globs: set[str] = set()
    dev_map: dict[str, tuple[str, list[str]]] = {}

    if not root.is_dir():
        statuses.append(SurfaceStatus("config", "missing", f"{root} not mounted"))
        # still produce verdicts from DB-backed signals below
    else:
        for fname, otype in (("automations.yaml", "automation"), ("scripts.yaml", "script"), ("scenes.yaml", "scene")):
            d, st = _collect_yaml_objects(root / fname, otype)
            docs.extend(d)
            statuses.append(SurfaceStatus(fname[:-5] if fname != "scenes.yaml" else "scenes", st, f"{len(d)} objects"))
        dash_docs, dash_status = _collect_dashboards(root)
        docs.extend(dash_docs)
        statuses.extend(dash_status)
        cfg_docs, excluded_ids, excluded_globs = _collect_config_sections(root)
        docs.extend(cfg_docs)
        statuses.append(SurfaceStatus("templates+config", "ok", f"{len(cfg_docs)} objects"))
        misc_docs = _collect_misc(root)
        docs.extend(misc_docs)
        statuses.append(SurfaceStatus("misc", "ok", f"{len(misc_docs)} files"))
        dev_map = _device_map(root)
        statuses.append(SurfaceStatus("device-registry", "ok" if dev_map else "empty", f"{len(dev_map)} devices"))

    # universe: known ids + anything live
    universe = set(known_entity_ids) | (live_entity_ids or set())
    # expand recorder globs against universe
    for g in excluded_globs:
        for eid in universe:
            if fnmatch.fnmatch(eid, g):
                excluded_ids.add(eid)

    pattern = _build_matcher(sorted(universe))
    refs: dict[str, dict[str, list[dict]]] = {}

    for doc in docs:
        # Template-call matches first; direct matches inside those spans are
        # the same occurrence, so exclude them to avoid double counting.
        tpl_spans: list[tuple[int, int]] = []
        tpl_hits: list[tuple[str, str]] = []
        for rx in TEMPLATE_RES:
            for tm in rx.finditer(doc.text):
                tpl_spans.append((tm.start(), tm.end()))
                groups = [g for g in tm.groups() if g]
                for g in groups:
                    # expand() may hold comma-separated lists
                    for cand in re.split(r"[,'\"]+", g):
                        cand = cand.strip().strip("'\"")
                        if ENTITY_RE.fullmatch(cand or ""):
                            tpl_hits.append((cand, doc.object_type))
        for m in pattern.finditer(doc.text):
            s, e = m.start(), m.end()
            if any(a < e and s < b for a, b in tpl_spans):
                continue
            _hit(refs, m.group(), doc.object_type, doc.where)
        for cand, otype in tpl_hits:
            _hit(refs, cand, otype, doc.where)
        for dm in DEVICE_RE.findall(doc.text):
            if dm in dev_map:
                dname, members = dev_map[dm]
                for eid in members:
                    _hit(refs, eid, doc.object_type, f"{doc.where} via device {dname}")

    # verdicts
    live_set = live_entity_ids  # None => unknown, never orphan
    live_canon = {canonical_entity_id(e) for e in live_set} if live_set is not None else None
    rows: list[dict] = []
    for eid in sorted(universe):
        by_type = refs.get(eid, {})
        total = sum(e["count"] for entries in by_type.values() for e in entries)
        excluded = eid in excluded_ids
        orphan = (
            eid not in live_set and canonical_entity_id(eid) not in live_canon
            if live_set is not None
            else None
        )
        if orphan:
            verdict = "orphan"
        elif total == 0:
            verdict = "unused"
        else:
            verdict = "used"
        rows.append(
            {
                "entity_id": eid,
                "refs": {t: entries for t, entries in by_type.items()},
                "total_refs": total,
                "verdict": verdict,
                "excluded": excluded,
                "recorded_in": recorded_by_entity.get(eid, []),
                "has_lts": eid in has_lts,
                "orphan": orphan,
            }
        )
    dangling_rows = [
        {"entity_id": eid, "refs": by_type, "total_refs": sum(e["count"] for entries in by_type.values() for e in entries), "verdict": "dangling", "excluded": False, "recorded_in": [], "has_lts": False, "orphan": None}
        for eid, by_type in sorted(refs.items())
        if eid not in universe
    ]
    return {
        "rows": rows,
        "dangling": dangling_rows,
        "surfaces": [{"name": s.name, "status": s.status, "detail": s.detail} for s in statuses],
        "counts": {
            "entities": len(rows),
            "used": sum(1 for r in rows if r["verdict"] == "used"),
            "unused": sum(1 for r in rows if r["verdict"] == "unused"),
            "orphan": sum(1 for r in rows if r["verdict"] == "orphan"),
            "dangling": len(dangling_rows),
        },
    }


def _hit(store: dict, eid: str, object_type: str, where: str) -> None:
    bucket = store.setdefault(eid, {})
    entries = bucket.setdefault(object_type, [])
    for en in entries:
        if en["where"] == where:
            en["count"] += 1
            return
    entries.append({"where": where, "count": 1})


async def run_full_usage_scan() -> dict:
    """Orchestrate a full usage scan: cache inputs + file scan + persist.

    Imports here (not top-level) to avoid import cycles: usage is imported
    by api.py and scan.py, neither of which it may import back.
    """
    from .cache import get_entity_metrics, replace_usage
    from .connectors import build_connector
    from .ha_api import get_live_entity_ids
    from .store import load_connections

    metrics = await get_entity_metrics()
    known = sorted({m["entity_id"] for m in metrics})
    recorded: dict[str, list[str]] = {}
    last_seen: dict[str, str] = {}
    for m in metrics:
        name = m.get("connection_name") or m.get("db_id")
        if name and name not in recorded.setdefault(m["entity_id"], []):
            recorded[m["entity_id"]].append(name)
        end = m.get("end_date")
        if end and (m["entity_id"] not in last_seen or end > last_seen[m["entity_id"]]):
            last_seen[m["entity_id"]] = end
    live = await get_live_entity_ids()
    has_lts: set[str] = set()
    for c in load_connections():
        if c.get("engine") in ("mysql", "sqlite"):
            try:
                connector = build_connector(c["engine"], c["connection_name"], c)
                has_lts.update(await connector.statistic_ids())
            except Exception:
                continue
    result = run_usage_scan(
        known, live_entity_ids=live, recorded_by_entity=recorded, has_lts=has_lts
    )
    for r in result["rows"]:
        r["last_seen"] = last_seen.get(r["entity_id"])
    await replace_usage(result["rows"], result["dangling"])
    return result
