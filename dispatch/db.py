"""SQLite storage. One connection per thread; WAL lets the API read while the
worker writes."""

import sqlite3
import threading

from . import config

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS transmissions (
    id           TEXT PRIMARY KEY,          -- OpenMHz call _id
    talkgroup    INTEGER NOT NULL,
    agency       TEXT,
    time_ms      INTEGER NOT NULL,          -- UTC epoch ms
    len_s        INTEGER,
    url          TEXT,
    status       TEXT NOT NULL DEFAULT 'pending',  -- pending|done|short|error
    attempts     INTEGER NOT NULL DEFAULT 0,
    transcript   TEXT,                      -- with misheard names fixed (what the LLM read)
    method       TEXT,                      -- llm|rules|skipped
    extracted    TEXT,                      -- JSON
    is_dispatch  INTEGER,
    incident_id  INTEGER,
    audio_path   TEXT,
    error        TEXT,
    created_at   TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS tx_status ON transmissions(status, time_ms);
CREATE INDEX IF NOT EXISTS tx_time ON transmissions(time_ms);
CREATE INDEX IF NOT EXISTS tx_incident ON transmissions(incident_id);

CREATE TABLE IF NOT EXISTS incidents (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    first_ms     INTEGER NOT NULL,
    last_ms      INTEGER NOT NULL,
    local_date   TEXT NOT NULL,             -- YYYY-MM-DD in county local time
    local_hour   INTEGER NOT NULL,          -- 0-23
    local_dow    INTEGER NOT NULL,          -- 0=Mon .. 6=Sun
    address      TEXT,                      -- as heard (cleaned)
    place        TEXT,
    matched      TEXT,                      -- geocoder's canonical address
    lat          REAL,
    lon          REAL,
    precision    TEXT,                      -- address|intersection|street|place
    geo_source   TEXT,
    zip          TEXT,
    category     TEXT NOT NULL,
    nature       TEXT NOT NULL,
    agencies     TEXT NOT NULL DEFAULT '[]',  -- departments dispatched ("TEAS", "Wea Township Fire")
    units        TEXT NOT NULL DEFAULT '[]',
    patient_age  INTEGER,
    patient_sex  TEXT,
    summary      TEXT,
    n_tx         INTEGER NOT NULL DEFAULT 1,
    method       TEXT
);
CREATE INDEX IF NOT EXISTS inc_time ON incidents(first_ms);
CREATE INDEX IF NOT EXISTS inc_cat ON incidents(category, first_ms);
CREATE INDEX IF NOT EXISTS inc_addr ON incidents(matched);

CREATE TABLE IF NOT EXISTS geocache (key TEXT PRIMARY KEY, result TEXT);
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT);
-- Places learned from earlier calls: "Independence Village" on a Fishers call was
-- 12950 Talblick St, so the next Fishers call naming it can be placed even if the
-- street is misheard. Keyed by department: Carmel has its own Independence Village.
-- Corrections made in the dashboard (see corrections.py). Rows with a transcript
-- are worked call-type examples for the LLM; rows without are the active fix
-- for one field of one call, matched to its transmissions.
CREATE TABLE IF NOT EXISTS corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT, tx_ids TEXT NOT NULL, field TEXT NOT NULL, value TEXT,
    transcript TEXT, extracted TEXT, created_ms INTEGER
);
CREATE TABLE IF NOT EXISTS word_fixes (heard TEXT PRIMARY KEY, actual TEXT NOT NULL, created_ms INTEGER);
-- Unit numbers a correction showed belong to a department ("Battalion 304" -> Carmel Fire)
CREATE TABLE IF NOT EXISTS unit_owners (number TEXT PRIMARY KEY, dept TEXT NOT NULL, created_ms INTEGER);
CREATE TABLE IF NOT EXISTS learned_places (
    name TEXT NOT NULL, dept TEXT NOT NULL, lat REAL NOT NULL, lon REAL NOT NULL,
    matched TEXT, n INTEGER NOT NULL DEFAULT 1, updated_ms INTEGER,
    PRIMARY KEY (name, dept)
);
"""


def conn() -> sqlite3.Connection:
    c = getattr(_local, "conn", None)
    if c is None:
        config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(config.DB_PATH, timeout=30, isolation_level=None)  # autocommit
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("PRAGMA busy_timeout=30000")
        _local.conn = c
    return c


# Columns added after the first release: (table, column, type)
MIGRATIONS = [
    ("transmissions", "transcript_raw", "TEXT"),   # Whisper output before name fixes
    ("incidents", "confidence", "TEXT"),           # confirmed | high | approximate | doubtful
    ("incidents", "geo_tries", "INTEGER NOT NULL DEFAULT 0"),   # background location retries
    ("incidents", "corrected", "TEXT NOT NULL DEFAULT ''"),     # fields fixed in the dashboard (locked)
]


def init():
    c = conn()
    c.executescript(SCHEMA)
    for table, col, typ in MIGRATIONS:
        cols = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
        if col not in cols:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")


def get_state(key: str, default=None):
    row = conn().execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def set_state(key: str, value):
    conn().execute("INSERT OR REPLACE INTO state(key, value) VALUES (?, ?)", (key, str(value)))
