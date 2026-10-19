"""API, settings and corrections against a throwaway database."""

import json
import time

import pytest
from fastapi.testclient import TestClient

from dispatch import api, config, corrections, db, departments, settings, units
from dispatch.taxonomy import category_for

client = TestClient(api.app)


def _incident(**kw) -> int:
    now = int(time.time() * 1000)
    row = dict(first_ms=now, last_ms=now, local_date="2026-01-01", local_hour=12, local_dow=0, address="1 Main St",
               place=None, category="Medical", nature="Other Medical", agencies=json.dumps([config.PROFILE.departments[0].name]),
               units="[]", n_tx=1, lat=None, lon=None)
    row.update(kw)
    cur = db.conn().execute(f"INSERT INTO incidents({','.join(row)}) VALUES ({','.join('?' * len(row))})",
                            tuple(row.values()))
    inc = cur.lastrowid
    db.conn().execute("INSERT INTO transmissions(id, talkgroup, agency, time_ms, status, transcript, extracted, "
                      "incident_id, is_dispatch) VALUES (?,?,?,?,?,?,?,?,1)",
                      (f"tx{inc}", next(iter(config.CHANNELS)), "test", now, "done", "medical run 1 Main Street",
                       json.dumps({"is_dispatch": True, "address": "1 Main Street", "nature": "Other Medical"}), inc))
    return inc


def test_status_and_meta():
    assert client.get("/api/status").status_code == 200
    m = client.get("/api/meta").json()
    assert m["county"]["key"] == config.PROFILE.key
    assert len(m["departments"]) >= len(config.PROFILE.departments)


def test_settings_validate_and_reset():
    r = client.put("/api/settings", json={"WHISPER_BEAM": 99})
    assert r.status_code == 400
    assert client.put("/api/settings", json={"WHISPER_BEAM": 3}).status_code == 200
    settings._cache_at = 0
    assert settings.get("WHISPER_BEAM") == 3
    client.put("/api/settings", json={"WHISPER_BEAM": None})
    settings._cache_at = 0
    assert settings.get("WHISPER_BEAM") == config.WHISPER_BEAM


def test_admin_password_required(monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_PASSWORD", "secret")
    assert client.put("/api/settings", json={"WHISPER_BEAM": 4}).status_code == 401
    assert client.put("/api/settings", json={"WHISPER_BEAM": 4},
                      headers={"X-Admin-Password": "wrong"}).status_code == 401
    assert client.put("/api/settings", json={"WHISPER_BEAM": 4},
                      headers={"X-Admin-Password": "secret"}).status_code == 200
    client.put("/api/settings", json={"WHISPER_BEAM": None}, headers={"X-Admin-Password": "secret"})


def test_correction_locks_and_teaches():
    inc = _incident()
    lat, lon = [(a + b) / 2 for a, b in zip(config.COUNTY_BBOX[:2], config.COUNTY_BBOX[2:])]
    r = client.put(f"/api/incidents/{inc}", json={
        "nature": "Lift Assist", "lat": lat, "lon": lon, "address": "1 Main St",
        "word_fixes": [{"heard": "Zorblat", "actual": "Main"}]})
    assert r.status_code == 200, r.text
    row = db.conn().execute("SELECT * FROM incidents WHERE id=?", (inc,)).fetchone()
    assert row["nature"] == "Lift Assist" and row["category"] == category_for("Lift Assist")
    assert row["geo_source"] == "manual" and set(row["corrected"].split(",")) >= {"location", "nature"}
    corrections._cache["at"] = 0
    assert "Main" in departments.normalize("medical run 1 Zorblat Street")
    learned = client.get("/api/learned").json()
    assert any(w["heard"] == "Zorblat" for w in learned["word_fixes"])
    assert learned["call_types"] >= 1


def test_correction_rejects_points_outside_the_county():
    inc = _incident()
    r = client.put(f"/api/incidents/{inc}", json={"lat": 0.0, "lon": 0.0})
    assert r.status_code == 400


def test_unknown_call_type_rejected():
    inc = _incident()
    assert client.put(f"/api/incidents/{inc}", json={"nature": "Alien Landing"}).status_code == 400


@pytest.mark.parametrize("path", ["/", "/api/incidents?days=0", "/api/stats?days=0", "/api/learned"])
def test_pages_load(path):
    assert client.get(path).status_code == 200


def test_stations_endpoint_lists_profile_stations():
    got = client.get("/api/stations").json()
    listed = {(s["lat"], s["lon"]) for s in got}
    assert {(s.lat, s.lon) for s in config.PROFILE.stations.values()} <= listed
    for s in got:
        assert config.COUNTY_BBOX[0] <= s["lat"] <= config.COUNTY_BBOX[2]


def test_active_calls_end_without_a_radio_clear():
    now, m = int(time.time() * 1000), 60_000
    # Units sent to a newer call have left the older one (they cleared on the CAD, not the radio)
    old = _incident(first_ms=now - 20 * m, last_ms=now - 20 * m, units=json.dumps(["Engine 901"]))
    new = _incident(first_ms=now - 5 * m, last_ms=now - 5 * m, units=json.dumps(["Engine 901", "Station Junk Fire"]))
    # A medical call nobody has mentioned for an hour is over; after half an hour it's "quiet"
    stale = _incident(first_ms=now - 60 * m, last_ms=now - 60 * m, units=json.dumps(["Medic 902"]))
    quiet = _incident(first_ms=now - 30 * m, last_ms=now - 30 * m, units=json.dumps(["Medic 903"]))
    got = {a["id"]: a for a in units.active(now)}
    assert old not in got and stale not in got
    assert [u["unit"] for u in got[new]["units"]] == ["Engine 901"]        # department names aren't units
    assert got[quiet]["quiet"] and not got[new]["quiet"]


def test_meta_lists_priority_types():
    m = client.get("/api/meta").json()
    assert "Cardiac Arrest" in m["priority"] and "unit_prefix" in m
    assert all("key" in s for s in client.get("/api/stations").json())
