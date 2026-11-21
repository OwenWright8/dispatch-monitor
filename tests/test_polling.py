"""The poller makes fewer requests: it stretches the interval when the feed is
quiet, sends every talkgroup in one request, and walks a gap back a few pages
per poll instead of in one burst."""

from datetime import datetime, timezone

import pytest

from dispatch import config, db, openmhz, pipeline, settings

TG = next(iter(settings.enabled_channels()))


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def _call(ms: int) -> dict:
    return {"_id": f"t-{ms}", "talkgroupNum": TG, "time": _iso(ms), "len": 10, "url": "u"}


@pytest.fixture(autouse=True)
def _clean():
    db.conn().execute("DELETE FROM transmissions WHERE id LIKE 't-%'")
    db.conn().commit()
    pipeline.status["catchup"] = None
    pipeline.status["poll_failures"] = 0
    yield
    db.conn().execute("DELETE FROM transmissions WHERE id LIKE 't-%'")
    db.conn().commit()
    pipeline.status["catchup"] = None


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(config, "PAGE_DELAY_S", 0.0)


def _seed(ms: int):
    db.conn().execute("INSERT OR IGNORE INTO transmissions(id, talkgroup, agency, time_ms, status) "
                      "VALUES (?,?,?,?,?)", (f"t-{ms}", TG, "test", ms, "done"))
    db.conn().commit()


def _fake_api(times: list[int], seen: dict):
    """times newest-first; latest() is the first 50, older(t) the next 50 below t."""
    def latest():
        seen["latest"] = seen.get("latest", 0) + 1
        return [_call(t) for t in times[:50]]

    def older(t):
        seen["older"] = seen.get("older", 0) + 1
        seen.setdefault("cursors", []).append(t)
        return [_call(x) for x in [x for x in times if x < t][:50]]

    return latest, older


def test_poll_delay_stretches_when_quiet_and_backs_off_on_failure():
    assert pipeline._poll_delay(30, 300, 0, 0, 0) == 30       # active feed: normal interval
    assert pipeline._poll_delay(30, 300, 1, 0, 0) == pytest.approx(45)
    assert pipeline._poll_delay(30, 300, 20, 0, 0) == 300     # quiet: capped at idle max
    assert pipeline._poll_delay(30, 300, 0, 1, 0) == 60       # failure: doubles
    assert pipeline._poll_delay(30, 300, 0, 10, 0) == 300     # failure cap
    assert pipeline._poll_delay(30, 300, 0, 0, 120) == 121    # Cloudflare cool-down wins
    assert pipeline._poll_delay(60, 30, 5, 0, 0) == 60        # idle max below base is ignored


def test_quiet_poll_is_one_request_and_reports_nothing_new(monkeypatch):
    base = 4_102_444_800_000                                   # year 2100, above anything else in the db
    _seed(base)
    seen = {"latest": 0}

    def latest():
        seen["latest"] += 1
        return [_call(base)]                                   # the only call we already have

    monkeypatch.setattr(openmhz, "latest", latest)
    monkeypatch.setattr(openmhz, "older", _fake_api([base], seen)[1])

    assert pipeline.poll_once() == 0
    assert seen["latest"] == 1
    assert "older" not in seen                                 # no gap, so no history requests
    assert pipeline.status["catchup"] is None


def test_gap_is_walked_back_a_few_pages_per_poll(monkeypatch):
    base = 4_102_444_800_000
    _seed(base - 1)                                            # oldest call we have
    times = [base + i * 1000 for i in range(400, 0, -1)]       # 400 calls, 8 pages
    seen = {}
    monkeypatch.setattr(openmhz, "latest", _fake_api(times, seen)[0])
    monkeypatch.setattr(openmhz, "older", _fake_api(times, seen)[1])
    monkeypatch.setattr(config, "POLL_CATCHUP_PAGES", 5)

    added = pipeline.poll_once()
    assert added == 300                                        # newest page + 5 pages, not all 8
    assert seen["older"] == 5
    assert pipeline.status["catchup"] == {"target": base - 1, "cursor": base + 101_000}

    added = pipeline.poll_once()                               # resumes where it stopped
    assert seen["older"] == 8                                  # 3 more pages; nothing fetched twice
    assert added == 100                                        # 2 pages of calls, then an empty page
    assert pipeline.status["catchup"] is None

    n = db.conn().execute("SELECT COUNT(*) FROM transmissions WHERE id LIKE 't-%'").fetchone()[0]
    assert n == 401                                            # 400 calls + the seeded row, each once
