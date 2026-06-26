"""
build_timeseries.py
===================
Reads from election_data.db and writes a single flat Parquet file:
    data/materialized/timeseries_estados.parquet

Each row is one (election_id, year, election_type, nombre_estado, party_key)
combination with:
  - votes_raw          : votes as recorded (coalition rows kept intact)
  - votes_split        : coalition votes split proportionally to member parties
                         (direct votes for non-coalition parties are unchanged)
  - total_votos        : total votes cast in that election x state
  - lista_nominal      : registered voters (from non-special casillas)
  - pct_raw            : votes_raw / total_votos
  - pct_split          : votes_split / total_votos
  - is_coalition        : whether this party_key is a coalition

Coalition splitting logic (mirrors the R script):
  For each (election, state, coalition_key), the coalition's votes are
  distributed to member parties proportionally to those members' own
  direct-vote counts in the same (election, state). If a member has 0
  direct votes (e.g. didn't run solo that cycle), weight falls back to
  equal share across members.

Usage:
    python build_timeseries.py
    python build_timeseries.py --db path/to/election_data.db
    python build_timeseries.py --out path/to/output_dir
"""

import argparse
import sqlite3
import unicodedata
from pathlib import Path

import pandas as pd

# ── Config ─────────────────────────────────────────────────────────────────────

DB_PATH  = "election_data.db"
OUT_DIR  = Path("data/materialized")
OUT_FILE = "timeseries_estados.parquet"

# Canonical party normalisation across cycles
# (2018 used full name; we normalise to short key here)
PARTY_ALIASES = {
    "MOVIMIENTO CIUDADANO": "MC",
    "NUEVA ALIANZA":        "NUEVA ALIANZA",  # keep as-is; minor party
    "ENCUENTRO SOCIAL":     "ENCUENTRO SOCIAL",
}

# ── Helpers ────────────────────────────────────────────────────────────────────

def _norm(s: str) -> str:
    s = str(s).upper().strip()
    s = unicodedata.normalize("NFD", s)
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def load_raw_votes(conn: sqlite3.Connection) -> pd.DataFrame:
    """Pull state-level vote totals for all elections, all parties."""
    return pd.read_sql_query("""
        SELECT
            e.election_id,
            e.year,
            e.election_type,
            g.id_estado,
            g.nombre_estado,
            f.party_key,
            SUM(f.votes)       AS votes_raw,
            SUM(f.total_votos) AS total_votos
        FROM fact_casilla_vote f
        JOIN dim_casilla   c ON  f.casilla_id  = c.casilla_id
                             AND f.election_id  = c.election_id
        JOIN dim_geography g ON  c.geo_id       = g.geo_id
        JOIN dim_election  e ON  f.election_id  = e.election_id
        WHERE c.tipo_casilla != 'S'
          AND g.seccion        > 0
        GROUP BY
            e.election_id, e.year, e.election_type,
            g.id_estado, g.nombre_estado,
            f.party_key
        ORDER BY e.year, g.id_estado, f.party_key
    """, conn)


def load_lista_nominal(conn: sqlite3.Connection) -> pd.DataFrame:
    """State-level lista nominal per election (non-special casillas only)."""
    return pd.read_sql_query("""
        SELECT
            c.election_id,
            g.id_estado,
            SUM(c.lista_nominal) AS lista_nominal
        FROM dim_casilla   c
        JOIN dim_geography g ON c.geo_id = g.geo_id
        WHERE c.tipo_casilla != 'S'
          AND g.seccion        > 0
        GROUP BY c.election_id, g.id_estado
    """, conn)


def load_coalitions(conn: sqlite3.Connection) -> pd.DataFrame:
    """Return a table of coalition_key → member_key pairs."""
    raw = pd.read_sql_query("""
        SELECT party_key, members
        FROM dim_party
        WHERE is_coalition = 1
          AND members IS NOT NULL
          AND members != ''
    """, conn)

    rows = []
    for _, r in raw.iterrows():
        for member in r["members"].split(","):
            member = member.strip()
            member = PARTY_ALIASES.get(member, member)
            rows.append({"coalition_key": r["party_key"], "member_key": member})
    return pd.DataFrame(rows)


def split_coalitions(
    state_raw: pd.DataFrame,
    coalitions: pd.DataFrame,
) -> pd.DataFrame:
    """
    For each (election_id, id_estado, coalition_key), distribute coalition
    votes to member parties proportional to each member's own direct votes.
    Fallback: equal weight when all members have 0 direct votes.

    Returns a long DataFrame with columns:
        election_id, id_estado, party_key, votes_split
    where only NON-coalition rows are included (coalitions are dissolved).
    """
    # normalise party aliases in raw data
    state_raw = state_raw.copy()
    state_raw["party_key"] = state_raw["party_key"].replace(PARTY_ALIASES)

    coalition_keys = set(coalitions["coalition_key"].unique())

    direct = state_raw[~state_raw["party_key"].isin(coalition_keys)][
        ["election_id", "id_estado", "party_key", "votes_raw"]
    ].rename(columns={"votes_raw": "direct_votes"})

    coalition_rows = state_raw[state_raw["party_key"].isin(coalition_keys)][
        ["election_id", "id_estado", "party_key", "votes_raw"]
    ].rename(columns={"party_key": "coalition_key", "votes_raw": "coalition_votes"})

    # Join coalition → members
    attributed = coalition_rows.merge(coalitions, on="coalition_key", how="inner")

    # Join member direct votes (for weighting)
    attributed = attributed.merge(
        direct.rename(columns={"party_key": "member_key", "direct_votes": "member_indiv"}),
        on=["election_id", "id_estado", "member_key"],
        how="left",
    )
    attributed["member_indiv"] = attributed["member_indiv"].fillna(0.0)

    # Compute weights within each (election, state, coalition)
    grp = attributed.groupby(["election_id", "id_estado", "coalition_key"])
    attributed["total_member_indiv"] = grp["member_indiv"].transform("sum")
    attributed["n_members"]          = grp["member_key"].transform("count")

    attributed["weight"] = attributed.apply(
        lambda r: (
            r["member_indiv"] / r["total_member_indiv"]
            if r["total_member_indiv"] > 0
            else 1.0 / r["n_members"]
        ),
        axis=1,
    )
    attributed["attributed_votes"] = attributed["coalition_votes"] * attributed["weight"]

    # Sum attributed votes to member parties
    split_attributed = (
        attributed
        .groupby(["election_id", "id_estado", "member_key"], as_index=False)["attributed_votes"]
        .sum()
        .rename(columns={"member_key": "party_key", "attributed_votes": "votes_split_from_coalitions"})
    )

    # Combine: direct votes + attributed votes
    combined = direct.merge(
        split_attributed,
        on=["election_id", "id_estado", "party_key"],
        how="outer",
    )
    combined["direct_votes"]                 = combined["direct_votes"].fillna(0.0)
    combined["votes_split_from_coalitions"]  = combined["votes_split_from_coalitions"].fillna(0.0)
    combined["votes_split"] = combined["direct_votes"] + combined["votes_split_from_coalitions"]

    return combined[["election_id", "id_estado", "party_key", "votes_split"]]


