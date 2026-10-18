"""Hamilton County, Indiana (Carmel, Fishers, Noblesville, Westfield).

Radio: MESA (formerly IDPS) System 3 simulcast, carried on OpenMHz as
"hamiltonco". Fire apparatus are numbered 3xx (often shortened on the air:
"Ladder 72" = Ladder 372), with the second digit naming
the department (RadioReference: wiki.radioreference.com/index.php/Hamilton_County_(IN)).
"""

from . import ChannelSpec, Department, Profile

FIRE = r"(?:\s+township)?\s+fire\b"

DEPARTMENTS = (
    Department("Carmel Fire", spoken="Carmel Fire", unit_prefixes=("34x", "4x"), area="Carmel / Clay Township",
               aliases=(r"\b(?:carmel|caramel|carmelle|car mel)" + FIRE,)),
    Department("Fishers Fire", spoken="Fishers Fire", unit_prefixes=("39x", "9x"), area="Fishers",
               aliases=(r"\b(?:fishers|fisher's|fisher|fischer'?s?)" + FIRE,)),
    Department("Noblesville Fire", spoken="Noblesville Fire", unit_prefixes=("37x", "7x"), area="Noblesville",
               aliases=(r"\b(?:noblesville|nobles ?ville|noble'?s ville|nobleville|noblesvill)" + FIRE,)),
    Department("Westfield Fire", spoken="Westfield Fire", unit_prefixes=("38x", "8x"), area="Westfield / Washington Township",
               aliases=(r"\b(?:west ?field|westfeld)" + FIRE,)),
    Department("Wayne Township Fire", spoken="Wayne Township Fire", unit_prefixes=("31x", "1x"), area="Wayne Township",
               aliases=(r"\bwayne township fire\b", r"\bwayne fire\b")),
    Department("Jackson Township Fire", spoken="Jackson Township Fire", unit_prefixes=("32x", "35x", "2x", "5x"),
               area="Cicero, Arcadia & Atlanta",
               aliases=(r"\bjackson township fire\b", r"\bjackson fire\b", r"\b(?:cicero|sisero|sicero)" + FIRE,
                        r"\b(?:arcadia|arcadian)" + FIRE, r"\batlanta" + FIRE)),
    Department("White River Township Fire", spoken="White River Fire", unit_prefixes=("33x", "3x"),
               area="White River Township",
               aliases=(r"\bwhite river" + FIRE,)),
    Department("Sheridan Fire", spoken="Sheridan Fire", unit_prefixes=("36x", "6x"), area="Sheridan / Adams Township",
               aliases=(r"\b(?:sheridan|sharidan|sheriden)" + FIRE,)),
)

PROFILE = Profile(
    key="hamilton",
    name="Hamilton County",
    state="IN",
    title="Hamilton County Dispatch Monitor",
    subtitle="Fire & EMS dispatch · Carmel · Fishers · Noblesville · Westfield",
    fips="18057",
    bbox=(39.91, -86.25, 40.25, -85.85),
    center=(40.03, -86.05),
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
    },
    whisper_prompt=(
        "Hamilton County fire and EMS dispatch, Carmel, Fishers, Noblesville, Westfield, Cicero, Sheridan, Indiana. "
        "Carmel Fire, Fishers Fire, Noblesville Fire, Westfield Fire, Wayne Township, White River Township, "
        "Jackson Township, Sheridan Fire. Medic 341, Engine 391, Ladder 381, Battalion 370, Squad 343. "
        "Medical run, respond, tone time. Meridian Street, Keystone Parkway, 116th Street, 146th Street, "
        "96th Street, Allisonville Road, State Road 37, State Road 32, Spring Mill Road, Hazel Dell Parkway, "
        "Olio Road, Range Line Road, Grand Park, Riverview Health, Clay Terrace, Hamilton Town Center."
    ),
    street_hints=("Meridian Street, Keystone Parkway, 116th Street, 131st Street, 146th Street, 96th Street, "
                  "Allisonville Road, State Road 37, State Road 32, State Road 38, Spring Mill Road, Hazel Dell "
                  "Parkway, Olio Road, Range Line Road, Rangeline, Carey Road, Gray Road, Hague Road, Cumberland Road"),
    place_examples='"Grand Park", "Riverview Health", "Ruoff Music Center", "Hamilton Town Center", "Clay Terrace"',
    unit_examples='"Medic 341", "Engine 391", "Ladder 381", "Battalion 370"',
    place_aliases={
        "riverview": "Riverview Health",
        "ruoff": "Ruoff Music Center",
        "deer creek": "Ruoff Music Center",
        "grand park": "Grand Park Sports Campus",
    },
)
