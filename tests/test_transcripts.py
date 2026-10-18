"""Real transcripts in, the right departments / addresses / name fixes out."""

import pytest

from dispatch import config, departments, extract
from dispatch.geo import parse_cross_streets

from .cases import CASES

MINE = [(t, e) for county, t, e in CASES if county == config.PROFILE.key]
if not MINE:
    pytest.skip(f"no transcript cases for {config.PROFILE.key}", allow_module_level=True)

SHARED = next(ch for ch in config.CHANNELS.values() if ch.shared)


@pytest.mark.parametrize("raw,expect", MINE, ids=[t[:50] for t, _ in MINE])
def test_case(raw, expect):
    text = departments.normalize(raw)
    rules = extract.rules_extract(text)
    for s in expect.get("contains", []):
        assert s in text, f"{s!r} missing from {text!r}"
    for s in expect.get("not_contains", []):
        assert s not in text, f"{s!r} should not be in {text!r}"
    if "departments" in expect:
        assert departments.detect(text, rules["units"], SHARED) == expect["departments"]
    if "address" in expect:
        assert rules["address"] == expect["address"]
    if "place" in expect:
        assert rules["place"] and rules["place"].startswith(expect["place"])
    if "cross" in expect:
        got = parse_cross_streets(text)
        for c in expect["cross"]:
            assert c in got, f"{c!r} not in {got}"
    if "transport" in expect:
        assert extract.is_transport_report(text) is expect["transport"]


def test_normalize_is_idempotent():
    for _, raw, _ in CASES:
        once = departments.normalize(raw)
        assert departments.normalize(once) == once
