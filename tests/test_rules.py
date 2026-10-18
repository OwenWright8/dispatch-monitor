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
