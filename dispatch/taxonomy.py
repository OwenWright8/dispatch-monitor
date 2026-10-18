"""Fixed call-type taxonomy.

The LLM (or keyword rules) picks exactly one *nature* from this list; the
category is derived from it. A closed vocabulary keeps statistics consistent —
free text would split "Fall" / "Fall victim" / "Fallen patient" into three rows.
"""

import re

# category -> natures. Order of categories is also the dashboard color order.
TAXONOMY: dict[str, list[str]] = {
    "Medical": [
        "Fall", "Breathing Problem", "Chest Pain", "Cardiac Arrest", "Heart Problem / Blood Pressure",
        "Unconscious / Fainting", "Seizure", "Stroke", "Overdose / Poisoning",
        "Diabetic Problem", "Sick Person", "Abdominal Pain", "Back Pain",
        "Bleeding", "Injury / Trauma", "Psychiatric / Behavioral",
        "Altered Mental Status", "Allergic Reaction", "Pregnancy / Childbirth",
        "Headache", "Choking", "Burns / Smoke Inhalation", "Eye Problem", "Heat / Cold Exposure", "Animal Bite",
        "Lift Assist", "Medical Alarm", "Unknown Problem / Person Down",
        "Assault / Violence", "Interfacility Transfer",
        "Other Medical",
    ],
    "Fire": [
        "Structure Fire", "Vehicle Fire", "Brush / Grass Fire", "Outside / Trash Fire",
        "Smoke Investigation", "Other Fire",
    ],
    "Alarm": ["Fire Alarm", "CO Alarm"],
    "Traffic": ["Crash with Injuries", "Crash", "Crash with Entrapment", "Pedestrian / Cyclist Struck"],
    "Hazmat": ["Gas Leak / Odor", "Hazmat / Spill"],
    "Rescue": ["Water Rescue", "Elevator / Entrapment", "Technical Rescue"],
    "Service": ["Public Assist", "Wires Down / Utility", "Lockout", "Standby / Detail", "Investigation"],
    "Other": ["Other"],
}

# What each easily-confused nature means, given to the LLM so it stops
# defaulting to "Other Medical" and stops calling every alarm a fire.
NATURE_GUIDE: dict[str, str] = {
    "Stroke": "slurred or garbled speech, speech problems, facial droop, sudden one-sided weakness or numbness, "
              "sudden loss of feeling or movement in limbs",
    "Seizure": "seizing, convulsions, shaking with foaming at the mouth",
    "Heart Problem / Blood Pressure": "high or low blood pressure, racing / irregular heartbeat, palpitations, "
                                      "pacemaker or defibrillator problems (not chest pain)",
    "Altered Mental Status": "confused, disoriented, not acting right, lethargic or not communicating normally",
    "Unconscious / Fainting": "passed out, unresponsive, syncope, fainted",
    "Sick Person": "general illness: vomiting, nausea, weakness, fever, dizziness, infection, flu symptoms, "
                   "catheter or feeding-tube problems, generally not feeling well",
    "Injury / Trauma": "injured without a fall: hit head, cut, broken bone, sports injury",
    "Fall": "the patient fell (injured or not) and needs evaluation",
    "Lift Assist": "uninjured person who only needs help getting up (often said 'lift assist')",
    "Medical Alarm": "a medical alert / Life Alert pendant or button went off, caller status unclear",
    "Unknown Problem / Person Down": "'unknown medical', 'medical assist' or a person down with no complaint given",
    "Eye Problem": "something in the eye, eye injury or eye pain",
    "Breathing Problem": "difficulty breathing, shortness of breath, asthma",
    "Other Medical": "LAST RESORT: a medical call that clearly fits none of the medical types above",
    "Structure Fire": "an actual fire in or on a building: flames, smoke showing, fire in a room, house fire. "
                      "NOT an alarm going off",
    "Fire Alarm": "an automatic fire alarm, smoke detector or alarm company call, pull station or sprinkler "
                  "waterflow, with no confirmed fire",
    "CO Alarm": "carbon monoxide alarm or detector going off",
    "Smoke Investigation": "smell of smoke or smoke seen from a distance with no known fire and no alarm",
    "Other Fire": "a fire that is not a building, vehicle, brush or trash fire (e.g. silo, grain bin, equipment)",
}

