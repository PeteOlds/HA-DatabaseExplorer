"""Read-only Home Assistant API client (via Supervisor).

Requires ``homeassistant_api: true`` in the add-on config, which makes the
Supervisor inject SUPERVISOR_TOKEN and proxy http://supervisor/core/api.

Every function here fails open: on a missing token or any request error it
returns None (never an empty set), so callers can distinguish "HA
unreachable" from "HA reports zero entities" and must never treat the
former as evidence of anything (e.g. never flag orphans off a failed fetch).
"""

from __future__ import annotations

import os

import httpx

SUPERVISOR_API = "http://supervisor/core/api"


def _token() -> str | None:
    return os.environ.get("SUPERVISOR_TOKEN") or None


async def get_live_entity_ids(timeout: float = 15.0) -> set[str] | None:
    """Return live HA entity ids, or None when unreachable (fail open)."""
    token = _token()
    if not token:
        return None
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.get(
                f"{SUPERVISOR_API}/states",
                headers={"Authorization": f"Bearer {token}"},
            )
            r.raise_for_status()
            data = r.json()
        if not isinstance(data, list):
            return None
        return {
            s.get("entity_id")
            for s in data
            if isinstance(s, dict) and s.get("entity_id")
        }
    except Exception:
        return None
