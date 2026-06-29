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

# Canonical party normalisation across cycles — each cycle's ingestion script
# names the same real party differently (2018 spells out full names; 2006 used
# its own short codes). Coalitions are NOT touched here: a coalition is a
# genuinely distinct entity each cycle (different member combinations), so
# only same-party single-party aliases belong in this dict.
PARTY_ALIASES = {
    "MOVIMIENTO CIUDADANO": "MC",
    "NUEVA ALIANZA":        "PANAL",       # 2018 spelling -> 2012/2015's code
    "NVA_A":                "PANAL",       # 2006 spelling -> 2012/2015's code
    "ENCUENTRO SOCIAL":     "PES",         # 2018 spelling -> 2015/2021's code
    "CAND_IND_01":          "CAND_IND_1",  # 2018 numbering -> 2015's convention
    "CAND_IND_02":          "CAND_IND_2",
    "CAND_IND1":            "CAND_IND_1",  # 2024 numbering -> 2015's convention
    "CAND_IND2":            "CAND_IND_2",
}

# Canonical id_estado -> state name. Each cycle's source data spells/accents
# state names differently (e.g. "MEXICO" vs "MÉXICO", "COAHUILA" vs "COAHUILA
# DE ZARAGOZA") even though id_estado (1-32) is already consistent everywhere
# — so override nombre_estado from id_estado rather than trust any one
# cycle's spelling. This is the INEGI-catalog long form, matching what
# 2018/2024's own source data already uses.
# Full Spanish names for standalone (non-coalition) parties, keyed by the
# post-alias canonical code. Coalitions don't need an entry here — their
# label is built from member codes instead (see build_party_labels below).
# Anything not listed here (e.g. UNO_PDM, whose exact historical meaning
# isn't confidently known) just falls back to showing its raw code, which is
# strictly safer than guessing a name that might be wrong.
PARTY_FULL_NAMES = {
    "PAN":      "Acción Nacional",
    "PRI":      "Revolucionario Institucional",
    "PRD":      "Revolución Democrática",
    "PVEM":     "Verde Ecologista de México",
    "PT":       "Trabajo",
    "MC":       "Movimiento Ciudadano",
    "MORENA":   "Movimiento Regeneración Nacional",
    "PANAL":    "Nueva Alianza",
    "PES":      "Encuentro Social",
    "PH":       "Humanista",
    "RSP":      "Redes Sociales Progresistas",
    "FXM":      "Fuerza por México",
    "ASDC":     "Alternativa Socialdemócrata y Campesina",
    "PARM":     "Auténtico de la Revolución Mexicana",
    "PFCRN":    "Frente Cardenista de Reconstrucción Nacional",
    "PPS":      "Popular Socialista",
    "PCD":      "Centro Democrático",
    "DSPPN":    "Democracia Social",
    "A. CAM.":  "Alianza por el Cambio",
    "A. MEX.":  "Alianza por México",
    "CI":               "Candidatura Independiente",
    "CAND_IND_1":       "Candidatura Independiente 1",
    "CAND_IND_2":       "Candidatura Independiente 2",
}


def build_party_labels(party_keys: list[str], coalitions: pd.DataFrame) -> dict[str, str]:
    """
    party_key -> human-readable label.
      - Coalitions: "{code} (PARTY+PARTY+...)" built from actual members.
      - Known standalone parties: "{code} — Full Name".
      - Unknown codes: left as-is (no guessing).
    """
    members_by_coalition = (
        coalitions.groupby("coalition_key")["member_key"]
        .apply(lambda s: "+".join(sorted(s)))
        .to_dict()
    )
    labels = {}
    for key in party_keys:
        if key in members_by_coalition:
            labels[key] = f"{key} ({members_by_coalition[key]})"
        elif key in PARTY_FULL_NAMES:
            labels[key] = f"{key} — {PARTY_FULL_NAMES[key]}"
        else:
            labels[key] = key
    return labels


CANONICAL_ESTADO_NOMBRES = {
     1: "AGUASCALIENTES",                  2: "BAJA CALIFORNIA",
     3: "BAJA CALIFORNIA SUR",             4: "CAMPECHE",
     5: "COAHUILA DE ZARAGOZA",            6: "COLIMA",
     7: "CHIAPAS",                         8: "CHIHUAHUA",
     9: "CIUDAD DE MÉXICO",               10: "DURANGO",
    11: "GUANAJUATO",                     12: "GUERRERO",
    13: "HIDALGO",                        14: "JALISCO",
    15: "MÉXICO",                         16: "MICHOACÁN DE OCAMPO",
    17: "MORELOS",                        18: "NAYARIT",
    19: "NUEVO LEÓN",                     20: "OAXACA",
    21: "PUEBLA",                         22: "QUERÉTARO",
    23: "QUINTANA ROO",                   24: "SAN LUIS POTOSÍ",
    25: "SINALOA",                        26: "SONORA",
    27: "TABASCO",                        28: "TAMAULIPAS",
    29: "TLAXCALA",                       30: "VERACRUZ DE IGNACIO DE LA LLAVE",
    31: "YUCATÁN",                        32: "ZACATECAS",
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
    # Override with the canonical name from id_estado — don't trust any one
    # cycle's spelling/accenting (see CANONICAL_ESTADO_NOMBRES above).
    state_raw["nombre_estado"] = state_raw["id_estado"].map(CANONICAL_ESTADO_NOMBRES)

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

    # Merge split votes back onto base — OUTER, not left: some coalition
    # members (e.g. 2006's PRD/PT/CONVERGENCIA inside PBT) never have their
    # own raw row at all — the source only ever recorded the coalition-level
    # total, never an individual member tally — so a left join starting from
    # state_raw would silently drop their split-derived share entirely.
    df = state_raw.merge(
        split,
        on=["election_id", "id_estado", "party_key"],
        how="outer",
    )
    # For coalition rows, votes_split is the dissolved total (not applicable);
    # for direct rows, votes_split = direct + attributed from coalitions
    # Coalition rows themselves get votes_split = NaN which is correct —
    # they are dissolved into members in the split view.

    # Backfill identity columns for member-only rows introduced by the outer
    # merge above (they exist in `split` but had no native state_raw row).
    member_only = df["nombre_estado"].isna()
    if member_only.any():
        elec_lookup = (
            state_raw[["election_id", "year", "election_type"]]
            .drop_duplicates()
            .set_index("election_id")
        )
        df.loc[member_only, "nombre_estado"]  = df.loc[member_only, "id_estado"].map(CANONICAL_ESTADO_NOMBRES)
        df.loc[member_only, "year"]           = df.loc[member_only, "election_id"].map(elec_lookup["year"])
        df.loc[member_only, "election_type"]  = df.loc[member_only, "election_id"].map(elec_lookup["election_type"])
        df.loc[member_only, "votes_raw"]      = 0.0
        print(f"  + {member_only.sum()} coalition-member rows added (never had a native row, only a split-derived share)")

    print("Building party display labels...")
    party_labels = build_party_labels(df["party_key"].unique().tolist(), coalitions)
    df["party_label"] = df["party_key"].map(party_labels)

    # Mark each row (covers member-only rows too — they are real standalone
    # parties, just never recorded directly in the source for that cycle)
    df["is_coalition"] = df["party_key"].isin(coalition_keys)

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