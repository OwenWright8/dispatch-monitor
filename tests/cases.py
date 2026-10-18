"""Real transcripts with the answers they should produce.

Each case is (county, raw Whisper transcript, expectations). Expectations:
  departments   exact list of departments credited on the shared county channel
  address       what the rules extractor should find (house number first)
  place         "at <place>" the rules extractor should find
  cross         cross streets that must be among the parsed ones
  contains      strings the normalized transcript must contain
  not_contains  strings it must not contain
  transport     True when it's a unit taking a patient to the hospital

Add a case whenever a real call comes out wrong: it keeps the fix from
quietly breaking later.
"""

CASES = [
    # ── Hamilton County ───────────────────────────────────────────────
    ("hamilton",
     "Medic 355, Engine 355, unconscious or fainting, 101, West Main Street, at Grand Coffee, cross of South "
     "Washington Street, North Washington Street, East Main Street, and West Street, North Left Street, 55th "
     "George, timeout, 8-5.",
     {"departments": ["Jackson Township Fire"], "address": "101 West Main Street", "place": "Grand Coffee",
      "cross": ["South Washington Street", "West Street"]}),
    ("hamilton",
     "Engine 346, Medic 346, 12999, Pennsylvania Street at Majestic Care, Unit 162 for an allergic reaction, "
     "Engine 346, Medic 346 at Majestic Care, Unit 162 for an allergic reaction, 1549.",
     {"departments": ["Carmel Fire"], "address": "12999 Pennsylvania Street", "place": "Majestic Care"}),
    ("hamilton",
     "Ambulance 315, Engine 315, injured person, 12,201, East 166th Street, cross of Cedar Creek Lane and Silo "
     "Meadows Drive, 15th George, timeout, 1547.",
     {"departments": ["Wayne Township Fire"], "address": "12201 East 166th Street",
      "cross": ["Cedar Creek Lane", "Silo Meadows Drive"]}),
    ("hamilton",
     "Engine 343, Medic 345, 9-9-7-9, Gwyn Drive, for a sick person. Engine 343, Medic 345, 9-9-7-9, Gwyn Drive, "
     "for a sick person, 14-14.",
     {"departments": ["Carmel Fire"], "address": "9979 Gwyn Drive"}),
    ("hamilton",
     "Medic 382, Engine 33, Sick person, 334, South Cherry Street, at Sanders Glen, cross of Sanders Glen "
     "Boulevard and Midland Trace Trail, tone time 610.",
     {"departments": ["Westfield Fire"], "address": "334 South Cherry Street"}),     # "Engine 33" is 383 misheard
    ("hamilton",
     "Engine 346, Engine 341, Ladder 341, Titan 304, Safety 304, High-Rise Alarm, 1111 West Main Street, tone "
     "time 432.",
     {"departments": ["Carmel Fire"], "contains": ["Battalion 304"], "not_contains": ["Titan"]}),
    ("hamilton",
     "Ambulance 377, Engine 373, EDO307, mental, emotional or suicide attempt, 10749, Brighton Knoll, Parkway, "
     "Cross Off, Atkinson Drive, and Bowen Street, 73 King, tone time 0024.",
     {"departments": ["Noblesville Fire"]}),
    ("hamilton",
     "Medic 361, Tanger 361, Mesoemotional, Ringel Alarm, chest pave, timeout 1940.",
     {"contains": ["Tanker 361", "mental/emotional", "residential alarm", "chest pain", "tone time 1940"]}),
    ("hamilton",
     "Engine 321, Residential Alarm, 1268, Lewison Drive, Cross Off Bowie Drive and Smithfield Drive, 81 "
     "Charles, Timeout, 1531.",
     {"departments": ["Cicero Fire"], "address": "1268 Lewison Drive", "cross": ["Bowie Drive", "Smithfield Drive"]}),
    ("hamilton",
     "Ladder 72, Engine 74P is on scene advising. I believe the fire is out.",
     {"departments": ["Noblesville Fire"]}),
    ("hamilton", "Control, Medic 381, en route to Riverview with one.", {"transport": True}),
    ("hamilton",
     "Engine 392, Medic 397, 12950, Tablix Street, Independence Village, Room 388 for a fall.",
     {"departments": ["Fishers Fire"], "address": "12950 Tablix Street", "transport": False}),

    # ── Tippecanoe County ─────────────────────────────────────────────
    ("tippecanoe",
     "South Medic, we all fire, South Medic, we all fire, being after the 2625 Brewster Lane, 2625 Brewster Lane, "
     "67 year old female fell and possibly broke her ankle, tone time 1417",
     {"departments": ["Wea Township Fire", "TEAS"], "contains": ["Wea Fire"], "address": "2625 Brewster Lane"}),
    ("tippecanoe",
     "T. Sheffield Fire, T. Sheffield Fire being around to Adams Road and State Road 38 East.",
     {"departments": ["TEAS", "Sheffield Fire"], "contains": ["TEAS Sheffield Fire"]}),
    ("tippecanoe",
     "Tees, Otterbun Fire, Tees, Otterbun Fire, being routed to the intersection of US 52 West and 500 North, "
     "for a minor PI crash.",
     {"departments": ["TEAS", "Otterbein Fire"], "contains": ["Otterbein Fire"], "not_contains": ["Tees"]}),
    ("tippecanoe",
     "T.E.'s about to go on fire, T.E.'s about to go on fire, medical run to 21, Woods Edge Court.",
     {"departments": ["TEAS", "Battle Ground Fire"], "contains": ["Battle Ground Fire"]}),
    ("tippecanoe", "Randall Fire, we have a smoke detector at 9950 U.S. Highway 231 South",
     {"departments": ["Randolph Township Fire"]}),
    ("tippecanoe", "TEAS, Clarks Hill Army Fire, medical run, 11749 Silver Street",
     {"departments": ["TEAS", "Clarks Hill Fire"], "contains": ["Clarks Hill Lauramie Fire"],
      "not_contains": ["Clarks Hill Clarks Hill"]}),
    ("tippecanoe", "1502, stand by for ellipticist, Westminster Village, 2741 North Salisbury",
     {"contains": ["lift assist"]}),
    ("tippecanoe", "available medic in Wabash 607, have a medical run at 3130 Cirrus Court",
     {"departments": ["Wabash Township Fire"]}),
    ("tippecanoe", "Medic 605 is en route to IU with one single pin. Clear.", {"transport": True}),
    ("tippecanoe",
     "Attention Purdue Fire Medic 17, being route to 690 Walgreens Street. Sigma Phi Epsilon for a male with "
     "possible alcohol poisoning.",
     {"contains": ["690 Waldron Street"]}),
]
