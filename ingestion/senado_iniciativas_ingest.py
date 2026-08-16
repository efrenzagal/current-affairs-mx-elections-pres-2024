"""
Load Senado initiative-proposer data into the warehouse.

Reads the CSV produced by
camara_de_senadores/iniciativas/crawl_senado_iniciativas.py and populates:

    dim_senado_iniciativa — one row per initiative, with proposer identity
    (when named), parliamentary group, committee referral, and publication
    date. Primary key is the source's own "ID Publicación".

No vote join exists yet for this table (unlike dim_gaceta_iniciativa) --
no reliable direct link between a Senado initiative and its eventual
dim_senado_vote row was found; that's flagged as future work.

Usage:
    /usr/bin/python3 ingestion/senado_iniciativas_ingest.py
    /usr/bin/python3 ingestion/senado_iniciativas_ingest.py --force
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import pandas as pd

from ingestion.congress_roster_ingest import canonical_party_from_text

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "election_data.db"
CLEAN_PATH = ROOT / "data" / "clean_senado_iniciativas" / "dim_senado_iniciativa.csv"

SCHEMA = """
CREATE TABLE IF NOT EXISTS dim_senado_iniciativa (
    senado_iniciativa_id  INTEGER PRIMARY KEY,
    category               TEXT,
    title                  TEXT,
    proposer_type          TEXT,
    proposer_name          TEXT,
    proposer_party         TEXT,
    proposer_party_canonical TEXT,
    proposer_raw           TEXT,
    comision                TEXT,
    fecha                    TEXT,
    source_url               TEXT,
    needs_review              INTEGER
);
"""

KNOWN_PROPOSER_TYPES = {"legislador", "otro"}


def _warn(msg: str) -> None:
    print(f"  WARNING: {msg}")


def _fail(msg: str) -> None:
    print(f"  ERROR:   {msg}")


def drop_table(conn: sqlite3.Connection) -> None:
    conn.execute("DROP TABLE IF EXISTS dim_senado_iniciativa")
    conn.commit()


def ingest(conn: sqlite3.Connection, clean_path: Path) -> None:
    df = pd.read_csv(clean_path)
    df = df.dropna(subset=["senado_iniciativa_id"]).copy()
    df["senado_iniciativa_id"] = df["senado_iniciativa_id"].astype(int)
    df["proposer_party_canonical"] = df["proposer_party"].map(canonical_party_from_text)

    before = len(df)
    df = df.drop_duplicates(subset=["senado_iniciativa_id"], keep="first")
    if len(df) < before:
        _warn(f"Dropped {before - len(df):,} duplicate initiative rows")

    cols = [
        "senado_iniciativa_id", "category", "title", "proposer_type",
        "proposer_name", "proposer_party", "proposer_party_canonical",
        "proposer_raw", "comision", "fecha", "source_url", "needs_review",
    ]
    df = df[cols]

    chunksize = max(1, 999 // len(df.columns))
    df.to_sql("dim_senado_iniciativa", conn, if_exists="append", index=False,
               method="multi", chunksize=chunksize)
    conn.commit()
    print(f"  iniciativas={len(df):,}")


def validate(conn: sqlite3.Connection) -> bool:
    print("\n── QA ──────────────────────────────────────────────────")
    hard_ok = True

    dup_ids = conn.execute("""
        SELECT COUNT(*) FROM (
            SELECT senado_iniciativa_id FROM dim_senado_iniciativa
            GROUP BY senado_iniciativa_id HAVING COUNT(*) > 1
        )
    """).fetchone()[0]
    if dup_ids:
        _fail(f"dim_senado_iniciativa has {dup_ids:,} duplicate primary keys — rerun with --force")
        hard_ok = False
    else:
        print("  OK: dim_senado_iniciativa primary keys unique")

    bad_types = conn.execute("""
        SELECT DISTINCT proposer_type FROM dim_senado_iniciativa WHERE proposer_type IS NOT NULL
    """).fetchall()
    unexpected = [r[0] for r in bad_types if r[0] not in KNOWN_PROPOSER_TYPES]
    if unexpected:
        _warn(f"Unexpected proposer_type values: {unexpected}")
    else:
        print("  OK: all proposer_type values are expected")

    review_share = conn.execute(
        "SELECT AVG(needs_review) FROM dim_senado_iniciativa"
    ).fetchone()[0] or 0.0
    print(f"  INFO: needs_review = {review_share:.1%} of rows")

    unmapped_party = conn.execute("""
        SELECT COUNT(*) FROM dim_senado_iniciativa
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
                         help="Drop and recreate dim_senado_iniciativa before loading")
    args = parser.parse_args()

    db_path = Path(args.db)
    clean_path = Path(args.clean_path)

    if not clean_path.exists():
        print(f"No clean initiative data found at {clean_path}")
        return

    print(f"Warehouse: {db_path}")
    conn = sqlite3.connect(db_path)

    if args.force:
        print("--force: dropping existing dim_senado_iniciativa")
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
