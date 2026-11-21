"""The OpenMHz client backs off after a Cloudflare challenge instead of retrying,
and presents an operator-issued credential instead of a browser identity when
one is configured."""

import pytest

from dispatch import config, openmhz


class _Resp:
    status_code, text, headers = 403, "<title>Just a moment...</title>", {}


class _Session:
    calls = 0

    def get(self, *a, **kw):
        _Session.calls += 1
        return _Resp()


def test_challenge_pauses_every_request(monkeypatch):
    monkeypatch.setattr(openmhz, "_session", lambda: _Session())
    monkeypatch.setattr(openmhz, "_cool", {"until": 0.0, "n": 0})
    with pytest.raises(openmhz.Challenged):
        openmhz._request("https://api.openmhz.com/x/calls", 5)
    assert _Session.calls == 1                       # no burst of retries
    assert 59 <= openmhz.cooling_down() <= 60
    with pytest.raises(openmhz.Challenged):
        openmhz._request("https://api.openmhz.com/x/calls", 5)
    assert _Session.calls == 1                       # nothing sent during the cool-down
    openmhz._cool["until"] = 0
    with pytest.raises(openmhz.Challenged):
        openmhz._request("https://api.openmhz.com/x/calls", 5)
    assert 119 <= openmhz.cooling_down() <= 120     # doubles while challenges continue


class _Ok:
    status_code, text, headers = 200, "{}", {}

    def raise_for_status(self):
        pass

    def json(self):
        return {"calls": []}


def test_credential_is_sent_and_no_browser_is_impersonated(monkeypatch):
    monkeypatch.setattr(config, "OPENMHZ_ACCESS_CLIENT_ID", "id")
    monkeypatch.setattr(config, "OPENMHZ_ACCESS_CLIENT_SECRET", "secret")
    monkeypatch.setattr(config, "OPENMHZ_TOKEN", "tok")
    seen = {}

    class _AuthSession:
        def get(self, url, **kw):
            seen.update(kw["headers"])
            return _Ok()

    monkeypatch.setattr(openmhz, "_session", lambda: _AuthSession())
    monkeypatch.setattr(openmhz, "_cool", {"until": 0.0, "n": 0})
    openmhz._request("https://api.openmhz.com/x/calls", 5)
    assert seen["CF-Access-Client-Id"] == "id"
    assert seen["CF-Access-Client-Secret"] == "secret"
    assert seen["Authorization"] == "Bearer tok"
    assert seen["User-Agent"] == config.USER_AGENT      # truthful, not a browser string
    assert openmhz.identity() == "authorized"


def test_authorized_session_does_not_impersonate(monkeypatch):
    monkeypatch.setattr(config, "OPENMHZ_TOKEN", "tok")
    built = []

    class _BareSession:
        def __init__(self, **kw):
            built.append(kw)

    monkeypatch.setattr(openmhz.cr, "Session", _BareSession)
    monkeypatch.setattr(openmhz, "_local", openmhz.threading.local())
    openmhz._session()
    assert built == [{}]                                # no impersonate= argument

