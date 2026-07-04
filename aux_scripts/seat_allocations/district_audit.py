"""
district_audit.py — district-level data quality explorer for DIP elections
===========================================================================
For a given election year this script cross-checks:

  1. Clean parquet  (data/clean_<year>/)    — source of truth from converter
  2. Warehouse DB   (election_data.db)      — after pipeline.py ingestion
  3. dim_geography join                     — how geo_id maps to districts

It surfaces:
  - District counts per state vs expected
  - Casillas with mismatched or null district assignments
  - Districts that appear in warehouse but not in clean parquet (and vice versa)
  - Phantom (estado, distrito) pairs driving the >300 seat bug
  - Party-key winners per district (so you can spot coalition attribution)

Usage:
    python3 aux_scripts/district_audit.py 2021
    python3 aux_scripts/district_audit.py 2018
    python3 aux_scripts/district_audit.py --all

Output: prints a structured report + writes a CSV footprint to
    aux_scripts/qa_reports/district_audit_<year>.csv
"""

from __future__ import annotations
import argparse
import os
import sqlite3
import sys
from pathlib import Path

import pandas as pd

# ── Config ──────────────────────────────────────────────────────────────────

ROOT     = Path(__file__).resolve().parent.parent
DB_PATH  = ROOT / "election_data.db"
OUT_DIR  = ROOT / "aux_scripts" / "qa_reports"

# Known DIP_MR election IDs and their clean-parquet folders
ELECTION_CYCLES: dict[str, str] = {
    "2000": "data/clean_2000",
    "2006": "data/clean_2006",
    "2012": "data/clean_2012",
    "2015": "data/clean_2015",
    "2018": "data/clean_2018",
    "2021": "data/clean_2021",
    "2024": "data/clean_2024",
}

ELECTION_ID_TEMPLATE = "DIP_MR_{year}"


# ── Helpers ──────────────────────────────────────────────────────────────────

def section(title: str) -> None:
    print(f"\n{'─' * 70}")
    print(f"  {title}")
    print(f"{'─' * 70}")


def load_clean_geo(clean_dir: Path) -> pd.DataFrame | None:
    geo_path = clean_dir / "dim_geography.parquet"
    if not geo_path.exists():
        return None
    df = pd.read_parquet(geo_path)
    df.columns = [c.upper() for c in df.columns]
    return df


def load_clean_casilla(clean_dir: Path) -> pd.DataFrame | None:
    path = clean_dir / "dim_casilla.parquet"
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    df.columns = [c.upper() for c in df.columns]
    return df


def load_clean_votes(clean_dir: Path, election_id: str) -> pd.DataFrame | None:
    path = clean_dir / "fact_casilla_vote.parquet"
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    df.columns = [c.upper() for c in df.columns]
    if "ELECTION_ID" in df.columns:
        df = df[df["ELECTION_ID"] == election_id]
    return df


def warehouse_districts(conn: sqlite3.Connection, election_id: str) -> pd.DataFrame:
    """Return one row per (id_estado, id_distrito_federal) in the warehouse."""
    return pd.read_sql_query(f"""
        SELECT
            g.id_estado,
            g.nombre_estado,
            g.id_distrito_federal,
            g.cabecera_distrital_federal  AS nombre_distrito,
            COUNT(DISTINCT c.casilla_id)  AS n_casillas,
            SUM(f.votes)                  AS total_votes
        FROM fact_casilla_vote f
        JOIN dim_casilla   c ON f.casilla_id  = c.casilla_id
                             AND f.election_id = c.election_id
        JOIN dim_geography g ON c.geo_id = g.geo_id AND c.election_id = g.election_id
        WHERE f.election_id            = '{election_id}'
          AND c.tipo_casilla          != 'S'
          AND g.seccion                > 0
          AND g.id_distrito_federal   IS NOT NULL
          AND g.id_distrito_federal    > 0
        GROUP BY g.id_estado, g.id_distrito_federal
        ORDER BY g.id_estado, g.id_distrito_federal
    """, conn)


