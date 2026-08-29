# HA Database Explorer

Audit storage, record retention, and cross-database entity overlap across your Home
Assistant databases — **SQLite**, **MariaDB/MySQL**, and **InfluxDB 1.8**. Privacy-first,
local-only, zero telemetry.

> Spec source of truth: `HA_Database_Explorer.md` in the `HomeAssistant` vault. This repo is
> the implementation; keep the two in sync when either changes.

## Features

- Auto-detect databases via the Docker socket, filesystem SQLite, and hassio preset hosts
  (`core-mariadb`, `local-mariadb`, `a0d7b954-influxdb` on `172.30.33.0/24`).
- Async deep-scan cached in a local SQLite store for instant dashboard rendering.
- Executive dashboard: total footprint, storage by engine, storage by HA domain.
- Entity Bloat Explorer: sortable/searchable/filterable table of every tracked entity.
- Overlap Matrix: entities logged to multiple databases, with a one-click
  `recorder: exclude:` YAML generator.
- Scheduled deep scan (default `30 3 * * *`) + manual "Trigger Deep Scan".
- Credentials encrypted at rest (Fernet); secrets scrubbed from all API responses.

## Install (Home Assistant Add-on)

1. Add the repo to HA → Settings → Add-ons → Add-on Store → ⋮ → Repositories:
   `https://github.com/PeteOlds/HA-DatabaseExplorer`.
2. Install **HA Database Explorer**. The add-on sets `protection_mode: false` (privileged)
   so it can mount `/var/run/docker.sock` for container discovery and InfluxDB disk sizing.
3. Start the add-on and open it via the **Database Explorer** sidebar panel (Ingress).

### Standalone Docker

```bash
docker build -t ha-db-explorer .
docker run -d --name ha-db-explorer \
  -p 8099:8099 \
  -v ha_db_explorer_data:/data \
  -v /var/run/docker.sock:/var/run/docker.sock \
  ha-db-explorer
```

## Adding databases

The Setup view auto-detects on first run. For off-host or external databases, use the manual
form (Host / Port / User / Password). The InfluxDB connector supports **blank auth** for
legacy `influxdb:1.8` containers. When a database exposes no disk-size API (InfluxDB without
socket access), set a manual size in the add-on options (`manual_sizes`) or leave it as N/A.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/health` | Health check |
| GET | `/api/databases` | Configured databases + last scan status |
| POST | `/api/databases` | Save a connection (encrypted) |
| DELETE | `/api/databases/{name}` | Remove a connection |
| POST | `/api/databases/discover` | Auto-detect databases |
| POST | `/api/databases/test-connection` | Validate credentials |
| POST | `/api/scan/trigger` | Start deep scan → `job_id` |
| GET | `/api/scan/status/{job_id}` | Scan progress |
| WS | `/api/scan/ws` | Live scan progress |
| GET | `/api/metrics/global` | Aggregated footprint |
| GET | `/api/metrics/entities` | Entity table (`?domain&?db_id&?sort&?order`) |
| GET | `/api/metrics/overlap` | Overlap matrix |
| POST | `/api/tools/generate-exclusions` | `recorder: exclude:` YAML |

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
ruff check app tests
pytest
uvicorn app.main:app --port 8099
```

## Notes / limitations

- `estimated_size_mb` is an **approximation** (no per-row byte accounting in InnoDB/SQLite).
- InfluxDB 1.8 exposes no disk-size API; size comes from the Docker socket (`du` on the
  mounted volume) or a manual entry.
- PostgreSQL/TimescaleDB support is deferred (interface raises `NotImplementedError`).
