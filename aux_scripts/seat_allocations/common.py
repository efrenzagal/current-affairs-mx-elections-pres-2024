"""
Shared helpers for reconstructing legislative seat counts from vote data.

These helpers intentionally separate two ideas:
  - actor/bloc reconstruction from votes
  - exact party ownership from INE's INTEGRACION_CARGOS files

Vote totals alone can identify winning districts/states and approximate RP
allocation. Exact coalition-to-party seat ownership requires candidate/list
metadata or the official integration file.
"""

from __future__ import annotations

from pathlib import Path
import sqlite3

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "election_data.db"

INTEGRACION_2024 = (
    ROOT / "data" / "electoral_data_raw" / "raw_2024" / "PRESIDENCIA_2024"
    / "CSV" / "INTEGRACION_CARGOS_PEF_2024.csv"
)

COALITION_TO_PARTY: dict[str, str] = {
    "PVEM_PT_MORENA": "MORENA",
    "PT_MORENA": "MORENA",
    "PVEM_MORENA": "MORENA",
    "PVEM_PT": "PVEM",
    "PT_MORENA_PES": "MORENA",
    "MORENA_PES": "MORENA",
    "PT_PES": "PT",
    "PAN_PRI_PRD": "PAN",
    "PAN_PRI": "PAN",
    "PAN_PRD": "PAN",
    "PRI_PRD": "PRI",
    "PAN_PRD_MC": "PAN",
    "PAN_MC": "PAN",
    "PRD_MC": "PRD",
    "PRI_PVEM_NA": "PRI",
    "PRI_PVEM": "PRI",
    "PRI_NA": "PRI",
    "PVEM_NA": "PVEM",
    "C_PRI_PVEM": "PRI",
    "C_PRD_PT": "PRD",
    "C_PRD_PT_MC": "PRD",
    "C_PRD_MC": "PRD",
    "C_PT_MC": "PT",
    "A. CAM.": "PAN",
    "A. MEX.": "PRD",
    "APM": "PRI",
    "PBT": "PRD",
}

ELECTION_GROUPS: dict[str, dict[str, list[str]]] = {
    "2024": {
        "Sigamos Haciendo Historia": [
            "MORENA", "PT", "PVEM", "PVEM_PT_MORENA", "PT_MORENA",
            "PVEM_MORENA", "PVEM_PT",
        ],
        "Fuerza y Corazon por Mexico": [
            "PAN", "PRI", "PRD", "PAN_PRI_PRD", "PAN_PRI", "PAN_PRD",
            "PRI_PRD",
        ],
        "MC": ["MC"],
    },
    "2021": {
        "Juntos Hacemos Historia": [
            "MORENA", "PT", "PVEM", "PVEM_PT_MORENA", "PT_MORENA",
            "PVEM_MORENA", "PVEM_PT",
        ],
        "Va por Mexico": ["PAN", "PRI", "PRD", "PAN_PRI_PRD", "PAN_PRI", "PAN_PRD", "PRI_PRD"],
        "MC": ["MC"],
    },
    "2018": {
        "Juntos Haremos Historia": ["MORENA", "PT", "PES", "PT_MORENA_PES", "MORENA_PES", "PT_PES"],
        "Por Mexico al Frente": ["PAN", "PRD", "MC", "PAN_PRD_MC", "PAN_MC", "PRD_MC"],
        "Todos por Mexico": ["PRI", "PVEM", "PANAL", "PRI_PVEM_NA", "PRI_PVEM", "PRI_NA", "PVEM_NA"],
    },
    "2012": {
        "Compromiso por Mexico": ["PRI", "PVEM", "PRI_PVEM"],
        "Movimiento Progresista": ["PRD", "PT", "MC", "C_PRD_PT", "C_PRD_PT_MC", "C_PRD_MC", "C_PT_MC"],
        "PAN": ["PAN"],
        "PANAL": ["PANAL"],
    },
    "2006": {
        "APM": ["PRI", "PVEM", "APM"],
        "Por el Bien de Todos": ["PRD", "PT", "CONV", "PBT"],
        "PAN": ["PAN"],
        "Nueva Alianza": ["NVA_A", "PANAL"],
    },
    "2000": {
        "Alianza por el Cambio": ["PAN", "PVEM", "A. CAM."],
        "Alianza por Mexico": ["PRD", "PT", "CONV", "PAS", "PSN", "A. MEX."],
        "PRI": ["PRI"],
    },
}


def year_from_election_id(election_id: str) -> str:
    return election_id.rsplit("_", 1)[-1]


def canonical_party(party_key: str) -> str:
    return COALITION_TO_PARTY.get(str(party_key), str(party_key))


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    return sqlite3.connect(db_path)


def discover_elections(conn: sqlite3.Connection, prefix: str) -> list[str]:
    rows = conn.execute(
        """
        SELECT DISTINCT election_id
        FROM fact_casilla_vote
        WHERE election_id LIKE ?
        ORDER BY election_id
        """,
        (f"{prefix}_%",),
    ).fetchall()
    return [r[0] for r in rows]


