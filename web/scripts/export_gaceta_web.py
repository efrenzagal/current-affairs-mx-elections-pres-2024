"""Export a compact LXVI Gaceta snapshot for the public website draft."""

from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "election_data.db"
OUT_PATH = ROOT / "web" / "public" / "data" / "legislature-66.json"
SENATE_OUT_PATH = ROOT / "web" / "public" / "data" / "senate-66.json"
INE_INTEGRATION_PATH = (
    ROOT
    / "data"
    / "electoral_data_raw"
    / "raw_2024"
    / "PRESIDENCIA_2024"
    / "CSV"
    / "INTEGRACION_CARGOS_PEF_2024.csv"
)


def rows(conn: sqlite3.Connection, query: str) -> list[dict]:
    conn.row_factory = sqlite3.Row
    return [dict(row) for row in conn.execute(query)]


def load_mr_election_results() -> dict[tuple[int, int], dict]:
    results: dict[tuple[int, int], dict] = {}
    with INE_INTEGRATION_PATH.open(encoding="utf-8-sig", newline="") as source:
        for row in csv.DictReader(source):
            if row["TIPO_DE_CANDIDATURA"] != "DIP_MR":
                continue
            pct = row["PORCENTAJE_VOTACION_GANADOR"].replace("%", "").strip()
            results[(int(row["ID_ESTADO"]), int(row["ID_DISTRITO_FEDERAL"]))] = {
                "districtSeat": row["CABECERA_DISTRITAL_FEDERAL"].strip() or None,
                "electionActor": row["NOMBRE_ACTOR_POLITICO"].strip() or None,
                "winningVotes": int(row["VOTACION_GANADOR"]),
                "winningPct": float(pct),
            }
    return results


def load_senate_election_results() -> dict[tuple[int, int], dict]:
    results: dict[tuple[int, int], dict] = {}
    with INE_INTEGRATION_PATH.open(encoding="utf-8-sig", newline="") as source:
        for row in csv.DictReader(source):
            if row["TIPO_DE_CANDIDATURA"] != "SEN_MR":
                continue
            pct = row["PORCENTAJE_VOTACION_GANADOR"].replace("%", "").strip()
            results[(int(row["ID_ESTADO"]), int(row["NUMERO_LISTA"]))] = {
                "districtSeat": None,
                "electionActor": row["NOMBRE_ACTOR_POLITICO"].strip() or None,
                "winningVotes": int(row["VOTACION_GANADOR"]),
                "winningPct": float(pct),
            }
    return results


SENATE_CHOICE = {
    "PRO": "Favor",
    "CONTRA": "Contra",
    "ABSTENCIÓN": "Abstención",
    "AUSENTE": "Ausente",
}