# ── Main ───────────────────────────────────────────────────────────────────────

def build(db_path: str = DB_PATH, out_dir: Path = OUT_DIR):
    print(f"Connecting to {db_path}...")
    conn = sqlite3.connect(db_path)

    print("Loading raw votes...")
    state_raw = load_raw_votes(conn)
    state_raw["party_key"] = state_raw["party_key"].replace(PARTY_ALIASES)
    state_raw["nombre_estado"] = state_raw["nombre_estado"].str.strip()

    print("Loading lista nominal...")
    lista = load_lista_nominal(conn)

    print("Loading coalition definitions...")
    coalitions = load_coalitions(conn)

    print("Splitting coalition votes...")
    split = split_coalitions(state_raw, coalitions)

    # ── Assemble final table ───────────────────────────────────────────────────

    # Base: raw votes per (election, state, party)
    # We keep coalition rows in votes_raw; they won't appear in votes_split
    election_meta = pd.read_sql_query(
        "SELECT election_id, year, election_type FROM dim_election", conn
    )
    conn.close()

    # total_votos per (election, state) — sum across all parties (deduplicated)
    total_votos = (
        state_raw
        .drop_duplicates(subset=["election_id", "id_estado", "party_key"])
        .groupby(["election_id", "id_estado"], as_index=False)["total_votos"]
        .max()  # total_votos is the same value repeated per party in each casilla
    )
    # Actually total_votos in our raw is per-party row, so we want it as a
    # state-level total votes cast — use the max across parties since it's
    # a repeated value (same casilla total on every party row).
    # Re-derive properly: sum of votes across ALL party_keys including nulos etc.
    # We'll use the max of total_votos per (election, estado) as the true total.
    total_per_state = (
        state_raw
        .groupby(["election_id", "id_estado"], as_index=False)["total_votos"]
        .max()
        .rename(columns={"total_votos": "total_votos_estado"})
    )

    # Identify coalition party_keys
    coalition_keys = set(coalitions["coalition_key"].unique())

    # Mark each row
    state_raw["is_coalition"] = state_raw["party_key"].isin(coalition_keys)

    # Merge split votes back onto base
    df = state_raw.merge(
        split,
        on=["election_id", "id_estado", "party_key"],
        how="left",
    )
    # For coalition rows, votes_split is the dissolved total (not applicable);
    # for direct rows, votes_split = direct + attributed from coalitions
    # Coalition rows themselves get votes_split = NaN which is correct —
    # they are dissolved into members in the split view.

    # Merge lista nominal
    df = df.merge(lista, on=["election_id", "id_estado"], how="left")

    # Merge state totals
    df = df.merge(total_per_state, on=["election_id", "id_estado"], how="left")

    # Percentages
    df["pct_raw"]   = df["votes_raw"]   / df["total_votos_estado"] * 100
    df["pct_split"] = df["votes_split"] / df["total_votos_estado"] * 100

    # Clean up column names and types
    df = df.rename(columns={"total_votos_estado": "total_votos_estado"})
    df["year"]           = df["year"].astype(int)
    df["id_estado"]      = df["id_estado"].astype(int)
    df["votes_raw"]      = df["votes_raw"].astype(float)
    df["votes_split"]    = df["votes_split"].astype(float)
    df["lista_nominal"]  = df["lista_nominal"].astype(float)

    # Drop total_votos (redundant with total_votos_estado)
    df = df.drop(columns=["total_votos"], errors="ignore")

    # Sort
    df = df.sort_values(["year", "election_type", "id_estado", "party_key"]).reset_index(drop=True)

    # Write
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / OUT_FILE
    df.to_parquet(out_path, index=False)

    mb = out_path.stat().st_size / 1024 / 1024
    print(f"\n✓ Written: {out_path}  ({len(df):,} rows · {mb:.2f} MB)")
    print(f"\nColumns: {list(df.columns)}")
    print(f"\nElections: {sorted(df['election_id'].unique())}")
    print(f"States:    {df['nombre_estado'].nunique()}")
    print(f"Parties:   {sorted(df['party_key'].unique())}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build timeseries parquet for Streamlit")
    parser.add_argument("--db",  default=DB_PATH,        help="Path to election_data.db")
    parser.add_argument("--out", default=str(OUT_DIR),   help="Output directory")
    args = parser.parse_args()
    build(db_path=args.db, out_dir=Path(args.out))