def largest_remainder(votes: dict[str, float], n_seats: int) -> dict[str, int]:
    """Allocate seats by natural quotient and largest remainder.

    This is the proportional-allocation rule used by Mexico's LGIPE.  It is
    still only a building block: chamber-specific thresholds, caps, and
    circunscripción rules must be applied by the caller.
    """
    positive_votes = {party: float(value) for party, value in votes.items() if value > 0}
    total_votes = sum(positive_votes.values())
    if total_votes <= 0 or n_seats <= 0:
        return {}

    quotient = total_votes / n_seats
    exact = {party: value / quotient for party, value in positive_votes.items()}
    seats = {party: int(value) for party, value in exact.items()}
    seats_left = n_seats - sum(seats.values())
    remainder_order = sorted(
        positive_votes,
        key=lambda party: (exact[party] - seats[party], positive_votes[party], party),
        reverse=True,
    )
    for party in remainder_order[:seats_left]:
        seats[party] += 1
    return seats


def load_integracion(path: Path = INTEGRACION_2024) -> pd.DataFrame:
    return pd.read_csv(path, encoding="utf-8-sig")


def integration_counts(tipo_prefix: str, path: Path = INTEGRACION_2024) -> pd.DataFrame:
    """Return official counts from INTEGRACION_CARGOS for DIP or SEN."""
    df = load_integracion(path)
    df = df[df["TIPO_DE_CANDIDATURA"].astype(str).str.startswith(tipo_prefix)].copy()
    if df.empty:
        return pd.DataFrame(columns=["party", "seat_type", "seats"])

    seat_type = df["TIPO_DE_CANDIDATURA"].str.replace(f"{tipo_prefix}_", "", regex=False)
    out = (
        df.assign(
            party=df["PARTIDO_POLITICO"].astype(str).map(canonical_party),
            seat_type=seat_type,
        )
        .groupby(["party", "seat_type"])
        .size()
        .reset_index(name="seats")
    )
    return out


def pivot_counts(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    out = (
        df.pivot_table(
            index="party",
            columns="seat_type",
            values="seats",
            aggfunc="sum",
            fill_value=0,
        )
        .rename_axis(None, axis=1)
    )
    out["Total"] = out.sum(axis=1)
    return out.sort_values("Total", ascending=False)


def compare_counts(computed: pd.DataFrame, official: pd.DataFrame) -> pd.DataFrame:
    comp = pivot_counts(computed)
    off = pivot_counts(official)
    all_cols = sorted((set(comp.columns) | set(off.columns)) - {"Total"})
    all_parties = sorted(set(comp.index) | set(off.index))
    rows = []
    for party in all_parties:
        for col in all_cols + ["Total"]:
            cv = int(comp[col].get(party, 0)) if col in comp.columns else 0
            ov = int(off[col].get(party, 0)) if col in off.columns else 0
            if cv != ov:
                rows.append({
                    "party": party,
                    "seat_type": col,
                    "computed": cv,
                    "official": ov,
                    "diff": cv - ov,
                })
    return pd.DataFrame(rows)


def actor_totals(votes: pd.DataFrame, election_id: str) -> dict[str, int]:
    """Aggregate party_key rows into election actors/blocs when configured."""
    year = year_from_election_id(election_id)
    groups = ELECTION_GROUPS.get(year)
    if not groups:
        return dict(zip(votes["party_key"].astype(str), votes["votes"].astype(int)))

    covered = {key for keys in groups.values() for key in keys}
    totals = {
        actor: int(votes[votes["party_key"].isin(keys)]["votes"].sum())
        for actor, keys in groups.items()
    }
    for _, row in votes[~votes["party_key"].isin(covered)].iterrows():
        totals[str(row["party_key"])] = int(row["votes"])
    return {k: v for k, v in totals.items() if v > 0}


def official_bloc_counts(tipo_prefix: str, election_id: str, path: Path = INTEGRACION_2024) -> pd.DataFrame:
    """Roll official party counts up to configured election actors/blocs."""
    party_counts = integration_counts(tipo_prefix, path)
    if party_counts.empty:
        return party_counts

    year = year_from_election_id(election_id)
    groups = ELECTION_GROUPS.get(year)
    if not groups:
        return party_counts

    covered = {party for parties in groups.values() for party in parties}
    rows = []
    for actor, parties in groups.items():
        sub = party_counts[party_counts["party"].isin(parties)]
        if sub.empty:
            continue
        for seat_type, seats in sub.groupby("seat_type")["seats"].sum().items():
            rows.append({"party": actor, "seat_type": seat_type, "seats": int(seats)})

    other = party_counts[~party_counts["party"].isin(covered)].copy()
    if not other.empty:
        rows.extend(other.to_dict("records"))
    return pd.DataFrame(rows)
