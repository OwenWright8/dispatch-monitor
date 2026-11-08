"""Calls uploaded by a recorder (Rdio Scanner format) instead of pulled from OpenMHz."""

import io
import struct
import wave

import pytest
from fastapi.testclient import TestClient

from dispatch import api, config, db, ingest, pipeline, settings

client = TestClient(api.app)
TG = next(iter(config.CHANNELS))


def _wav(seconds: float) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(struct.pack("<h", 0) * int(8000 * seconds))
    return buf.getvalue()


def _post(key="k3y", tg=TG, when="1794120000", audio=None, name="call.wav"):
    files = {"audio": (name, audio if audio is not None else _wav(8), "audio/wav")}
    data = {"key": key, "talkgroup": str(tg), "dateTime": when, "audioName": name, "audioType": "audio/wav",
            "system": "1", "systemLabel": "tippco"}
    return client.post("/api/call-upload", data=data, files=files)


@pytest.fixture
def upload_key(monkeypatch):
    monkeypatch.setattr(config, "UPLOAD_API_KEY", "k3y")


def test_refused_without_configured_key():
    assert _post().status_code == 403


def test_wrong_key(upload_key):
    assert _post(key="nope").status_code == 401


def test_queued_then_read_from_disk(upload_key):
    r = _post(when="1794120001")
    assert r.status_code == 200 and "imported" in r.text
    row = db.conn().execute("SELECT * FROM transmissions WHERE id LIKE ? ORDER BY created_at DESC",
                            (f"up-{TG}-1794120001000-%",)).fetchone()
    assert row["status"] == "pending" and row["len_s"] == 8 and row["time_ms"] == 1794120001000
    assert ingest.is_local(row["url"]) and row["url"].endswith(".wav")
    assert pipeline.fetch_audio(row["url"])[:4] == b"RIFF"
    assert client.get(f"/api/audio/{row['id']}").headers["content-type"] == "audio/wav"
    assert "Call duplicate" in _post(when="1794120001").text          # resent by the recorder
    ingest.cleanup(row["url"])
    assert not ingest.local_path(row["url"]).exists()


def test_short_and_unmonitored(upload_key):
    assert "short" in _post(when="1794120002", audio=_wav(1)).text
    r = db.conn().execute("SELECT status, url FROM transmissions WHERE id LIKE ?",
                          (f"up-{TG}-1794120002000-%",)).fetchone()
    assert r["status"] == "short" and r["url"] is None
    assert "ignored" in _post(tg=999999).text


def test_time_formats():
    assert ingest.parse_time_ms("1794120000") == 1794120000000
    assert ingest.parse_time_ms("1794120000123") == 1794120000123
    assert ingest.parse_time_ms("2026-11-08T08:00:00Z") == 1794124800000


def test_openmhz_switch_in_settings():
    assert settings.BY_KEY["OPENMHZ_ENABLED"].default is True
    assert client.get("/api/status").json()["upload"]["enabled"] in (True, False)
