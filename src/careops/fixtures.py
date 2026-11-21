"""Make the fake source files for the lab.

Everything here is made up: message IDs, facilities, providers and shifts.
There is no real patient or provider data anywhere in this project.
"""
from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

UTC = timezone.utc

SOURCE_FILES = (
    "messages_01.jsonl",
    "messages_02_updates.jsonl",
    "shifts_01.csv",
    "shifts_02_updates.csv",
)

SHIFT_COLUMNS = ["shift_id", "facility_id", "provider_id", "start_at", "end_at", "updated_at"]


def iso(t: datetime) -> str:
    """Format a datetime as UTC text like 2026-11-01T08:00:00Z."""
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_messages(start: datetime) -> tuple[list[dict], list[dict]]:
    """Return (first load, update batch) for the message feed."""
    first_load = []
    updates = []

    for i in range(120):
        sent = start + timedelta(minutes=17 * i)

        # Spread out the outcomes so the numbers look a bit realistic.
        if i % 13 == 0:
            status = "failed"
        elif i % 3 == 0:
            status = "responded"
        else:
            status = "delivered"

        delivered = None if status == "failed" else sent + timedelta(minutes=2 + i % 8)
        responded = sent + timedelta(minutes=4 + i % 19) if status == "responded" else None

        message = {
            "message_id": f"MSG-{i:04d}",
            "facility_id": f"FAC-{i % 3 + 1:02d}",
            "provider_id": f"PROV-{i % 9 + 1:02d}",
            "channel": ("page", "call", "secure_message")[i % 3],
            "sent_at": iso(sent),
            "delivered_at": iso(delivered) if delivered else None,
            "responded_at": iso(responded) if responded else None,
            "status": status,
            "updated_at": iso(sent + timedelta(minutes=20)),
        }
        first_load.append(message)

        # Every 11th message gets a correction two days later.
        if i % 11 == 0:
            corrected = dict(message)
            corrected["updated_at"] = iso(sent + timedelta(days=2))
            corrected["status"] = "responded"
            corrected["delivered_at"] = iso(sent + timedelta(minutes=2))
            corrected["responded_at"] = iso(sent + timedelta(minutes=9))
            updates.append(corrected)

    # Add one exact duplicate and one old record that shows up late.
    # Silver should ignore both.
    updates.append(dict(updates[0]))
    updates.append(dict(first_load[0]))
    return first_load, updates


def build_shifts(start: datetime) -> tuple[list[dict], list[dict]]:
    """Return (first load, update batch) for the shift schedule."""
    first_load = []
    updates = []

    for day in range(3):
        for k in range(9):
            shift_start = start.replace(hour=0, minute=0) + timedelta(days=day, hours=6)
            shift = {
                "shift_id": f"SHIFT-{day}-{k}",
                "facility_id": f"FAC-{k % 3 + 1:02d}",
                "provider_id": f"PROV-{k + 1:02d}",
                "start_at": iso(shift_start),
                "end_at": iso(shift_start + timedelta(hours=16)),
                "updated_at": iso(shift_start + timedelta(hours=1)),
            }
            first_load.append(shift)

            # Three first-day shifts get extended by an hour later on.
            if day == 0 and k < 3:
                extended = dict(shift)
                extended["end_at"] = iso(shift_start + timedelta(hours=17))
                extended["updated_at"] = iso(shift_start + timedelta(days=2))
                updates.append(extended)

    return first_load, updates


def seed(root: Path, force: bool = False) -> dict:
    """Write the four source files under data/source.

    Refuses to overwrite existing files unless force=True, so we don't
    wipe out inputs by accident.
    """
    source_dir = root / "data" / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    paths = [source_dir / name for name in SOURCE_FILES]

    if not force and any(p.exists() for p in paths):
        raise FileExistsError("Source files already exist. Use seed --force to replace them.")

    start = datetime(2026, 11, 1, 8, 0, tzinfo=UTC)
    messages, message_updates = build_messages(start)
    shifts, shift_updates = build_shifts(start)

    for path, rows in zip(paths[:2], [messages, message_updates]):
        lines = [json.dumps(row, sort_keys=True) + "\n" for row in rows]
        path.write_text("".join(lines), encoding="utf-8")

    for path, rows in zip(paths[2:], [shifts, shift_updates]):
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=SHIFT_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)

    return {p.name: p.stat().st_size for p in paths}
