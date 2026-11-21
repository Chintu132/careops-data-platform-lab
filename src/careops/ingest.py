"""Load source files into Bronze and Silver.

How a rerun stays safe:
1. Each file's content gets a SHA-256 hash. If we've loaded that exact
   content before, we skip the file.
2. Every row is checked before we write anything. One bad row stops
   the whole file.
3. Bronze, Silver and the manifest row are written in one transaction.
   If anything fails, all of it rolls back.
4. Silver only takes a row if its updated_at is newer than what we have,
   so an old record that arrives late can't overwrite a newer one.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .storage import connect

VALID_CHANNELS = {"page", "call", "secure_message"}
VALID_STATUSES = {"sent", "delivered", "responded", "failed"}

MESSAGE_FIELDS = ["message_id", "facility_id", "provider_id", "channel", "sent_at", "status", "updated_at"]
SHIFT_FIELDS = ["shift_id", "facility_id", "provider_id", "start_at", "end_at", "updated_at"]

# Upserts: only replace the Silver row when the incoming version is newer.
# If two versions have the same timestamp, the record hash breaks the tie
# so reruns always land on the same row.
UPSERT_MESSAGE = """
INSERT INTO silver_messages (
    message_id, facility_id, provider_id, channel, sent_at,
    delivered_at, responded_at, status, source_updated_at, record_hash
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (message_id) DO UPDATE SET
    facility_id       = excluded.facility_id,
    provider_id       = excluded.provider_id,
    channel           = excluded.channel,
    sent_at           = excluded.sent_at,
    delivered_at      = excluded.delivered_at,
    responded_at      = excluded.responded_at,
    status            = excluded.status,
    source_updated_at = excluded.source_updated_at,
    record_hash       = excluded.record_hash
WHERE excluded.source_updated_at > silver_messages.source_updated_at
   OR (excluded.source_updated_at = silver_messages.source_updated_at
       AND excluded.record_hash > silver_messages.record_hash)
"""

UPSERT_SHIFT = """
INSERT INTO silver_shifts (
    shift_id, facility_id, provider_id, start_at, end_at, source_updated_at, record_hash
)
VALUES (?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (shift_id) DO UPDATE SET
    facility_id       = excluded.facility_id,
    provider_id       = excluded.provider_id,
    start_at          = excluded.start_at,
    end_at            = excluded.end_at,
    source_updated_at = excluded.source_updated_at,
    record_hash       = excluded.record_hash
WHERE excluded.source_updated_at > silver_shifts.source_updated_at
   OR (excluded.source_updated_at = silver_shifts.source_updated_at
       AND excluded.record_hash > silver_shifts.record_hash)
"""


def check_utc(value) -> str:
    """Make sure a timestamp is UTC text ending in Z, and return it in one standard format."""
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"Timestamp must be UTC and end with Z: {value!r}")
    t = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def require_fields(row: dict, fields: list[str], domain: str) -> None:
    """Fail if any required field is missing or blank."""
    for field in fields:
        value = row.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Missing or empty {domain} field: {field}")


def validate_message(row: dict) -> dict:
    require_fields(row, MESSAGE_FIELDS, "message")

    row["sent_at"] = check_utc(row["sent_at"])
    row["updated_at"] = check_utc(row["updated_at"])
    for field in ("delivered_at", "responded_at"):
        row[field] = check_utc(row[field]) if row.get(field) else None

    if row["channel"] not in VALID_CHANNELS:
        raise ValueError(f"Unknown channel: {row['channel']}")
    if row["status"] not in VALID_STATUSES:
        raise ValueError(f"Unknown status: {row['status']}")

    # Simple time-order rules. ISO text in the same format sorts like time does.
    if row["delivered_at"] and row["delivered_at"] < row["sent_at"]:
        raise ValueError("Delivered before sent")
    if row["responded_at"] and (not row["delivered_at"] or row["responded_at"] < row["sent_at"]):
        raise ValueError("Response time is not valid")
    if row["status"] == "responded" and not row["responded_at"]:
        raise ValueError("Status is responded but there is no response time")
    if row["status"] == "failed" and (row["delivered_at"] or row["responded_at"]):
        raise ValueError("Failed message has a delivery or response time")
    return row


def validate_shift(row: dict) -> dict:
    require_fields(row, SHIFT_FIELDS, "shift")

    for field in ("start_at", "end_at", "updated_at"):
        row[field] = check_utc(row[field])

    if row["end_at"] <= row["start_at"]:
        raise ValueError("Shift end must be after shift start")
    return row


def parse_file(path: Path, domain: str) -> list[dict]:
    """Read a whole file and validate every row before anything is loaded."""
    text = path.read_text(encoding="utf-8")

    if domain == "messages":
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
        return [validate_message(row) for row in rows]

    if domain == "shifts":
        reader = csv.DictReader(io.StringIO(text))
        missing = set(SHIFT_FIELDS) - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path.name} is missing columns: {sorted(missing)}")
        return [validate_shift(row) for row in reader]

    raise ValueError(f"Unknown domain: {domain}")


def record_hash(row: dict) -> tuple[str, str]:
    """Return the row as sorted JSON text, plus its SHA-256 hash."""
    normalized = json.dumps(row, sort_keys=True, separators=(",", ":"))
    return normalized, hashlib.sha256(normalized.encode()).hexdigest()


def load_file(conn: sqlite3.Connection, path: Path, domain: str) -> dict:
    """Load one file. Returns what happened so the CLI can print it."""
    file_hash = hashlib.sha256(path.read_bytes()).hexdigest()

    already_loaded = conn.execute(
        "SELECT id FROM ingestion_manifest WHERE domain = ? AND source_hash = ?",
        (domain, file_hash),
    ).fetchone()
    if already_loaded:
        return {"file": path.name, "domain": domain, "loaded": 0, "skipped": True}

    rows = parse_file(path, domain)

    try:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            "INSERT INTO ingestion_manifest (domain, source_hash, filename, row_count, loaded_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (domain, file_hash, path.name, len(rows), datetime.now(timezone.utc).isoformat()),
        )
        manifest_id = cursor.lastrowid

        for line_no, row in enumerate(rows, start=1):
            raw_json, row_hash = record_hash(row)

            if domain == "messages":
                conn.execute(
                    "INSERT INTO bronze_messages "
                    "(manifest_id, file_name, line_no, message_id, source_updated_at, raw_json) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (manifest_id, path.name, line_no, row["message_id"], row["updated_at"], raw_json),
                )
                conn.execute(UPSERT_MESSAGE, (
                    row["message_id"], row["facility_id"], row["provider_id"], row["channel"],
                    row["sent_at"], row["delivered_at"], row["responded_at"], row["status"],
                    row["updated_at"], row_hash,
                ))
            else:
                conn.execute(
                    "INSERT INTO bronze_shifts "
                    "(manifest_id, file_name, line_no, shift_id, source_updated_at, raw_json) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (manifest_id, path.name, line_no, row["shift_id"], row["updated_at"], raw_json),
                )
                conn.execute(UPSERT_SHIFT, (
                    row["shift_id"], row["facility_id"], row["provider_id"],
                    row["start_at"], row["end_at"], row["updated_at"], row_hash,
                ))

        conn.commit()
    except Exception:
        conn.rollback()
        raise

    return {"file": path.name, "domain": domain, "loaded": len(rows), "skipped": False}


def find_inputs(source_dir: Path) -> list[tuple[Path, str]]:
    """List source files in name order so loads always happen in the same order."""
    inputs = [(p, "messages") for p in source_dir.glob("messages_*.jsonl")]
    inputs += [(p, "shifts") for p in source_dir.glob("shifts_*.csv")]
    return sorted(inputs, key=lambda item: item[0].name)


def ingest_all(root: Path) -> list[dict]:
    source_dir = root / "data" / "source"
    inputs = find_inputs(source_dir) if source_dir.exists() else []
    if not inputs:
        raise FileNotFoundError("No source files found. Run `python run.py seed` first.")

    with connect(root) as conn:
        return [load_file(conn, path, domain) for path, domain in inputs]
