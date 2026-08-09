"""Export a compact LXVI Gaceta snapshot for the public website draft."""

from __future__ import annotations

import csv
import json
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# One alias table for the whole project. The Gaceta prints MORENA as "MRN" and
# independents as "CAND_INDEPENDIENTE", so without this the hemicycle legend and
# the vote breakdown on the same page label the same bench differently.
from ingestion.congress_roster_ingest import canonical_party  # noqa: E402

DB_PATH = ROOT / "election_data.db"
OUT_PATH = ROOT / "web" / "public" / "data" / "legislature-66.json"
SENATE_OUT_PATH = ROOT / "web" / "public" / "data" / "senate-66.json"
# Just the manifests, so the /visualizaciones index can print live counts
# without pulling the 6 MB of seats and histories behind them.
SUMMARY_PATH = ROOT / "web" / "public" / "data" / "visualizaciones.json"
INE_INTEGRATION_PATH = (
    ROOT
    / "data"
    / "electoral_data_raw"
    / "raw_2024"
    / "PRESIDENCIA_2024"
    / "CSV"
    / "INTEGRACION_CARGOS_PEF_2024.csv"
)


def rows(conn: sqlite3.Connection, query: str, params: tuple = ()) -> list[dict]:
    conn.row_factory = sqlite3.Row
    return [dict(row) for row in conn.execute(query, params)]


def load_current_roster(conn: sqlite3.Connection, chamber: str) -> tuple[dict[str, dict], dict]:
    """Latest official-directory occupancy for every constitutional seat.

    The INE integration names the person *elected* in 2024. By this cutoff a
    tenth of the Camara and a fifth of the Senado is occupied by someone else —
    suplentes who took over, members on licencia, one vacancy. Those are the
    people actually casting the votes this site publishes, so the site has to be
    able to show them. `fact_congress_roster_seat` is the same source the
    Streamlit "Composicion actual" view reads; keep the two in agreement.
    """
    snapshot = rows(
        conn,
        """
        SELECT snapshot_id, observed_at, source_url
        FROM dim_congress_roster_snapshot
        WHERE chamber = ?
        ORDER BY observed_at DESC, created_at DESC
        LIMIT 1
        """,
        (chamber,),
    )
    if not snapshot:
        raise SystemExit(
            f"No roster snapshot for {chamber}. Run ingestion/congress_roster_ingest.py first."
        )
    meta = snapshot[0]
    roster = {
        row["seatId"]: row
        for row in rows(
            conn,
            """
            SELECT
                seat_id AS seatId,
                current_name AS currentName,
                current_party AS currentParty,
                member_status AS currentStatus,
                vote_person_id AS currentPersonId
            FROM fact_congress_roster_seat
            WHERE snapshot_id = ?
            """,
            (meta["snapshot_id"],),
        )
    }
    return roster, {
        "observedAt": meta["observed_at"],
        "sourceUrl": meta["source_url"],
    }


def apply_roster(seats: list[dict], roster: dict[str, dict]) -> None:
    """Attach current occupancy to seats, keeping the elected identity intact.

    A seat with no roster row keeps its elected occupant rather than vanishing:
    an incomplete crawl must not silently empty the hemicycle.
    """
    missing = [seat["id"] for seat in seats if seat["id"] not in roster]
    if missing:
        print(f"  warning: {len(missing)} seats absent from the roster snapshot")
    for seat in seats:
        # INE writes CAND_INDEPENDIENTE where the directory writes IND. Canonicalize
        # both benches or the two views disagree about a party that never changed.
        seat["electedParty"] = canonical_party(seat["electedParty"])
        entry = roster.get(seat["id"])
        if entry is None:
            seat.update(
                currentName=seat["electedName"],
                currentParty=seat["electedParty"],
                currentStatus="sin_directorio",
                currentPersonId=seat["electedPersonId"],
            )
            continue
        seat.update(
            currentName=entry["currentName"],
            currentParty=canonical_party(entry["currentParty"]),
            currentStatus=entry["currentStatus"],
            currentPersonId=entry["currentPersonId"] or None,
        )


