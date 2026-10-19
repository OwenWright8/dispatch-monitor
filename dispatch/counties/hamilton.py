"""Hamilton County, Indiana (Carmel, Fishers, Noblesville, Westfield and the
smaller departments: Cicero, Jackson Township (Arcadia and Atlanta),
Sheridan, Wayne Township, White River Township).

Radio: MESA (formerly IDPS) System 3 simulcast, carried on OpenMHz as
"hamiltonco". County dispatch tones out by unit, not by department name
("Engine 343, Medic 345, 9979 Gwyn Drive, sick person"), so departments are
mostly identified by unit number. Fire apparatus are numbered 3xx with the
second digit naming the department, often shortened on the air
("Ladder 72" = Ladder 372). The EMS duty officers are EDO 307 (Noblesville),
EDO 308 (Westfield) and EDO 309 (Fishers).

Departments: Hamilton County government fire department directory
(hamiltoncounty.in.gov/577/County-Fire-Departments). Unit numbering:
RadioReference (wiki.radioreference.com/index.php/Hamilton_County_(IN)).
"""

from . import ChannelSpec, Department, Profile, Station

FIRE = r"(?:\s+township)?\s+fire\b"

DEPARTMENTS = (
    # ── The four city departments ──────────────────────────────────────
    Department("Carmel Fire", spoken="Carmel Fire", towns=("carmel",), unit_prefixes=("34x", "4x"), area="Carmel / Clay Township",
               aliases=(r"\b(?:carmel|caramel|carmelle|car mel)" + FIRE,)),
    Department("Fishers Fire", spoken="Fishers Fire", towns=("fishers",), unit_prefixes=("39x", "9x", "309"), area="Fishers / Fall Creek Township",
               aliases=(r"\b(?:fishers|fisher's|fisher|fischer'?s?)" + FIRE,)),
    Department("Noblesville Fire", spoken="Noblesville Fire", towns=("noblesville",), unit_prefixes=("37x", "7x", "307"),
               area="Noblesville / Noblesville Township",
               aliases=(r"\b(?:noblesville|nobles ?ville|noble'?s ville|nobleville|noblesvill)" + FIRE,)),
    Department("Westfield Fire", spoken="Westfield Fire", towns=("westfield", "eagletown"), unit_prefixes=("38x", "8x", "308"),
               area="Westfield / Washington Township",
               aliases=(r"\b(?:west ?field|westfeld)" + FIRE,)),
    # ── Smaller town and township departments ──────────────────────────
    Department("Cicero Fire", spoken="Cicero Fire", towns=("cicero",), unit_prefixes=("32x", "2x"), area="Cicero",
               aliases=(r"\b(?:cicero|sisero|sicero|cisero|sis arrow|sister o)" + FIRE,)),
    # Jackson Township covers Arcadia and Atlanta; there is no separate Atlanta department.
    Department("Jackson Township Fire", spoken="Jackson Township Fire", towns=("arcadia", "atlanta"), unit_prefixes=("35x", "5x"),
               area="Jackson Township / Arcadia & Atlanta",
               aliases=(r"\bjackson township fire\b", r"\bjackson (?:twp\.? )?fire\b", r"\bjackson fire territory\b",
                        r"\b(?:arcadia|arcadian|arcadia's|atlanta|atlantic)" + FIRE)),
    Department("Sheridan Fire", spoken="Sheridan Fire", towns=("sheridan",), unit_prefixes=("36x", "6x"), area="Sheridan / Adams Township",
               aliases=(r"\b(?:sheridan|sharidan|sheriden|sherridan)" + FIRE,)),
    Department("Wayne Township Fire", spoken="Wayne Township Fire", towns=("durbin",), unit_prefixes=("31x", "1x"),
               area="Wayne Township (east of Noblesville)",
               aliases=(r"\bwayne township fire\b", r"\bwayne (?:twp\.? )?fire\b")),
    Department("White River Township Fire", spoken="White River Fire", towns=("strawtown",), unit_prefixes=("33x", "3x"),
               area="White River Township / Strawtown",
               aliases=(r"\bwhite river" + FIRE, r"\bwhite rivers? fire\b")),
    # ── Mutual aid from neighbouring counties ──────────────────────────
    Department("Zionsville Fire", area="Mutual aid · Boone County", detect=(r"\bzionsville fire\b",)),
    Department("Lawrence Fire", area="Mutual aid · Marion County", detect=(r"\blawrence fire\b",)),
    Department("Indianapolis Fire", area="Mutual aid · Marion County", detect=(r"\bindianapolis fire\b", r"\bifd\b")),
    Department("Lapel Fire", area="Mutual aid · Madison County", detect=(r"\blapel fire\b",)),
    Department("Pendleton Fire", area="Mutual aid · Madison County", detect=(r"\bpendleton fire\b",)),
    Department("Tipton Fire", area="Mutual aid · Tipton County", detect=(r"\btipton fire\b",)),
)

