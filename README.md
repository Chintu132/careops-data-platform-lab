# CareOps Data Platform Lab

A small data pipeline I built to practice the parts of data engineering that usually go wrong in real life: files that get re-sent, records that get corrected later, old data that shows up late, and bad rows that shouldn't break everything.

The data is fake. It's made-up healthcare operations data: messages sent to providers (pages, calls, secure messages) and provider shift schedules. There are no real patients, providers or facilities anywhere in here.

## What it does

```
messages (JSONL) ─┐
                  ├─> Bronze (every version, never changed)
shifts (CSV) ─────┘        │
                           v
                    Silver (one current row per ID)
                           │
                           v
                    Gold (fact + dimension views, daily summary)
                           │
                           v
                    Quality checks ──> CSV exports ──> S3 (optional, not done yet)
```

- **Bronze** keeps every row exactly as received, including duplicates and old versions. That's the audit trail.
- **Silver** keeps one row per message or shift. A newer `updated_at` replaces the old row; an older one is ignored.
- **Gold** is SQL views: a message fact, a shift fact, small dimensions, and a daily summary by facility and channel.
- **Quality checks** are SQL queries that must return 0 (no duplicate IDs, no shifts ending before they start, daily totals that add back up to the facts, and so on). Exports don't run if any check fails.

## Running it

Needs Python 3.10 or newer. No packages to install for the local pipeline.

```bash
python run.py demo           # make sample data if missing, load it, check it, export it
python run.py replay-check   # load the same files again and prove nothing changes
python scripts/check.py      # everything: compile, unit tests, demo, replay check
```

Single steps if you want to go one at a time:

```bash
python run.py seed       # write the sample source files (add --force to overwrite)
python run.py run        # load source files into Bronze and Silver
python run.py validate   # run the quality checks
python run.py report     # print a few headline numbers
python run.py export     # write Gold tables to data/out/ as CSV
```

The warehouse is a local SQLite file at `data/warehouse.db`. Delete it to start fresh.

## What I wanted to get right

**Reruns are safe.** Each file's content is hashed. If the same content comes in again, it's skipped. Running the pipeline twice gives the exact same result, and `replay-check` proves it by comparing a hash of Silver and Gold before and after.

**Late and corrected data.** The second message file has 11 corrections, one exact duplicate and one old record that arrives late. Silver ends up with the corrections and ignores the other two. Bronze keeps all of them.

**All or nothing per file.** Every row is validated before anything is written, and the write happens in one transaction. A file with one bad row loads nothing.

**No double counting.** The "was the provider on shift?" flag uses `EXISTS` instead of a join, so overlapping shifts can't duplicate a message row.

## Results from a clean run

| | Count |
|---|---|
| Message rows received (Bronze) | 133 |
| Unique messages (Silver) | 120 |
| Shift rows received (Bronze) | 30 |
| Unique shifts (Silver) | 27 |
| Quality checks passing | 11 of 11 |
| Unit tests passing | 9 of 9 |

## Tests

`tests/test_pipeline.py` covers:

- row counts and quality checks after a normal load
- rerunning the same files changes nothing
- a newer correction wins over an old record that arrives late
- a bad message file rolls back completely
- a shift file with one bad row loads nothing (not even the good row)
- a CSV with a missing column is rejected
- a quality check fails when the data is broken on purpose
- exports get written
- the S3 dry run works without any AWS credentials

## Limits

This is a learning project, not a production system.

- SQLite stands in for a cloud warehouse.
- Change tracking is by file hash and `updated_at`, not real CDC from a database log.
- Ties on `updated_at` are broken by a hash so reruns are stable. That keeps results repeatable, but it doesn't prove which version is really newer.
- No scheduler, retries or alerting yet.

## Next steps

- [ ] Upload the Gold CSVs to S3 and query them with Athena (`src/careops/s3.py` and `sql/athena_example.sql` are ready but not yet run against AWS; see `docs/AWS_PLAN.md`)
- [ ] Rebuild the Silver and Gold logic as dbt models with dbt tests
- [ ] Schedule the pipeline with an Airflow DAG

## Project layout

```
run.py                     entry point
src/careops/fixtures.py    makes the fake source files
src/careops/ingest.py      loads files into Bronze and Silver
src/careops/storage.py     table definitions and Gold views
src/careops/quality.py     data-quality checks
src/careops/exports.py     writes Gold to CSV
src/careops/s3.py          optional upload to S3 (dry run by default)
src/careops/cli.py         the commands
tests/test_pipeline.py     unit tests
scripts/check.py           runs every check in one go
sql/athena_example.sql     example Athena table and query (not run yet)
docs/ARCHITECTURE.md       design notes
docs/AWS_PLAN.md           plan for the S3 step
```