def warehouse_winners(conn: sqlite3.Connection, election_id: str) -> pd.DataFrame:
    """Winner (max-vote party) per district in the warehouse."""
    df = pd.read_sql_query(f"""
        SELECT
            g.id_estado,
            g.nombre_estado,
            g.id_distrito_federal,
            g.cabecera_distrital_federal AS nombre_distrito,
            f.party_key,
            SUM(f.votes) AS votes
        FROM fact_casilla_vote f
        JOIN dim_casilla   c ON f.casilla_id  = c.casilla_id
                             AND f.election_id = c.election_id
        JOIN dim_geography g ON c.geo_id = g.geo_id AND c.election_id = g.election_id
        WHERE f.election_id            = '{election_id}'
          AND c.tipo_casilla          != 'S'
          AND g.seccion                > 0
          AND g.id_distrito_federal   IS NOT NULL
          AND g.id_distrito_federal    > 0
        GROUP BY g.id_estado, g.id_distrito_federal, f.party_key
    """, conn)
    idx = df.groupby(["id_estado", "id_distrito_federal"])["votes"].idxmax()
    winners = df.loc[idx, ["id_estado", "nombre_estado", "id_distrito_federal",
                            "nombre_distrito", "party_key", "votes"]].copy()
    winners["total_votes"] = df.groupby(
        ["id_estado", "id_distrito_federal"])["votes"].sum().values
    return winners.sort_values(["id_estado", "id_distrito_federal"])


# ── Core audit ───────────────────────────────────────────────────────────────

