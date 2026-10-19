"""Tippecanoe County, Indiana (Lafayette, West Lafayette, Purdue).

Department list and unit numbering from the RadioReference county page
(wiki.radioreference.com/index.php/Tippecanoe_County_(IN)). The county fire
channel tones out township departments by name ("TEAS, Wea Fire, ...") and
units answer by number, where the leading digits identify the department.
"""

from . import ChannelSpec, Department, Profile, Station

# A department name followed by "Fire" (optionally "Township" / "Department").
FIRE = r"(?:\s+township)?\s+fire\b"

DEPARTMENTS = (
    Department(
        "TEAS", kind="ems", spoken="TEAS", area="County ambulance service",
        aliases=(
            # Opening a tone-out: "Tees, Buck Creek Fire", "T.E.'s, ...", "T. Sheffield Fire", "T, stand by"
            r"^\W*(?:(?:at)?tention,?\s+)?(?:t\.?\s?e\.?\s?a\.?\s?s\b\.?|t\.\s?e\.?\s?'?s\b|tees\b|teas\b|tease\b|"
            r"tgs\b|t\.\s?g\.?\s?s\b\.?|t\.(?=\s)|t(?=,)|t's\b|tis\b)",
            # Mid-sentence, right before a department / medic / medical run / "units"
            r"(?<![\w.])(?:t\.\s?e\.?\s?a\.?\s?s\b\.?|t\.\s?e\.?\s?'?s\b|tees\b|teas\b|tease\b|tgs\b|t\.\s?g\.?\s?s\b\.?|"
            r"t's\b|t\.)(?=,?\s+(?:[a-z']+\s+){0,2}(?:fire|medic|medical|zone|units?)\b)",
            r"\bteas\b",
        ),
        detect=(r"\b(?:west|east|north|south)side medic\b", r"\b(?:south|north|central|zone \d) medic\b",
                r"\bmedic from central\b"),
    ),
    Department("Lafayette Fire", area="City of Lafayette", towns=("lafayette",),
               detect=(r"(?<!west )\blafayette fire\b", r"\blfd\b")),
    Department("West Lafayette Fire", area="City of West Lafayette", towns=("west lafayette",), detect=(r"\bwest lafayette fire\b", r"\bwlfd\b")),
    Department("Purdue Fire", area="Purdue University", towns=("purdue",), reach_km=3, detect=(r"\bpurdue fire\b",)),
    Department(
        "Wea Township Fire", spoken="Wea Fire", towns=("wea",), unit_prefixes=("4xx",), area="Wea Township",
        aliases=(r"\b(?:wea|we all|we|wee|way|whey|weigh|we a|weah|wia|why a)" + FIRE,),
    ),
    Department(
        "Wabash Township Fire", spoken="Wabash Fire", towns=("west lafayette", "montmorenci"), unit_prefixes=("6xx",), area="Wabash & Shelby Townships",
        aliases=(r"\b(?:wabash|wobash|wall bash|wa bash|wabish)" + FIRE,),
        detect=(r"\bwabash,?\s+(?:medic\s+|rescue\s+|engine\s+)?6\d\d\b",),
    ),
    Department(
        "Shadeland Fire", spoken="Shadeland Fire", towns=("shadeland",), unit_prefixes=("3xx",), area="Union Township / Shadeland",
        aliases=(r"\b(?:shade ?land|shady ?land|shailin|shailand|shayland|shaylin|chadland|shadlin|shadelin|"
                 r"shellan|sheltland)" + FIRE,),
    ),
    Department(
        "Sheffield Fire", spoken="Sheffield Fire", towns=("dayton",), unit_prefixes=("2xx",), area="Dayton / Sheffield & Perry Townships",
        aliases=(r"\b(?:sheffield|shefield|sheffeld|shuffield|chefield|sheffel)" + FIRE,
                 r"\bdayton" + FIRE),
    ),
    Department(
        "West Point Fire", spoken="West Point Fire", towns=("west point",), unit_prefixes=("5xx",), area="Wayne & Jackson Townships",
        aliases=(r"\bwest ?point" + FIRE,),
    ),
    Department(
        "Battle Ground Fire", spoken="Battle Ground Fire", towns=("battle ground",), unit_prefixes=("7xx",), area="Tippecanoe Township",
        aliases=(r"\b(?:battle ?ground|battlegrounds?|a?bout to go(?:\s+on)?|battle grand|powder ground|"
                 r"batter ground|bottle ground)" + FIRE,
                 r"\btippecanoe township fire\b"),
    ),
    Department(
        "Buck Creek Fire", spoken="Buck Creek Fire", towns=("buck creek",), unit_prefixes=("9xx",), area="Washington Township",
        aliases=(r"\b(?:buck ?creek|bug creek|buck crick|book creek|buck greek)" + FIRE,),
    ),
    Department(
        "Randolph Township Fire", spoken="Randolph Fire", towns=("romney",), unit_prefixes=("11xx",), area="Randolph & Jackson / Romney",
        aliases=(r"\b(?:randolph|randall|randle|randolf|rudolph|random|romney)" + FIRE,),
    ),
    Department(
        "Clarks Hill Fire", spoken="Clarks Hill Lauramie Fire", towns=("clarks hill", "stockwell"),
        unit_prefixes=("12xx", "8xx"), area="Lauramie Township",
        # toned out as "Clarks Hill Lauramie Fire"; Whisper hears "Clarks Hill Army Fire"
        aliases=(r"\b(?:clarks?'?\s?hill|clark'?s hill|clarkshill|clark hill)(?:[\s,-]+(?:lauramie|laramie|lorami|"
                 r"loramie|lauren?ie|army|army's|ramie|la ?ramie|lorraine|laurie|lori|mommy))?" + FIRE,
                 r"(?<!hill )(?<!hill, )\b(?:lauramie|laramie|loramie|lorami)" + FIRE),
    ),
    Department(
        "Otterbein Fire", spoken="Otterbein Fire", towns=("otterbein",), unit_prefixes=("13xx",), area="Shelby Township / Otterbein",
        aliases=(r"\b(?:otter ?bein|otter ?bun|otter ?man|otter ?bean|otter ?bine|otter ?bind|otter ?bay|otterbin)"
                 + FIRE,),
    ),
    Department(
        "Stockwell Fire", spoken="Stockwell Fire", towns=("stockwell",), area="Lauramie Township / Stockwell",
        aliases=(r"\b(?:stockwell|stock well|stockwill)" + FIRE,),
    ),
    Department(
        "Evonik Fire", spoken="Evonik Fire", towns=("lafayette",), unit_prefixes=("16xx",), area="Evonik Tippecanoe Labs plant",
        aliases=(r"\b(?:evonik|evonic|a vonic|avonic|eve onyx)" + FIRE,),
    ),
)

