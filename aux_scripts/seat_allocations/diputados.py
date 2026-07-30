"""
Reconstruct Cámara de Diputados seat counts from election data.

This module computes:
  - 300 MR district winners from warehouse votes
  - 200 RP seats with a transparent natural-quotient approximation

For 2024, use `official_counts()` as ground truth when exact
coalition-to-party ownership matters.
"""

from __future__ import annotations

import argparse
import sqlite3

import pandas as pd

from aux_scripts.seat_allocations.common import (
    actor_totals,
    canonical_party,
    compare_counts,
    connect,
    largest_remainder,
    discover_elections,
    integration_counts,
    official_bloc_counts,
    pivot_counts,
)

RP_SEATS_PER_CIRC = 40
N_CIRCUNSCRIPCIONES = 5
TOTAL_SEATS = 500
THRESHOLD_PCT = 0.03
MAX_SEATS_ABSOLUTE = 300
SOBREREPR_CAP_PTS = 8.0


def district_votes(conn: sqlite3.Connection, election_id: str) -> pd.DataFrame:
    """Votes by federal district and party_key.

    Grouping intentionally excludes `nombre_estado` to avoid historical accent
    variants creating phantom districts.
    """
    return pd.read_sql_query(
        """
        SELECT
            g.id_estado,
            MAX(g.nombre_estado) AS nombre_estado,
            g.id_distrito_federal,
            MAX(g.cabecera_distrital_federal) AS cabecera_distrital_federal,
            f.party_key,
            SUM(f.votes) AS votes
        FROM fact_casilla_vote f
        JOIN dim_casilla c
          ON f.casilla_id = c.casilla_id
         AND f.election_id = c.election_id
        JOIN dim_geography g
          ON c.geo_id = g.geo_id AND c.election_id = g.election_id
        WHERE f.election_id = ?
          AND c.tipo_casilla != 'S'
          AND g.seccion > 0
          AND g.id_distrito_federal IS NOT NULL
          AND g.id_distrito_federal > 0
        GROUP BY g.id_estado, g.id_distrito_federal, f.party_key
        """,
        conn,
        params=(election_id,),
    )


def mr_winners(conn: sqlite3.Connection, election_id: str) -> pd.DataFrame:
    df = district_votes(conn, election_id)
    if df.empty:
        return pd.DataFrame()
    idx = df.groupby(["id_estado", "id_distrito_federal"])["votes"].idxmax()
    winners = df.loc[idx].copy()
    winners["party"] = winners["party_key"].map(canonical_party)
    winners["seat_type"] = "MR"
    winners["seats"] = 1
    return winners.reset_index(drop=True)


def mr_actor_winners(conn: sqlite3.Connection, election_id: str) -> pd.DataFrame:
    """MR winners by configured election actor/bloc.

    This is the most faithful vote-only reconstruction for coalition years:
    votes cast for individual parties and valid coalition combinations are
    summed into the registered electoral actor before selecting the district
    winner.
    """
    df = district_votes(conn, election_id)
    rows = []
    for (id_estado, distrito), sub in df.groupby(["id_estado", "id_distrito_federal"]):
        totals = actor_totals(sub[["party_key", "votes"]], election_id)
        if not totals:
            continue
        winner, votes = max(totals.items(), key=lambda kv: kv[1])
        rows.append({
            "id_estado": id_estado,
            "id_distrito_federal": distrito,
            "party": winner,
            "seat_type": "MR",
            "seats": 1,
            "votes": votes,
        })
    return pd.DataFrame(rows)


def circ_votes(conn: sqlite3.Connection, election_id: str) -> pd.DataFrame:
    return pd.read_sql_query(
        """
        SELECT
            g.circunscripcion,
            f.party_key,
            SUM(f.votes) AS votes
        FROM fact_casilla_vote f
        JOIN dim_casilla c
          ON f.casilla_id = c.casilla_id
         AND f.election_id = c.election_id
        JOIN dim_geography g
          ON c.geo_id = g.geo_id AND c.election_id = g.election_id
        WHERE f.election_id = ?
          AND c.tipo_casilla != 'S'
          AND g.seccion > 0
          AND g.circunscripcion IS NOT NULL
        GROUP BY g.circunscripcion, f.party_key
        """,
        conn,
        params=(election_id,),
    )


