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
from ui.person_names import display_person_name  # noqa: E402

# Quorum and the three majority thresholds are derived, not stored. Import the
# derivation the Streamlit app reads through data/materialized/ rather than
# restating the arithmetic here: "mayoria calificada" must mean the same thing
# on both front ends, and the parquet those columns live in is gitignored.
from ingestion.gaceta_materialize import add_vote_thresholds  # noqa: E402

DB_PATH = ROOT / "election_data.db"
OUT_PATH = ROOT / "web" / "public" / "data" / "legislature-66.json"
SENATE_OUT_PATH = ROOT / "web" / "public" / "data" / "senate-66.json"
VOTES_OUT_PATH = ROOT / "web" / "public" / "data" / "votes-66.json"
# Names for the individual squares, split out because the vote search itself
# does not need them: the list, the filters and the totals all work from
# votes-66.json, and this only has to arrive before a reader hovers a square.
BALLOTS_OUT_PATH = ROOT / "web" / "public" / "data" / "vote-ballots-66.json"
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

GACETA_HOST = "https://gaceta.diputados.gob.mx"
MESES_ES = {
    1: "ene", 2: "feb", 3: "mar", 4: "abr", 5: "may", 6: "jun",
    7: "jul", 8: "ago", 9: "sep", 10: "oct", 11: "nov", 12: "dic",
}

# Both chambers classify against the same schema but not the same spelling.
# `dictamen_de_comision(es)` is one concept written two ways, and on a page that
# lists both chambers it would otherwise render as two separate filter chips
# meaning the same thing. The minuta codes look like the same case and are not:
# each one names the chamber the bill arrived *from*, so they stay distinct.
CLASSIFICATION_ALIASES = {
    "dictamen_de_comisiones": "dictamen_de_comision",
}


def canonical_code(value: str | None) -> str | None:
    """Collapse spelling variants of one classification code. Compare with
    `canonical_party` — same problem, same reason to solve it in the exporter."""
    if not value:
        return None
    return CLASSIFICATION_ALIASES.get(value, value)


def gaceta_issue_url(gaceta_date: str | None) -> str | None:
    """Daily Gaceta Parlamentaria issue carrying a vote's dictamen. A vote's own
    `source_url` points only at its tally table, never at the bill text."""
    if not gaceta_date:
        return None
    try:
        year, month, day = str(gaceta_date)[:10].split("-")
        month_name = MESES_ES[int(month)]
    except (ValueError, KeyError):
        return None
    return f"{GACETA_HOST}/Gaceta/66/{year}/{month_name}/{year}{month}{day}.html"


def has_columns(conn: sqlite3.Connection, table: str, columns: set[str]) -> bool:
    present = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    return columns.issubset(present)


def camara_votes(conn: sqlite3.Connection) -> list[dict]:
    """Every LXVI Camara roll call with its tallies and full classification.

    One definition, three consumers: the hemicycle payload, the Senado-facing
    counterpart and the vote explorer. They diverged once already on which
    classification axes were carried; keeping the SELECT here is what stops the
    same vote from describing itself differently on two routes of one site.
    """
    # review_status/review_notes arrived with the deterministic review pass. A
    # warehouse built before it still classifies, so degrade rather than crash.
    review_select = (
        "c.review_status AS reviewStatus"
        if has_columns(conn, "fact_gaceta_vote_classification", {"review_status"})
        else "'legacy_model_only' AS reviewStatus"
    )
    votes = rows(
        conn,
        f"""
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
            v.gaceta_date AS gacetaDate,
            t.favor,
            t.contra,
            t.abstention,
            t.absent,
            t.presentNoVote,
            t.total,
            c.etapa_votacion AS stage,
            c.tema_politica AS topic,
            c.origen AS origin,
            c.tipo_instrumento AS instrument,
            c.requiere_revision AS requiresReview,
            {review_select}
        FROM dim_gaceta_vote v
        JOIN totals t USING (gaceta_vote_id)
        LEFT JOIN fact_gaceta_vote_classification c USING (gaceta_vote_id)
        WHERE v.legislature = 66
        ORDER BY v.vote_date DESC, v.gaceta_vote_id DESC
        """,
    )
    for vote in votes:
        vote["chamber"] = "diputados"
        vote["gacetaUrl"] = gaceta_issue_url(vote.pop("gacetaDate"))
        for field in ("stage", "topic", "origin", "instrument"):
            vote[field] = canonical_code(vote[field])
        vote["review"] = {
            # Camara review is deterministic (rule_checked / audited), so it
            # records an outcome. The Senado has none and reports null.
            "status": vote.pop("reviewStatus"),
            "requiresReview": bool(vote.pop("requiresReview")),
        }
    return add_camara_thresholds(votes)


