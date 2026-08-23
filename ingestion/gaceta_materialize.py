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

    gaceta_vote_quality.parquet
        One row per vote: summary/detail reconciliation flags. Used to
        exclude incomplete deputy-detail roll calls from person/party metrics.

    gaceta_party_vote_positions.parquet
        One row per party × complete roll call with the party's substantive
        position: (Favor - Contra) / (Favor + Contra). Abstentions, absences,
        and quorum records are excluded from this measure.

    gaceta_party_vote_correlations.parquet
        Pearson correlations between party positions within each legislature.

    gaceta_party_vote_correlations_rolling.parquet
        The same correlations in trailing six-month windows, for tracking
        party alignment through time.

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
import math
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
MIN_PARTY_DIRECTIONAL_VOTES = 5
MIN_CORRELATION_VOTES = 20
ROLLING_CORRELATION_DAYS = 183


def add_vote_thresholds(votes: pd.DataFrame) -> pd.DataFrame:
    votes = votes.copy()
    for col in ["favor", "contra", "abstencion", "ausente", "total"]:
        votes[col] = pd.to_numeric(votes[col], errors="coerce").fillna(0).astype(int)

    if "quorum" not in votes.columns:
        votes["quorum"] = 0
    votes["quorum"] = pd.to_numeric(votes["quorum"], errors="coerce").fillna(0).astype(int)

    votes["presentes"] = (
        votes["favor"] + votes["contra"] + votes["abstencion"] + votes["quorum"]
    )
    votes["quorum_requerido"] = (votes["total"] // 2) + 1
    votes["mayoria_absoluta_requerida"] = (votes["presentes"] // 2) + 1
    votes["mayoria_calificada_requerida"] = votes["presentes"].map(
        lambda x: int(math.ceil((2 * x) / 3)) if x > 0 else 0
    )

    votes["quorum_ok"] = votes["presentes"] >= votes["quorum_requerido"]
    votes["mayoria_simple_ok"] = votes["favor"] > votes["contra"]
    votes["mayoria_absoluta_ok"] = votes["favor"] >= votes["mayoria_absoluta_requerida"]
    votes["mayoria_calificada_ok"] = votes["favor"] >= votes["mayoria_calificada_requerida"]
    return votes


LXVI_VOTE_THRESHOLD_SCHEMA = """
CREATE TABLE IF NOT EXISTS fact_legislature_66_vote_threshold (
    gaceta_vote_id                TEXT NOT NULL PRIMARY KEY,
    presentes                     INTEGER NOT NULL,
    quorum_requerido              INTEGER NOT NULL,
    mayoria_absoluta_requerida    INTEGER NOT NULL,
    mayoria_calificada_requerida  INTEGER NOT NULL,
    quorum_ok                     INTEGER NOT NULL CHECK (quorum_ok IN (0, 1)),
    mayoria_simple_ok             INTEGER NOT NULL CHECK (mayoria_simple_ok IN (0, 1)),
    mayoria_absoluta_ok           INTEGER NOT NULL CHECK (mayoria_absoluta_ok IN (0, 1)),
    mayoria_calificada_ok         INTEGER NOT NULL CHECK (mayoria_calificada_ok IN (0, 1)),
    created_at                    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def materialize_lxvi_vote_thresholds(conn: sqlite3.Connection) -> int:
    """Store quorum and the three majority thresholds for the LXVI Camara.

    Camara-only on purpose. The arithmetic keys off `total` meaning the full
    500-seat chamber, which is what the Gaceta tally reports. A Senado tally's
    total is the number of senators recorded in that roll call, not the 128-seat
    chamber, so the same formula there would compute a quorum floor against a
    denominator that already excludes the absent.

    Persisted rather than recomputed per consumer: "mayoria calificada" has to
    mean the same thing in Streamlit and on the website, and the parquet the
    Streamlit app reads is gitignored, so the static exporter could not share it.
    """
    votes = load_votes(conn)
    votes = votes[votes["legislature"] == 66]
    if votes.empty:
        return 0
    derived = add_vote_thresholds(votes)

    conn.executescript(LXVI_VOTE_THRESHOLD_SCHEMA)
    with conn:
        conn.execute("DELETE FROM fact_legislature_66_vote_threshold")
        conn.executemany(
            """
            INSERT INTO fact_legislature_66_vote_threshold (
                gaceta_vote_id, presentes, quorum_requerido,
                mayoria_absoluta_requerida, mayoria_calificada_requerida,
                quorum_ok, mayoria_simple_ok, mayoria_absoluta_ok,
                mayoria_calificada_ok
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    str(row.gaceta_vote_id),
                    int(row.presentes),
                    int(row.quorum_requerido),
                    int(row.mayoria_absoluta_requerida),
                    int(row.mayoria_calificada_requerida),
                    int(bool(row.quorum_ok)),
                    int(bool(row.mayoria_simple_ok)),
                    int(bool(row.mayoria_absoluta_ok)),
                    int(bool(row.mayoria_calificada_ok)),
                )
                for row in derived.itertuples()
            ],
        )
    return len(derived)


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
            v.legislature,
            v.vote_date
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
            s.quorum,
            s.ausente,
            s.total
        FROM dim_gaceta_vote v
        LEFT JOIN (
            SELECT
                gaceta_vote_id,
                SUM(CASE WHEN vote_choice = 'Favor'      AND party_key = 'Total' THEN count ELSE 0 END) AS favor,
                SUM(CASE WHEN vote_choice = 'Contra'     AND party_key = 'Total' THEN count ELSE 0 END) AS contra,
                SUM(CASE WHEN vote_choice IN ('Abstención','Abstencion') AND party_key = 'Total' THEN count ELSE 0 END) AS abstencion,
                SUM(CASE WHEN vote_choice = 'Quórum *'   AND party_key = 'Total' THEN count ELSE 0 END) AS quorum,
                SUM(CASE WHEN vote_choice = 'Ausente'    AND party_key = 'Total' THEN count ELSE 0 END) AS ausente,
                SUM(CASE WHEN vote_choice = 'Total'      AND party_key = 'Total' THEN count ELSE 0 END) AS total
            FROM fact_gaceta_vote_summary
            GROUP BY gaceta_vote_id
        ) s ON v.gaceta_vote_id = s.gaceta_vote_id
        ORDER BY v.legislature, v.vote_date
    """, conn)


def load_vote_quality(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query("""
        WITH summary_totals AS (
            SELECT
                gaceta_vote_id,
                SUM(CASE
                    WHEN vote_choice <> 'Total' AND party_key = 'Total'
                    THEN count ELSE 0
                END) AS summary_choice_total,
                SUM(CASE
                    WHEN vote_choice = 'Total' AND party_key <> 'Total'
                    THEN count ELSE 0
                END) AS summary_party_total,
                SUM(CASE
                    WHEN vote_choice = 'Total' AND party_key = 'Total'
                    THEN count ELSE 0
                END) AS summary_grand_total
            FROM fact_gaceta_vote_summary
            GROUP BY gaceta_vote_id
        ),
        detail_totals AS (
            SELECT gaceta_vote_id, COUNT(*) AS detail_rows
            FROM fact_gaceta_deputy_vote
            GROUP BY gaceta_vote_id
        )
        SELECT
            v.gaceta_vote_id,
            v.legislature,
            COALESCE(s.summary_choice_total, 0) AS summary_choice_total,
            COALESCE(s.summary_party_total, 0) AS summary_party_total,
            COALESCE(s.summary_grand_total, 0) AS summary_grand_total,
            COALESCE(d.detail_rows, 0) AS detail_rows,
            COALESCE(s.summary_party_total, 0) - COALESCE(d.detail_rows, 0) AS missing_detail_rows,
            COALESCE(s.summary_choice_total, 0) - COALESCE(s.summary_party_total, 0) AS choice_party_total_diff,
            COALESCE(s.summary_grand_total, 0) - COALESCE(s.summary_party_total, 0) AS grand_party_total_diff,
            CASE
                WHEN COALESCE(s.summary_party_total, 0) = COALESCE(d.detail_rows, 0)
                THEN 1 ELSE 0
            END AS detail_complete
        FROM dim_gaceta_vote v
        LEFT JOIN summary_totals s ON v.gaceta_vote_id = s.gaceta_vote_id
        LEFT JOIN detail_totals d ON v.gaceta_vote_id = d.gaceta_vote_id
        ORDER BY v.legislature, v.gaceta_vote_id
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


