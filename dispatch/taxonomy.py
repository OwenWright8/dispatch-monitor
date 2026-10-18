"""Fixed call-type taxonomy.

The LLM (or keyword rules) picks exactly one *nature* from this list; the
category is derived from it. A closed vocabulary keeps statistics consistent —
free text would split "Fall" / "Fall victim" / "Fallen patient" into three rows.
"""

# category -> natures. Order of categories is also the dashboard color order.
TAXONOMY: dict[str, list[str]] = {
    "Medical": [
        "Fall", "Breathing Problem", "Chest Pain", "Cardiac Arrest",
        "Unconscious / Fainting", "Seizure", "Stroke", "Overdose / Poisoning",
        "Diabetic Problem", "Sick Person", "Abdominal Pain", "Back Pain",
        "Bleeding", "Injury / Trauma", "Psychiatric / Behavioral",
        "Altered Mental Status", "Allergic Reaction", "Pregnancy / Childbirth",
        "Headache", "Lift Assist", "Assault / Violence", "Interfacility Transfer",
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

NATURE_TO_CATEGORY = {n: c for c, ns in TAXONOMY.items() for n in ns}
ALL_NATURES = list(NATURE_TO_CATEGORY)
CATEGORIES = list(TAXONOMY)


def category_for(nature: str | None) -> str:
    return NATURE_TO_CATEGORY.get(nature or "", "Other")


# Keyword rules: first match wins, so more specific phrases come first.
# Used as the no-LLM fallback and to sanity-check LLM output.
KEYWORD_RULES: list[tuple[str, list[str]]] = [
    ("Cardiac Arrest", ["cardiac arrest", "not breathing", "cpr in progress", "code blue", "no pulse"]),
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
    ("Overdose / Poisoning", ["overdose", "od ", "narcan", "ingested", "poison"]),
    ("Seizure", ["seizure", "seizing", "convuls"]),
    ("Stroke", ["stroke", "facial droop", "slurred speech"]),
    ("Chest Pain", ["chest pain", "chest pains", "heart attack"]),
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
    ("Injury / Trauma", ["injury", "injured", "trauma", "broken", "fracture"]),
    ("Abdominal Pain", ["abdominal", "stomach pain", "belly pain"]),
    ("Back Pain", ["back pain"]),
    ("Headache", ["headache"]),
    ("Interfacility Transfer", ["transfer", "transport from", "interfacility"]),
    ("Public Assist", ["public assist", "citizen assist", "assist the"]),
    ("Sick Person", ["sick", "vomiting", "nausea", "weakness", "fever", "dizzy", "pain"]),
]


def nature_from_keywords(text: str) -> str | None:
    t = " " + text.lower() + " "
    for nature, keys in KEYWORD_RULES:
        if any(k in t for k in keys):
            return nature
    return None