def add_camara_thresholds(votes: list[dict]) -> list[dict]:
    """Attach quorum and the three majority thresholds to each Camara vote.

    Camara-only on purpose. The arithmetic keys off `total` meaning the full
    500-seat chamber, which is what the Gaceta tally reports. A Senado tally's
    total is the number of senators recorded in that roll call, not the 128-seat
    chamber, so the same formula there would compute a quorum floor against a
    denominator that already excludes the absent.
    """
    if not votes:
        return votes
    import pandas as pd

    frame = pd.DataFrame(
        {
            "favor": [vote["favor"] for vote in votes],
            "contra": [vote["contra"] for vote in votes],
            "abstencion": [vote["abstention"] for vote in votes],
            "quorum": [vote["presentNoVote"] for vote in votes],
            "ausente": [vote["absent"] for vote in votes],
            "total": [vote["total"] for vote in votes],
        }
    )
    derived = add_vote_thresholds(frame)
    for vote, (_, row) in zip(votes, derived.iterrows()):
        vote["thresholds"] = {
            "present": int(row["presentes"]),
            "quorumRequired": int(row["quorum_requerido"]),
            "absoluteRequired": int(row["mayoria_absoluta_requerida"]),
            "qualifiedRequired": int(row["mayoria_calificada_requerida"]),
            "quorumOk": bool(row["quorum_ok"]),
            "simpleOk": bool(row["mayoria_simple_ok"]),
            "absoluteOk": bool(row["mayoria_absoluta_ok"]),
            "qualifiedOk": bool(row["mayoria_calificada_ok"]),
        }
    return votes


