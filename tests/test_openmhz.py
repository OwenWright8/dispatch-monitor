"""The OpenMHz client backs off after a Cloudflare challenge instead of retrying."""

import pytest

from dispatch import openmhz


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
