"""Exploratory reconstruction of Senado seat counts from election data.

The Senate has 128 seats:
  - 96 state seats: 2 for the winning state slate, 1 for first minority
  - 32 national RP seats

Vote-only reconstruction is strongest at the actor/slate level. Exact party
ownership inside coalitions should be validated against INE integration files.
This is an optional QA tool, not part of Senate ingestion or website export.
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

SEN_RP_SEATS = 32


def state_votes(conn: sqlite3.Connection, election_id: str) -> pd.DataFrame:
    return pd.read_sql_query(
        """
        SELECT
            g.id_estado,
            MAX(g.nombre_estado) AS nombre_estado,
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
        GROUP BY g.id_estado, f.party_key
        """,
        conn,
        params=(election_id,),
    )


def mr_actor_seats(conn: sqlite3.Connection, election_id: str) -> pd.DataFrame:
    """Allocate 2 winner + 1 first-minority Senate seats by state actor."""
    votes = state_votes(conn, election_id)
    rows = []
    for (id_estado, nombre_estado), state_df in votes.groupby(["id_estado", "nombre_estado"]):
        totals = actor_totals(state_df[["party_key", "votes"]], election_id)
        ranked = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
        if not ranked:
            continue
        rows.append({
            "id_estado": id_estado,
            "nombre_estado": nombre_estado,
            "party": ranked[0][0],
            "seat_type": "MR",
            "seats": 2,
            "rank": 1,
            "votes": ranked[0][1],
        })
        if len(ranked) > 1:
            rows.append({
                "id_estado": id_estado,
                "nombre_estado": nombre_estado,
                "party": ranked[1][0],
                "seat_type": "FIRST_MINORITY",
                "seats": 1,
                "rank": 2,
                "votes": ranked[1][1],
            })
    return pd.DataFrame(rows)


def national_votes(conn: sqlite3.Connection, election_id: str) -> pd.DataFrame:
    return pd.read_sql_query(
        """
        SELECT party_key, SUM(votes) AS votes
        FROM fact_casilla_vote
        WHERE election_id = ?
        GROUP BY party_key
        """,
        conn,
        params=(election_id,),
    )


def rp_allocation(conn: sqlite3.Connection, election_id: str) -> pd.DataFrame:
    """Approximate 32 Senate RP seats by natural quotient and largest remainder."""
    votes = national_votes(conn, election_id)
    if votes.empty:
        return pd.DataFrame(columns=["party", "seat_type", "seats"])
    votes["party"] = votes["party_key"].map(canonical_party)
    totals = votes.groupby("party")["votes"].sum()
    seats = largest_remainder({p: float(v) for p, v in totals.items()}, SEN_RP_SEATS)
    return pd.DataFrame(
        [{"party": party, "seat_type": "RP", "seats": n} for party, n in seats.items() if n > 0]
    )


def computed_actor_counts(conn: sqlite3.Connection, election_id: str) -> pd.DataFrame:
    mr = (
        mr_actor_seats(conn, election_id)
        .groupby(["party", "seat_type"])["seats"]
        .sum()
        .reset_index()
    )
    rp = rp_allocation(conn, election_id)
    return pd.concat([mr, rp], ignore_index=True)


def official_counts() -> pd.DataFrame:
    return integration_counts("SEN")


def print_report(election_id: str, official: bool) -> None:
    with connect() as conn:
        computed = computed_actor_counts(conn, election_id)
    print(f"\nSENADORES computed from votes · {election_id}")
    print("  Note: MR/FIRST_MINORITY are slate/actor seats; RP is a party-level approximation.")
    print(pivot_counts(computed).to_string())
    if official and election_id == "SEN_MR_2024":
        off = official_counts()
        off_bloc = official_bloc_counts("SEN", election_id)
        print("\nOfficial INTEGRACION_CARGOS · SEN 2024")
        print(pivot_counts(off).to_string())
        print("\nOfficial rolled up to election actors/blocs")
        print(pivot_counts(off_bloc).to_string())
        computed_mr = computed[computed["seat_type"].isin(["MR", "FIRST_MINORITY"])].copy()
        computed_mr["seat_type"] = "MR"
        official_mr = off_bloc[off_bloc["seat_type"] == "MR"]
        diff = compare_counts(computed_mr, official_mr)
        print("\nState-seat actor diff computed - official bloc MR")
        print("  OK: no differences" if diff.empty else diff.to_string(index=False))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("election_id", nargs="?", default="SEN_MR_2024")
    parser.add_argument("--official", action="store_true", help="Compare with 2024 INTEGRACION_CARGOS when available.")
    parser.add_argument("--all", action="store_true", help="Run all SEN_MR elections found in the warehouse.")
    args = parser.parse_args()

    if args.all:
        with connect() as conn:
            elections = discover_elections(conn, "SEN_MR")
        for election_id in elections:
            print_report(election_id, args.official)
    else:
        print_report(args.election_id, args.official)


if __name__ == "__main__":
    main()
