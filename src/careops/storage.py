"""Local SQLite warehouse: table definitions and Gold views.

SQLite keeps the lab free and easy to run on any laptop. It is a stand-in
for a real cloud warehouse, not a replacement for one.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

# Bronze keeps every version we receive. Silver keeps one current row per ID.
SCHEMA = """
CREATE TABLE IF NOT EXISTS ingestion_manifest (
    id          INTEGER PRIMARY KEY,
    domain      TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    filename    TEXT NOT NULL,
    row_count   INTEGER NOT NULL,
    loaded_at   TEXT NOT NULL,
    UNIQUE (domain, source_hash)
);

CREATE TABLE IF NOT EXISTS bronze_messages (
    id                INTEGER PRIMARY KEY,
    manifest_id       INTEGER NOT NULL REFERENCES ingestion_manifest (id),
    file_name         TEXT NOT NULL,
    line_no           INTEGER NOT NULL,
    message_id        TEXT NOT NULL,
    source_updated_at TEXT NOT NULL,
    raw_json          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bronze_shifts (
    id                INTEGER PRIMARY KEY,
    manifest_id       INTEGER NOT NULL REFERENCES ingestion_manifest (id),
    file_name         TEXT NOT NULL,
    line_no           INTEGER NOT NULL,
    shift_id          TEXT NOT NULL,
    source_updated_at TEXT NOT NULL,
    raw_json          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS silver_messages (
    message_id        TEXT PRIMARY KEY,
    facility_id       TEXT NOT NULL,
    provider_id       TEXT NOT NULL,
    channel           TEXT NOT NULL,
    sent_at           TEXT NOT NULL,
    delivered_at      TEXT,
    responded_at      TEXT,
    status            TEXT NOT NULL,
    source_updated_at TEXT NOT NULL,
    record_hash       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS silver_shifts (
    shift_id          TEXT PRIMARY KEY,
    facility_id       TEXT NOT NULL,
    provider_id       TEXT NOT NULL,
    start_at          TEXT NOT NULL,
    end_at            TEXT NOT NULL,
    source_updated_at TEXT NOT NULL,
    record_hash       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_facility_time ON silver_messages (facility_id, sent_at);
CREATE INDEX IF NOT EXISTS idx_shifts_provider ON silver_shifts (provider_id, start_at, end_at);
"""

# Gold is plain views on top of Silver, so it always matches Silver exactly.
VIEWS = """
DROP VIEW IF EXISTS gold_daily_facility;
DROP VIEW IF EXISTS gold_fact_message;
DROP VIEW IF EXISTS gold_fact_shift;
DROP VIEW IF EXISTS gold_dim_facility;
DROP VIEW IF EXISTS gold_dim_channel;
DROP VIEW IF EXISTS gold_dim_date;

CREATE VIEW gold_dim_facility AS
SELECT facility_id FROM silver_messages
UNION
SELECT facility_id FROM silver_shifts;

CREATE VIEW gold_dim_channel AS
SELECT DISTINCT channel FROM silver_messages;

CREATE VIEW gold_dim_date AS
SELECT DISTINCT SUBSTR(sent_at, 1, 10) AS calendar_date FROM silver_messages
UNION
SELECT DISTINCT SUBSTR(start_at, 1, 10) FROM silver_shifts;

-- One row per shift.
CREATE VIEW gold_fact_shift AS
SELECT shift_id,
       facility_id,
       provider_id,
       start_at,
       end_at,
       ROUND((JULIANDAY(end_at) - JULIANDAY(start_at)) * 24, 2) AS shift_hours
FROM silver_shifts;

-- One row per message. We use EXISTS for the on-shift flag instead of a join,
-- so a message never gets counted twice when shifts overlap.
CREATE VIEW gold_fact_message AS
SELECT m.message_id,
       m.facility_id,
       m.provider_id,
       m.channel,
       SUBSTR(m.sent_at, 1, 10) AS calendar_date,
       m.sent_at,
       m.delivered_at,
       m.responded_at,
       m.status,
       CASE WHEN m.delivered_at IS NOT NULL THEN 1 ELSE 0 END AS delivered_flag,
       CASE WHEN m.responded_at IS NOT NULL THEN 1 ELSE 0 END AS responded_flag,
       CASE WHEN m.responded_at IS NOT NULL
            THEN ROUND((JULIANDAY(m.responded_at) - JULIANDAY(m.sent_at)) * 24 * 60, 2)
       END AS response_minutes,
       CASE WHEN EXISTS (
                SELECT 1
                FROM silver_shifts s
                WHERE s.facility_id = m.facility_id
                  AND s.provider_id = m.provider_id
                  AND s.start_at <= m.sent_at
                  AND s.end_at > m.sent_at
            ) THEN 1 ELSE 0 END AS on_shift_at_send
FROM silver_messages m;

-- One row per date + facility + channel.
CREATE VIEW gold_daily_facility AS
SELECT calendar_date,
       facility_id,
       channel,
       COUNT(*) AS messages_sent,
       SUM(delivered_flag) AS messages_delivered,
       SUM(responded_flag) AS messages_responded,
       ROUND(100.0 * SUM(delivered_flag) / COUNT(*), 2) AS delivery_pct,
       ROUND(100.0 * SUM(responded_flag) / COUNT(*), 2) AS response_pct,
       ROUND(AVG(response_minutes), 2) AS avg_response_minutes,
       SUM(on_shift_at_send) AS on_shift_messages
FROM gold_fact_message
GROUP BY calendar_date, facility_id, channel;
"""


@contextmanager
def connect(root: Path):
    """Open the warehouse file, make sure tables and views exist, then close it after."""
    db_path = root / "data" / "warehouse.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.executescript(SCHEMA)
    conn.executescript(VIEWS)
    try:
        yield conn
    finally:
        conn.close()
