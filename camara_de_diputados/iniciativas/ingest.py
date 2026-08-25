"""
Load Camara de Diputados initiative-proposer data into the warehouse.

Reads the CSV produced by
camara_de_diputados/iniciativas/crawl_gaceta_iniciativas.py and populates:

    dim_gaceta_iniciativa — one row per initiative, with proposer identity
    (when named), parliamentary group, committee referral, and — when the
    initiative reached a floor vote — the vote_url that joins to
    dim_gaceta_vote.source_url.

After loading, runs automatic QA. Hard errors abort with exit code 1;
soft warnings are printed but do not abort.

Usage:
    /usr/bin/python3 camara_de_diputados/iniciativas/ingest.py
    /usr/bin/python3 camara_de_diputados/iniciativas/ingest.py --force
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import pandas as pd

from lib.canonical import canonical_party_from_text

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "election_data.db"
CLEAN_PATH = ROOT / "data" / "clean_gaceta_iniciativas" / "dim_gaceta_iniciativa.csv"

SCHEMA = """
CREATE TABLE IF NOT EXISTS dim_gaceta_iniciativa (
    gaceta_iniciativa_id  TEXT PRIMARY KEY,
    legislature           INTEGER NOT NULL,
    sequence_number        INTEGER,
    title                  TEXT,
    proposer_type          TEXT,
    proposer_name          TEXT,
    proposer_party         TEXT,
    proposer_party_canonical TEXT,
    proposer_raw           TEXT,
    comision                TEXT,
    gaceta_number           TEXT,
    gaceta_date             TEXT,
    vote_url                TEXT,
    period_date             TEXT,
    period_url              TEXT,
    needs_review             INTEGER
);
"""

KNOWN_PROPOSER_TYPES = {"legislador", "ejecutivo", "minuta", "otro"}


def _warn(msg: str) -> None:
    print(f"  WARNING: {msg}")


def _fail(msg: str) -> None:
    print(f"  ERROR:   {msg}")


def drop_table(conn: sqlite3.Connection) -> None:
    conn.execute("DROP TABLE IF EXISTS dim_gaceta_iniciativa")
    conn.commit()


def ingest(conn: sqlite3.Connection, clean_path: Path) -> None:
    df = pd.read_csv(clean_path)
    df = df.dropna(subset=["legislature", "sequence_number"]).copy()
    df["legislature"] = df["legislature"].astype(int)
    df["sequence_number"] = df["sequence_number"].astype(int)
    df["gaceta_iniciativa_id"] = (
        "GACETA_INI_L" + df["legislature"].astype(str) + "_" + df["sequence_number"].astype(str)
    )
    df["proposer_party_canonical"] = df["proposer_party"].map(canonical_party_from_text)

    before = len(df)
    df = df.drop_duplicates(subset=["gaceta_iniciativa_id"], keep="first")
    if len(df) < before:
        _warn(f"Dropped {before - len(df):,} duplicate initiative rows")

    cols = [
        "gaceta_iniciativa_id", "legislature", "sequence_number", "title",
        "proposer_type", "proposer_name", "proposer_party", "proposer_party_canonical",
        "proposer_raw", "comision", "gaceta_number", "gaceta_date", "vote_url",
        "period_date", "period_url", "needs_review",
    ]
    df = df[cols]

    chunksize = max(1, 999 // len(df.columns))
    df.to_sql("dim_gaceta_iniciativa", conn, if_exists="append", index=False,
               method="multi", chunksize=chunksize)
    conn.commit()
    print(f"  iniciativas={len(df):,}")


def validate(conn: sqlite3.Connection) -> bool:
    print("\n── QA ──────────────────────────────────────────────────")
    hard_ok = True

    dup_ids = conn.execute("""
        SELECT COUNT(*) FROM (
            SELECT gaceta_iniciativa_id FROM dim_gaceta_iniciativa
            GROUP BY gaceta_iniciativa_id HAVING COUNT(*) > 1
        )
    """).fetchone()[0]
    if dup_ids:
        _fail(f"dim_gaceta_iniciativa has {dup_ids:,} duplicate primary keys — rerun with --force")
        hard_ok = False
    else:
        print("  OK: dim_gaceta_iniciativa primary keys unique")

    bad_types = conn.execute("""
        SELECT DISTINCT proposer_type FROM dim_gaceta_iniciativa WHERE proposer_type IS NOT NULL
    """).fetchall()
    unexpected = [r[0] for r in bad_types if r[0] not in KNOWN_PROPOSER_TYPES]
    if unexpected:
        _warn(f"Unexpected proposer_type values: {unexpected}")
    else:
        print("  OK: all proposer_type values are expected")

    total = conn.execute("SELECT COUNT(*) FROM dim_gaceta_iniciativa").fetchone()[0]
    with_vote = conn.execute(
        "SELECT COUNT(*) FROM dim_gaceta_iniciativa WHERE vote_url IS NOT NULL"
    ).fetchone()[0]
    resolved = conn.execute("""
        SELECT COUNT(*) FROM dim_gaceta_iniciativa i
        JOIN dim_gaceta_vote v ON v.source_url = i.vote_url
        WHERE i.vote_url IS NOT NULL
    """).fetchone()[0]
    if with_vote and resolved < with_vote:
        _warn(f"{with_vote - resolved:,}/{with_vote:,} vote_url values don't resolve against dim_gaceta_vote.source_url")
    else:
        print(f"  OK: all {with_vote:,} vote_url values resolve against dim_gaceta_vote (of {total:,} total)")

    review_share = conn.execute(
        "SELECT AVG(needs_review) FROM dim_gaceta_iniciativa"
    ).fetchone()[0] or 0.0
    print(f"  INFO: needs_review = {review_share:.1%} of rows")

    unmapped_party = conn.execute("""
        SELECT COUNT(*) FROM dim_gaceta_iniciativa
        WHERE proposer_type = 'legislador' AND proposer_party_canonical IS NULL
    """).fetchone()[0]
    if unmapped_party:
        _warn(f"{unmapped_party:,} legislador rows have an unmapped proposer_party_canonical")
    else:
        print("  OK: every legislador row has a canonical party")

    print("────────────────────────────────────────────────────────")
    return hard_ok


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--clean-path", default=str(CLEAN_PATH))
    parser.add_argument("--force", action="store_true",
                         help="Drop and recreate dim_gaceta_iniciativa before loading")
    args = parser.parse_args()

    db_path = Path(args.db)
    clean_path = Path(args.clean_path)

    if not clean_path.exists():
        print(f"No clean initiative data found at {clean_path}")
        return

    print(f"Warehouse: {db_path}")
    conn = sqlite3.connect(db_path)

    if args.force:
        print("--force: dropping existing dim_gaceta_iniciativa")
        drop_table(conn)

    conn.execute(SCHEMA)
    conn.commit()

    try:
        ingest(conn, clean_path)
    except Exception as e:
        conn.rollback()
        print(f"  ERROR: {e}")
        conn.close()
        sys.exit(1)

    qa_ok = validate(conn)
    conn.close()

    if not qa_ok:
        print("Ingest completed with QA errors — see ERROR lines above.")
        sys.exit(1)

    print("Done.")


if __name__ == "__main__":
    main()