def roster_stats(seats: list[dict]) -> dict:
    return {
        "substitutedSeats": sum(
            seat["currentPersonId"] != seat["electedPersonId"] for seat in seats
        ),
        "partyChangedSeats": sum(
            seat["currentStatus"] == "en_funciones"
            and seat["currentParty"] != seat["electedParty"]
            for seat in seats
        ),
        "onLeaveSeats": sum(seat["currentStatus"] == "licencia" for seat in seats),
        "vacantSeats": sum(seat["currentStatus"] == "vacante" for seat in seats),
        "currentLinkedSeats": sum(bool(seat["currentPersonId"]) for seat in seats),
    }


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
                gaceta_deputy_id AS electedPersonId,
                display_name AS electedName,
                party_key AS electedParty,
                seat_type AS seatType,
                id_estado AS stateId,
                nombre_estado AS state,
                id_distrito_federal AS district,
                circunscripcion,
                numero_lista AS listNumber,
                source_name_role AS electedNameRole
            FROM dim_diputados
            WHERE legislature = 66
            ORDER BY party_key, display_name
            """,
        )
        roster, roster_meta = load_current_roster(conn, "DIP")
        apply_roster(seats, roster)
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

        # Histories are keyed by *person*, not by seat: a seat can have had two
        # occupants and each one owns their own record. The client looks up
        # whichever identity the active view resolves to.
        people = sorted(
            {seat["electedPersonId"] for seat in seats if seat["electedPersonId"]}
            | {seat["currentPersonId"] for seat in seats if seat["currentPersonId"]}
        )
        histories: dict[str, list[list[str]]] = {person: [] for person in people}
        placeholders = ",".join("?" * len(people))
        for row in rows(
            conn,
            f"""
            SELECT f.deputy_id AS personId, f.gaceta_vote_id AS voteId, f.vote_choice AS choice
            FROM fact_gaceta_deputy_vote f
            JOIN dim_gaceta_vote v USING (gaceta_vote_id)
            WHERE v.legislature = 66 AND f.deputy_id IN ({placeholders})
            ORDER BY f.deputy_id, v.vote_date DESC, f.gaceta_vote_id DESC
            """,
            tuple(people),
        ):
            histories[row["personId"]].append([row["voteId"], row["choice"]])

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
            party = vote.setdefault(canonical_party(row["party"]), {})
            choice = row["choice"]
            party[choice] = party.get(choice, 0) + int(row["count"] or 0)

    payload = {
        "manifest": {
            "schemaVersion": 2,
            "legislature": 66,
            "chamber": "diputados",
            "sourceThrough": max(vote["date"] for vote in votes),
            "seatCount": len(seats),
            "voteCount": len(votes),
            "linkedSeats": sum(bool(seat["electedPersonId"]) for seat in seats),
            "roster": roster_meta,
            **roster_stats(seats),
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
                CAST(senador_id AS TEXT) AS electedPersonId,
                display_name AS electedName,
                party_key AS electedParty,
                seat_type AS seatType,
                id_estado AS stateId,
                nombre_estado AS state,
                NULL AS district,
                NULL AS circunscripcion,
                numero_lista AS listNumber,
                source_name_role AS electedNameRole
            FROM dim_senadores
            WHERE legislature = 66
            ORDER BY party_key, display_name
            """,
        )
        roster, roster_meta = load_current_roster(conn, "SEN")
        apply_roster(seats, roster)
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
                COALESCE(c.etapa_votacion, v.vote_type) AS stage,
                c.tema_politica AS topic
            FROM dim_senado_vote v
            JOIN totals t USING (votacion_id)
            LEFT JOIN fact_senado_vote_classification c USING (votacion_id)
            WHERE v.legislature = 66
            ORDER BY v.vote_date DESC, v.votacion_id DESC
            """,
        )

        people = sorted(
            {seat["electedPersonId"] for seat in seats if seat["electedPersonId"]}
            | {seat["currentPersonId"] for seat in seats if seat["currentPersonId"]}
        )
        histories: dict[str, list[list[str]]] = {person: [] for person in people}
        placeholders = ",".join("?" * len(people))
        for row in rows(
            conn,
            f"""
            SELECT
                CAST(f.senador_id AS TEXT) AS personId,
                CAST(f.votacion_id AS TEXT) AS voteId,
                f.voto AS choice
            FROM fact_senador_vote f
            JOIN dim_senado_vote v USING (votacion_id)
            WHERE v.legislature = 66 AND CAST(f.senador_id AS TEXT) IN ({placeholders})
            ORDER BY f.senador_id, v.vote_date DESC, f.votacion_id DESC
            """,
            tuple(people),
        ):
            choice = SENATE_CHOICE.get(row["choice"])
            if choice:
                histories[row["personId"]].append([row["voteId"], choice])

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
            party = vote.setdefault(canonical_party(row["party"]), {})
            party[choice] = party.get(choice, 0) + int(row["count"] or 0)

    payload = {
        "manifest": {
            "schemaVersion": 2,
            "legislature": 66,
            "sourceThrough": max(vote["date"] for vote in votes),
            "chamber": "senado",
            "seatCount": len(seats),
            "voteCount": len(votes),
            "linkedSeats": sum(seat["electedPersonId"] is not None for seat in seats),
            "roster": roster_meta,
            **roster_stats(seats),
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


def export_summary() -> None:
    """Manifest-only digest for the dashboard index."""
    summary = {
        slug: json.loads(path.read_text(encoding="utf-8"))["manifest"]
        for slug, path in (("diputados", OUT_PATH), ("senado", SENATE_OUT_PATH))
    }
    SUMMARY_PATH.write_text(
        json.dumps(summary, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(f"Wrote {SUMMARY_PATH} ({SUMMARY_PATH.stat().st_size} B)")


if __name__ == "__main__":
    export()
    export_senate()
    export_summary()
