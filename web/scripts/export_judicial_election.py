"""Export 2025 judicial-election turnout and at-large results for the public web dashboard.

MVP scope: turnout by state (compared against 2024 presidential turnout, read
from the already-exported electoral-trajectory.json rather than recomputed),
plus winners for the four at-large races (SCJN, Tribunal de Disciplina
Judicial, TEPJF Sala Superior, TEPJF Salas Regionales). TCCA/JD are excluded:
those are ~60 separate per-judicial-district races and INE's per-district
seat-allocation counts are not in the warehouse, so "who won" isn't
computable yet -- see documentation/table_dictionaries/_dictionary_README.md.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from electoral.states import DB_PATH, canonical_estado  # noqa: E402

TURNOUT_RACE = "MIN_2025"  # representative: all 6 judicial ballots were cast
                            # by the same voters on the same day, so per-casilla
                            # turnout barely varies race to race (12.93-13.02%).
AT_LARGE_RACES = ["MIN_2025", "TDJ_2025", "TEPJF_SS_2025", "TEPJF_SR_2025"]
RACE_LABELS = {
    "MIN_2025": "Ministras y Ministros de la Suprema Corte de Justicia",
    "TDJ_2025": "Tribunal de Disciplina Judicial",
    "TEPJF_SS_2025": "Sala Superior del TEPJF",
    "TEPJF_SR_2025": "Salas Regionales del TEPJF",
}

TRAJECTORY_PATH = ROOT / "web" / "public" / "data" / "electoral-trajectory.json"
TURNOUT_OUT_PATH = ROOT / "web" / "public" / "data" / "judicial-turnout.json"
RESULTS_OUT_PATH = ROOT / "web" / "public" / "data" / "judicial-results.json"


def get_conn() -> sqlite3.Connection:
    return sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)


def load_2024_turnout_by_state() -> dict[int, float | None]:
    """Reads presidential 2024 state turnout from the trajectory export
    rather than recomputing it, so both pages agree on one number."""
    payload = json.loads(TRAJECTORY_PATH.read_text(encoding="utf-8"))
    pre = payload["contests"]["PRE"]["geographies"]
    out = {}
    for state_id_str, geo in pre.items():
        if state_id_str == "national":
            continue
        election = geo["elections"].get("2024")
        out[int(state_id_str)] = election["turnout"] if election else None
    return out


def national_2024_turnout() -> float | None:
    payload = json.loads(TRAJECTORY_PATH.read_text(encoding="utf-8"))
    election = payload["contests"]["PRE"]["geographies"]["national"]["elections"].get("2024")
    return election["turnout"] if election else None


def build_turnout(conn: sqlite3.Connection) -> dict:
    # numero_personas_votaron/lista_nominal_casilla repeat on every candidato
    # row for a casilla -- collapse with MAX() per casilla before summing by
    # state, same pattern documented in fact_judicial_casilla_vote's _GOTCHA.
    #
    # Deliberately NOT total_votos_casilla: SCJN/TDJ/TEPJF_SS/TEPJF_SR let a
    # voter mark multiple boxes (up to total_seats, one per open seat), so
    # total_votos_casilla is a sum of box-marks, not a headcount -- confirmed
    # directly (Coahuila casillas showed total_votos_casilla running ~2x
    # lista_nominal_casilla for MIN_2025's 9-seat ballot). numero_personas_votaron
    # is the actual per-casilla headcount and reproduces the published
    # state turnout figures exactly (Coahuila 24.3%, Guanajuato 6.7%).
    per_casilla = pd.read_sql_query(
        """
        SELECT
            f.casilla_id,
            g.id_estado,
            MAX(f.numero_personas_votaron) AS numero_personas_votaron,
            MAX(f.lista_nominal_casilla)   AS lista_nominal_casilla
        FROM fact_judicial_casilla_vote f
        JOIN dim_casilla   c ON f.casilla_id = c.casilla_id AND f.election_id = c.election_id
        JOIN dim_geography g ON c.geo_id = g.geo_id AND c.election_id = g.election_id
        WHERE f.election_id = ?
        GROUP BY f.casilla_id
        """,
        conn,
        params=(TURNOUT_RACE,),
    )
    by_state = per_casilla.groupby("id_estado")[["numero_personas_votaron", "lista_nominal_casilla"]].sum()

    state_names = pd.read_sql_query(
        "SELECT DISTINCT id_estado, nombre_estado FROM dim_geography WHERE election_id = ?",
        conn,
        params=(TURNOUT_RACE,),
    ).set_index("id_estado")["nombre_estado"]

    turnout_2024 = load_2024_turnout_by_state()

    states = []
    for id_estado, row in by_state.iterrows():
        pct = round(row["numero_personas_votaron"] / row["lista_nominal_casilla"] * 100, 1) if row["lista_nominal_casilla"] else None
        states.append({
            "stateId": int(id_estado),
            "name": canonical_estado(id_estado, state_names.get(id_estado, "")).title(),
            "turnoutPct": pct,
            "turnoutPct2024": turnout_2024.get(int(id_estado)),
        })
    states.sort(key=lambda s: s["stateId"])

    national_pct = conn.execute(
        "SELECT pct_participacion_ciudadana FROM dim_election WHERE election_id = ?", (TURNOUT_RACE,)
    ).fetchone()[0]

    return {
        "electionId": TURNOUT_RACE,
        "label": "Elección judicial 2025",
        "nationalTurnoutPct": round(national_pct, 1),
        "nationalTurnoutPct2024": national_2024_turnout(),
        "states": states,
    }


def race_candidates(conn: sqlite3.Connection, election_id: str) -> pd.DataFrame:
    df = pd.read_sql_query(
        """
        SELECT
            f.candidato_id,
            SUM(f.votes) AS votes,
            c.nombre_candidato,
            c.genero,
            c.poder_postulante,
            c.circunscripcion
        FROM fact_judicial_casilla_vote f
        JOIN dim_candidato_judicial c ON f.candidato_id = c.candidato_id
        WHERE f.election_id = ?
        GROUP BY f.candidato_id
        """,
        conn,
        params=(election_id,),
    )
    total = df["votes"].sum()
    df["pct"] = (df["votes"] / total * 100).round(2) if total else 0.0
    return df.sort_values("votes", ascending=False)


def candidate_entry(row) -> dict:
    return {
        "name": row.nombre_candidato.title(),
        "genero": row.genero.strip(),
        "poderPostulante": row.poder_postulante,
        "votes": int(row.votes),
        "pct": float(row.pct),
    }


def build_results(conn: sqlite3.Connection) -> dict:
    races = []
    for election_id in AT_LARGE_RACES:
        seats, term_years = conn.execute(
            "SELECT total_seats, term_years FROM dim_election WHERE election_id = ?", (election_id,)
        ).fetchone()
        ranked = race_candidates(conn, election_id)

        race = {
            "electionId": election_id,
            "label": RACE_LABELS[election_id],
            "seats": int(seats),
            "termYears": int(term_years),
        }

        if election_id == "TEPJF_SR_2025":
            # Scoped by circunscripcion: 5 regions elect 3 each.
            regions = []
            for circ, group in ranked.groupby("circunscripcion"):
                seats_per_region = seats // ranked["circunscripcion"].nunique()
                winners = group.head(seats_per_region)
                regions.append({
                    "circunscripcion": int(circ),
                    "winners": [candidate_entry(r) for r in winners.itertuples()],
                })
            race["regions"] = sorted(regions, key=lambda r: r["circunscripcion"])
        else:
            winners = ranked.head(seats)
            race["winners"] = [candidate_entry(r) for r in winners.itertuples()]

        races.append(race)

    return {"races": races}


def export() -> None:
    with get_conn() as conn:
        turnout = build_turnout(conn)
        results = build_results(conn)

    TURNOUT_OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    TURNOUT_OUT_PATH.write_text(
        json.dumps(turnout, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    RESULTS_OUT_PATH.write_text(
        json.dumps(results, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    print(f"Wrote {TURNOUT_OUT_PATH} ({TURNOUT_OUT_PATH.stat().st_size / 1024:.1f} KB)")
    print(f"Wrote {RESULTS_OUT_PATH} ({RESULTS_OUT_PATH.stat().st_size / 1024:.1f} KB)")


if __name__ == "__main__":
    export()
