"""Export a compact LXVI Gaceta snapshot for the public website draft."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# One alias table for the whole project. The Gaceta prints MORENA as "MRN" and
# independents as "CAND_INDEPENDIENTE", so without this the hemicycle legend and
# the vote breakdown on the same page label the same bench differently.
from lib.canonical import (  # noqa: E402
    canonical_classification_code as canonical_code,
    canonical_party,
)

# The Camara still reads its persisted resolved-seat tables. The Senado takes
# the simpler path: resolve its website model in memory from source tables,
# current-roster CSV and audited overrides, then serialize it directly.
from camara_de_diputados.escanos.seat_members import (  # noqa: E402
    person_histories as camara_person_histories,
)
from camara_de_senadores.escanos.seat_members import (  # noqa: E402
    person_histories as senado_person_histories,
    resolve_display_names as resolve_senado_display_names,
    resolve_seats as resolve_senado_seats,
)

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
def rows(conn: sqlite3.Connection, query: str, params: tuple = ()) -> list[dict]:
    conn.row_factory = sqlite3.Row
    return [dict(row) for row in conn.execute(query, params)]


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
    return add_camara_thresholds(conn, votes)


def add_camara_thresholds(
    conn: sqlite3.Connection, votes: list[dict]
) -> list[dict]:
    """Attach the stored quorum and majority thresholds to each Camara vote.

    Camara-only, because the arithmetic keys off `total` meaning the full
    500-seat chamber. Read rather than recomputed: the derivation lives in
    camara_de_diputados/votos/materialize.py so that "mayoria calificada" means the same
    thing in Streamlit and here.
    """
    stored = {
        row.pop("id"): {
            "present": row["presentes"],
            "quorumRequired": row["quorum_requerido"],
            "absoluteRequired": row["mayoria_absoluta_requerida"],
            "qualifiedRequired": row["mayoria_calificada_requerida"],
            "quorumOk": bool(row["quorum_ok"]),
            "simpleOk": bool(row["mayoria_simple_ok"]),
            "absoluteOk": bool(row["mayoria_absoluta_ok"]),
            "qualifiedOk": bool(row["mayoria_calificada_ok"]),
        }
        for row in rows(
            conn,
            "SELECT gaceta_vote_id AS id, * FROM fact_legislature_66_vote_threshold",
        )
    }
    missing = [vote["id"] for vote in votes if vote["id"] not in stored]
    if missing:
        raise SystemExit(
            f"{len(missing)} Camara votes have no stored thresholds "
            f"(first: {missing[0]}). Run camara_de_diputados/votos/materialize.py first."
        )
    for vote in votes:
        vote["thresholds"] = stored[vote["id"]]
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


# The Camara payload still uses its persisted resolution. Senate resolution is
# assembled directly below. Column order is the published JSON contract.
CAMARA_RESOLVED_SEAT_SQL = """
        SELECT
            d.diputado_id AS id,
            r.elected_person_id AS electedPersonId,
            d.display_name AS electedName,
            r.titular_name AS titularName,
            r.substitute_name AS substituteName,
            r.elected_party AS electedParty,
            d.seat_type AS seatType,
            d.id_estado AS stateId,
            d.nombre_estado AS state,
            d.id_distrito_federal AS district,
            d.circunscripcion,
            d.numero_lista AS listNumber,
            d.source_name_role AS electedNameRole,
            r.current_name AS currentName,
            r.current_party AS currentParty,
            r.current_status AS currentStatus,
            r.current_person_id AS currentPersonId,
            e.district_seat AS districtSeat,
            e.election_actor AS electionActor,
            e.winning_votes AS winningVotes,
            e.winning_pct AS winningPct
        FROM dim_diputados d
        JOIN fact_legislature_66_seat_resolved r
          ON r.seat_id = d.diputado_id AND r.chamber = 'DIP'
        LEFT JOIN fact_legislature_66_seat_election_result e
          ON e.seat_id = r.seat_id AND e.chamber = 'DIP'
        WHERE d.legislature = 66
        ORDER BY d.party_key, d.display_name
    """

def load_camara_resolved_seats(conn: sqlite3.Connection) -> list[dict]:
    """Seats with occupancy and winning margin already resolved upstream.

    Raises rather than degrading if the resolution tables are missing: an empty
    hemicycle published as if it were the real chamber is worse than no build.
    """
    try:
        seats = rows(conn, CAMARA_RESOLVED_SEAT_SQL)
    except sqlite3.OperationalError as error:
        # A missing table means the resolution step has never run here, which is
        # the likeliest reason this script fails on a fresh checkout. Say so,
        # rather than surfacing "no such table" from four frames down.
        raise SystemExit(
            f"Cannot export DIP: {error}. "
            "Run this chamber's escanos/seat_members.py and "
            "escanos/seat_margins.py first."
        ) from error
    if not seats:
        raise SystemExit(
            "No resolved seats for DIP. "
            "Run this chamber's escanos/seat_members.py first."
        )
    return seats


def load_roster_meta(conn: sqlite3.Connection, chamber: str) -> dict:
    row = rows(
        conn,
        """
        SELECT roster_observed_at AS observedAt, roster_source_url AS sourceUrl
        FROM fact_legislature_66_seat_resolved
        WHERE chamber = ?
        LIMIT 1
        """,
        (chamber,),
    )
    return dict(row[0]) if row else {"observedAt": None, "sourceUrl": None}


def load_former_members(conn: sqlite3.Connection, chamber: str) -> list[dict]:
    return rows(
        conn,
        """
        SELECT
            person_id AS personId,
            person_name AS name,
            party_key AS party,
            seat_id AS seatId,
            seat_role AS seatRole,
            relationship_source_url AS relationshipSourceUrl
        FROM fact_legislature_66_former_member
        WHERE chamber = ?
        ORDER BY person_name
        """,
        (chamber,),
    )


def load_person_alias_map(conn: sqlite3.Connection, chamber: str) -> dict[str, str]:
    return {
        row["personId"]: row["canonicalPersonId"]
        for row in rows(
            conn,
            """
            SELECT person_id AS personId, canonical_person_id AS canonicalPersonId
            FROM fact_legislature_66_person_alias
            WHERE chamber = ?
            ORDER BY person_id
            """,
            (chamber,),
        )
    }


def load_seat_members(
    conn: sqlite3.Connection, chamber: str, seats: list[dict]
) -> dict[str, list[dict]]:
    """Seat -> its members, in published order.

    Two orderings matter and neither is cosmetic. Within a seat, `member_index`
    is the contract: the client stores a member's *position*, not their id, in
    every history entry. Across seats, the mapping follows `seats`, so a seat
    that no roll-call identity could be linked to still appears -- as an empty
    list rather than a missing key.
    """
    members: dict[str, list[dict]] = {seat["id"]: [] for seat in seats}
    for row in rows(
        conn,
        """
        SELECT
            seat_id AS seatId,
            person_id AS personId,
            person_name AS name,
            party_key AS party,
            seat_role AS role,
            relationship_source_url AS sourceUrl,
            vote_count AS voteCount
        FROM fact_legislature_66_seat_member
        WHERE chamber = ?
        ORDER BY seat_id, member_index
        """,
        (chamber,),
    ):
        members[row.pop("seatId")].append(row)
    return members


def load_seat_vote_conflicts(conn: sqlite3.Connection, chamber: str) -> list[dict]:
    return [
        {
            "seatId": row["seatId"],
            "voteId": row["voteId"],
            "countedPersonId": row["countedPersonId"],
            "reportedPersonIds": row["reportedPersonIds"].split(","),
        }
        for row in rows(
            conn,
            """
            SELECT
                seat_id AS seatId,
                vote_id AS voteId,
                counted_person_id AS countedPersonId,
                reported_person_ids AS reportedPersonIds
            FROM fact_legislature_66_seat_vote_conflict
            WHERE chamber = ?
            ORDER BY seat_id, vote_id
            """,
            (chamber,),
        )
    ]


def build_chamber_payload(
    conn: sqlite3.Connection,
    chamber: str,
    votes: list[dict],
    party_votes: dict,
    schema_version: int = 6,
) -> dict:
    """Assemble one chamber's hemicycle payload from the resolved warehouse.

    Every identity decision behind this -- which roll-call name is which person,
    which seat they occupied, which of two reported votes a seat actually cast --
    was made and recorded by each chamber's escanos/seat_members.py. This
    reads those answers and shapes them; it does not re-derive any of them.
    """
    if chamber != "DIP":
        raise ValueError("build_chamber_payload is the legacy Camara-only path")
    seats = load_camara_resolved_seats(conn)

    return {
        "manifest": {
            "schemaVersion": schema_version,
            "legislature": 66,
            "chamber": "diputados" if chamber == "DIP" else "senado",
            "sourceThrough": max(vote["date"] for vote in votes),
            "seatCount": len(seats),
            "voteCount": len(votes),
            "linkedSeats": sum(bool(seat["electedPersonId"]) for seat in seats),
            "roster": load_roster_meta(conn, chamber),
            **roster_stats(seats),
        },
        "seats": seats,
        "formerMembers": load_former_members(conn, chamber),
        "personAliases": load_person_alias_map(conn, chamber),
        "votes": votes,
        "histories": camara_person_histories(conn),
        "seatMembers": load_seat_members(conn, chamber, seats),
        "seatVoteConflicts": load_seat_vote_conflicts(conn, chamber),
        "partyVotes": party_votes,
    }


def build_senate_payload(
    conn: sqlite3.Connection,
    votes: list[dict],
    party_votes: dict,
    schema_version: int = 6,
) -> dict:
    """Resolve and serialize Senado directly, without derived warehouse tables."""
    resolved = resolve_senado_seats(conn)
    seats = resolved["seats"]
    if len(seats) != 128:
        raise ValueError(f"Expected 128 Senate seats; resolved {len(seats)}")
    return {
        "manifest": {
            "schemaVersion": schema_version,
            "legislature": 66,
            "chamber": "senado",
            "sourceThrough": max(vote["date"] for vote in votes),
            "seatCount": len(seats),
            "voteCount": len(votes),
            "linkedSeats": sum(bool(seat["electedPersonId"]) for seat in seats),
            "roster": resolved["roster"],
            **roster_stats(seats),
        },
        "seats": seats,
        "formerMembers": resolved["formerMembers"],
        "personAliases": resolved["aliases"],
        "votes": votes,
        "histories": senado_person_histories(conn, resolved),
        "seatMembers": resolved["seatMembers"],
        "seatVoteConflicts": resolved["conflicts"],
        "partyVotes": party_votes,
    }


def write_payload(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(f"Wrote {path} ({path.stat().st_size / 1_048_576:.1f} MB)")


def export() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        payload = build_chamber_payload(
            conn, "DIP", camara_votes(conn), camara_party_votes(conn)
        )
    write_payload(OUT_PATH, payload)


def export_senate() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        payload = build_senate_payload(conn, senado_votes(conn), senado_party_votes(conn))
    write_payload(SENATE_OUT_PATH, payload)


def ballot_names(conn: sqlite3.Connection) -> tuple[dict[str, str], dict[str, str]]:
    """Person id -> display name, per chamber, for everyone who cast a ballot.

    The precedence between the seat tables and the chamber's own spelling is
    decided upstream and stored; this only reads it.
    """
    names: dict[str, dict[str, str]] = {"DIP": {}, "SEN": {}}
    for row in rows(
        conn,
        """
        SELECT chamber, person_id AS personId, display_name AS name
        FROM fact_legislature_66_person
        WHERE chamber = 'DIP'
        """,
    ):
        names[row["chamber"]][row["personId"]] = row["name"]
    for _, person_id, name, _ in resolve_senado_display_names(conn):
        names["SEN"][person_id] = name
    return names["DIP"], names["SEN"]


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
