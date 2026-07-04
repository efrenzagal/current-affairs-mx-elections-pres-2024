"""
Load Gaceta Parlamentaria roll-call vote data into the warehouse.

Reads per-legislature CSV folders from data/gaceta_votes/clean/by_legislature/
and populates four tables in election_data.db:

    dim_gaceta_vote         — one row per roll-call vote page
    dim_gaceta_deputy       — normalized deputy names (union across legislatures)
    fact_gaceta_vote_summary — vote counts by choice × party
    fact_gaceta_deputy_vote  — individual deputy votes

After loading, runs automatic QA. Hard errors abort with exit code 1;
soft warnings are printed but do not abort.

Usage:
    /usr/bin/python3 ingestion/gaceta_ingest.py
    /usr/bin/python3 ingestion/gaceta_ingest.py --db path/to/other.db
    /usr/bin/python3 ingestion/gaceta_ingest.py --legislature 62 66
    /usr/bin/python3 ingestion/gaceta_ingest.py --force
"""

from __future__ import annotations

import argparse
import sys
import sqlite3
from pathlib import Path

import pandas as pd

ROOT       = Path(__file__).resolve().parents[1]
DB_PATH    = ROOT / "election_data.db"
LEG_DIR    = ROOT / "data" / "gaceta_votes" / "clean" / "by_legislature"

# Columns written to each warehouse table. Extras in the CSV are silently dropped.
DIM_VOTE_COLS = [
    "gaceta_vote_id", "source_url", "source_path", "table_slug",
    "legislature", "period_url", "chamber", "title",
    "vote_date", "gaceta_number", "gaceta_date", "status_text", "vote_context",
]
DIM_DEPUTY_COLS   = ["deputy_id", "deputy_name"]
FACT_SUMMARY_COLS = ["gaceta_vote_id", "vote_choice", "party_key", "count"]
FACT_DEPUTY_COLS  = ["gaceta_vote_id", "deputy_id", "vote_choice", "party_key", "ordinal"]


SCHEMA = """
CREATE TABLE IF NOT EXISTS dim_gaceta_vote (
    gaceta_vote_id  TEXT PRIMARY KEY,
    source_url      TEXT,
    source_path     TEXT,
    table_slug      TEXT,
    legislature     INTEGER,
    period_url      TEXT,
    chamber         TEXT,
    title           TEXT,
    vote_date       TEXT,
    gaceta_number   TEXT,
    gaceta_date     TEXT,
    status_text     TEXT,
    vote_context    TEXT
);

CREATE TABLE IF NOT EXISTS dim_gaceta_deputy (
    deputy_id   TEXT PRIMARY KEY,
    deputy_name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fact_gaceta_vote_summary (
    gaceta_vote_id  TEXT NOT NULL,
    vote_choice     TEXT NOT NULL,
    party_key       TEXT NOT NULL,
    count           INTEGER,
    PRIMARY KEY (gaceta_vote_id, vote_choice, party_key)
);

CREATE TABLE IF NOT EXISTS fact_gaceta_deputy_vote (
    gaceta_vote_id  TEXT NOT NULL,
    deputy_id       TEXT NOT NULL,
    vote_choice     TEXT,
    party_key       TEXT,
    ordinal         INTEGER,
    PRIMARY KEY (gaceta_vote_id, deputy_id)
);
"""


def discover_legislatures() -> list[int]:
    if not LEG_DIR.exists():
        return []
    legs = []
    for p in sorted(LEG_DIR.iterdir()):
        if p.is_dir() and p.name.startswith("legislature_"):
            try:
                legs.append(int(p.name.split("_")[1]))
            except ValueError:
                pass
    return legs