def senado_votes(conn: sqlite3.Connection) -> list[dict]:
    """Every LXVI Senado roll call. Same shape as `camara_votes`, minus the
    thresholds and the daily-Gaceta link, which have no Senado equivalent."""
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
            c.tema_politica AS topic,
            c.origen AS origin,
            c.tipo_instrumento AS instrument,
            c.requiere_revision AS requiresReview
        FROM dim_senado_vote v
        JOIN totals t USING (votacion_id)
        LEFT JOIN fact_senado_vote_classification c USING (votacion_id)
        WHERE v.legislature = 66
        ORDER BY v.vote_date DESC, v.votacion_id DESC
        """,
    )
    for vote in votes:
        vote["chamber"] = "senado"
        vote["gacetaUrl"] = None
        vote["thresholds"] = None
        for field in ("stage", "topic", "origin", "instrument"):
            vote[field] = canonical_code(vote[field])
        vote["review"] = {
            # The Senado pass never ran the deterministic review, so it has no
            # status. Do not invent one: a reader must be able to tell a
            # reviewed label from an unreviewed one.
            "status": None,
            "requiresReview": bool(vote.pop("requiresReview")),
        }
    return votes


def camara_party_votes(conn: sqlite3.Connection) -> dict[str, dict[str, dict[str, int]]]:
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
    return party_votes


def senado_party_votes(conn: sqlite3.Connection) -> dict[str, dict[str, dict[str, int]]]:
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
    return party_votes


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

        votes = camara_votes(conn)

        # A roll-call identity can outlive both seat snapshots available here:
        # the elected result and today's directory. Export those interim former
        # members explicitly so the person-level search remains a full LXVI
        # voting archive rather than only a current-roster browser.
        linked_people = (
            {seat["electedPersonId"] for seat in seats if seat["electedPersonId"]}
            | {seat["currentPersonId"] for seat in seats if seat["currentPersonId"]}
        )
        voter_people = {
            row["personId"]
            for row in rows(
                conn,
                """
                SELECT DISTINCT f.deputy_id AS personId
                FROM fact_gaceta_deputy_vote f
                JOIN dim_gaceta_vote v USING (gaceta_vote_id)
                WHERE v.legislature = 66
                """,
            )
        }
        people = sorted(linked_people | voter_people)
        former_members = []
        for member in rows(
            conn,
            """
            WITH ranked AS (
                SELECT
                    f.deputy_id AS personId,
                    d.deputy_name AS name,
                    f.party_key AS party,
                    ROW_NUMBER() OVER (
                        PARTITION BY f.deputy_id
                        ORDER BY v.vote_date DESC, f.gaceta_vote_id DESC
                    ) AS recency
                FROM fact_gaceta_deputy_vote f
                JOIN dim_gaceta_vote v USING (gaceta_vote_id)
                JOIN dim_gaceta_deputy d ON d.deputy_id = f.deputy_id
                WHERE v.legislature = 66
            )
            SELECT personId, name, party
            FROM ranked
            WHERE recency = 1
            ORDER BY name
            """,
        ):
            if member["personId"] not in linked_people:
                member["party"] = canonical_party(member["party"] or "SG")
                former_members.append(member)

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

        party_votes = camara_party_votes(conn)

    payload = {
        "manifest": {
            "schemaVersion": 3,
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
        "formerMembers": former_members,
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

        votes = senado_votes(conn)

        linked_people = (
            {seat["electedPersonId"] for seat in seats if seat["electedPersonId"]}
            | {seat["currentPersonId"] for seat in seats if seat["currentPersonId"]}
        )
        voter_people = {
            row["personId"]
            for row in rows(
                conn,
                """
                SELECT DISTINCT CAST(f.senador_id AS TEXT) AS personId
                FROM fact_senador_vote f
                JOIN dim_senado_vote v USING (votacion_id)
                WHERE v.legislature = 66
                """,
            )
        }
        people = sorted(linked_people | voter_people)
        former_members = []
        for member in rows(
            conn,
            """
            WITH ranked AS (
                SELECT
                    CAST(f.senador_id AS TEXT) AS personId,
                    d.senador_name AS name,
                    COALESCE(NULLIF(f.grupo_parlamentario, ''), 'SG') AS party,
                    ROW_NUMBER() OVER (
                        PARTITION BY f.senador_id
                        ORDER BY v.vote_date DESC, f.votacion_id DESC
                    ) AS recency
                FROM fact_senador_vote f
                JOIN dim_senado_vote v USING (votacion_id)
                JOIN dim_senador d ON d.senador_id = f.senador_id
                WHERE v.legislature = 66
            )
            SELECT personId, name, party
            FROM ranked
            WHERE recency = 1
            ORDER BY name
            """,
        ):
            if member["personId"] not in linked_people:
                member["party"] = canonical_party(member["party"])
                former_members.append(member)

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

        party_votes = senado_party_votes(conn)

    payload = {
        "manifest": {
            "schemaVersion": 3,
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
        "formerMembers": former_members,
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


def senator_display_name(raw: str | None) -> str:
    """`Sen. Martín del Campo Martín del Campo, Juan Antonio` -> given-name first.

    The Senado roll call prints an honorific and puts surnames before a comma;
    everywhere else on this site a person reads as `Nombre Apellido`. Only the
    comma is trusted to split the name — the deputy roll call has no comma and
    guessing where its surnames end would rename people.
    """
    name = str(raw or "").strip()
    if name.lower().startswith("sen."):
        name = name[4:].strip()
    if "," in name:
        surnames, _, given = name.partition(",")
        name = f"{given.strip()} {surnames.strip()}"
    return display_person_name(name)


def ballot_names(conn: sqlite3.Connection) -> tuple[dict[str, str], dict[str, str]]:
    """Person ID -> display name for everyone who cast an LXVI ballot.

    Prefers the seat tables, so a legislator is named the same way here as in
    the hemicycle and the profile search. Roll calls reach people the seat
    tables never held — substitutes who served an interim — so the chamber's own
    spelling is the fallback rather than dropping the name.
    """
    deputies = {
        row["personId"]: display_person_name(row["name"])
        for row in rows(
            conn,
            "SELECT deputy_id AS personId, deputy_name AS name FROM dim_gaceta_deputy",
        )
    }
    deputies.update(
        {
            row["personId"]: row["name"]
            for row in rows(
                conn,
                """
                SELECT gaceta_deputy_id AS personId, display_name AS name
                FROM dim_diputados
                WHERE legislature = 66 AND gaceta_deputy_id IS NOT NULL
                """,
            )
        }
    )
    senators = {
        row["personId"]: senator_display_name(row["name"])
        for row in rows(
            conn,
            "SELECT CAST(senador_id AS TEXT) AS personId, senador_name AS name FROM dim_senador",
        )
    }
    senators.update(
        {
            row["personId"]: row["name"]
            for row in rows(
                conn,
                """
                SELECT CAST(senador_id AS TEXT) AS personId, display_name AS name
                FROM dim_senadores
                WHERE legislature = 66
                """,
            )
        }
    )
    return deputies, senators


def export_ballots(party_votes: dict[str, dict[str, dict[str, int]]]) -> None:
    """Per-square legislator names, shaped to mirror `partyVotes` exactly.

    The counts stay authoritative. `partyVotes` is the chamber's own tally and is
    what sizes every grid; this file only says who the squares are, as an
    ordered name list per party and choice that the client zips against those
    counts. Where the two sources disagree the count wins and the surplus
    squares simply go unnamed.

    They do disagree, rarely: on six LXVI Camara roll calls the per-deputy table
    files an independent under `SP` while the summary files them under `IND`.
    Same person, same choice, different bench label. Rebuilding the grid from
    ballots would render a party block that the official tally does not contain.
    """
    with sqlite3.connect(DB_PATH) as conn:
        deputy_names, senator_names = ballot_names(conn)

        # party/choice -> ordered names, per vote. Sorted by name so the squares
        # are in a stable, readable order rather than warehouse insertion order.
        grouped: dict[str, dict[str, dict[str, list[str]]]] = {}

        for row in rows(
            conn,
            """
            SELECT f.gaceta_vote_id AS voteId, f.deputy_id AS personId,
                   f.party_key AS party, f.vote_choice AS choice
            FROM fact_gaceta_deputy_vote f
            JOIN dim_gaceta_vote v USING (gaceta_vote_id)
            WHERE v.legislature = 66
            """,
        ):
            name = deputy_names.get(row["personId"])
            if not name:
                continue
            key = f"diputados:{row['voteId']}"
            party = canonical_party(row["party"] or "SG")
            grouped.setdefault(key, {}).setdefault(party, {}).setdefault(
                row["choice"], []
            ).append(name)

        for row in rows(
            conn,
            """
            SELECT CAST(f.votacion_id AS TEXT) AS voteId,
                   CAST(f.senador_id AS TEXT) AS personId,
                   COALESCE(NULLIF(f.grupo_parlamentario, ''), 'SG') AS party,
                   f.voto AS choice
            FROM fact_senador_vote f
            JOIN dim_senado_vote v USING (votacion_id)
            WHERE v.legislature = 66 AND f.voto IS NOT NULL
            """,
        ):
            choice = SENATE_CHOICE.get(row["choice"])
            name = senator_names.get(row["personId"])
            if not choice or not name:
                continue
            key = f"senado:{row['voteId']}"
            party = canonical_party(row["party"])
            grouped.setdefault(key, {}).setdefault(party, {}).setdefault(
                choice, []
            ).append(name)

    names: dict[str, int] = {}
    ballots: dict[str, dict[str, dict[str, list[int]]]] = {}
    squares = 0
    named = 0
    for key, counts_by_party in party_votes.items():
        by_party = grouped.get(key, {})
        entry: dict[str, dict[str, list[int]]] = {}
        for party, counts in counts_by_party.items():
            for choice, count in counts.items():
                if count <= 0:
                    continue
                squares += count
                # Clamp to the official count in both directions: never name
                # more squares than the tally drew, never assume a name exists.
                found = sorted(by_party.get(party, {}).get(choice, []))[:count]
                if not found:
                    continue
                named += len(found)
                entry.setdefault(party, {})[choice] = [
                    names.setdefault(name, len(names)) for name in found
                ]
        if entry:
            ballots[key] = entry

    coverage = named / squares if squares else 0
    payload = {
        "manifest": {
            "schemaVersion": 1,
            "legislature": 66,
            "squares": squares,
            "namedSquares": named,
            "people": len(names),
        },
        "names": list(names),
        "ballots": ballots,
    }
    BALLOTS_OUT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(
        f"Wrote {BALLOTS_OUT_PATH} ({BALLOTS_OUT_PATH.stat().st_size / 1024:.0f} KB, "
        f"{coverage:.2%} of squares named)"
    )


def export_votes() -> dict[str, dict[str, dict[str, int]]]:
    """Both chambers' roll calls in one payload, for the vote explorer.

    Deliberately carries no seats and no histories. Those are 90% of the
    hemicycle payloads and none of what a vote search needs; reusing
    `legislature-66.json` here would cost a reader 5.9 MB to read 0.4 MB.

    Returns the namespaced party breakdown so `export_ballots` can name squares
    against the very counts this file publishes, rather than re-deriving them.
    """
    with sqlite3.connect(DB_PATH) as conn:
        votes = camara_votes(conn) + senado_votes(conn)
        # Vote IDs are chamber-local -- the Camara's are Gaceta slugs, the
        # Senado's are small integers -- so they are namespaced here rather than
        # merged. `5115` is a real Senado vote and a plausible future Camara one.
        party_votes = {
            f"diputados:{vote_id}": breakdown
            for vote_id, breakdown in camara_party_votes(conn).items()
        } | {
            f"senado:{vote_id}": breakdown
            for vote_id, breakdown in senado_party_votes(conn).items()
        }

    votes.sort(key=lambda vote: (vote["date"], vote["chamber"], vote["id"]), reverse=True)
    missing = [vote["id"] for vote in votes if f"{vote['chamber']}:{vote['id']}" not in party_votes]
    if missing:
        print(f"  warning: {len(missing)} votes have no party breakdown")

    payload = {
        "manifest": {
            "schemaVersion": 1,
            "legislature": 66,
            "sourceThrough": max(vote["date"] for vote in votes),
            "voteCount": len(votes),
            "chambers": {
                chamber: sum(vote["chamber"] == chamber for vote in votes)
                for chamber in ("diputados", "senado")
            },
            "topicCount": len({vote["topic"] for vote in votes if vote["topic"]}),
        },
        "votes": votes,
        "partyVotes": party_votes,
    }
    VOTES_OUT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(f"Wrote {VOTES_OUT_PATH} ({VOTES_OUT_PATH.stat().st_size / 1024:.0f} KB)")
    return party_votes


def export_summary() -> None:
    """Manifest-only digest for the dashboard index."""
    summary = {
        slug: json.loads(path.read_text(encoding="utf-8"))["manifest"]
        for slug, path in (
            ("diputados", OUT_PATH),
            ("senado", SENATE_OUT_PATH),
            ("votaciones", VOTES_OUT_PATH),
        )
    }
    SUMMARY_PATH.write_text(
        json.dumps(summary, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(f"Wrote {SUMMARY_PATH} ({SUMMARY_PATH.stat().st_size} B)")


if __name__ == "__main__":
    export()
    export_senate()
    export_ballots(export_votes())
    export_summary()
