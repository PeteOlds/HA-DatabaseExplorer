from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.cache import (
    get_overlap,
    init_cache,
    replace_domain_metrics,
    replace_entity_metrics,
    upsert_database,
)
from app.connectors import build_connector
from app.crypto import decrypt_secret, encrypt_secret
from app.scan import _build_overlap


def test_crypto_roundtrip():
    tok = encrypt_secret("supersecret")
    assert tok != "supersecret"
    assert decrypt_secret(tok) == "supersecret"
    assert encrypt_secret("") == ""
    assert decrypt_secret("") == ""


def test_build_connector_unsupported():
    with pytest.raises(ValueError):
        build_connector("oracle", "x", {})


@pytest.mark.asyncio
async def test_cache_and_overlap():
    await init_cache()
    db1 = await upsert_database("mysql", "rec1", 100.0, "connected")
    db2 = await upsert_database("influxdb", "rec2", None, "connected")
    a_row = {
        "entity_id": "sensor.a",
        "record_count": 100,
        "start_date": None,
        "updates_per_hour": 1.0,
    }
    b_row = {
        "entity_id": "sensor.b",
        "record_count": 50,
        "start_date": None,
        "updates_per_hour": 0.5,
    }
    await replace_entity_metrics(db1, [a_row, b_row])
    await replace_entity_metrics(db2, [a_row])
    await _build_overlap()
    overlap = await get_overlap()
    by_id = {r["entity_id"]: r for r in overlap}
    assert "sensor.a" in by_id
    assert set(by_id["sensor.a"]["present_in"]) == {db1, db2}
    # redundant = secondary db count only (primary is the smaller? both equal -> one side)
    assert by_id["sensor.a"]["total_redundant_records"] == 100
    assert "sensor.b" not in by_id  # only in one db


def test_exclusions_endpoint():
    from app.main import app

    with TestClient(app) as client:
        r = client.get("/api/health")
        assert r.status_code == 200
        r = client.post(
            "/api/tools/generate-exclusions",
            json={"entity_ids": ["sensor.a", "sensor.b"]},
        )
        assert r.status_code == 200
        yaml = r.json()["yaml"]
        assert "recorder:" in yaml
        assert "- sensor.a" in yaml
        assert "- sensor.b" in yaml


@pytest.mark.asyncio
async def test_retention_advice():
    await init_cache()
    db = await upsert_database("mysql", "rec", 100.0, "connected")
    await replace_entity_metrics(
        db,
        [
            {
                "entity_id": "sensor.a",
                "record_count": 100000,
                "start_date": "2020-01-01T00:00:00+00:00",
                "updates_per_hour": 10.0,
            }
        ],
    )
    await replace_domain_metrics(
        db, [{"domain": "sensor", "total_records": 100000, "estimated_size_mb": 10.0}]
    )
    from app.main import app

    with TestClient(app) as client:
        r = client.post(
            "/api/tools/retention-advice",
            json={"retention_days": 30, "entity_ids": ["sensor.a"]},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["total_freed_mb"] > 0
        assert body["entities"][0]["freed_records"] > 0


def test_read_only_guidance():
    from app.main import app

    with TestClient(app) as client:
        r = client.get("/api/tools/read-only-guidance?engine=mysql")
        assert r.status_code == 200
        assert "CREATE USER" in r.json()["sql"]


def test_privileged_user_detection():
    from app.api import is_privileged_user

    assert is_privileged_user("mysql", "homeassistant") is True
    assert is_privileged_user("influxdb", "") is False
    assert is_privileged_user("mysql", "ha_reader") is False
