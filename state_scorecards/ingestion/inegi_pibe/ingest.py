"""Load the clean PIBE long table into the SQLite warehouse.

This script only reads the artifact produced by ``raw_to_parquet.py``. It creates
one denormalized table, ``fact_pibe_state_annual``, optimized for state-scorecard
queries while retaining INEGI provenance and hierarchy fields.

Usage:
    python3 state_scorecards/ingestion/inegi_pibe/ingest.py
    python3 state_scorecards/ingestion/inegi_pibe/ingest.py --force
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[3]
DB_PATH = REPO_ROOT / "election_data.db"
CLEAN_PATH = (
    REPO_ROOT / "state_scorecards" / "data" / "clean" / "pibe_state_annual.parquet"
)
TABLE_NAME = "fact_pibe_state_annual"
NEXT_TABLE_NAME = f"{TABLE_NAME}_next"

OBSERVATION_KEY = [
    "geography",
    "activity_id",
    "concept_name",
    "unit",
    "year",
]

CLEAN_COLUMNS = [
    "dataset",
    "source_file",
    "series_path",
    "series_name",
    "path_level_1",
    "path_level_2",
    "path_level_3",
    "geography",
    "geography_note",
    "activity_id",
    "activity_group",
    "activity_level",
    "activity_code",
    "activity_name",
    "activity_full_name",
    "sector_code",
    "sector_name",
    "subsector_code",
    "subsector_name",
    "unit",
    "concept_code",
    "concept_name",
    "year",
    "revision_status",
    "value",
    "historical_coverage",
]

TEXT_COLUMNS = [column for column in CLEAN_COLUMNS if column not in {"year", "value"}]


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def create_table(conn: sqlite3.Connection, table_name: str) -> None:
    conn.execute(
        f"""
        CREATE TABLE {table_name} (
            pibe_observation_id INTEGER PRIMARY KEY,
            dataset              TEXT NOT NULL,
            source_file          TEXT NOT NULL,
            series_path          TEXT NOT NULL,
            series_name          TEXT NOT NULL,
            path_level_1         TEXT,
            path_level_2         TEXT,
            path_level_3         TEXT,
            geography            TEXT NOT NULL,
            geography_note       TEXT,
            activity_id          TEXT NOT NULL,
            activity_group       TEXT NOT NULL,
            activity_level       TEXT NOT NULL CHECK (
                activity_level IN ('total', 'activity_group', 'sector', 'subsector')
            ),
            activity_code        TEXT,
            activity_name        TEXT NOT NULL,
            activity_full_name   TEXT NOT NULL,
            sector_code          TEXT,
            sector_name          TEXT,
            subsector_code       TEXT,
            subsector_name       TEXT,
            unit                 TEXT NOT NULL,
            concept_code         TEXT,
            concept_name         TEXT NOT NULL,
            year                 INTEGER NOT NULL CHECK (year BETWEEN 1900 AND 2100),
            revision_status      TEXT NOT NULL CHECK (
                revision_status IN ('revised', 'not_marked')
            ),
            value                REAL,
            historical_coverage  TEXT NOT NULL CHECK (
                historical_coverage IN ('retropolated_aggregate', 'detailed')
            ),
            UNIQUE (geography, activity_id, concept_name, unit, year)
        )
        """
    )


def read_clean_parquet(clean_path: Path) -> pd.DataFrame:
    if not clean_path.exists():
        raise FileNotFoundError(
            f"Clean PIBE data not found at {clean_path}. Run raw_to_parquet.py first."
        )

    data = pd.read_parquet(clean_path, engine="pyarrow")

    missing = sorted(set(CLEAN_COLUMNS) - set(data.columns))
    extra = sorted(set(data.columns) - set(CLEAN_COLUMNS))
    if missing or extra:
        raise ValueError(f"Unexpected clean Parquet schema: missing={missing}, extra={extra}")
    data = data[CLEAN_COLUMNS].copy()

    for column in TEXT_COLUMNS:
        data[column] = data[column].astype("string")

    data["year"] = pd.to_numeric(data["year"], errors="raise").astype(int)
    data["value"] = pd.to_numeric(data["value"], errors="coerce")

    required = [
        "dataset",
        "source_file",
        "series_path",
        "series_name",
        "geography",
        "activity_id",
        "activity_group",
        "activity_level",
        "activity_name",
        "activity_full_name",
        "unit",
        "concept_name",
        "year",
        "revision_status",
        "historical_coverage",
    ]
    null_counts = data[required].isna().sum()
    bad_nulls = null_counts[null_counts > 0]
    if not bad_nulls.empty:
        raise ValueError(f"Required clean fields contain nulls: {bad_nulls.to_dict()}")

    duplicates = data.duplicated(OBSERVATION_KEY, keep=False)
    if duplicates.any():
        sample = data.loc[duplicates, OBSERVATION_KEY].head(10).to_dict("records")
        raise ValueError(f"Clean Parquet contains duplicate observation keys: {sample}")

    return data


def load_next_table(conn: sqlite3.Connection, data: pd.DataFrame) -> None:
    conn.execute(f"DROP TABLE IF EXISTS {NEXT_TABLE_NAME}")
    create_table(conn, NEXT_TABLE_NAME)
    conn.commit()

    data.to_sql(
        NEXT_TABLE_NAME,
        conn,
        if_exists="append",
        index=False,
        chunksize=5_000,
    )
    conn.commit()


def validate_table(
    conn: sqlite3.Connection,
    table_name: str,
    expected_rows: int,
) -> bool:
    print("\n── Warehouse QA ───────────────────────────────────────")
    hard_ok = True

    row_count, min_year, max_year = conn.execute(
        f"SELECT COUNT(*), MIN(year), MAX(year) FROM {table_name}"
    ).fetchone()
    if row_count != expected_rows:
        print(f"  ERROR: expected {expected_rows:,} rows; found {row_count:,}")
        hard_ok = False
    else:
        print(f"  OK: {row_count:,} rows loaded")

    if (min_year, max_year) != (1980, 2024):
        print(f"  ERROR: expected years 1980-2024; found {min_year}-{max_year}")
        hard_ok = False
    else:
        print("  OK: annual coverage is 1980-2024")

    duplicate_keys = conn.execute(
        f"""
        SELECT COUNT(*) FROM (
            SELECT geography, activity_id, concept_name, unit, year
            FROM {table_name}
            GROUP BY geography, activity_id, concept_name, unit, year
            HAVING COUNT(*) > 1
        )
        """
    ).fetchone()[0]
    if duplicate_keys:
        print(f"  ERROR: {duplicate_keys:,} duplicate observation keys")
        hard_ok = False
    else:
        print("  OK: observation keys are unique")

    overlapping_retro = conn.execute(
        f"SELECT COUNT(*) FROM {table_name} "
        "WHERE dataset = 'pibe_retro' AND year >= 2003"
    ).fetchone()[0]
    if overlapping_retro:
        print(f"  ERROR: {overlapping_retro:,} overlapping retro rows remain")
        hard_ok = False
    else:
        print("  OK: retro observations stop at 2002")

    historical_detail = conn.execute(
        f"SELECT COUNT(*) FROM {table_name} "
        "WHERE year < 2003 AND activity_level IN ('sector', 'subsector')"
    ).fetchone()[0]
    if historical_detail:
        print(f"  ERROR: {historical_detail:,} pre-2003 detailed activity rows")
        hard_ok = False
    else:
        print("  OK: pre-2003 rows contain aggregate activities only")

    geography_count = conn.execute(
        f"SELECT COUNT(DISTINCT geography) FROM {table_name}"
    ).fetchone()[0]
    if geography_count != 33:
        print(f"  ERROR: expected 33 geographies; found {geography_count}")
        hard_ok = False
    else:
        print("  OK: 32 states plus national total")

    print("\n  Rows by source:")
    for dataset, coverage, count in conn.execute(
        f"""
        SELECT dataset, historical_coverage, COUNT(*)
        FROM {table_name}
        GROUP BY dataset, historical_coverage
        ORDER BY dataset, historical_coverage
        """
    ):
        print(f"    {dataset} / {coverage}: {count:,}")

    print("────────────────────────────────────────────────────────")
    return hard_ok


def publish_next_table(conn: sqlite3.Connection, force: bool) -> None:
    conn.execute("BEGIN")
    try:
        if table_exists(conn, TABLE_NAME):
            if not force:
                raise RuntimeError(f"{TABLE_NAME} already exists; rerun with --force")
            conn.execute(f"DROP TABLE {TABLE_NAME}")
        conn.execute(f"ALTER TABLE {NEXT_TABLE_NAME} RENAME TO {TABLE_NAME}")
        conn.execute(
            f"CREATE INDEX idx_pibe_geo_activity_year "
            f"ON {TABLE_NAME} (geography, activity_id, year)"
        )
        conn.execute(
            f"CREATE INDEX idx_pibe_activity_year "
            f"ON {TABLE_NAME} (activity_id, year)"
        )
        conn.execute(f"CREATE INDEX idx_pibe_year ON {TABLE_NAME} (year)")
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest clean PIBE data into SQLite.")
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--clean-path", type=Path, default=CLEAN_PATH)
    parser.add_argument(
        "--force",
        action="store_true",
        help=f"Atomically replace {TABLE_NAME} if it already exists",
    )
    args = parser.parse_args()

    db_path = args.db.resolve()
    clean_path = args.clean_path.resolve()
    print(f"Clean data: {clean_path}")
    print(f"Warehouse:  {db_path}")

    try:
        data = read_clean_parquet(clean_path)
        print(f"Read {len(data):,} clean rows")

        conn = sqlite3.connect(db_path)
        try:
            if table_exists(conn, TABLE_NAME) and not args.force:
                raise RuntimeError(f"{TABLE_NAME} already exists; rerun with --force")

            load_next_table(conn, data)
            qa_ok = validate_table(conn, NEXT_TABLE_NAME, len(data))
            if not qa_ok:
                conn.execute(f"DROP TABLE IF EXISTS {NEXT_TABLE_NAME}")
                conn.commit()
                raise RuntimeError("Warehouse validation failed")

            publish_next_table(conn, force=args.force)
        finally:
            conn.close()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Done: {TABLE_NAME} is ready.")


if __name__ == "__main__":
    main()