def to_sql_safe(df: pd.DataFrame, table: str, conn: sqlite3.Connection) -> None:
    """Insert df into table, chunking to stay under SQLite's 999-variable limit."""
    if df.empty:
        return
    chunksize = max(1, 999 // len(df.columns))
    df.to_sql(table, conn, if_exists="append", index=False, method="multi", chunksize=chunksize)


def load_csv(leg_dir: Path, filename: str, keep_cols: list[str]) -> pd.DataFrame:
    path = leg_dir / filename
    if not path.exists():
        print(f"    WARNING: {path.name} not found, skipping")
        return pd.DataFrame(columns=keep_cols)
    df = pd.read_csv(path, low_memory=False)
    missing = [c for c in keep_cols if c not in df.columns]
    if missing:
        print(f"    WARNING: {filename} missing columns {missing}")
    present = [c for c in keep_cols if c in df.columns]
    return df[present].copy()


def ingest_legislature(
    conn: sqlite3.Connection, legislature: int
) -> pd.DataFrame:
    """Load one legislature. Returns the deputy dim for deferred cross-leg dedup."""
    leg_dir = LEG_DIR / f"legislature_{legislature}"
    print(f"  Legislature {legislature}: {leg_dir.name}")

    dim_vote   = load_csv(leg_dir, "dim_gaceta_vote.csv",          DIM_VOTE_COLS)
    dim_deputy = load_csv(leg_dir, "dim_gaceta_deputy.csv",         DIM_DEPUTY_COLS)
    fact_summ  = load_csv(leg_dir, "fact_gaceta_vote_summary.csv",  FACT_SUMMARY_COLS)
    fact_dep   = load_csv(leg_dir, "fact_gaceta_deputy_vote.csv",   FACT_DEPUTY_COLS)

    dim_vote   = dim_vote.dropna(subset=["gaceta_vote_id"])
    dim_deputy = dim_deputy.dropna(subset=["deputy_id"])
    fact_summ  = fact_summ.dropna(subset=["gaceta_vote_id", "vote_choice", "party_key"])
    fact_dep   = fact_dep.dropna(subset=["gaceta_vote_id", "deputy_id"])

    # Guard against duplicate (gaceta_vote_id, deputy_id) within a single legislature CSV
    before = len(fact_dep)
    fact_dep = fact_dep.drop_duplicates(subset=["gaceta_vote_id", "deputy_id"], keep="first")
    if len(fact_dep) < before:
        _warn(f"Legislature {legislature}: dropped {before - len(fact_dep):,} duplicate deputy-vote rows")

    # Deputies are shared across legislatures — insert deferred; load the rest now
    to_sql_safe(dim_vote,  "dim_gaceta_vote",          conn)
    to_sql_safe(fact_summ, "fact_gaceta_vote_summary", conn)
    to_sql_safe(fact_dep,  "fact_gaceta_deputy_vote",  conn)

    print(f"    votes={len(dim_vote):,}  deputies={len(dim_deputy):,}  "
          f"summary_rows={len(fact_summ):,}  deputy_votes={len(fact_dep):,}")

    return dim_deputy


KNOWN_VOTE_CHOICES = {
    "Favor", "Contra", "Abstención", "Abstencion",
    "Ausente", "Presente", "Total",
    # older legislature variants
    "A favor", "En contra", "Abstención/Ausente",
    # quorum call — deputy registered attendance but did not vote
    "Quórum *",
}


def _warn(msg: str) -> None:
    print(f"  WARNING: {msg}")


def _fail(msg: str) -> None:
    print(f"  ERROR:   {msg}")


def validate(conn: sqlite3.Connection, loaded_legislatures: list[int]) -> bool:
    """Run QA checks. Returns True if all hard checks pass."""
    print("\n── QA ──────────────────────────────────────────────────")
    hard_ok = True

    # ── Hard: referential integrity ──────────────────────────────────────────
    orphan_summary = conn.execute("""
        SELECT COUNT(*) FROM fact_gaceta_vote_summary f
        WHERE NOT EXISTS (SELECT 1 FROM dim_gaceta_vote d WHERE d.gaceta_vote_id = f.gaceta_vote_id)
    """).fetchone()[0]
    if orphan_summary:
        _fail(f"fact_gaceta_vote_summary has {orphan_summary:,} rows with no matching dim_gaceta_vote")
        hard_ok = False
    else:
        print("  OK: fact_gaceta_vote_summary → dim_gaceta_vote refs intact")

    orphan_dep_vote = conn.execute("""
        SELECT COUNT(*) FROM fact_gaceta_deputy_vote f
        WHERE NOT EXISTS (SELECT 1 FROM dim_gaceta_vote d WHERE d.gaceta_vote_id = f.gaceta_vote_id)
    """).fetchone()[0]
    if orphan_dep_vote:
        _fail(f"fact_gaceta_deputy_vote has {orphan_dep_vote:,} rows with no matching dim_gaceta_vote")
        hard_ok = False
    else:
        print("  OK: fact_gaceta_deputy_vote → dim_gaceta_vote refs intact")

    orphan_deputies = conn.execute("""
        SELECT COUNT(*) FROM fact_gaceta_deputy_vote f
        WHERE NOT EXISTS (SELECT 1 FROM dim_gaceta_deputy d WHERE d.deputy_id = f.deputy_id)
    """).fetchone()[0]
    if orphan_deputies:
        _fail(f"fact_gaceta_deputy_vote has {orphan_deputies:,} rows with no matching dim_gaceta_deputy")
        hard_ok = False
    else:
        print("  OK: fact_gaceta_deputy_vote → dim_gaceta_deputy refs intact")

    # ── Hard: duplicate primary keys (can happen if --force not used on re-run) ─
    dup_votes = conn.execute("""
        SELECT COUNT(*) FROM (
            SELECT gaceta_vote_id FROM dim_gaceta_vote GROUP BY gaceta_vote_id HAVING COUNT(*) > 1
        )
    """).fetchone()[0]
    if dup_votes:
        _fail(f"dim_gaceta_vote has {dup_votes:,} duplicate gaceta_vote_id values — rerun with --force")
        hard_ok = False
    else:
        print("  OK: dim_gaceta_vote primary keys unique")

    dup_dep_votes = conn.execute("""
        SELECT COUNT(*) FROM (
            SELECT gaceta_vote_id, deputy_id FROM fact_gaceta_deputy_vote
            GROUP BY gaceta_vote_id, deputy_id HAVING COUNT(*) > 1
        )
    """).fetchone()[0]
    if dup_dep_votes:
        _fail(f"fact_gaceta_deputy_vote has {dup_dep_votes:,} duplicate (gaceta_vote_id, deputy_id) pairs")
        hard_ok = False
    else:
        print("  OK: fact_gaceta_deputy_vote primary keys unique")

    # ── Hard: every loaded legislature has votes ──────────────────────────────
    for leg in loaded_legislatures:
        n = conn.execute(
            "SELECT COUNT(*) FROM dim_gaceta_vote WHERE legislature = ?", (leg,)
        ).fetchone()[0]
        if n == 0:
            _fail(f"Legislature {leg} loaded but has 0 rows in dim_gaceta_vote")
            hard_ok = False

    # ── Soft: unexpected vote_choice values ───────────────────────────────────
    unexpected = conn.execute("""
        SELECT DISTINCT vote_choice FROM fact_gaceta_deputy_vote
        WHERE vote_choice IS NOT NULL
    """).fetchall()
    bad_choices = [r[0] for r in unexpected if r[0] not in KNOWN_VOTE_CHOICES]
    if bad_choices:
        _warn(f"Unexpected vote_choice values in fact_gaceta_deputy_vote: {bad_choices}")
    else:
        print("  OK: all vote_choice values are expected")

    # ── Soft: summary Total count vs deputy row count per vote ────────────────
    mismatches = conn.execute("""
        SELECT
            s.gaceta_vote_id,
            s.count          AS summary_total,
            COUNT(f.deputy_id) AS deputy_rows
        FROM fact_gaceta_vote_summary s
        LEFT JOIN fact_gaceta_deputy_vote f
          ON s.gaceta_vote_id = f.gaceta_vote_id
        WHERE s.vote_choice = 'Total' AND s.party_key = 'Total'
        GROUP BY s.gaceta_vote_id, s.count
        HAVING ABS(summary_total - deputy_rows) > 5
        ORDER BY ABS(summary_total - deputy_rows) DESC
        LIMIT 10
    """).fetchall()
    if mismatches:
        _warn(f"{len(mismatches)} votes where summary Total differs from deputy row count by >5:")
        for vote_id, st, dr in mismatches:
            print(f"    {vote_id}: summary={st}, deputy_rows={dr}")
    else:
        print("  OK: summary totals align with deputy row counts")

    # ── Soft: votes with no deputy rows at all ────────────────────────────────
    no_deputies = conn.execute("""
        SELECT COUNT(*) FROM dim_gaceta_vote v
        WHERE NOT EXISTS (
            SELECT 1 FROM fact_gaceta_deputy_vote f WHERE f.gaceta_vote_id = v.gaceta_vote_id
        )
    """).fetchone()[0]
    if no_deputies:
        _warn(f"{no_deputies:,} votes in dim_gaceta_vote have no deputy rows (detail pages may not have been scraped)")
    else:
        print("  OK: all votes have at least one deputy row")

    print("────────────────────────────────────────────────────────")
    return hard_ok


def drop_gaceta_tables(conn: sqlite3.Connection) -> None:
    for table in [
        "fact_gaceta_deputy_vote", "fact_gaceta_vote_summary",
        "dim_gaceta_deputy", "dim_gaceta_vote",
    ]:
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db",          default=str(DB_PATH))
    parser.add_argument("--legislature", type=int, nargs="+",
                        help="Specific legislature numbers to load (default: all available)")
    parser.add_argument("--force",       action="store_true",
                        help="Drop and recreate gaceta tables before loading")
    args = parser.parse_args()

    db_path     = Path(args.db)
    legislatures = args.legislature or discover_legislatures()

    if not legislatures:
        print(f"No legislature folders found under {LEG_DIR}")
        return

    print(f"Warehouse: {db_path}")
    print(f"Legislatures to load: {legislatures}")

    conn = sqlite3.connect(db_path)

    if args.force:
        print("--force: dropping existing gaceta tables")
        drop_gaceta_tables(conn)

    for sql in SCHEMA.strip().split(";"):
        sql = sql.strip()
        if sql:
            conn.execute(sql)
    conn.commit()

    all_deputies: list[pd.DataFrame] = []
    for leg in legislatures:
        try:
            deputy_df = ingest_legislature(conn, leg)
            conn.commit()
            all_deputies.append(deputy_df)
        except Exception as e:
            conn.rollback()
            print(f"  ERROR on legislature {leg}: {e}")

    # Deduplicate deputies across all legislatures and insert once
    print("Loading dim_gaceta_deputy (deduplicated across legislatures)...")
    if all_deputies:
        deputies = (
            pd.concat(all_deputies, ignore_index=True)
            .drop_duplicates(subset=["deputy_id"])
            .reset_index(drop=True)
        )
        to_sql_safe(deputies, "dim_gaceta_deputy", conn)
        conn.commit()
        print(f"  {len(deputies):,} unique deputies loaded")

    # Row count summary
    print("\n── Row counts ──────────────────────────────────────────")
    for table in ["dim_gaceta_vote", "dim_gaceta_deputy",
                  "fact_gaceta_vote_summary", "fact_gaceta_deputy_vote"]:
        n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {table}: {n:,}")

    # Per-legislature vote counts
    print("\n── Votes per legislature ───────────────────────────────")
    rows = conn.execute(
        "SELECT legislature, COUNT(*) FROM dim_gaceta_vote GROUP BY legislature ORDER BY legislature"
    ).fetchall()
    for leg, n in rows:
        print(f"  L{leg}: {n:,} votes")

    qa_ok = validate(conn, legislatures)
    conn.close()

    if not qa_ok:
        print("Ingest completed with QA errors — see ERROR lines above.")
        sys.exit(1)

    print("Done.")


if __name__ == "__main__":
    main()