NATURE_TO_CATEGORY = {n: c for c, ns in TAXONOMY.items() for n in ns}
ALL_NATURES = list(NATURE_TO_CATEGORY)
CATEGORIES = list(TAXONOMY)


def category_for(nature: str | None) -> str:
    return NATURE_TO_CATEGORY.get(nature or "", "Other")


# Keyword rules: first match wins, so more specific phrases come first.
# Used as the no-LLM fallback and to sanity-check LLM output.
KEYWORD_RULES: list[tuple[str, list[str]]] = [
    ("Cardiac Arrest", ["cardiac arrest", "not breathing", "cpr in progress", "code blue", "no pulse"]),
    ("Medical Alarm", ["medical alert", "medical alarm", "life alert", "lifeline", "alert button", "alert pendant"]),
    ("Crash with Entrapment", ["entrapment", "entrapped", "pinned in"]),
    ("Pedestrian / Cyclist Struck", ["pedestrian struck", "ped struck", "hit by a car", "struck by a vehicle",
                                     "bicyclist struck", "cyclist struck", "struck by a car"]),
    ("Crash with Injuries", ["accident with injur", "crash with injur", "pi accident", "injury accident",
                             "personal injury", "mvc with injur", "rollover"]),
    ("Crash", ["accident", "crash", "mvc", "mva", "collision", "vehicle versus", "car versus"]),
    ("Structure Fire", ["structure fire", "house fire", "building fire", "apartment fire", "residential fire",
                        "commercial fire", "fire in the", "flames showing", "smoke showing", "working fire"]),
    ("Vehicle Fire", ["vehicle fire", "car fire", "truck fire", "car on fire", "vehicle on fire"]),
    ("Brush / Grass Fire", ["brush fire", "grass fire", "field fire", "mulch fire", "crop fire"]),
    ("Outside / Trash Fire", ["dumpster fire", "trash fire", "outside fire", "open burn", "burn complaint",
                              "illegal burn", "bonfire"]),
    ("CO Alarm", ["carbon monoxide", "co alarm", "co detector", "co investigation"]),
    ("Fire Alarm", ["fire alarm", "alarm activation", "general alarm", "smoke detector", "alarm company",
                    "pull station", "waterflow", "water flow alarm", "smoke alarm"]),
    ("Gas Leak / Odor", ["gas leak", "odor of gas", "smell of gas", "natural gas", "gas odor", "gas line"]),
    ("Hazmat / Spill", ["hazmat", "hazardous material", "fuel spill", "chemical spill", "fuel leak"]),
    ("Smoke Investigation", ["odor of smoke", "smell of smoke", "smoke investigation", "smoke in the"]),
    ("Water Rescue", ["water rescue", "swift water", "in the river", "person in the water", "ice rescue"]),
    ("Elevator / Entrapment", ["elevator", "stuck in"]),
    ("Wires Down / Utility", ["wires down", "wire down", "power line", "transformer", "arcing"]),
    ("Lockout", ["lockout", "locked in", "child locked"]),
    ("Lift Assist", ["lift assist", "help getting up", "assist getting up", "assist off the floor"]),
    ("Overdose / Poisoning", ["overdose", " od ", "narcan", "ingested", "poison"]),
    ("Seizure", ["seizure", "seizing", "convuls", "foam coming out", "foaming at the mouth"]),
    ("Stroke", ["stroke", "facial droop", "slurred speech", "speech issue", "speech problem", "trouble speaking",
                "can't feel", "numbness"]),
    ("Choking", ["choking", "choked"]),
    ("Chest Pain", ["chest pain", "chest pains", "heart attack"]),
    ("Heart Problem / Blood Pressure", ["blood pressure", "palpitation", "heart rate", "racing heart",
                                        "irregular heart", "pacemaker", "defibrillator"]),
    ("Burns / Smoke Inhalation", ["burned", "burns to", "burn victim", "scalded", "smoke inhalation"]),
    ("Eye Problem", ["in her eye", "in his eye", "in their eye", "eye injury", "eye pain", "in the eye"]),
    ("Heat / Cold Exposure", ["heat exhaustion", "heat stroke", "hypothermia", "frostbite", "overheated"]),
    ("Animal Bite", ["dog bite", "bitten by", "animal bite"]),
    ("Breathing Problem", ["difficulty breathing", "trouble breathing", "shortness of breath", "can't breathe",
                           "breathing problem", "respiratory", "asthma"]),
    ("Unconscious / Fainting", ["unconscious", "unresponsive", "passed out", "syncop", "fainted", "collapsed"]),
    ("Diabetic Problem", ["diabetic", "blood sugar", "hypoglycem"]),
    ("Allergic Reaction", ["allergic", "anaphyla", "bee sting"]),
    ("Pregnancy / Childbirth", ["pregnan", "in labor", "childbirth", "water broke"]),
    ("Psychiatric / Behavioral", ["suicid", "psych", "mental health", "behavioral", "emotionally disturbed"]),
    ("Assault / Violence", ["assault", "stabb", "gunshot", "shot ", "fight"]),
    ("Altered Mental Status", ["altered mental", "ams", "confused", "disoriented"]),
    ("Bleeding", ["bleeding", "hemorrhag", "laceration"]),
    ("Fall", ["fell", "fall ", "fallen", "a fall"]),
    ("Injury / Trauma", ["injury", "injured", "trauma", "broken", "fracture", "hit her head", "hit his head",
                         "hit their head", "hit the head", "head injury", "struck her head", "struck his head"]),
    ("Abdominal Pain", ["abdominal", "stomach pain", "belly pain"]),
    ("Back Pain", ["back pain"]),
    ("Headache", ["headache"]),
    ("Interfacility Transfer", ["transfer", "transport from", "interfacility"]),
    ("Unknown Problem / Person Down", ["unknown medical", "unknown problem", "person down", "man down",
                                       "medical assist"]),
    ("Public Assist", ["public assist", "citizen assist", "assist the"]),
    ("Sick Person", ["sick", "vomiting", "nausea", "weakness", "fever", "dizzy", "infection", "catheter",
                     "not feeling well", "pain"]),
]