def audit_year(year: str, conn: sqlite3.Connection) -> pd.DataFrame:
    election_id = ELECTION_ID_TEMPLATE.format(year=year)
    clean_dir   = ROOT / ELECTION_CYCLES.get(year, f"data/clean_{year}")

    print(f"\n{'=' * 70}")
    print(f"  AUDIT: {election_id}")
    print(f"{'=' * 70}")

    # ── 1. Warehouse district footprint ─────────────────────────────────────
    section("1. Warehouse district footprint (fact → dim_geography join)")
    wh_districts = warehouse_districts(conn, election_id)
    state_summary = (
        wh_districts.groupby(["id_estado", "nombre_estado"])
        .agg(n_districts=("id_distrito_federal", "count"),
             max_distrito=("id_distrito_federal", "max"),
             total_votes=("total_votes", "sum"))
        .reset_index()
        .sort_values("id_estado")
    )
    print(f"Total (estado, distrito) pairs in warehouse: {len(wh_districts)}")
    print(f"Unique estados: {wh_districts['id_estado'].nunique()}")
    print()
    print(state_summary.to_string(index=False))

    # ── 2. Phantom districts — distrito number > expected local count ────────
    section("2. Suspicious district entries (max distrito >> n_districts)")
    state_summary["gap"] = state_summary["max_distrito"] - state_summary["n_districts"]
    suspicious = state_summary[state_summary["gap"] > 0].copy()
    if suspicious.empty:
        print("  None found — district numbering looks local and contiguous.")
    else:
        print(suspicious[["id_estado","nombre_estado","n_districts","max_distrito","gap"]].to_string(index=False))
        print(f"\n  Total excess districts: {suspicious['gap'].sum()}")

    # ── 3. Compare with clean parquet geo ────────────────────────────────────
    section("3. Clean parquet dim_geography (source before warehouse ingestion)")
    clean_geo = load_clean_geo(clean_dir)
    if clean_geo is None:
        print(f"  No dim_geography.parquet found in {clean_dir}")
    else:
        dist_col = next((c for c in ["ID_DISTRITO","ID_DISTRITO_FEDERAL"] if c in clean_geo.columns), None)
        estado_col = next((c for c in ["ID_ESTADO"] if c in clean_geo.columns), None)
        if dist_col and estado_col:
            clean_dists = (
                clean_geo[clean_geo[dist_col].notna() & (clean_geo[dist_col] > 0)]
                .groupby([estado_col, dist_col])
                .size()
                .reset_index(name="n_secciones")
            )
            clean_state = clean_dists.groupby(estado_col)[dist_col].count().rename("n_districts_clean")
            wh_state    = wh_districts.groupby("id_estado")["id_distrito_federal"].count().rename("n_districts_wh")
            compare = pd.concat([clean_state, wh_state], axis=1).fillna(0).astype(int)
            compare["diff_wh_minus_clean"] = compare["n_districts_wh"] - compare["n_districts_clean"]
            diffs = compare[compare["diff_wh_minus_clean"] != 0]
            if diffs.empty:
                print("  ✓ Clean parquet and warehouse district counts match per state.")
            else:
                print(f"  States where warehouse ≠ clean parquet:")
                print(diffs.to_string())
        else:
            print(f"  Columns: {clean_geo.columns.tolist()}")

    # ── 4. Casillas with seccion=0 or null distrito ──────────────────────────
    section("4. Casillas excluded by current filter (seccion=0 or distrito IS NULL/0)")
    excluded = pd.read_sql_query(f"""
        SELECT
            g.id_estado, g.nombre_estado,
            g.seccion, g.id_distrito_federal,
            COUNT(DISTINCT c.casilla_id) AS n_casillas
        FROM fact_casilla_vote f
        JOIN dim_casilla   c ON f.casilla_id  = c.casilla_id
                             AND f.election_id = c.election_id
        JOIN dim_geography g ON c.geo_id = g.geo_id AND c.election_id = g.election_id
        WHERE f.election_id = '{election_id}'
          AND c.tipo_casilla != 'S'
          AND (g.seccion = 0 OR g.id_distrito_federal IS NULL OR g.id_distrito_federal = 0)
        GROUP BY g.id_estado, g.seccion, g.id_distrito_federal
        ORDER BY g.id_estado
    """, conn)
    if excluded.empty:
        print("  None — no casillas excluded by the filter.")
    else:
        print(f"  {excluded['n_casillas'].sum()} casillas excluded across {len(excluded)} geo groups:")
        print(excluded.to_string(index=False))

    # ── 5. Winners per district (spot-check coalition attribution) ───────────
    section("5. MR winners per district — party attribution sample")
    winners = warehouse_winners(conn, election_id)
    print(f"  Total district winners computed: {len(winners)}")
    party_counts = winners["party_key"].value_counts()
    print(f"\n  Seats by party_key (raw coalition keys):")
    print(party_counts.to_string())

    # ── 6. Cross-table consistency: geo_id linkage ───────────────────────────
    section("6. Cross-table: geo_id coverage in dim_casilla vs dim_geography (warehouse)")
    geo_ids = pd.read_sql_query(
        f"SELECT DISTINCT geo_id FROM dim_geography WHERE election_id='{election_id}'", conn
    )["geo_id"]
    cas_geo = pd.read_sql_query(
        f"SELECT DISTINCT geo_id FROM dim_casilla WHERE election_id='{election_id}'", conn
    )["geo_id"]
    orphan_cas = set(cas_geo) - set(geo_ids)
    orphan_geo = set(geo_ids) - set(cas_geo)
    print(f"  geo_ids in dim_casilla but NOT in dim_geography: {len(orphan_cas)}")
    if orphan_cas:
        print(f"    Examples: {sorted(orphan_cas)[:10]}")
    print(f"  geo_ids in dim_geography but NOT in dim_casilla: {len(orphan_geo)}")

    # ── 7. Write footprint CSV ───────────────────────────────────────────────
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"district_audit_{year}.csv"
    footprint = wh_districts.copy()
    footprint["election_id"] = election_id
    footprint["winner_party"] = winners.set_index(
        ["id_estado","id_distrito_federal"])["party_key"].reindex(
        pd.MultiIndex.from_frame(footprint[["id_estado","id_distrito_federal"]])
    ).values
    footprint.to_csv(out_path, index=False)
    print(f"\n  ✓ Footprint written → {out_path.relative_to(ROOT)}")

    return footprint


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="District-level audit for DIP elections")
    parser.add_argument("year", nargs="?", help="Election year (e.g. 2021), or omit with --all")
    parser.add_argument("--all", action="store_true", help="Audit all available cycles")
    args = parser.parse_args()

    if not DB_PATH.exists():
        sys.exit(f"Warehouse not found: {DB_PATH}\nRun: python3 ingestion/pipeline.py")

    conn = sqlite3.connect(DB_PATH)

    years: list[str] = []
    if args.all:
        years = list(ELECTION_CYCLES.keys())
    elif args.year:
        years = [args.year]
    else:
        parser.print_help()
        sys.exit(1)

    for year in years:
        if year not in ELECTION_CYCLES:
            print(f"Unknown year {year}. Available: {list(ELECTION_CYCLES.keys())}")
            continue
        audit_year(year, conn)

    conn.close()


if __name__ == "__main__":
    main()
