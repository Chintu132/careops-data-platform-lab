"""Tests for the pipeline. Run with: python -m unittest discover -s tests -v

Each test gets its own temp folder with fresh source files, so tests
never touch each other or the real data folder.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from careops.cli import fingerprint  # noqa: E402
from careops.exports import export  # noqa: E402
from careops.fixtures import seed  # noqa: E402
from careops.ingest import ingest_all  # noqa: E402
from careops.quality import DataQualityError, validate  # noqa: E402
from careops.s3 import publish  # noqa: E402
from careops.storage import connect  # noqa: E402


def count(root: Path, sql: str) -> int:
    with connect(root) as conn:
        return conn.execute(sql).fetchone()[0]


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / "data" / "source"
        seed(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_counts_and_quality(self):
        results = ingest_all(self.root)

        self.assertEqual(len(results), 4)
        # 120 messages + 13 updates (11 corrections, 1 duplicate, 1 late old record)
        # + 27 shifts + 3 shift updates
        self.assertEqual(sum(r["loaded"] for r in results), 120 + 13 + 27 + 3)
        self.assertTrue(all(v == 0 for v in validate(self.root).values()))
        self.assertEqual(count(self.root, "SELECT COUNT(*) FROM silver_messages"), 120)
        self.assertEqual(count(self.root, "SELECT COUNT(*) FROM silver_shifts"), 27)
        self.assertEqual(count(self.root, "SELECT COUNT(*) FROM gold_fact_message"), 120)

    def test_rerun_changes_nothing(self):
        ingest_all(self.root)
        first = fingerprint(self.root)

        second_run = ingest_all(self.root)

        self.assertTrue(all(r["skipped"] for r in second_run))
        self.assertEqual(first, fingerprint(self.root))

    def test_newer_update_wins_over_late_old_record(self):
        ingest_all(self.root)

        with connect(self.root) as conn:
            row = conn.execute(
                "SELECT status, source_updated_at FROM silver_messages WHERE message_id = 'MSG-0000'"
            ).fetchone()
        self.assertEqual(row["status"], "responded")
        self.assertTrue(row["source_updated_at"].startswith("2026-11-03"))
        # Bronze still keeps all 4 versions we received for this message.
        self.assertEqual(count(self.root, "SELECT COUNT(*) FROM bronze_messages WHERE message_id = 'MSG-0000'"), 4)

    def test_bad_message_file_changes_nothing(self):
        ingest_all(self.root)
        before = fingerprint(self.root)

        bad = {"message_id": "BROKEN", "facility_id": "FAC-1", "provider_id": "PROV-1",
               "channel": "page", "sent_at": "bad", "status": "delivered", "updated_at": "bad"}
        (self.source / "messages_bad.jsonl").write_text(json.dumps(bad) + "\n")

        with self.assertRaises(ValueError):
            ingest_all(self.root)
        self.assertEqual(before, fingerprint(self.root))

    def test_bad_shift_row_changes_nothing(self):
        ingest_all(self.root)
        before = fingerprint(self.root)

        # Shift that ends before it starts. The first row is fine, so this also
        # checks we don't load half a file.
        (self.source / "shifts_03_bad.csv").write_text(
            "shift_id,facility_id,provider_id,start_at,end_at,updated_at\n"
            "SHIFT-OK,FAC-01,PROV-01,2026-11-05T06:00:00Z,2026-11-05T14:00:00Z,2026-11-05T07:00:00Z\n"
            "SHIFT-BAD,FAC-01,PROV-01,2026-11-05T06:00:00Z,2026-11-05T05:00:00Z,2026-11-05T07:00:00Z\n"
        )

        with self.assertRaises(ValueError):
            ingest_all(self.root)
        self.assertEqual(before, fingerprint(self.root))
        self.assertEqual(count(self.root, "SELECT COUNT(*) FROM silver_shifts WHERE shift_id = 'SHIFT-OK'"), 0)

    def test_csv_missing_column_is_rejected(self):
        ingest_all(self.root)

        # No end_at column at all.
        (self.source / "shifts_03_missing_col.csv").write_text(
            "shift_id,facility_id,provider_id,start_at,updated_at\n"
            "SHIFT-X,FAC-01,PROV-01,2026-11-05T06:00:00Z,2026-11-05T07:00:00Z\n"
        )

        with self.assertRaisesRegex(ValueError, "missing columns"):
            ingest_all(self.root)

    def test_quality_check_catches_bad_data(self):
        ingest_all(self.root)

        # Break Silver on purpose, the way a bug in a later change might.
        with connect(self.root) as conn:
            conn.execute("UPDATE silver_shifts SET end_at = start_at WHERE shift_id = 'SHIFT-0-0'")
            conn.commit()

        with self.assertRaises(DataQualityError) as caught:
            validate(self.root)
        self.assertIn("invalid_shifts", str(caught.exception))

    def test_export_writes_files(self):
        ingest_all(self.root)

        counts = export(self.root)

        self.assertEqual(counts["gold_fact_message"], 120)
        self.assertTrue((self.root / "data" / "out" / "manifest.json").exists())
        self.assertTrue((self.root / "data" / "out" / "gold_daily_facility.csv").exists())


class S3DryRunTests(unittest.TestCase):
    def test_dry_run_needs_no_aws(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            seed(root)
            ingest_all(root)

            paths = publish(root, "my-practice-bucket", confirm=False)

            self.assertEqual(len(paths), 7)
            self.assertTrue(all(p.startswith("s3://my-practice-bucket/careops/") for p in paths))


if __name__ == "__main__":
    unittest.main()