# ─────────────────────────────────────────────
# Alarm vs. actual fire
# ─────────────────────────────────────────────
_ALARM_CUES = ("alarm", "detector", "alarm company", "pull station", "waterflow", "water flow", "sprinkler",
               "panel", "going off", "activation")
_CO_CUES = ("carbon monoxide", "co alarm", "co detector", " co ")
_FIRE_EVIDENCE = ("flames", "on fire", "fire showing", "smoke showing", "working fire", "fully involved",
                  "house fire", "structure fire", "building fire", "apartment fire", "fire in the", "caught fire",
                  "is burning", "visible fire", "heavy smoke", "black smoke", "smoke coming from", "smoke pouring",
                  "kitchen fire", "fire in a", "grease fire", "flames visible", "silo fire", "barn fire",
                  "garage fire", "chimney fire", "dryer fire", "fire inside")


# "no smoke or flames", "nothing showing", "without fire" are not evidence of fire
_NEGATED = re.compile(r"\b(?:no|not|nothing|without|negative for|no sign of|no signs of)\s+(?:visible\s+)?"
                      r"(?:smoke|flames?|fire)(?:\s*(?:,|or|and|nor)\s*(?:smoke|flames?|fire))*"
                      r"|\bnothing (?:showing|visible|found|evident)\b|\bno (?:problems?|issues?) (?:found|showing)\b")


def reconcile_fire_alarm(nature: str | None, text: str) -> str | None:
    """An alarm going off is not a fire. Correct the LLM when the words disagree."""
    low = (text or "").lower()
    negated = bool(_NEGATED.search(low))
    t = " " + _NEGATED.sub(" ", low) + " "
    alarm = any(k in t for k in _ALARM_CUES)
    co = any(k in t for k in _CO_CUES)
    fire = any(k in t for k in _FIRE_EVIDENCE)
    if nature in ("Structure Fire", "Smoke Investigation", "Other Fire") and alarm and not fire:
        return "CO Alarm" if co else "Fire Alarm"
    if nature == "Structure Fire" and negated and not fire:
        return "Smoke Investigation"      # "arriving, nothing showing": no fire was ever reported
    if nature == "Fire Alarm" and fire and not alarm:
        return "Structure Fire"
    if nature == "Fire Alarm" and co:
        return "CO Alarm"
    return nature


def nature_from_keywords(text: str, category: str | None = None) -> str | None:
    t = " " + text.lower() + " "
    for nature, keys in KEYWORD_RULES:
        if category and NATURE_TO_CATEGORY.get(nature) != category:
            continue
        if any(k in t for k in keys):
            return nature
    return None