def rp_allocation(conn: sqlite3.Connection, election_id: str, winners: pd.DataFrame) -> pd.DataFrame:
    """Approximate 200 RP seats from vote totals.

    This uses canonical party aggregation and the same simple cap mechanics as
    the hemicycle prototype. It is a QA reconstruction, not a substitute for the
    official integration file.
    """
    df = circ_votes(conn, election_id)
    if df.empty:
        return pd.DataFrame(columns=["party", "seat_type", "seats"])
    df["party"] = df["party_key"].map(canonical_party)
    national = df.groupby("party")["votes"].sum()
    total_votes = national.sum()
    qualified = national[national / total_votes >= THRESHOLD_PCT].index.tolist()

    mr_counts = winners.groupby("party")["seats"].sum().to_dict()
    rp_counts = {p: 0 for p in qualified}
    for circ in range(1, N_CIRCUNSCRIPCIONES + 1):
        circ_totals = df[df["circunscripcion"] == circ].groupby("party")["votes"].sum()
        circ_q = {p: float(circ_totals.get(p, 0)) for p in qualified if circ_totals.get(p, 0) > 0}
        for party, seats in largest_remainder(circ_q, RP_SEATS_PER_CIRC).items():
            rp_counts[party] = rp_counts.get(party, 0) + seats

    vote_shares = {p: national.get(p, 0) / total_votes for p in qualified}

    def total_for(party: str) -> int:
        return int(mr_counts.get(party, 0) + rp_counts.get(party, 0))

    changed = True
    while changed:
        changed = False
        for party in list(rp_counts):
            over_abs = total_for(party) > MAX_SEATS_ABSOLUTE
            over_sobrep = (total_for(party) / TOTAL_SEATS - vote_shares.get(party, 0)) * 100 > SOBREREPR_CAP_PTS
            if not ((over_abs or over_sobrep) and rp_counts[party] > 0):
                continue
            rp_counts[party] -= 1
            others = {
                q: national.get(q, 0) / (rp_counts.get(q, 0) + 1)
                for q in qualified
                if q != party and total_for(q) < MAX_SEATS_ABSOLUTE
            }
            if others:
                rp_counts[max(others, key=others.get)] = rp_counts.get(max(others, key=others.get), 0) + 1
            changed = True
            break

    rows = [
        {"party": party, "seat_type": "RP", "seats": seats}
        for party, seats in rp_counts.items()
        if seats > 0
    ]
    return pd.DataFrame(rows)


def computed_counts(conn: sqlite3.Connection, election_id: str) -> pd.DataFrame:
    winners = mr_winners(conn, election_id)
    mr = winners.groupby("party")["seats"].sum().reset_index()
    mr["seat_type"] = "MR"
    mr = mr[["party", "seat_type", "seats"]]
    rp = rp_allocation(conn, election_id, winners)
    return pd.concat([mr, rp], ignore_index=True)


def computed_actor_mr_counts(conn: sqlite3.Connection, election_id: str) -> pd.DataFrame:
    winners = mr_actor_winners(conn, election_id)
    if winners.empty:
        return pd.DataFrame(columns=["party", "seat_type", "seats"])
    return (
        winners.groupby(["party", "seat_type"])["seats"]
        .sum()
        .reset_index()
    )


def official_counts() -> pd.DataFrame:
    return integration_counts("DIP")


def print_report(election_id: str, official: bool) -> None:
    with connect() as conn:
        computed_actor = computed_actor_mr_counts(conn, election_id)
        computed_party = computed_counts(conn, election_id)
    print(f"\nDIPUTADOS MR actors from votes · {election_id}")
    print(pivot_counts(computed_actor).to_string())
    print("\nDIPUTADOS party-level approximation")
    print("  Note: exact coalition-to-party ownership and RP assignment require official metadata.")
    print(pivot_counts(computed_party).to_string())
    if official and election_id == "DIP_MR_2024":
        off = official_counts()
        off_bloc = official_bloc_counts("DIP", election_id)
        print("\nOfficial INTEGRACION_CARGOS · DIP 2024")
        print(pivot_counts(off).to_string())
        print("\nOfficial rolled up to election actors/blocs")
        print(pivot_counts(off_bloc).to_string())
        diff = compare_counts(computed_actor, off_bloc[off_bloc["seat_type"] == "MR"])
        print("\nMR actor diff computed - official bloc MR")
        print("  OK: no differences" if diff.empty else diff.to_string(index=False))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("election_id", nargs="?", default="DIP_MR_2024")
    parser.add_argument("--official", action="store_true", help="Compare with 2024 INTEGRACION_CARGOS when available.")
    parser.add_argument("--all", action="store_true", help="Run all DIP_MR elections found in the warehouse.")
    args = parser.parse_args()

    if args.all:
        with connect() as conn:
            elections = discover_elections(conn, "DIP_MR")
        for election_id in elections:
            print_report(election_id, args.official)
    else:
        print_report(args.election_id, args.official)


if __name__ == "__main__":
    main()
