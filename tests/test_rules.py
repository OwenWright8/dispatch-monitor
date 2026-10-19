"""Call-type rules and helpers that have broken before."""

from dispatch import departments, extract
from dispatch.geo import Hints, parse_cross_streets
from dispatch.taxonomy import ALL_NATURES, nature_from_keywords, reconcile_fire_alarm


def _refined(nature, transcript, **kw):
    out = dict(extract.EMPTY, is_dispatch=True, nature=nature, units=[], **kw)
    return extract.refine(out, transcript)


# ── alarm vs fire ──────────────────────────────────────────────────────
def test_detector_going_off_is_an_alarm_not_a_fire():
    assert reconcile_fire_alarm("Structure Fire", "smoke detector going off in the barn") == "Fire Alarm"
    assert reconcile_fire_alarm("Smoke Investigation", "smoke detector going off in the barn") == "Fire Alarm"


def test_co_alarm():
    assert reconcile_fire_alarm("Structure Fire", "carbon monoxide detector going off") == "CO Alarm"


def test_negated_fire_is_not_evidence():
    assert reconcile_fire_alarm("CO Alarm", "There are no smoke or flames. The dryer is fine.") == "CO Alarm"
    assert reconcile_fire_alarm("Structure Fire", "units arriving, multi-story dorm, nothing showing") \
        == "Smoke Investigation"


def test_real_fire_stays_a_fire():
    assert reconcile_fire_alarm("Structure Fire", "fire alarm, caller reports flames showing from the garage") \
        == "Structure Fire"
    assert reconcile_fire_alarm("Fire Alarm", "house fire with smoke showing") == "Structure Fire"


# ── keyword traps ──────────────────────────────────────────────────────
def test_blood_and_good_are_not_overdoses():
    assert nature_from_keywords("possible high blood pressure issue") != "Overdose / Poisoning"
    assert nature_from_keywords("Good morning, medic 2 in service") != "Overdose / Poisoning"
    assert nature_from_keywords("possible OD in the bathroom") == "Overdose / Poisoning"


def test_other_medical_gets_specific():
    assert _refined("Other Medical", "80 year old female, speech issues")["nature"] == "Stroke"
    assert _refined("Other Medical", "medical alert button, getting out of the chair")["nature"] == "Medical Alarm"
    assert _refined("Other Medical", "high blood pressure 238 over 118")["nature"] == "Heart Problem / Blood Pressure"
    assert _refined("Other Medical", "request for lift assist, resident not injured")["nature"] == "Lift Assist"


def test_other_medical_stays_medical():
    # smoke inhalation at a fire is a medical call, not a structure fire
    assert _refined("Other Medical", "2 people with smoke inhalation, engine on scene with a structure fire")[
        "nature"] in ("Burns / Smoke Inhalation", "Other Medical")


def test_every_keyword_nature_exists():
    from dispatch.taxonomy import KEYWORD_RULES
    for nature, _ in KEYWORD_RULES:
        assert nature in ALL_NATURES


# ── hospital transports ────────────────────────────────────────────────
def test_transport_is_not_a_dispatch():
    out = _refined("Other Medical", "Medic 16 is en route to IU with one")
    assert out["is_dispatch"] is False


def test_dispatch_with_tone_time_is_kept():
    out = _refined("Sick Person", "Engine 343, Medic 345, 9979 Gwyn Drive, sick person, tone time 1414")
    assert out["is_dispatch"] is True


def test_crash_with_two_vehicles_is_not_a_transport():
    assert not extract.is_transport_report("crash with two vehicles at Main and 3rd, units en route")


# ── units ──────────────────────────────────────────────────────────────
def test_department_names_are_not_units():
    from dispatch import config
    for name in ["Fire", "TEAS"] + [d.spoken or d.name for d in config.PROFILE.departments[:4]]:
        assert departments.clean_unit(name) is None


# ── house numbers & cross streets ──────────────────────────────────────
def test_house_numbers_rejoined():
    j = departments.join_house_numbers
    assert j("12999, Pennsylvania Street at Majestic Care") == "12999 Pennsylvania Street at Majestic Care"
    assert j("12,201, East 166th Street") == "12201 East 166th Street"
    assert j("9-9-7-9, Gwyn Drive") == "9979 Gwyn Drive"
    assert j("9730 Prairie Lake, Boulevard East") == "9730 Prairie Lake Boulevard East"
    assert j("Medic 355, Main Street") == "Medic 355, Main Street"          # a unit, not a house number
    assert j("Ladder 72, 1667 Sheridan Road") == "Ladder 72, 1667 Sheridan Road"


def test_unit_number_is_not_the_house_number():
    assert extract.rules_extract("Medic 355 101 West Main Street, cross of A Street")["address"] \
        == "101 West Main Street"


