"""
Load Senado de la Republica roll-call vote data into the warehouse.

Reads the LXVI-legislature CSVs produced by
camara_de_senadores/votos/crawl_senado_votes.py and populates three tables in
election_data.db:

    dim_senado_vote   — one row per roll-call vote page
    dim_senador       — normalized senator names (union across votes)
    fact_senador_vote — individual senator votes

After loading, runs automatic QA. Hard errors abort with exit code 1;
soft warnings are printed but do not abort.

Usage:
    /usr/bin/python3 ingestion/senado_ingest.py
    /usr/bin/python3 ingestion/senado_ingest.py --db path/to/other.db
    /usr/bin/python3 ingestion/senado_ingest.py --force
"""

from __future__ import annotations

import argparse
import sys
import sqlite3
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "election_data.db"
CLEAN_DIR = ROOT / "data" / "clean_senado_votes"
LEGISLATURE = 66

DIM_VOTE_COLS = [
    "votacion_id", "url", "vote_date", "period_type", "ordinal_period",
    "exercise_year", "description", "vote_type", "en_pro", "en_contra", "abstencion",
]
FACT_VOTE_COLS = [
    "votacion_id", "senator_id", "senator_name", "grupo_parlamentario", "voto", "voto_detail",
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS dim_senado_vote (
    votacion_id     INTEGER PRIMARY KEY,
    legislature     INTEGER NOT NULL,
    url             TEXT,
    vote_date       TEXT,
    period_type     TEXT,
    ordinal_period  TEXT,
    exercise_year   TEXT,
    description     TEXT,
    vote_type       TEXT,
    en_pro          INTEGER,
    en_contra       INTEGER,
    abstencion      INTEGER
);

CREATE TABLE IF NOT EXISTS dim_senador (
    senador_id      INTEGER PRIMARY KEY,
    senador_name    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fact_senador_vote (
    votacion_id          INTEGER NOT NULL,
    senador_id           INTEGER NOT NULL,
    grupo_parlamentario  TEXT,
    voto                 TEXT,
    voto_detail          TEXT,
    PRIMARY KEY (votacion_id, senador_id)
);
"""


def to_sql_safe(df: pd.DataFrame, table: str, conn: sqlite3.Connection) -> None:
    """Insert df into table, chunking to stay under SQLite's 999-variable limit."""
    if df.empty:
        return
    chunksize = max(1, 999 // len(df.columns))
    df.to_sql(table, conn, if_exists="append", index=False, method="multi", chunksize=chunksize)


def load_csv(path: Path, keep_cols: list[str]) -> pd.DataFrame:
    if not path.exists():
        print(f"    WARNING: {path.name} not found, skipping")
        return pd.DataFrame(columns=keep_cols)
    df = pd.read_csv(path, low_memory=False)
    missing = [c for c in keep_cols if c not in df.columns]
    if missing:
        print(f"    WARNING: {path.name} missing columns {missing}")
    present = [c for c in keep_cols if c in df.columns]
    return df[present].copy()


def drop_senado_tables(conn: sqlite3.Connection) -> None:
    for table in ["dim_senadores", "fact_senador_vote", "dim_senador", "dim_senado_vote"]:
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.commit()


def ingest(conn: sqlite3.Connection, clean_dir: Path) -> None:
    dim_vote = load_csv(clean_dir / "dim_senado_vote.csv", DIM_VOTE_COLS)
    fact_vote = load_csv(clean_dir / "senado_vote_detail.csv", FACT_VOTE_COLS)

    dim_vote = dim_vote.dropna(subset=["votacion_id"]).copy()
    dim_vote["legislature"] = LEGISLATURE
    dim_vote = dim_vote[["votacion_id", "legislature", *DIM_VOTE_COLS[1:]]]

    fact_vote = fact_vote.dropna(subset=["votacion_id", "senator_id"]).copy()
    before = len(fact_vote)
    fact_vote = fact_vote.drop_duplicates(subset=["votacion_id", "senator_id"], keep="first")
    if len(fact_vote) < before:
        _warn(f"Dropped {before - len(fact_vote):,} duplicate senator-vote rows")

    dim_senador = (
        fact_vote[["senator_id", "senator_name"]]
        .drop_duplicates(subset=["senator_id"])
        .rename(columns={"senator_id": "senador_id", "senator_name": "senador_name"})
    )
    fact_senador_vote = fact_vote.rename(columns={"senator_id": "senador_id"})[
        ["votacion_id", "senador_id", "grupo_parlamentario", "voto", "voto_detail"]
    ]

    to_sql_safe(dim_vote, "dim_senado_vote", conn)
    to_sql_safe(dim_senador, "dim_senador", conn)
    to_sql_safe(fact_senador_vote, "fact_senador_vote", conn)
    conn.commit()

    print(f"  votes={len(dim_vote):,}  senadores={len(dim_senador):,}  senador_votes={len(fact_senador_vote):,}")


def _warn(msg: str) -> None:
    print(f"  WARNING: {msg}")


def _fail(msg: str) -> None:
    print(f"  ERROR:   {msg}")


KNOWN_VOTO_VALUES = {"PRO", "CONTRA", "ABSTENCIÓN", "AUSENTE"}


def validate(conn: sqlite3.Connection) -> bool:
    print("\n── QA ──────────────────────────────────────────────────")
    hard_ok = True

    orphan_votes = conn.execute("""
        SELECT COUNT(*) FROM fact_senador_vote f
        WHERE NOT EXISTS (SELECT 1 FROM dim_senado_vote d WHERE d.votacion_id = f.votacion_id)
    """).fetchone()[0]
    if orphan_votes:
        _fail(f"fact_senador_vote has {orphan_votes:,} rows with no matching dim_senado_vote")
        hard_ok = False
    else:
        print("  OK: fact_senador_vote → dim_senado_vote refs intact")

    orphan_senadores = conn.execute("""
        SELECT COUNT(*) FROM fact_senador_vote f
        WHERE NOT EXISTS (SELECT 1 FROM dim_senador d WHERE d.senador_id = f.senador_id)
    """).fetchone()[0]
    if orphan_senadores:
        _fail(f"fact_senador_vote has {orphan_senadores:,} rows with no matching dim_senador")
        hard_ok = False
    else:
        print("  OK: fact_senador_vote → dim_senador refs intact")

    dup_votes = conn.execute("""
        SELECT COUNT(*) FROM (
            SELECT votacion_id FROM dim_senado_vote GROUP BY votacion_id HAVING COUNT(*) > 1
        )
    """).fetchone()[0]
    if dup_votes:
        _fail(f"dim_senado_vote has {dup_votes:,} duplicate votacion_id values — rerun with --force")
        hard_ok = False
    else:
        print("  OK: dim_senado_vote primary keys unique")

    unexpected = conn.execute("""
        SELECT DISTINCT voto FROM fact_senador_vote WHERE voto IS NOT NULL
    """).fetchall()
    bad_votos = [r[0] for r in unexpected if r[0] not in KNOWN_VOTO_VALUES]
    if bad_votos:
        _warn(f"Unexpected voto values in fact_senador_vote: {bad_votos}")
    else:
        print("  OK: all voto values are expected")

    no_senadores = conn.execute("""
        SELECT COUNT(*) FROM dim_senado_vote v
        WHERE NOT EXISTS (SELECT 1 FROM fact_senador_vote f WHERE f.votacion_id = v.votacion_id)
    """).fetchone()[0]
    if no_senadores:
        _warn(f"{no_senadores:,} votes in dim_senado_vote have no senador rows")
    else:
        print("  OK: all votes have at least one senador row")

    print("────────────────────────────────────────────────────────")
    return hard_ok


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--clean-dir", default=str(CLEAN_DIR))
    parser.add_argument("--force", action="store_true", help="Drop and recreate Senado tables before loading")
    args = parser.parse_args()

    db_path = Path(args.db)
    clean_dir = Path(args.clean_dir)

    if not clean_dir.exists():
        print(f"No clean Senado data found under {clean_dir}")
        return

    print(f"Warehouse: {db_path}")
    conn = sqlite3.connect(db_path)

    if args.force:
        print("--force: dropping existing Senado tables")
        drop_senado_tables(conn)

    for sql in SCHEMA.strip().split(";"):
        sql = sql.strip()
        if sql:
            conn.execute(sql)
    conn.commit()

    try:
        ingest(conn, clean_dir)
    except Exception as e:
        conn.rollback()
        print(f"  ERROR: {e}")
        conn.close()
        sys.exit(1)

    print("Building dim_senadores (official INE seats → Senado identities)...")
    try:
        from ingestion.senadores_ingest import materialize_dim_senadores

        senadores = materialize_dim_senadores(conn)
        match_counts = senadores["match_method"].value_counts().to_dict()
        print(f"  {len(senadores):,} official seats loaded; mappings={match_counts}")
    except FileNotFoundError as exc:
        _warn(str(exc))

    print("\n── Row counts ──────────────────────────────────────────")
    for table in ["dim_senado_vote", "dim_senador", "dim_senadores", "fact_senador_vote"]:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not exists:
            continue
        n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {table}: {n:,}")

    qa_ok = validate(conn)
    conn.close()

    if not qa_ok:
        print("Ingest completed with QA errors — see ERROR lines above.")
        sys.exit(1)

    print("Done.")


if __name__ == "__main__":
    main()