PROFILE = Profile(
    key="tippecanoe",
    name="Tippecanoe County",
    state="IN",
    title="Tippecanoe Dispatch Monitor",
    subtitle="Fire & EMS dispatch · Lafayette · West Lafayette · Purdue · township fire",
    fips="18157",
    bbox=(40.20, -87.11, 40.58, -86.68),
    center=(40.4167, -86.8753),
    zoom=12,
    timezone="America/Indiana/Indianapolis",
    openmhz_system="tippco",
    channels=(
        ChannelSpec(1827, "County fire dispatch", "Lafayette", shared=True),
        ChannelSpec(1833, "TEAS EMS dispatch", "Lafayette", default_department="TEAS"),
        ChannelSpec(1901, "Lafayette FD dispatch", "Lafayette", default_department="Lafayette Fire"),
        ChannelSpec(2021, "West Lafayette FD dispatch", "West Lafayette", default_department="West Lafayette Fire"),
        ChannelSpec(2105, "Purdue FD dispatch", "West Lafayette", default_department="Purdue Fire"),
    ),
    departments=DEPARTMENTS,
    city_centers={
        "lafayette": (40.4167, -86.8753),
        "west lafayette": (40.4259, -86.9081),
        "purdue": (40.4237, -86.9212),
        "battle ground": (40.5089, -86.8417),
        "dayton": (40.3745, -86.7689),
        "otterbein": (40.4906, -87.0961),
        "clarks hill": (40.2464, -86.7253),
        "romney": (40.2495, -86.9050),
        "west point": (40.3459, -87.0458),
        "shadeland": (40.3734, -86.9489),
        "buck creek": (40.4875, -86.7631),
        "stockwell": (40.2853, -86.7703),
        "montmorenci": (40.4728, -87.0300),
        "wea": (40.3650, -86.8800),
    },
    whisper_prompt=(
        "Tippecanoe County fire and EMS dispatch, Lafayette and West Lafayette, Indiana. "
        "TEAS, Wea Fire, Wabash Fire, Shadeland Fire, Sheffield Fire, Buck Creek Fire, Battle Ground Fire, "
        "Otterbein Fire, Randolph Fire, Clarks Hill Lauramie Fire, West Point Fire. "
        "Station 1, Engine 1502, Medic 5, Westside Medic, Central Medic, Battalion 14. "
        "Medical run, lift assist, stand by for dispatch, go channel 2, tone time 1417. "
        "North Salisbury Street, Sagamore Parkway, Teal Road, Creasy Lane, Greenbush Street, Kossuth Street, "
        "Concord Road, McCarty Lane, Yeager Road, Klondike Road, Northwestern Avenue, County Road 500 East. "
        "Westminster Village, LTHC, Sycamore Springs, IU Health Arnett, Franciscan, Purdue."
    ),
    street_hints=("Sagamore Parkway, Salisbury, Teal Road, Creasy Lane, Greenbush, Union, Kossuth, Concord Road, "
                  "McCarty Lane, Yeager Road, Klondike Road, Cumberland Avenue, Northwestern Avenue, Stadium Avenue, "
                  "State Street, River Road, Shadeland Road, Wea School Road"),
    place_examples='"Westminster Village", "Central Presbyterian Church", "Purdue Police Department", "LTHC", "IVH"',
    unit_examples='"Station 3", "Engine 1502", "Medic 5", "Westside Medic", "Engine 924", "Rescue 607"',
    # Whisper's local-vocabulary hints pull these toward the wrong word.
    transcript_fixes=(
        (r"\bsagamore springs\b", "Sycamore Springs"),                 # the hospital; Sagamore is the parkway
        (r"\bpark ?view (?=boulevard|blvd)", "Park East "),             # 833 Park East Blvd (Sycamore Springs)
        (r"\bwalgreens? (?=street|st\b)", "Waldron "),                 # Waldron St, Purdue fraternities
    ),
    hospitals=("IU Health Arnett", "Arnett", "Franciscan", "Franciscan East", "St. Elizabeth", "Unity",
               "Sycamore Springs"),
    examples=(
        ("TEAS, Buck Creek Fire, TEAS, Buck Creek Fire, be en route 5585 North 900 East, 5585 North 900 East, "
         "smoke detector going off in the barn, no smoke or fire seen, tone time 0705.",
         {"is_dispatch": True, "address": "5585 North 900 East", "place": None, "nature": "Fire Alarm",
          "units": [], "patient_age": None, "patient_sex": None,
          "summary": "Smoke detector sounding in barn, no fire seen."}),
        ("Medic 605 is en route to IU with one, single patient. Clear.",
         {"is_dispatch": False, "address": None, "place": None, "nature": "Other", "units": ["Medic 605"],
          "patient_age": None, "patient_sex": None, "summary": None}),
    ),
    place_aliases={
        "corec": "France A. Córdova Recreational Sports Center",
        "co rec": "France A. Córdova Recreational Sports Center",
        "cordova": "France A. Córdova Recreational Sports Center",
        "pupd": "Purdue Police Department",
        "purdue police": "Purdue Police Department",
        "purdue university police department": "Purdue Police Department",
        "pmu": "Purdue Memorial Union",
        "memorial union": "Purdue Memorial Union",
        "lthc": "LTHC Homeless Services",
        "ross ade": "Ross-Ade Stadium",
        "mackey": "Mackey Arena",
        "mackey arena": "Mackey Arena",
        "ivh": "Indiana Veterans' Home",
        "sycamore springs": "Sycamore Springs Hospital",
        "sycamore": "Sycamore Springs Hospital",
    },
    # Station numbers repeat between departments, so keys are unique and `number` is what the map shows.
    # Locations from NERIS (neris.fsri.org), Oct 2026.
    stations={
        "LFD HQ": Station(40.41787, -86.89069, "Lafayette Fire", "Lafayette Fire Headquarters", "20 N 6th St", "HQ"),
        "LFD 2": Station(40.42518, -86.87958, "Lafayette Fire", "Lafayette Station 2", "800 Erie St", "2"),
        "LFD 3": Station(40.41737, -86.87772, "Lafayette Fire", "Lafayette Station 3", "1710 South St", "3"),
        "LFD 5": Station(40.42461, -86.83914, "Lafayette Fire", "Lafayette Station 5", "750 N Creasy Ln", "5"),
        "LFD 6": Station(40.44457, -86.86574, "Lafayette Fire", "Lafayette Station 6", "2561 Schuyler Ave", "6"),
        "LFD 7": Station(40.39592, -86.87651, "Lafayette Fire", "Lafayette Station 7", "2150 S 18th St", "7"),
        "LFD 8": Station(40.36704, -86.88476, "Lafayette Fire", "Lafayette Station 8", "911 Veterans Memorial Pkwy S", "8"),
        "LFD 9": Station(40.39216, -86.81115, "Lafayette Fire", "Lafayette Station 9", "1757 Veterans Memorial Pkwy E", "9"),
        "WLFD 1": Station(40.42581, -86.90858, "West Lafayette Fire", "West Lafayette Station 1", "300 North St", "1"),
        "WLFD 2": Station(40.45176, -86.91528, "West Lafayette Fire", "West Lafayette Station 2", "531 W Navajo St", "2"),
        "WLFD 3": Station(40.46840, -86.91999, "West Lafayette Fire", "West Lafayette Station 3", "1100 Kalberer Rd", "3"),
        "PU": Station(40.42772, -86.92386, "Purdue Fire", "Purdue Fire Station", "1250 3rd St", "PU"),
        "Wea": Station(40.34486, -86.86852, "Wea Township Fire", "Wea Township Fire", "1700 Wea School Rd", "W"),
        "Wabash 1": Station(40.45947, -86.96125, "Wabash Township Fire", "Wabash Township Station 1", "2899 Klondike Rd", "1"),
        "Wabash 2": Station(40.41595, -86.96115, "Wabash Township Fire", "Wabash Township Station 2", "51 S Newman Rd", "2"),
        "Shadeland": Station(40.37105, -86.95187, "Shadeland Fire", "Shadeland Fire", "3209 S 250 W", "S"),
        "Sheffield": Station(40.37597, -86.76727, "Sheffield Fire", "Sheffield Township Fire", "285 Market St, Dayton", "S"),
        "West Point": Station(40.34524, -87.04469, "West Point Fire", "West Point Fire", "4949 State Road 25 S", "WP"),
        "Battle Ground 1": Station(40.51048, -86.83870, "Battle Ground Fire", "Tippecanoe Township Station 1",
                                   "112 North St, Battle Ground", "1"),
        "Battle Ground 2": Station(40.51139, -86.91570, "Battle Ground Fire", "Tippecanoe Township Station 2", "448 W 650 N", "2"),
        "Buck Creek": Station(40.48756, -86.76277, "Buck Creek Fire", "Buck Creek Fire", "7400 Railroad St, Buck Creek", "BC"),
        "Randolph": Station(40.25105, -86.90404, "Randolph Township Fire", "Randolph Township Fire",
                            "11508 US Highway 231 S, Romney", "R"),
        "Clarks Hill 1": Station(40.24707, -86.72565, "Clarks Hill Fire", "Clarks Hill-Lauramie Station 1",
                                 "9401 White St, Clarks Hill", "1"),
        "Clarks Hill 2": Station(40.28357, -86.77107, "Clarks Hill Fire", "Stockwell Station 2", "9220 Yorktown St, Stockwell", "2"),
    },
)
