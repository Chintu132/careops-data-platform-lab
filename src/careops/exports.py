"""Export the Gold tables to CSV, plus a manifest of what went into them."""
from __future__ import annotations

import csv
import json
import os
import tempfile
from pathlib import Path

from .quality import validate
from .storage import connect

GOLD_TABLES = (
    "gold_dim_facility",
    "gold_dim_channel",
    "gold_dim_date",
    "gold_fact_message",
    "gold_fact_shift",
    "gold_daily_facility",
)


def export(root: Path) -> dict[str, int]:
    # Don't export anything if the data fails its checks.
    validate(root)

    out_dir = root / "data" / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    counts = {}

    with connect(root) as conn:
        for table in GOLD_TABLES:
            cursor = conn.execute(f"SELECT * FROM {table} ORDER BY 1")
            columns = [col[0] for col in cursor.description]
            rows = cursor.fetchall()

            # Write to a temp file first, then swap it in. That way a crash
            # halfway through never leaves a half-written CSV behind.
            with tempfile.NamedTemporaryFile("w", newline="", encoding="utf-8", dir=out_dir, delete=False) as f:
                writer = csv.writer(f)
                writer.writerow(columns)
                writer.writerows(rows)
                temp_path = f.name
            os.replace(temp_path, out_dir / f"{table}.csv")
            counts[table] = len(rows)

        loaded_files = [
            dict(row)
            for row in conn.execute(
                "SELECT domain, source_hash, filename, row_count, loaded_at "
                "FROM ingestion_manifest ORDER BY domain, filename"
            )
        ]

    manifest = {"dataset": "synthetic_careops", "record_counts": counts, "input_manifest": loaded_files}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return counts