def export() -> None:
    mr_results = load_mr_election_results()
    with sqlite3.connect(DB_PATH) as conn:
        seats = rows(
            conn,
            """
            SELECT
                diputado_id AS id,
                gaceta_deputy_id AS deputyId,
                display_name AS name,
                party_key AS party,
                seat_type AS seatType,
                id_estado AS stateId,
                nombre_estado AS state,
                id_distrito_federal AS district,
                circunscripcion,
                numero_lista AS listNumber,
                source_name_role AS nameRole
            FROM dim_diputados
            WHERE legislature = 66
            ORDER BY party_key, display_name
            """,
        )
        for seat in seats:
            result = (
                mr_results.get((seat["stateId"], seat["district"]))
                if seat["seatType"] == "MR"
                else None
            )
            seat.update(
                result
                or {
                    "districtSeat": None,
                    "electionActor": None,
                    "winningVotes": None,
                    "winningPct": None,
                }
            )

        votes = rows(
            conn,
            """
            WITH totals AS (
                SELECT
                    gaceta_vote_id,
                    SUM(CASE WHEN party_key = 'Total' AND vote_choice = 'Favor' THEN count ELSE 0 END) AS favor,
                    SUM(CASE WHEN party_key = 'Total' AND vote_choice = 'Contra' THEN count ELSE 0 END) AS contra,
                    SUM(CASE WHEN party_key = 'Total' AND vote_choice IN ('Abstención', 'Abstencion') THEN count ELSE 0 END) AS abstention,
                    SUM(CASE WHEN party_key = 'Total' AND vote_choice = 'Ausente' THEN count ELSE 0 END) AS absent,
                    SUM(CASE WHEN party_key = 'Total' AND vote_choice = 'Quórum *' THEN count ELSE 0 END) AS presentNoVote,
                    SUM(CASE WHEN party_key = 'Total' AND vote_choice = 'Total' THEN count ELSE 0 END) AS total
                FROM fact_gaceta_vote_summary
                GROUP BY gaceta_vote_id
            )
            SELECT
                v.gaceta_vote_id AS id,
                v.vote_date AS date,
                v.title,
                v.status_text AS status,
                v.source_url AS sourceUrl,
                t.favor,
                t.contra,
                t.abstention,
                t.absent,
                t.presentNoVote,
                t.total,
                c.etapa_votacion AS stage,
                c.tema_politica AS topic
            FROM dim_gaceta_vote v
            JOIN totals t USING (gaceta_vote_id)
            LEFT JOIN fact_gaceta_vote_classification c USING (gaceta_vote_id)
            WHERE v.legislature = 66
            ORDER BY v.vote_date DESC, v.gaceta_vote_id DESC
            """,
        )

        histories: dict[str, list[list[str]]] = {seat["id"]: [] for seat in seats}
        for row in rows(
            conn,
            """
            SELECT d.diputado_id AS seatId, f.gaceta_vote_id AS voteId, f.vote_choice AS choice
            FROM dim_diputados d
            JOIN fact_gaceta_deputy_vote f ON f.deputy_id = d.gaceta_deputy_id
            JOIN dim_gaceta_vote v USING (gaceta_vote_id)
            WHERE d.legislature = 66 AND v.legislature = 66
            ORDER BY d.diputado_id, v.vote_date DESC, f.gaceta_vote_id DESC
            """,
        ):
            histories[row["seatId"]].append([row["voteId"], row["choice"]])

        party_votes: dict[str, dict[str, dict[str, int]]] = {}
        for row in rows(
            conn,
            """
            SELECT s.gaceta_vote_id AS voteId, s.party_key AS party, s.vote_choice AS choice, s.count
            FROM fact_gaceta_vote_summary s
            JOIN dim_gaceta_vote v USING (gaceta_vote_id)
            WHERE v.legislature = 66
              AND s.party_key <> 'Total'
              AND s.vote_choice <> 'Total'
            ORDER BY s.gaceta_vote_id, s.party_key, s.vote_choice
            """,
        ):
            vote = party_votes.setdefault(row["voteId"], {})
            party = vote.setdefault(row["party"], {})
            party[row["choice"]] = int(row["count"] or 0)

    payload = {
        "manifest": {
            "schemaVersion": 1,
            "legislature": 66,
            "sourceThrough": max(vote["date"] for vote in votes),
            "seatCount": len(seats),
            "voteCount": len(votes),
            "linkedSeats": sum(bool(seat["deputyId"]) for seat in seats),
        },
        "seats": seats,
        "votes": votes,
        "histories": histories,
        "partyVotes": party_votes,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(f"Wrote {OUT_PATH} ({OUT_PATH.stat().st_size / 1_048_576:.1f} MB)")


def export_senate() -> None:
    election_results = load_senate_election_results()
    with sqlite3.connect(DB_PATH) as conn:
        seats = rows(
            conn,
            """
            SELECT
                senador_seat_id AS id,
                senador_id AS senatorId,
                display_name AS name,
                party_key AS party,
                seat_type AS seatType,
                id_estado AS stateId,
                nombre_estado AS state,
                NULL AS district,
                NULL AS circunscripcion,
                numero_lista AS listNumber,
                source_name_role AS nameRole
            FROM dim_senadores
            WHERE legislature = 66
            ORDER BY party_key, display_name
            """,
        )
        for seat in seats:
            result = (
                election_results.get((seat["stateId"], seat["listNumber"]))
                if seat["seatType"] in {"MR", "FM"}
                else None
            )
            seat.update(
                result
                or {
                    "districtSeat": None,
                    "electionActor": None,
                    "winningVotes": None,
                    "winningPct": None,
                }
            )

        votes = rows(
            conn,
            """
            WITH totals AS (
                SELECT
                    votacion_id,
                    SUM(voto = 'PRO') AS favor,
                    SUM(voto = 'CONTRA') AS contra,
                    SUM(voto = 'ABSTENCIÓN') AS abstention,
                    SUM(voto = 'AUSENTE') AS absent,
                    COUNT(*) AS total
                FROM fact_senador_vote
                GROUP BY votacion_id
            )
            SELECT
                CAST(v.votacion_id AS TEXT) AS id,
                v.vote_date AS date,
                v.description AS title,
                v.period_type AS status,
                v.url AS sourceUrl,
                t.favor,
                t.contra,
                t.abstention,
                t.absent,
                0 AS presentNoVote,
                t.total,
                v.vote_type AS stage,
                'Senado' AS topic
            FROM dim_senado_vote v
            JOIN totals t USING (votacion_id)
            WHERE v.legislature = 66
            ORDER BY v.vote_date DESC, v.votacion_id DESC
            """,
        )

        histories: dict[str, list[list[str]]] = {seat["id"]: [] for seat in seats}
        for row in rows(
            conn,
            """
            SELECT
                d.senador_seat_id AS seatId,
                CAST(f.votacion_id AS TEXT) AS voteId,
                f.voto AS choice
            FROM dim_senadores d
            JOIN fact_senador_vote f ON f.senador_id = d.senador_id
            JOIN dim_senado_vote v USING (votacion_id)
            WHERE d.legislature = 66 AND v.legislature = 66
            ORDER BY d.senador_seat_id, v.vote_date DESC, f.votacion_id DESC
            """,
        ):
            choice = SENATE_CHOICE.get(row["choice"])
            if choice:
                histories[row["seatId"]].append([row["voteId"], choice])

        party_votes: dict[str, dict[str, dict[str, int]]] = {}
        for row in rows(
            conn,
            """
            SELECT
                CAST(f.votacion_id AS TEXT) AS voteId,
                COALESCE(NULLIF(f.grupo_parlamentario, ''), 'SG') AS party,
                f.voto AS choice,
                COUNT(*) AS count
            FROM fact_senador_vote f
            JOIN dim_senado_vote v USING (votacion_id)
            WHERE v.legislature = 66 AND f.voto IS NOT NULL
            GROUP BY f.votacion_id, party, f.voto
            ORDER BY f.votacion_id, party, f.voto
            """,
        ):
            choice = SENATE_CHOICE.get(row["choice"])
            if not choice:
                continue
            vote = party_votes.setdefault(row["voteId"], {})
            party = vote.setdefault(row["party"], {})
            party[choice] = int(row["count"] or 0)

    payload = {
        "manifest": {
            "schemaVersion": 1,
            "legislature": 66,
            "sourceThrough": max(vote["date"] for vote in votes),
            "seatCount": len(seats),
            "voteCount": len(votes),
            "linkedSeats": sum(seat["senatorId"] is not None for seat in seats),
        },
        "seats": seats,
        "votes": votes,
        "histories": histories,
        "partyVotes": party_votes,
    }
    SENATE_OUT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(
        f"Wrote {SENATE_OUT_PATH} "
        f"({SENATE_OUT_PATH.stat().st_size / 1_048_576:.1f} MB)"
    )


if __name__ == "__main__":
    export()
    export_senate()
