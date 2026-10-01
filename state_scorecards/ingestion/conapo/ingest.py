"""Load validated CONAPO Parquet files into the existing SQLite warehouse.

Usage:
    python3 state_scorecards/ingestion/conapo/ingest.py
    python3 state_scorecards/ingestion/conapo/ingest.py --force

Tables are loaded under temporary names, checked, and published together.
Existing CONAPO tables remain available if an earlier load or check fails.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import unicodedata
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


ROOT = Path(__file__).resolve().parents[3]
DB_PATH = ROOT / "election_data.db"
CLEAN_DIR = ROOT / "state_scorecards" / "data" / "clean"
VIEW_NAME = "view_conapo_pibe_per_capita"
PYRAMID_VIEW = "view_conapo_population_pyramid"

# Five-year bands with an open 85+ band, the same cut as CONAPO's municipal
# tables. Reconstruction years only: CONAPO's projections start in 2020. The
# national rows ('00') are the sum of the 32 states, because the single-age
# table carries no national geography; those sums equal the national
# population_total in fact_conapo_state_annual for every year.
PYRAMID_VIEW_SQL = f"""CREATE VIEW {PYRAMID_VIEW} AS
WITH banded AS (
    SELECT state_code, year, sex,
           CASE WHEN age >= 85 THEN 85 ELSE (age / 5) * 5 END AS age_start,
           population
    FROM fact_conapo_state_population_age_annual
    WHERE estimate_phase = 'reconstruction'
),
geographies AS (
    SELECT state_code, year, sex, age_start, SUM(population) AS population
    FROM banded GROUP BY state_code, year, sex, age_start
    UNION ALL
    SELECT '00', year, sex, age_start, SUM(population)
    FROM banded GROUP BY year, sex, age_start
)
SELECT g.state_code, s.state_name, g.year, g.sex, g.age_start,
       CASE WHEN g.age_start = 85 THEN NULL ELSE g.age_start + 4 END AS age_end,
       CASE WHEN g.age_start = 85 THEN '85+'
            ELSE g.age_start || '-' || (g.age_start + 4) END AS age_band,
       g.population,
       100.0 * g.population / SUM(g.population) OVER (PARTITION BY g.state_code, g.year)
           AS pct_of_population
