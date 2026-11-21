"""Data-quality checks on the warehouse.

Each check is a SQL query that should return 0. Anything else is a failure.
"""
from __future__ import annotations

from pathlib import Path

from .storage import connect


class DataQualityError(RuntimeError):
    pass


CHECKS = {
    # Gold should never be empty after a load.
    "empty_message_facts": "SELECT COUNT(*) = 0 FROM gold_fact_message",
    "empty_shift_facts": "SELECT COUNT(*) = 0 FROM gold_fact_shift",

    # Silver must have one row per ID.
    "duplicate_message_ids": """
        SELECT COUNT(*) FROM (
            SELECT message_id FROM silver_messages GROUP BY message_id HAVING COUNT(*) > 1
        )""",
    "duplicate_shift_ids": """
        SELECT COUNT(*) FROM (
            SELECT shift_id FROM silver_shifts GROUP BY shift_id HAVING COUNT(*) > 1
        )""",

    # Basic row sanity.
    "invalid_messages": """
        SELECT COUNT(*) FROM silver_messages
        WHERE message_id IS NULL OR facility_id IS NULL OR sent_at IS NULL""",
    "invalid_shifts": "SELECT COUNT(*) FROM silver_shifts WHERE end_at <= start_at",
    "negative_response_minutes": "SELECT COUNT(*) FROM gold_fact_message WHERE response_minutes < 0",

    # Every fact row needs a matching dimension row.
    "missing_facility_dimension": """
        SELECT COUNT(*) FROM gold_fact_message f
        LEFT JOIN gold_dim_facility d USING (facility_id)
        WHERE d.facility_id IS NULL""",
    "missing_channel_dimension": """
        SELECT COUNT(*) FROM gold_fact_message f
        LEFT JOIN gold_dim_channel d USING (channel)
        WHERE d.channel IS NULL""",

    # The daily summary has to add back up to the message-level facts.
    "daily_fact_reconciliation": """
        SELECT ABS(
            (SELECT COUNT(*) FROM gold_fact_message)
          - (SELECT COALESCE(SUM(messages_sent), 0) FROM gold_daily_facility)
        )""",
    "invalid_delivery_counts": """
        SELECT COUNT(*) FROM gold_daily_facility
        WHERE messages_delivered > messages_sent
           OR messages_responded > messages_delivered""",
}


def run_checks(root: Path) -> dict[str, int]:
    """Run every check and return the result of each one."""
    with connect(root) as conn:
        return {name: int(conn.execute(sql).fetchone()[0]) for name, sql in CHECKS.items()}


def validate(root: Path) -> dict[str, int]:
    """Run the checks and raise an error if any of them fail."""
    results = run_checks(root)
    failures = {name: value for name, value in results.items() if value != 0}
    if failures:
        raise DataQualityError(f"Data-quality checks failed: {failures}")
    return results
