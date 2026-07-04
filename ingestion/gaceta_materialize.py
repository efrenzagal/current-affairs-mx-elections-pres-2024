"""
Materialize Gaceta Parlamentaria warehouse data into Streamlit-ready parquets.

Reads from election_data.db and writes to data/materialized/:

    gaceta_deputy_alignment.parquet
        Per deputy × legislature: alignment rate with party majority,
        votes cast, absences, party. Used for deputy ranking views.

    gaceta_party_cohesion.parquet
        Per party × legislature: cohesion score (fraction of votes where
        the party voted unanimously in one direction). Used for party
        discipline overview.

    gaceta_vote_index.parquet
        One row per vote: date, title, status, legislature, outcome counts.
        Used for the vote browser / timeline.

Alignment definition:
    For each vote, the party majority choice is the most common active
    vote (Favor / Contra / Abstención) among present party members.
    Ausente and Quórum * are excluded from the majority calculation
    but counted as absences. A deputy is "aligned" on a vote if their
    active vote matches the party majority. Alignment rate = aligned /
    votes_with_active_vote.

Usage:
    /usr/bin/python3 ingestion/gaceta_materialize.py
    /usr/bin/python3 ingestion/gaceta_materialize.py --db path/to/other.db
    /usr/bin/python3 ingestion/gaceta_materialize.py --force
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import pandas as pd

ROOT        = Path(__file__).resolve().parents[1]
DB_PATH     = ROOT / "election_data.db"
OUT_DIR     = ROOT / "data" / "materialized"

ABSENCE_CHOICES = {"Ausente", "Quórum *"}
ACTIVE_CHOICES  = {"Favor", "Contra", "Abstención", "Abstencion", "A favor", "En contra"}

# Minimum active votes a deputy must have in a legislature to be included
MIN_ACTIVE_VOTES = 10


def connect(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(db_path)


# ── Raw loads ─────────────────────────────────────────────────────────────────

def load_deputy_votes(conn: sqlite3.Connection) -> pd.DataFrame:
    print("  Loading fact_gaceta_deputy_vote + dim_gaceta_vote...")
    return pd.read_sql_query("""
        SELECT
            f.gaceta_vote_id,
            f.deputy_id,
            f.vote_choice,
            f.party_key,
            v.legislature
        FROM fact_gaceta_deputy_vote f
        JOIN dim_gaceta_vote v ON f.gaceta_vote_id = v.gaceta_vote_id
    """, conn)


def load_deputies(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query("SELECT deputy_id, deputy_name FROM dim_gaceta_deputy", conn)


def load_votes(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query("""
        SELECT
            v.gaceta_vote_id,
            v.legislature,
            v.vote_date,
            v.title,
            v.status_text,
            v.gaceta_number,
            v.vote_context,
            s.favor,
            s.contra,
            s.abstencion,
            s.ausente,
            s.total
        FROM dim_gaceta_vote v
        LEFT JOIN (
            SELECT
                gaceta_vote_id,
                SUM(CASE WHEN vote_choice = 'Favor'      AND party_key = 'Total' THEN count ELSE 0 END) AS favor,
                SUM(CASE WHEN vote_choice = 'Contra'     AND party_key = 'Total' THEN count ELSE 0 END) AS contra,
                SUM(CASE WHEN vote_choice IN ('Abstención','Abstencion') AND party_key = 'Total' THEN count ELSE 0 END) AS abstencion,
                SUM(CASE WHEN vote_choice = 'Ausente'    AND party_key = 'Total' THEN count ELSE 0 END) AS ausente,
                SUM(CASE WHEN vote_choice = 'Total'      AND party_key = 'Total' THEN count ELSE 0 END) AS total
            FROM fact_gaceta_vote_summary
            GROUP BY gaceta_vote_id
        ) s ON v.gaceta_vote_id = s.gaceta_vote_id
        ORDER BY v.legislature, v.vote_date
    """, conn)


# ── Deputy alignment ──────────────────────────────────────────────────────────

def build_deputy_alignment(df: pd.DataFrame, deputies: pd.DataFrame) -> pd.DataFrame:
    print("  Computing party majority per vote...")

    # Classify each row
    df["is_active"]  = df["vote_choice"].isin(ACTIVE_CHOICES)
    df["is_absence"] = df["vote_choice"].isin(ABSENCE_CHOICES)

    # Party majority: most common active choice per (legislature, vote, party)
    active = df[df["is_active"]].copy()
    majority = (
        active.groupby(["legislature", "gaceta_vote_id", "party_key", "vote_choice"])
        .size()
        .reset_index(name="n")
    )
    majority = (
        majority.sort_values("n", ascending=False)
        .drop_duplicates(subset=["legislature", "gaceta_vote_id", "party_key"])
        .rename(columns={"vote_choice": "party_majority"})
        [["legislature", "gaceta_vote_id", "party_key", "party_majority"]]
    )

    # Join majority back to individual rows
    df = df.merge(majority, on=["legislature", "gaceta_vote_id", "party_key"], how="left")

    # Alignment: active vote matches party majority
    df["aligned"] = df["is_active"] & (df["vote_choice"] == df["party_majority"])

    print("  Aggregating per deputy × legislature...")
    agg = (
        df.groupby(["legislature", "deputy_id", "party_key"])
        .agg(
            votes_total   =("gaceta_vote_id", "count"),
            votes_active  =("is_active",  "sum"),
            votes_absent  =("is_absence", "sum"),
            votes_aligned =("aligned",    "sum"),
        )
        .reset_index()
    )

    # Keep only deputies with enough active votes to be meaningful
    agg = agg[agg["votes_active"] >= MIN_ACTIVE_VOTES].copy()

    agg["alignment_rate"] = (agg["votes_aligned"] / agg["votes_active"]).round(4)
    agg["absence_rate"]   = (agg["votes_absent"]  / agg["votes_total"]).round(4)

    # Attach deputy names
    agg = agg.merge(deputies, on="deputy_id", how="left")

    return agg[[
        "legislature", "deputy_id", "deputy_name", "party_key",
        "votes_total", "votes_active", "votes_absent", "votes_aligned",
        "alignment_rate", "absence_rate",
    ]].sort_values(["legislature", "alignment_rate"], ascending=[True, False])


# ── Party cohesion ────────────────────────────────────────────────────────────

def build_party_cohesion(df: pd.DataFrame) -> pd.DataFrame:
    print("  Computing party cohesion per legislature...")

    active = df[df["vote_choice"].isin(ACTIVE_CHOICES)].copy()

    # For each vote × party, get share of the majority choice
    vote_party = (
        active.groupby(["legislature", "gaceta_vote_id", "party_key", "vote_choice"])
        .size()
        .reset_index(name="n")
    )
    vote_totals = vote_party.groupby(["legislature", "gaceta_vote_id", "party_key"])["n"].sum().reset_index(name="total")
    vote_party  = vote_party.merge(vote_totals, on=["legislature", "gaceta_vote_id", "party_key"])
    vote_party["majority_share"] = vote_party["n"] / vote_party["total"]

    # Cohesion per vote = max majority share (1.0 = unanimous)
    cohesion_per_vote = (
        vote_party.groupby(["legislature", "gaceta_vote_id", "party_key"])["majority_share"]
        .max()
        .reset_index(name="cohesion")
    )

    # Average cohesion per party × legislature
    party_cohesion = (
        cohesion_per_vote.groupby(["legislature", "party_key"])
        .agg(
            cohesion_mean =("cohesion", "mean"),
            cohesion_unanimous=("cohesion", lambda x: (x == 1.0).sum()),
            votes_counted =("cohesion", "count"),
        )
        .reset_index()
    )
    party_cohesion["cohesion_mean"]       = party_cohesion["cohesion_mean"].round(4)
    party_cohesion["pct_unanimous"]       = (
        party_cohesion["cohesion_unanimous"] / party_cohesion["votes_counted"]
    ).round(4)

    return party_cohesion.sort_values(["legislature", "cohesion_mean"], ascending=[True, False])


# ── Main ──────────────────────────────────────────────────────────────────────

def materialize(db_path: Path, out_dir: Path, force: bool) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    outputs = {
        "gaceta_deputy_alignment.parquet": out_dir / "gaceta_deputy_alignment.parquet",
        "gaceta_party_cohesion.parquet":   out_dir / "gaceta_party_cohesion.parquet",
        "gaceta_vote_index.parquet":       out_dir / "gaceta_vote_index.parquet",
    }

    if not force and all(p.exists() for p in outputs.values()):
        print("All outputs already exist. Use --force to regenerate.")
        return

    conn = connect(db_path)

    print("Loading gaceta_vote_index...")
    votes = load_votes(conn)
    votes.to_parquet(outputs["gaceta_vote_index.parquet"], index=False)
    print(f"  → {len(votes):,} votes written")

    print("Loading deputy votes for alignment + cohesion...")
    df       = load_deputy_votes(conn)
    deputies = load_deputies(conn)

    print("Building gaceta_deputy_alignment...")
    alignment = build_deputy_alignment(df, deputies)
    alignment.to_parquet(outputs["gaceta_deputy_alignment.parquet"], index=False)
    print(f"  → {len(alignment):,} deputy × legislature rows written")

    print("Building gaceta_party_cohesion...")
    cohesion = build_party_cohesion(df)
    cohesion.to_parquet(outputs["gaceta_party_cohesion.parquet"], index=False)
    print(f"  → {len(cohesion):,} party × legislature rows written")

    conn.close()

    print("\nDone. Files written:")
    for name, path in outputs.items():
        mb = path.stat().st_size / 1_048_576
        print(f"  {name}: {mb:.1f} MB")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db",    default=str(DB_PATH))
    parser.add_argument("--out",   default=str(OUT_DIR))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    materialize(Path(args.db), Path(args.out), args.force)


if __name__ == "__main__":
    main()