FROM geographies g
JOIN dim_conapo_state s ON s.state_code = g.state_code"""

TABLES = {
    "fact_conapo_state_annual": (
        "conapo_state_annual.parquet", ("state_code", "year"), 3_353, (1950, 2070)
    ),
    "fact_conapo_state_population_age_annual": (
        "conapo_state_population_age_annual.parquet",
        ("state_code", "year", "sex", "age"), 711_040, (1970, 2070)
    ),
    "fact_conapo_municipality_annual": (
        "conapo_municipality_annual.parquet",
        ("municipality_code", "year"), 126_225, (1990, 2040)
    ),
    "fact_conapo_municipality_population_age_annual": (
        "conapo_municipality_population_age_annual.parquet",
        ("municipality_code", "year", "sex", "age_band"),
        4_544_100, (1990, 2040)
    ),
    "fact_conapo_municipality_life_stage_annual": (
        "conapo_municipality_life_stage_annual.parquet",
        ("municipality_code", "year", "sex"), 252_450, (1990, 2040)
    ),
    "fact_conapo_state_fertility_age_annual": (
        "conapo_state_fertility_age_annual.parquet",
        ("state_code", "year", "mother_age_band"), 23_471, (1950, 2070)
    ),
}

PIBE_NAME_OVERRIDES = {
    "00": "Estados Unidos Mexicanos",
    "05": "Coahuila de Zaragoza",
    "16": "Michoacán de Ocampo",
    "30": "Veracruz de Ignacio de la Llave",
}

SQL_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


def exists(conn: sqlite3.Connection, kind: str, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = ? AND name = ?", (kind, name)
    ).fetchone() is not None


def sql_type(field: pa.Field) -> str:
    if pa.types.is_integer(field.type):
        return "INTEGER"
    if pa.types.is_floating(field.type):
        return "REAL"
    if pa.types.is_string(field.type) or pa.types.is_large_string(field.type):
        return "TEXT"
    raise ValueError(f"Unsupported Parquet type: {field.name} {field.type}")


def create_stage(conn: sqlite3.Connection, name: str, parquet: pq.ParquetFile, keys: tuple[str, ...]) -> None:
    if not SQL_NAME.fullmatch(name) or any(not SQL_NAME.fullmatch(field.name) for field in parquet.schema_arrow):
        raise ValueError(f"Unsafe SQL identifier in {name}")
    columns = []
    for field in parquet.schema_arrow:
        required = " NOT NULL" if field.name in keys or field.name in {"population_total", "population"} else ""
        columns.append(f'"{field.name}" {sql_type(field)}{required}')
    if not set(keys).issubset(parquet.schema_arrow.names):
        raise ValueError(f"Missing key columns in {name}")
    conn.execute(f'CREATE TABLE "{name}" ({", ".join(columns)})')
    conn.commit()


def load_stage(conn: sqlite3.Connection, table: str, path: Path, keys: tuple[str, ...], expected: int) -> None:
    parquet = pq.ParquetFile(path)
    if parquet.metadata.num_rows != expected:
        raise ValueError(f"Unexpected row count in {path}: {parquet.metadata.num_rows:,}, expected {expected:,}")
    stage = table + "_next"
    conn.execute(f'DROP TABLE IF EXISTS "{stage}"')
    conn.commit()
    create_stage(conn, stage, parquet, keys)
    loaded = 0
    for batch in parquet.iter_batches(batch_size=50_000):
        frame = batch.to_pandas()
        frame.to_sql(stage, conn, if_exists="append", index=False, chunksize=5_000)
        loaded += len(frame)
    if loaded != expected:
        raise ValueError(f"Loaded {loaded:,} rows for {table}, expected {expected:,}")
    print(f"  {table}: {loaded:,} rows")


def check_stage(conn: sqlite3.Connection, table: str, expected: int, years: tuple[int, int]) -> None:
    stage = table + "_next"
    count, first, last = conn.execute(
        f'SELECT COUNT(*), MIN(year), MAX(year) FROM "{stage}"'
    ).fetchone()
    if (count, first, last) != (expected, *years):
        raise ValueError(f"{table} coverage: {(count, first, last)}; expected {(expected, *years)}")
    if "population" in table or table.endswith("_annual") and "fertility" not in table:
        population_column = "population" if "population_age" in table else "population_total"
        invalid = conn.execute(
            f'SELECT COUNT(*) FROM "{stage}" WHERE {population_column} < 0 OR {population_column} IS NULL'
        ).fetchone()[0]
        if invalid:
            raise ValueError(f"{table} has {invalid} invalid population values")


def normalize(value: str) -> str:
    text = unicodedata.normalize("NFKD", value.casefold())
    return "".join(char for char in text if not unicodedata.combining(char))


def build_state_dimension(conn: sqlite3.Connection, clean_dir: Path) -> None:
    if not exists(conn, "table", "fact_pibe_state_annual"):
        raise RuntimeError("PIBE table is missing; run the PIBE ingest first")
    states = pd.read_parquet(
        clean_dir / TABLES["fact_conapo_state_annual"][0],
        columns=["state_code", "state_name"],
    ).drop_duplicates()
    if len(states) != 33 or states.state_code.nunique() != 33:
        raise ValueError("Expected 32 states plus the national geography")
    pibe_names = [row[0] for row in conn.execute("SELECT DISTINCT geography FROM fact_pibe_state_annual")]
    by_normalized = {normalize(name): name for name in pibe_names}
    matched = []
    for row in states.itertuples(index=False):
        pibe_name = PIBE_NAME_OVERRIDES.get(row.state_code, by_normalized.get(normalize(row.state_name)))
        if pibe_name not in pibe_names:
            raise ValueError(f"No PIBE state name for CONAPO {row.state_code}: {row.state_name}")
        matched.append((row.state_code, row.state_name, pibe_name))
    if len({row[2] for row in matched}) != 33:
        raise ValueError("PIBE geography mapping is not one-to-one")
    conn.execute("DROP TABLE IF EXISTS dim_conapo_state_next")
    conn.execute(
        "CREATE TABLE dim_conapo_state_next ("
        "state_code TEXT PRIMARY KEY, state_name TEXT NOT NULL, "
        "pibe_geography TEXT NOT NULL UNIQUE)"
    )
    conn.executemany("INSERT INTO dim_conapo_state_next VALUES (?, ?, ?)", matched)
    conn.commit()
    print("  dim_conapo_state: 33 matched geographies")


def validate_relationships(conn: sqlite3.Connection) -> None:
    queries = {
        "state age to state total": """
            SELECT COUNT(*) FROM (
                SELECT a.state_code, a.year, SUM(a.population) AS total
                FROM fact_conapo_state_population_age_annual_next a
                GROUP BY a.state_code, a.year
            ) a JOIN fact_conapo_state_annual_next s
              ON s.state_code = a.state_code AND s.year = a.year
            WHERE a.total != s.population_total
        """,
        "municipal age to municipal total": """
            SELECT COUNT(*) FROM (
                SELECT a.municipality_code, a.year, SUM(a.population) AS total
                FROM fact_conapo_municipality_population_age_annual_next a
                GROUP BY a.municipality_code, a.year
            ) a JOIN fact_conapo_municipality_annual_next m
              ON m.municipality_code = a.municipality_code AND m.year = a.year
            WHERE a.total != m.population_total
        """,
        "municipal life stages to municipal total": """
            SELECT COUNT(*) FROM (
                SELECT a.municipality_code, a.year, SUM(a.population_total) AS total
                FROM fact_conapo_municipality_life_stage_annual_next a
                GROUP BY a.municipality_code, a.year
            ) a JOIN fact_conapo_municipality_annual_next m
              ON m.municipality_code = a.municipality_code AND m.year = a.year
            WHERE a.total != m.population_total
        """,
        "fertility births to state annual": """
            SELECT COUNT(*) FROM (
                SELECT f.state_code, f.year, SUM(f.births) AS total
                FROM fact_conapo_state_fertility_age_annual_next f
                GROUP BY f.state_code, f.year
            ) f JOIN fact_conapo_state_annual_next s
              ON s.state_code = f.state_code AND s.year = f.year
            WHERE f.total != s.births
        """,
    }
    for label, query in queries.items():
        count = conn.execute(query).fetchone()[0]
        if count:
            raise ValueError(f"{label}: {count:,} mismatched rows")
        print(f"  OK: {label}")


def publish(conn: sqlite3.Connection, force: bool) -> None:
    names = list(TABLES) + ["dim_conapo_state"]
    existing = [name for name in names if exists(conn, "table", name)]
    if existing and not force:
        raise RuntimeError(f"Tables already exist: {existing}; rerun with --force")
    conn.execute("BEGIN")
    try:
        conn.execute(f"DROP VIEW IF EXISTS {VIEW_NAME}")
        conn.execute(f"DROP VIEW IF EXISTS {PYRAMID_VIEW}")
        for name in names:
            if exists(conn, "table", name):
                conn.execute(f'DROP TABLE "{name}"')
            conn.execute(f'ALTER TABLE "{name}_next" RENAME TO "{name}"')
        for name, (_, keys, _, _) in TABLES.items():
            columns = ", ".join(f'"{column}"' for column in keys)
            conn.execute(f'CREATE UNIQUE INDEX "ux_{name}" ON "{name}" ({columns})')
        conn.execute(
            "CREATE INDEX idx_conapo_municipality_state_year "
            "ON fact_conapo_municipality_annual(state_code, year)"
        )
        conn.execute(
            "CREATE INDEX idx_conapo_municipality_age_state_year "
            "ON fact_conapo_municipality_population_age_annual(state_code, year)"
        )
        conn.execute(
            "CREATE INDEX idx_conapo_municipality_life_stage_state_year "
            "ON fact_conapo_municipality_life_stage_annual(state_code, year)"
        )
        conn.execute(
            f"""CREATE VIEW {VIEW_NAME} AS
            SELECT p.pibe_observation_id, p.geography, s.state_code, p.year,
                   p.activity_id, p.activity_group, p.activity_level,
                   p.activity_name, p.concept_name, p.unit,
                   p.value AS pibe_millions_2018_mxn,
                   c.population_total,
                   p.value * 1000000.0 / c.population_total
                       AS value_per_capita_2018_mxn
            FROM fact_pibe_state_annual p
            JOIN dim_conapo_state s ON s.pibe_geography = p.geography
            JOIN fact_conapo_state_annual c
              ON c.state_code = s.state_code AND c.year = p.year
            WHERE p.unit = 'Millones de pesos a precios de 2018'
              AND p.value IS NOT NULL AND c.population_total > 0"""
        )
        conn.execute(PYRAMID_VIEW_SQL)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--clean-dir", type=Path, default=CLEAN_DIR)
    parser.add_argument("--force", action="store_true", help="Replace existing CONAPO tables and view")
    args = parser.parse_args()
    db_path = args.db.resolve()
    clean_dir = args.clean_dir.resolve()
    if not db_path.is_file():
        raise FileNotFoundError(db_path)
    for filename, _, _, _ in TABLES.values():
        if not (clean_dir / filename).is_file():
            raise FileNotFoundError(clean_dir / filename)
    print(f"CONAPO Parquet: {clean_dir}")
    print(f"Warehouse: {db_path}")
    conn = sqlite3.connect(db_path, timeout=60)
    try:
        if not args.force:
            existing = [name for name in TABLES if exists(conn, "table", name)]
            if existing:
                raise RuntimeError(f"Tables already exist: {existing}; rerun with --force")
        for table, (filename, keys, expected, years) in TABLES.items():
            load_stage(conn, table, clean_dir / filename, keys, expected)
            check_stage(conn, table, expected, years)
        build_state_dimension(conn, clean_dir)
        validate_relationships(conn)
        publish(conn, args.force)
        view_rows = conn.execute(f"SELECT COUNT(*) FROM {VIEW_NAME}").fetchone()[0]
        print(f"Published {len(TABLES)} CONAPO fact tables and {VIEW_NAME} ({view_rows:,} PIBE rows).")
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