PROFILE = Profile(
    key="hamilton",
    name="Hamilton County",
    state="IN",
    title="Hamilton County Dispatch Monitor",
    subtitle="Fire & EMS dispatch · Carmel · Fishers · Noblesville · Westfield · Cicero · Sheridan · Arcadia · Atlanta",
    fips="18057",
    bbox=(39.91, -86.25, 40.25, -85.85),
    center=(40.05, -86.03),
    zoom=11,
    timezone="America/Indiana/Indianapolis",
    openmhz_system="hamiltonco",
    channels=(
        ChannelSpec(29010, "County fire dispatch", "Noblesville", shared=True),
        ChannelSpec(29006, "County fire control", "Noblesville", shared=True),
        ChannelSpec(29155, "Noblesville FD dispatch", "Noblesville", default_department="Noblesville Fire"),
    ),
    departments=DEPARTMENTS,
    city_centers={
        "carmel": (39.9784, -86.1180),
        "fishers": (39.9568, -86.0134),
        "noblesville": (40.0456, -86.0086),
        "westfield": (40.0428, -86.1275),
        "cicero": (40.1239, -86.0133),
        "arcadia": (40.1756, -86.0217),
        "atlanta": (40.2153, -86.0264),
        "sheridan": (40.1350, -86.2205),
        "strawtown": (40.1239, -85.9533),
        "durbin": (40.0870, -85.9286),
        "eagletown": (40.0453, -86.1597),
    },
    whisper_prompt=(
        "Hamilton County fire and EMS dispatch, Indiana. Carmel Fire, Fishers Fire, Noblesville Fire, "
        "Westfield Fire, Cicero Fire, Jackson Township Fire, Arcadia, Atlanta, Sheridan Fire, "
        "Wayne Township Fire, White River Township Fire. Medic 341, Engine 391, Ladder 381, Engine 321, "
        "Medic 355, Engine 361, Battalion 370. Sick person, respond, tone time. Meridian Street, Keystone "
        "Parkway, 116th Street, 146th Street, Allisonville Road, State Road 37, State Road 32, State Road 19, "
        "Spring Mill Road, Hazel Dell Parkway, Olio Road, Range Line Road, Grand Park, Riverview Health."
    ),
    street_hints=("Meridian Street, Keystone Parkway, 116th Street, 131st Street, 146th Street, 96th Street, "
                  "Allisonville Road, State Road 37, State Road 32, State Road 38, State Road 19, State Road 213, "
                  "Spring Mill Road, Hazel Dell Parkway, Olio Road, Range Line Road, Carey Road, Gray Road, Hague Road, "
                  "Cumberland Road, Little Chicago Road, Durbin Road, Strawtown Avenue, Jackson Street, Main Street"),
    place_examples='"Grand Park", "Riverview Health", "Ruoff Music Center", "Hamilton Town Center", "Clay Terrace"',
    unit_examples='"Medic 341", "Engine 391", "Ladder 381", "Engine 321", "Medic 355", "Battalion 370"',
    # Mishearings found transcribing two days of county dispatch audio.
    transcript_fixes=(
        (r"\b(?:titan|italian|batallion|battalian|battle ?ion|medallion)\s+(?=30\d\b)", "Battalion "),
        (r"\b(?:meso|medal|metal|mental)[\s/-]*emotional\b", "mental/emotional"),
        (r"\btang(?:er|o)\s+(?=3?\d{2}\b)", "Tanker "),
        (r"\b(?:ringel|ringle|residual|rezidential|residental)\s+alarm\b", "residential alarm"),
        (r"\bchest\s+(?:pave|pains?|paint)\b", "chest pain"),
        (r"\bdifficulty\s+(?:rebate|breeding|breathe|breathin)\b", "difficulty breathing"),
        (r"\bunconscious or (?:thinking|fanting|fainting)\b", "unconscious or fainting"),
        (r"\binjured (?:version|person)\b", "injured person"),
        (r"\b(?:time ?out|timeout)\b(?=,?\s*\d{3,4}\b)", "tone time"),
    ),
    # "55 George": first-due station 55 (Jackson Township, unit 355). Addresses from
    # the departments' station pages and the NERIS registry, geocoded with the Census
    # geocoder (2026-10). The map data labels Carmel 46 as "Fire Station 48", so the
    # official list wins.
    grid_codes=True,
    stations={
        "15": Station(40.0568, -85.9105, "Wayne Township Fire", "Station 15", "18441 Durbin Rd"),
        "21": Station(40.1203, -86.0217, "Cicero Fire", "Station 21", "Cicero"),
        "31": Station(40.1615, -85.9406, "White River Township Fire", "Station 31", "12695 E 256th St"),
        "41": Station(39.9664, -86.1290, "Carmel Fire", "Station 41", "2 Civic Sq"),
        "42": Station(39.9410, -86.2227, "Carmel Fire", "Station 42", "3610 W 106th St"),
        "43": Station(39.9415, -86.1095, "Carmel Fire", "Station 43", "3242 E 106th St"),
        "44": Station(39.9785, -86.0841, "Carmel Fire", "Station 44", "5032 E Main St"),
        "45": Station(39.9428, -86.1459, "Carmel Fire", "Station 45", "10701 N College Ave"),
        "46": Station(39.9850, -86.1682, "Carmel Fire", "Station 46", "540 W 136th St"),
        "55": Station(40.1761, -86.0286, "Jackson Township Fire", "Station 55", "508 W Main St, Arcadia"),
        "61": Station(40.1339, -86.2207, "Sheridan Fire", "Station 61", "506 S Main St"),
        "71": Station(40.0443, -86.0136, "Noblesville Fire", "Station 71", "135 S 9th St"),
        "72": Station(40.0622, -86.0490, "Noblesville Fire", "Station 72", "400 S Harbour Dr"),
        "73": Station(40.0266, -86.0006, "Noblesville Fire", "Station 73", "2101 Greenfield Ave"),
        "74": Station(40.0894, -86.0335, "Noblesville Fire", "Station 74", "20777 Hague Rd"),
        "75": Station(40.0658, -85.9873, "Noblesville Fire", "Station 75", "10170 E 191st St"),
        "76": Station(40.0316, -86.0706, "Noblesville Fire", "Station 76", "16800 Hazel Dell Rd"),
        "77": Station(40.0110, -85.9193, "Noblesville Fire", "Station 77", "15251 Olio Rd"),
        "81": Station(40.0334, -86.1835, "Westfield Fire", "Station 81", "17001 Ditch Rd"),
        "82": Station(40.0075, -86.1246, "Westfield Fire", "Station 82", "1920 E 151st St"),
        "83": Station(40.0484, -86.1093, "Westfield Fire", "Station 83", "17944 Grassy Branch Rd"),
        "91": Station(39.9595, -86.0178, "Fishers Fire", "Station 91", "2 Municipal Dr"),
        "92": Station(39.9574, -85.9424, "Fishers Fire", "Station 92", "11595 Brooks School Rd"),
        "93": Station(39.9386, -86.0523, "Fishers Fire", "Station 93", "10501 Allisonville Rd"),
        "94": Station(39.9434, -85.9949, "Fishers Fire", "Station 94", "10701 Cumberland Rd"),
        "95": Station(39.9792, -85.9735, "Fishers Fire", "Station 95", "10870 E 131st St"),
        "96": Station(39.9396, -85.8907, "Fishers Fire", "Station 96", "E 104th St & Florida Rd"),
        "97": Station(39.9873, -85.8953, "Fishers Fire", "Station 97", "15109 E 136th St"),
    },
    hospitals=("Riverview", "Riverview Health", "IU North", "IU Health North", "St. Vincent", "Ascension",
               "Community North", "Community Heart and Vascular", "Methodist", "Riley", "Eskenazi"),
    # County dispatch reads: units, call type, house number, street, "at" place,
    # "cross of" the cross streets, a map-grid code ("55 George") and "timeout".
    examples=(
        ("Engine 346, Medic 346, 12999 Pennsylvania Street at Majestic Care, Unit 162 for an allergic reaction, "
         "cross of 131st Street and 136th Street, 46 Adam, tone time 1549.",
         {"is_dispatch": True, "address": "12999 Pennsylvania Street", "place": "Majestic Care",
          "nature": "Allergic Reaction", "units": ["Engine 346", "Medic 346"], "patient_age": None,
          "patient_sex": None, "summary": "Allergic reaction at Majestic Care, unit 162."}),
        ("Medic 355, Engine 355, unconscious or fainting, 101 West Main Street, at Grand Coffee, cross of "
         "South Washington Street and West Street, 55 George, tone time 0805.",
         {"is_dispatch": True, "address": "101 West Main Street", "place": "Grand Coffee",
          "nature": "Unconscious / Fainting", "units": ["Medic 355", "Engine 355"], "patient_age": None,
          "patient_sex": None, "summary": "Person unconscious or fainting at Grand Coffee."}),
        ("Engine 321, Medic 355, 22510 Hinkle Road, smoke detector going off in the barn, caller sees no smoke "
         "or fire, cross of 226th Street and 236th Street, tone time 0705.",
         {"is_dispatch": True, "address": "22510 Hinkle Road", "place": None, "nature": "Fire Alarm",
          "units": ["Engine 321", "Medic 355"], "patient_age": None, "patient_sex": None,
          "summary": "Smoke detector sounding in barn, no fire seen."}),
        ("Control, Medic 381, en route to Riverview with one.",
         {"is_dispatch": False, "address": None, "place": "Riverview", "nature": "Other", "units": ["Medic 381"],
          "patient_age": None, "patient_sex": None, "summary": None}),
    ),
    place_aliases={
        "riverview": "Riverview Health",
        "ruoff": "Ruoff Music Center",
        "deer creek": "Ruoff Music Center",
        "grand park": "Grand Park Sports Campus",
    },
)
