# HA Database Explorer

A privacy-first Home Assistant add-on that audits your database footprint across backends
— **SQLite**, **MariaDB/MySQL**, and **InfluxDB 1.8** — so you can see what is eating your
disk and which entities are being logged redundantly.

## Highlights

- **Zero configuration.** On first start it auto-discovers databases via the Docker socket,
  the HA config SQLite file, and standard hassio preset hostnames. No YAML required.
- **Executive dashboard:** total disk footprint, storage by engine, storage by HA domain.
- **Entity Bloat Explorer:** sortable/searchable table of every tracked entity with record
  counts and ingestion rates.
- **Overlap Matrix:** entities written to more than one database, with a one-click
  `recorder: exclude:` YAML generator.
- **Purge / Retention Advisor:** estimate disk savings from shortening a retention window.
- **Read-only guidance:** prompts to use dedicated read-only DB users instead of
  `root` / `homeassistant` write credentials.
- **Local-first:** no telemetry, no external calls. Credentials are encrypted at rest.

## Installation

1. Add this repo to HA → Settings → Add-ons → Add-on Store → ⋮ → Repositories:
   `https://github.com/PeteOlds/HA-DatabaseExplorer`
2. Install **HA Database Explorer** (it runs privileged to mount `/var/run/docker.sock`).
3. Start it and open the **Database Explorer** sidebar panel.

## Permissions

`protection_mode: false` is required so the add-on can mount the Docker socket for container
discovery and InfluxDB disk sizing, and read `/config` for the SQLite recorder. The add-on
only ever opens **read-only / low-priority** connections to your databases.

## Options

| Option | Default | Description |
| --- | --- | --- |
| `log_level` | `info` | Log verbosity |
| `scan_cron` | `30 3 * * *` | Deep-scan schedule (HA local time) |
| `manual_sizes` | `{}` | Manual MB sizes for DBs with no disk-size API (e.g. InfluxDB without socket access) |
