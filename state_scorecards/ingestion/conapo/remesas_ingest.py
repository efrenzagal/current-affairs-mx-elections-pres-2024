"""Load the validated CONAPO remittance Parquet file into the SQLite warehouse.

Usage:
    python3 state_scorecards/ingestion/conapo/remesas_ingest.py
    python3 state_scorecards/ingestion/conapo/remesas_ingest.py --force

Requires the CONAPO tables (ingest.py) for the geography checks. The
table is loaded under a temporary name, checked, and published in one
transaction; an existing table stays available if loading or a check fails.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from ingest import CLEAN_DIR, DB_PATH, check_stage, exists, load_stage


TABLE = "fact_conapo_remittances_municipality_quarterly"
FILENAME = "conapo_remittances_municipality_quarterly.parquet"
KEYS = ("municipality_code", "year", "quarter")
EXPECTED_ROWS = 119_424
YEARS = (2013, 2024)


def validate(conn: sqlite3.Connection) -> None:
    for table in ("dim_conapo_state", "fact_conapo_municipality_annual"):
        if not exists(conn, "table", table):
            raise RuntimeError(f"{table} is missing; run ingest.py first")
    stage = TABLE + "_next"
    queries = {
        "state codes in dim_conapo_state": f"""
            SELECT COUNT(DISTINCT r.state_code) FROM "{stage}" r
            LEFT JOIN dim_conapo_state s ON s.state_code = r.state_code
            WHERE s.state_code IS NULL
        """,
        "identified municipios in CONAPO municipal table": f"""
            SELECT COUNT(DISTINCT r.municipality_code) FROM "{stage}" r
            WHERE r.municipality_code NOT LIKE '__999'
              AND r.municipality_code NOT IN (
                  SELECT municipality_code FROM fact_conapo_municipality_annual)
        """,
        "non-negative remittances": f"""
            SELECT COUNT(*) FROM "{stage}"
            WHERE remittances_usd_millions IS NULL OR remittances_usd_millions < 0
        """,
        "quarters 1-4": f'SELECT COUNT(*) FROM "{stage}" WHERE quarter NOT BETWEEN 1 AND 4',
    }
    for label, query in queries.items():
        count = conn.execute(query).fetchone()[0]
        if count:
            raise ValueError(f"{label}: {count:,} failing values")
        print(f"  OK: {label}")


def publish(conn: sqlite3.Connection, force: bool) -> None:
    if exists(conn, "table", TABLE) and not force:
        raise RuntimeError(f"{TABLE} already exists; rerun with --force")
    conn.execute("BEGIN")
    try:
        if exists(conn, "table", TABLE):
            conn.execute(f'DROP TABLE "{TABLE}"')
        conn.execute(f'ALTER TABLE "{TABLE}_next" RENAME TO "{TABLE}"')
        columns = ", ".join(f'"{column}"' for column in KEYS)
        conn.execute(f'CREATE UNIQUE INDEX "ux_{TABLE}" ON "{TABLE}" ({columns})')
        conn.execute(
            "CREATE INDEX idx_conapo_remittances_state_period "
            f'ON "{TABLE}" (state_code, year, quarter)'
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--clean-dir", type=Path, default=CLEAN_DIR)
    parser.add_argument("--force", action="store_true", help="Replace the existing remittance table")
    args = parser.parse_args()
    db_path = args.db.resolve()
    path = args.clean_dir.resolve() / FILENAME
    if not db_path.is_file():
        raise FileNotFoundError(db_path)
    if not path.is_file():
        raise FileNotFoundError(path)
    print(f"Remittance Parquet: {path}")
    print(f"Warehouse: {db_path}")
    conn = sqlite3.connect(db_path, timeout=60)
    try:
        if exists(conn, "table", TABLE) and not args.force:
            raise RuntimeError(f"{TABLE} already exists; rerun with --force")
        load_stage(conn, TABLE, path, KEYS, EXPECTED_ROWS)
        check_stage(conn, TABLE, EXPECTED_ROWS, YEARS)
        validate(conn)
        publish(conn, args.force)
        total = conn.execute(f'SELECT ROUND(SUM(remittances_usd_millions), 1) FROM "{TABLE}"').fetchone()[0]
        print(f"Published {TABLE} ({EXPECTED_ROWS:,} rows; {total:,} million USD, {YEARS[0]}-{YEARS[1]}).")
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