# ── Party positions and pairwise alignment ───────────────────────────────────

def build_party_vote_positions(df: pd.DataFrame) -> pd.DataFrame:
    """Summarise each party's substantive position on each complete roll call."""
    directional = df[df["vote_choice"].isin({"Favor", "Contra", "A favor", "En contra"})].copy()
    directional["direction"] = directional["vote_choice"].map({
        "Favor": 1, "A favor": 1, "Contra": -1, "En contra": -1,
    })
    positions = (
        directional.groupby(["legislature", "gaceta_vote_id", "vote_date", "party_key"])
        .agg(
            directional_votes=("direction", "size"),
            favor=("direction", lambda values: (values == 1).sum()),
            contra=("direction", lambda values: (values == -1).sum()),
            position=("direction", "mean"),
        )
        .reset_index()
    )
    positions = positions[positions["directional_votes"] >= MIN_PARTY_DIRECTIONAL_VOTES].copy()
    positions["position"] = positions["position"].round(6)
    positions["vote_date"] = pd.to_datetime(positions["vote_date"], errors="coerce")
    return positions.sort_values(["legislature", "vote_date", "gaceta_vote_id", "party_key"])


def _pairwise_correlations(positions: pd.DataFrame, group_columns: list[str]) -> pd.DataFrame:
    """Correlate all party pairs on the roll calls they have in common."""
    rows: list[dict[str, object]] = []
    for group_values, group in positions.groupby(group_columns, dropna=False):
        if not isinstance(group_values, tuple):
            group_values = (group_values,)
        wide = group.pivot(index="gaceta_vote_id", columns="party_key", values="position")
        parties = sorted(wide.columns)
        for left_index, party_a in enumerate(parties):
            for party_b in parties[left_index + 1:]:
                paired = wide[[party_a, party_b]].dropna()
                n = len(paired)
                if (n < MIN_CORRELATION_VOTES or
                        paired[party_a].nunique() < 2 or
                        paired[party_b].nunique() < 2):
                    continue
                correlation = paired[party_a].corr(paired[party_b])
                if pd.isna(correlation):
                    continue
                row = dict(zip(group_columns, group_values))
                row.update({
                    "party_a": party_a,
                    "party_b": party_b,
                    "roll_calls": n,
                    "pearson_correlation": round(float(correlation), 6),
                })
                rows.append(row)
    return pd.DataFrame(rows)