def test_cross_streets_skip_fragments():
    got = parse_cross_streets("cross of Prairie Neto Drive and Prairie Lake, Boulevard, North Prairie Lake "
                              "Boulevard, 73 Paul, tone time 1702")
    assert "Boulevard" not in got
    assert "North Prairie Lake Boulevard" in got


def test_hints_rank_cross_streets_before_town():
    near_cross = (40.0, -86.0)
    h = Hints(anchors=[(40.2, -86.2)], cross=[[(40.0, -86.0001)]])
    assert h.rank(near_cross) < h.rank((40.2, -86.2))


def test_both_address_readings_found():
    r = extract.address_readings("Battalion 304, Engine 341, 1455 James Dean Drive on a high life alarm. "
                                 "Battalion 304, Engine 341, 1465 James Dean Drive on a life alarm, 948.")
    assert set(r) == {"1455 James Dean Drive", "1465 James Dean Drive"}
    assert r[0] == "1465 James Dean Drive"            # equal counts: the later readback first
    r = extract.address_readings("Engine 374, 18623 Wood Place. Engine 374, 18623 Wood Place, 1720.")
    assert r == ["18623 Wood Place"]


def test_grid_codes():
    from dispatch.geo import parse_grid
    assert parse_grid("cross of South Washington Street and West Street, 55th George, timeout, 8-5") == ("55", "G")
    assert parse_grid("unconscious person in Quarter 72, John 1720.") == ("72", "J")
    assert parse_grid("Quadrant 42, Boy 1722.") == ("42", "B")
    assert parse_grid("46, Adams, tone time 607") == ("46", "A")
    assert parse_grid("Engine 74 from Ladder 72. Clear.") is None
    assert parse_grid("Room 38 David is the caller") is None           # a room number, not a grid code


def test_grid_far_from_pin_is_doubtful():
    from dispatch.geo import Geocoder, GeoResult, Hints
    g = Geocoder.__new__(Geocoder)
    near = GeoResult(40.176, -86.029, "address", "101 Main St", None, "tiger")
    far = GeoResult(39.978, -86.128, "address", "101 W Main St", None, "tiger")
    h = Hints(anchors=[(40.1756, -86.0217)], grid=(40.1761, -86.0286))
    assert g._confidence(near, h) == "high"
    assert g._confidence(far, h) == "doubtful"
    assert h.rank((near.lat, near.lon)) < h.rank((far.lat, far.lon))


def test_unit_status_reports():
    from dispatch import units
    assert units.parse("Dispatch, Engine 41 is on scene. Engine 41.") == [("Engine 41", "41", "on scene")]
    assert units.parse("Medic 605 is en route to IU with one single pin. Clear.")[0][2] == "transporting"
    assert ("Ladder 72", "72", "cleared") in units.parse(
        "Control, Ladder 72. Fire's out, no other hazards here on scene, we're back at service.")
    assert units.parse("Control, Medic 381. We're going to be out of service for training.")[0][2] == "out of service"
    assert ("924", "924", "on scene") in units.parse("Dispatch 924. 924 is arriving. Nothing visible.")
    assert units.parse("Engine 343, Medic 345, 9979 Gwyn Drive, for a sick person, 1414.") == []


def test_unit_shorthand(monkeypatch):
    import dataclasses
    from dispatch import config, units
    monkeypatch.setattr(config, "PROFILE", dataclasses.replace(config.PROFILE, unit_shorthand_prefix="3"))
    assert units.same_unit("72", "372")           # "Ladder 72" on the air is Ladder 372
    assert units.same_unit("381", "381")
    assert not units.same_unit("381", "382")
    assert not units.same_unit("72", "472")


def test_tone_out_without_house_number_is_a_dispatch():
    # Jackson Township crash checkout: cross streets only, no house number (missed on 2026-10-19)
    out, _ = extract.extract(departments.normalize(
        "Medic 355, Utility 355, Accident Checkout, East 234th Street, Cross Street, Cumberland Road, 55 King, "
        "tone time 1407."), use_llm=False)
    assert out["is_dispatch"] and out["nature"].startswith("Crash")
    assert out["address"] == "East 234th Street and Cumberland Road"
    # "PI" is a personal-injury crash
    out, _ = extract.extract("Medic 345, PI Exodus, Old Meridian Street, Cross Street, Pennsylvania Street, 41 David, "
                             "tone time 1028.", use_llm=False)
    assert out["is_dispatch"] and out["nature"] == "Crash with Injuries"
    assert out["address"] == "Old Meridian Street and Pennsylvania Street"


def test_cancellations_and_tests_are_not_dispatches():
    for t in ("Station 84, test tones only. Station 84, test tones only. No run, 1931.",
              "Ladder 81, Medic 382, disregard this call. Ladder 81, Medic 382, disregard this call. 0443."):
        assert not extract.extract(t, use_llm=False)[0]["is_dispatch"]
