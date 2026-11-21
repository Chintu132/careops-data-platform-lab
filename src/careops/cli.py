"""Command line for the lab: python run.py <command>"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from .exports import export
from .fixtures import seed
from .ingest import ingest_all
from .quality import validate
from .s3 import publish
from .storage import connect

COMMANDS = ["seed", "run", "validate", "report", "export", "replay-check", "demo", "publish-s3"]


def fingerprint(root: Path) -> str:
    """Hash the contents of Silver and Gold, so we can tell if a rerun changed anything."""
    snapshot = []
    with connect(root) as conn:
        for table in ("silver_messages", "silver_shifts", "gold_fact_message", "gold_daily_facility"):
            rows = conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
            snapshot.append([table, [tuple(row) for row in rows]])
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()


def report(root: Path) -> dict:
    """A few headline numbers to show the pipeline did what we expect."""
    queries = {
        "bronze_message_versions": "SELECT COUNT(*) FROM bronze_messages",
        "silver_unique_messages": "SELECT COUNT(*) FROM silver_messages",
        "bronze_shift_versions": "SELECT COUNT(*) FROM bronze_shifts",
        "silver_unique_shifts": "SELECT COUNT(*) FROM silver_shifts",
        "daily_metric_rows": "SELECT COUNT(*) FROM gold_daily_facility",
        "delivery_pct_overall": "SELECT ROUND(100.0 * SUM(delivered_flag) / COUNT(*), 2) FROM gold_fact_message",
        "response_pct_overall": "SELECT ROUND(100.0 * SUM(responded_flag) / COUNT(*), 2) FROM gold_fact_message",
        "on_shift_messages": "SELECT SUM(on_shift_at_send) FROM gold_fact_message",
    }
    with connect(root) as conn:
        return {name: conn.execute(sql).fetchone()[0] for name, sql in queries.items()}


def show(label: str, value) -> None:
    print(f"{label}: {json.dumps(value, indent=2)}")


def replay_check(root: Path) -> None:
    """Load the same files again and confirm nothing changes."""
    before = fingerprint(root)
    results = ingest_all(root)
    after = fingerprint(root)

    if before != after or any(not r["skipped"] for r in results):
        raise AssertionError("Replay changed the warehouse or reloaded a file")
    print(f"PASS: same inputs replayed with no changes (fingerprint {after})")


def main(argv=None, root: Path | None = None) -> None:
    parser = argparse.ArgumentParser(prog="careops", description="CareOps data platform lab (synthetic data)")
    parser.add_argument("command", choices=COMMANDS)
    parser.add_argument("--force", action="store_true", help="seed only: replace existing source files")
    parser.add_argument("--bucket", help="publish-s3 only: S3 bucket name")
    parser.add_argument("--prefix", default="careops", help="publish-s3 only: folder prefix in the bucket")
    parser.add_argument("--confirm", action="store_true", help="publish-s3 only: really upload (may cost money)")
    args = parser.parse_args(argv)

    root = Path(root or Path(__file__).resolve().parents[2]).resolve()
    cmd = args.command

    if cmd == "seed":
        show("SEED", seed(root, force=args.force))
    elif cmd == "run":
        show("INGEST", ingest_all(root))
    elif cmd == "validate":
        show("QUALITY", validate(root))
    elif cmd == "report":
        show("REPORT", report(root))
    elif cmd == "export":
        show("EXPORT", export(root))
    elif cmd == "replay-check":
        replay_check(root)
    elif cmd == "demo":
        # Seed only if there are no inputs yet, then run every step.
        try:
            show("SEED", seed(root))
        except FileExistsError:
            print("SEED: source files already exist, keeping them")
        show("INGEST", ingest_all(root))
        show("QUALITY", validate(root))
        show("REPORT", report(root))
        show("EXPORT", export(root))
    elif cmd == "publish-s3":
        if not args.bucket:
            parser.error("publish-s3 needs --bucket BUCKET")
        paths = publish(root, args.bucket, args.prefix, args.confirm)
        show("UPLOADED" if args.confirm else "DRY RUN (nothing uploaded)", paths)