def build_party_vote_correlations(positions: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return legislature-level and trailing-six-month party correlations."""
    overall = _pairwise_correlations(positions, ["legislature"])

    dated = positions.dropna(subset=["vote_date"]).copy()
    window_rows: list[pd.DataFrame] = []
    for legislature, group in dated.groupby("legislature"):
        endpoints = group["vote_date"].drop_duplicates().sort_values()
        for window_end in endpoints:
            window_start = window_end - pd.Timedelta(days=ROLLING_CORRELATION_DAYS)
            window = group[(group["vote_date"] > window_start) & (group["vote_date"] <= window_end)]
            correlations = _pairwise_correlations(window, ["legislature"])
            if correlations.empty:
                continue
            correlations["window_start"] = window_start.date().isoformat()
            correlations["window_end"] = window_end.date().isoformat()
            window_rows.append(correlations)
    rolling = pd.concat(window_rows, ignore_index=True) if window_rows else pd.DataFrame()
    return overall, rolling


# ── Main ──────────────────────────────────────────────────────────────────────

def materialize(db_path: Path, out_dir: Path, force: bool) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    outputs = {
        "gaceta_deputy_alignment.parquet": out_dir / "gaceta_deputy_alignment.parquet",
        "gaceta_party_cohesion.parquet":   out_dir / "gaceta_party_cohesion.parquet",
        "gaceta_vote_index.parquet":       out_dir / "gaceta_vote_index.parquet",
        "gaceta_vote_quality.parquet":     out_dir / "gaceta_vote_quality.parquet",
        "gaceta_party_vote_positions.parquet": out_dir / "gaceta_party_vote_positions.parquet",
        "gaceta_party_vote_correlations.parquet": out_dir / "gaceta_party_vote_correlations.parquet",
        "gaceta_party_vote_correlations_rolling.parquet": out_dir / "gaceta_party_vote_correlations_rolling.parquet",
    }

    if not force and all(p.exists() for p in outputs.values()):
        print("All outputs already exist. Use --force to regenerate.")
        return

    conn = connect(db_path)

    print("Loading gaceta_vote_index...")
    votes = load_votes(conn)
    votes = add_vote_thresholds(votes)
    votes.to_parquet(outputs["gaceta_vote_index.parquet"], index=False)
    print(f"  → {len(votes):,} votes written")

    print("Loading gaceta_vote_quality...")
    quality = load_vote_quality(conn)
    quality.to_parquet(outputs["gaceta_vote_quality.parquet"], index=False)
    incomplete = quality[quality["detail_complete"] == 0]
    print(f"  → {len(quality):,} quality rows written ({len(incomplete):,} incomplete)")

    print("Loading deputy votes for alignment + cohesion...")
    df       = load_deputy_votes(conn)
    deputies = load_deputies(conn)
    complete_vote_ids = set(quality.loc[quality["detail_complete"] == 1, "gaceta_vote_id"])
    before_votes = df["gaceta_vote_id"].nunique()
    df = df[df["gaceta_vote_id"].isin(complete_vote_ids)].copy()
    after_votes = df["gaceta_vote_id"].nunique()
    print(f"  Using {after_votes:,}/{before_votes:,} complete-detail votes for deputy metrics")

    print("Building gaceta_deputy_alignment...")
    alignment = build_deputy_alignment(df, deputies)
    alignment.to_parquet(outputs["gaceta_deputy_alignment.parquet"], index=False)
    print(f"  → {len(alignment):,} deputy × legislature rows written")

    print("Building gaceta_party_cohesion...")
    cohesion = build_party_cohesion(df)
    cohesion.to_parquet(outputs["gaceta_party_cohesion.parquet"], index=False)
    print(f"  → {len(cohesion):,} party × legislature rows written")

    print("Building party positions and correlations...")
    positions = build_party_vote_positions(df)
    positions.to_parquet(outputs["gaceta_party_vote_positions.parquet"], index=False)
    correlations, rolling_correlations = build_party_vote_correlations(positions)
    correlations.to_parquet(outputs["gaceta_party_vote_correlations.parquet"], index=False)
    rolling_correlations.to_parquet(outputs["gaceta_party_vote_correlations_rolling.parquet"], index=False)
    print(f"  → {len(positions):,} party × vote positions, {len(correlations):,} legislature pairs, "
          f"{len(rolling_correlations):,} rolling-window pairs written")

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
