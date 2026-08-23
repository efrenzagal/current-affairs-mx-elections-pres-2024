"""Resolve LXVI roll-call identities to constitutional seats.

Roll-call rows carry no geography: `dim_gaceta_deputy` and `dim_senador` are an
id and a name. Turning "this person voted" into "this seat voted" therefore
takes real inference — matching three registers that spell the same human three
ways, deciding which of two roll-call identities is an alias rather than a
substitute, and refusing to let one constitutional seat cast two votes.

That inference used to live in `web/scripts/export_gaceta_web.py`, which meant
the warehouse only held the answer if somebody had run the website build, and
that Streamlit could not ask who actually cast a vote at all. It belongs here,
and the static export now reads the result rather than deriving it.

Legislature 66 only, by deliberate scope: the tables are named for it and the
suplente register this depends on is the 2024 INE integration.

Writes five tables, all rebuilt wholesale on every run:

    fact_legislature_66_seat_resolved      current occupancy per seat
    fact_legislature_66_seat_member        seat <-> person bridge, ordered
    fact_legislature_66_former_member      voters not in either seat snapshot
    fact_legislature_66_person_alias       roll-call identity -> canonical
    fact_legislature_66_seat_vote_conflict audit of double-voted seats
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import unicodedata
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ingestion.audited_overrides import (  # noqa: E402
    load_person_aliases,
    load_seat_overrides,
)
from ingestion.congress_roster_ingest import canonical_party  # noqa: E402
from ingestion.person_names import display_person_name  # noqa: E402


DB_PATH = ROOT / "election_data.db"
LEGISLATURE = 66

INE_INTEGRATION_PUBLIC_URL = (
    "https://ine.mx/integracion-de-diputaciones-y-senadurias-pef-2023-2024/"
)

AUDITED_FORMER_SEAT_OVERRIDES = load_seat_overrides()
AUDITED_PERSON_ALIASES = load_person_aliases()

# The Senado roll call stores its own vocabulary. Normalize to the Camara's so
# a merged history reads the same either side of the building.
SENATE_CHOICE = {
    "PRO": "Favor",
    "CONTRA": "Contra",
    "ABSTENCIÓN": "Abstención",
    "AUSENTE": "Ausente",
}

# Per-chamber SQL kept explicit rather than templated. The two chambers differ
# in more than table names -- the Senado stores integer ids that have to be cast
# and a vote vocabulary that has to be translated -- and a half-parameterized
# query that silently works for one chamber is worse than two readable ones.
CHAMBERS: dict[str, dict[str, str]] = {
    "DIP": {
        "seats": """
            SELECT
                diputado_id AS id,
                gaceta_deputy_id AS electedPersonId,
                display_name AS electedName,
                ine_candidate_name AS titularName,
                ine_substitute_name AS substituteName,
                party_key AS electedParty,
                source_name_role AS electedNameRole
            FROM dim_diputados
            WHERE legislature = 66
            ORDER BY party_key, display_name
        """,
        "voters": """
            SELECT DISTINCT f.deputy_id AS personId
            FROM fact_gaceta_deputy_vote f
            JOIN dim_gaceta_vote v USING (gaceta_vote_id)
            WHERE v.legislature = 66
        """,
        "former": """
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
        "vote_rows": """
            SELECT f.deputy_id AS personId, f.gaceta_vote_id AS voteId, f.vote_choice AS choice
            FROM fact_gaceta_deputy_vote f
            JOIN dim_gaceta_vote v USING (gaceta_vote_id)
            WHERE v.legislature = 66
            ORDER BY f.deputy_id, v.vote_date DESC, f.gaceta_vote_id DESC
        """,
        # Must mirror the exporter's camara_votes join exactly. That query
        # INNER JOINs the tallies, so a vote with no summary row is absent from
        # the ordering -- and history entries sort by position in this list.
        "vote_order": """
            SELECT v.gaceta_vote_id AS id
            FROM dim_gaceta_vote v
            JOIN (SELECT DISTINCT gaceta_vote_id FROM fact_gaceta_vote_summary)
                 USING (gaceta_vote_id)
            WHERE v.legislature = 66
            ORDER BY v.vote_date DESC, v.gaceta_vote_id DESC
        """,
        "default_party": "SG",
    },
    "SEN": {
        "seats": """
            SELECT
                senador_seat_id AS id,
                CAST(senador_id AS TEXT) AS electedPersonId,
                display_name AS electedName,
                ine_candidate_name AS titularName,
                ine_substitute_name AS substituteName,
                party_key AS electedParty,
                source_name_role AS electedNameRole
            FROM dim_senadores
            WHERE legislature = 66
            ORDER BY party_key, display_name
        """,
        "voters": """
            SELECT DISTINCT CAST(f.senador_id AS TEXT) AS personId
            FROM fact_senador_vote f
            JOIN dim_senado_vote v USING (votacion_id)
            WHERE v.legislature = 66
        """,
        "former": """
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
        "vote_rows": """
            SELECT
                CAST(f.senador_id AS TEXT) AS personId,
                CAST(f.votacion_id AS TEXT) AS voteId,
                f.voto AS choice
            FROM fact_senador_vote f
            JOIN dim_senado_vote v USING (votacion_id)
            WHERE v.legislature = 66
            ORDER BY f.senador_id, v.vote_date DESC, f.votacion_id DESC
        """,
        # Mirrors senado_votes' INNER JOIN on the per-senator tally, for the
        # same reason as the Camara above.
        "vote_order": """
            SELECT CAST(v.votacion_id AS TEXT) AS id
            FROM dim_senado_vote v
            JOIN (SELECT DISTINCT votacion_id FROM fact_senador_vote)
                 USING (votacion_id)
            WHERE v.legislature = 66
            ORDER BY v.vote_date DESC, v.votacion_id DESC
        """,
        "default_party": "SG",
    },
}


SCHEMA = """
CREATE TABLE IF NOT EXISTS fact_legislature_66_seat_resolved (
    chamber              TEXT NOT NULL CHECK (chamber IN ('DIP', 'SEN')),
    seat_id              TEXT NOT NULL,
    elected_person_id    TEXT,
    elected_party        TEXT NOT NULL,
    titular_name         TEXT,
    substitute_name      TEXT,
    current_person_id    TEXT,
    current_name         TEXT,
    current_party        TEXT NOT NULL,
    current_status       TEXT NOT NULL,
    roster_observed_at   TEXT,
    roster_source_url    TEXT,
    created_at           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (chamber, seat_id)
);

CREATE TABLE IF NOT EXISTS fact_legislature_66_seat_member (
    chamber                 TEXT NOT NULL CHECK (chamber IN ('DIP', 'SEN')),
    seat_id                 TEXT NOT NULL,
    member_index            INTEGER NOT NULL,
    person_id               TEXT NOT NULL,
    person_name             TEXT NOT NULL,
    party_key               TEXT NOT NULL,
    seat_role               TEXT NOT NULL CHECK (seat_role IN ('titular', 'suplente')),
    is_current_occupant     INTEGER NOT NULL CHECK (is_current_occupant IN (0, 1)),
    vote_count              INTEGER NOT NULL,
    relationship_source_url TEXT,
    created_at              TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (chamber, seat_id, person_id)
);

CREATE INDEX IF NOT EXISTS idx_l66_seat_member_person
    ON fact_legislature_66_seat_member(person_id);

CREATE TABLE IF NOT EXISTS fact_legislature_66_former_member (
    chamber                 TEXT NOT NULL CHECK (chamber IN ('DIP', 'SEN')),
    person_id               TEXT NOT NULL,
    person_name             TEXT NOT NULL,
    party_key               TEXT NOT NULL,
    seat_id                 TEXT,
    seat_role               TEXT,
    relationship_source_url TEXT,
    created_at              TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (chamber, person_id)
);

CREATE TABLE IF NOT EXISTS fact_legislature_66_person_alias (
    chamber             TEXT NOT NULL CHECK (chamber IN ('DIP', 'SEN')),
    person_id           TEXT NOT NULL,
    canonical_person_id TEXT NOT NULL,
    created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (chamber, person_id)
);

CREATE TABLE IF NOT EXISTS fact_legislature_66_person (
    chamber      TEXT NOT NULL CHECK (chamber IN ('DIP', 'SEN')),
    person_id    TEXT NOT NULL,
    display_name TEXT NOT NULL,
    name_source  TEXT NOT NULL CHECK (name_source IN ('seat_table', 'roll_call')),
    created_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (chamber, person_id)
);

CREATE TABLE IF NOT EXISTS fact_legislature_66_seat_vote_conflict (
    chamber             TEXT NOT NULL CHECK (chamber IN ('DIP', 'SEN')),
    seat_id             TEXT NOT NULL,
    vote_id             TEXT NOT NULL,
    counted_person_id   TEXT NOT NULL,
    reported_person_ids TEXT NOT NULL,
    created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (chamber, seat_id, vote_id)
);
"""


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
                currentName=seat["titularName"],
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


def add_registered_seat_names(seats: list[dict]) -> None:
    """Keep the constitutional seat label separate from its voting identity.

    ``electedName`` is the identity that successfully bridges to roll calls and
    can therefore be the registered suplente.  The public seat label must stay
    anchored to INE's titular whenever that name is available.
    """
    for seat in seats:
        seat["titularName"] = (
            display_person_name(seat.get("titularName")) or seat["electedName"]
        )
        seat["substituteName"] = (
            display_person_name(seat.get("substituteName")) or None
        )


def load_substitutes(conn: sqlite3.Connection, chamber: str) -> dict[str, str]:
    """Seat -> the suplente the INE registered for it, by seat id."""
    table, key = (
        ("dim_diputados", "diputado_id")
        if chamber == "DIP"
        else ("dim_senadores", "senador_seat_id")
    )
    return {
        row["seatId"]: row["name"]
        for row in rows(
            conn,
            f"""
            SELECT {key} AS seatId, ine_substitute_name AS name
            FROM {table}
            WHERE legislature = 66 AND ine_substitute_name IS NOT NULL
            """,
        )
    }


def name_tokens(raw: str | None) -> list[str]:
    """Comparable tokens for one person, order and orthography discarded.

    Three registers spell the same human three ways: the Senado roll call writes
    `Sen. Macías Rábago, Julieta`, the Camara roll call writes
    `PEREZ JAEN ZERMEÑO M. ELENA`, and the INE substitute column writes
    `MARIA ELENA PEREZ-JAEN ZERMEÑO`. Sorting the tokens drops the surname-order
    question entirely; folding `y` to `i` absorbs the one spelling drift that
    survives it (Maribel/Marybel), which is otherwise a whole missed linkage.
    """
    name = str(raw or "").strip()
    if name.lower().startswith(("sen.", "dip.")):
        name = name[4:].strip()
    if "," in name:
        surnames, _, given = name.partition(",")
        name = f"{given.strip()} {surnames.strip()}"
    name = unicodedata.normalize("NFKD", name)
    name = "".join(char for char in name if not unicodedata.combining(char))
    name = re.sub(r"[^a-z ]", " ", name.lower()).replace("y", "i")
    return sorted(token for token in name.split() if token)


def covers_name(voter: list[str], registered: list[str]) -> bool:
    """True when every roll-call token finds a partner in the registered name.

    A lone initial stands in for a full given name, and given names the roll
    call dropped are tolerated on the registry side — both are routine in these
    two sources. Requiring two full-length agreements stops that tolerance from
    matching on a shared surname alone.
    """
    pool = list(registered)
    long_hits = 0
    for token in sorted(voter, key=len, reverse=True):
        if len(token) == 1:
            partner = next((other for other in pool if other.startswith(token)), None)
        elif token in pool:
            partner = token
        else:
            partner = next(
                (other for other in pool if len(other) == 1 and token.startswith(other)),
                None,
            )
        if partner is None:
            return False
        pool.remove(partner)
        if len(token) >= 3 and len(partner) >= 3:
            long_hits += 1
    return long_hits >= 2


def link_former_members(
    chamber: str,
    former_members: list[dict],
    seats: list[dict],
    substitutes: dict[str, str],
) -> None:
    """Attach the seat each former member covered, through the INE suplente register.

    Roll-call rows carry no geography — `dim_gaceta_deputy` is an id and a name —
    so the suplente registered per seat is the only route from a voting record
    back to a place in the hemicycle. Without it these members are unplaceable
    and the chamber can only go dark behind them.

    Exact token match first, then the initial-aware fallback, and either only
    counts when it names a single seat in the chamber: an ambiguous name stays
    unlinked rather than being guessed onto a bench.

    `seatRole` separates two groups the seat alone cannot tell apart. Most are
    suplentes who served a licencia and left when the titular came back. A few
    are the person *currently* in the seat, filed by the roll call under a second
    identity the directory never linked — for those the hemicycle must not go
    dark, because they are sitting in it.
    """
    tokens_by_seat = {
        seat_id: name_tokens(name) for seat_id, name in substitutes.items()
    }
    exact: dict[str, list[str]] = {}
    for seat_id, tokens in tokens_by_seat.items():
        exact.setdefault(" ".join(tokens), []).append(seat_id)
    seats_by_id = {seat["id"]: seat for seat in seats}

    linked = 0
    for member in former_members:
        override = AUDITED_FORMER_SEAT_OVERRIDES.get(
            (chamber, str(member["personId"]))
        )
        if override is not None:
            member["seatId"] = override["seatId"]
            member["seatRole"] = "suplencia_concluida"
            member["relationshipSourceUrl"] = override["sourceUrl"]
            linked += 1
            continue
        tokens = name_tokens(member["name"])
        candidates = exact.get(" ".join(tokens), [])
        if len(candidates) != 1:
            candidates = [
                seat_id
                for seat_id, registered in tokens_by_seat.items()
                if covers_name(tokens, registered)
            ]
        if len(candidates) != 1:
            member["seatId"] = None
            member["seatRole"] = None
            member["relationshipSourceUrl"] = None
            continue
        seat = seats_by_id[candidates[0]]
        member["seatId"] = seat["id"]
        member["seatRole"] = (
            "en_funciones"
            if covers_name(tokens, name_tokens(seat["currentName"]))
            else "suplencia_concluida"
        )
        member["relationshipSourceUrl"] = INE_INTEGRATION_PUBLIC_URL
        linked += 1
    print(f"  linked {linked}/{len(former_members)} former members to a seat")


def reconcile_person_aliases(
    chamber: str,
    seats: list[dict],
    former_members: list[dict],
) -> dict[str, str]:
    """Collapse source-string aliases while preserving real substitute people.

    A current occupant occasionally has a second roll-call identity, and an
    interim substitute can be printed once in abbreviated form and once in
    full. Both cases should produce one person profile and one continuous vote
    history. This function only merges identities already tied to the same seat,
    plus the two audited Chamber aliases above; it never merges a titular and a
    genuine substitute merely because they share a seat.
    """
    aliases = dict(AUDITED_PERSON_ALIASES[chamber])
    seats_by_id = {seat["id"]: seat for seat in seats}

    # A roll-call identity that matches the directory's current occupant is an
    # alias, not an additional substitute. If the directory has no vote id, the
    # roll-call id becomes its canonical identity.
    for member in former_members:
        if member.get("seatRole") != "en_funciones" or not member.get("seatId"):
            continue
        seat = seats_by_id[member["seatId"]]
        current_id = seat.get("currentPersonId")
        if current_id:
            aliases[str(member["personId"])] = str(current_id)
        else:
            seat["currentPersonId"] = str(member["personId"])

    # The same concluded substitute can also arrive under two spellings. Only
    # compare records already resolved to the same seat and role.
    concluded = [
        member
        for member in former_members
        if member.get("seatId") and member.get("seatRole") == "suplencia_concluida"
    ]
    for index, left in enumerate(concluded):
        for right in concluded[index + 1 :]:
            if left["seatId"] != right["seatId"]:
                continue
            left_tokens = name_tokens(left["name"])
            right_tokens = name_tokens(right["name"])
            if not (
                covers_name(left_tokens, right_tokens)
                or covers_name(right_tokens, left_tokens)
            ):
                continue
            # Prefer the fuller, human-cased display string as the canonical id.
            quality = lambda row: (
                not str(row["name"]).isupper(),
                len(name_tokens(row["name"])),
                len(str(row["name"])),
            )
            canonical, alternate = sorted((left, right), key=quality, reverse=True)
            aliases[str(alternate["personId"])] = str(canonical["personId"])

    def canonical(person_id: object) -> str:
        value = str(person_id)
        seen: set[str] = set()
        while value in aliases and value not in seen:
            seen.add(value)
            value = aliases[value]
        return value

    # Flatten any chains before the mapping is serialized or applied.
    aliases = {source: canonical(target) for source, target in aliases.items()}
    for seat in seats:
        for field in ("electedPersonId", "currentPersonId"):
            if seat.get(field):
                seat[field] = canonical(seat[field])

    canonical_ids = {
        str(seat[field])
        for seat in seats
        for field in ("electedPersonId", "currentPersonId")
        if seat.get(field)
    }
    former_members[:] = [
        member
        for member in former_members
        if str(member["personId"]) not in aliases
        and str(member["personId"]) not in canonical_ids
    ]
    return aliases


def canonical_histories(
    people: set[str],
    vote_rows: list[dict],
    aliases: dict[str, str],
    votes: list[dict],
) -> dict[str, list[list[str]]]:
    """Merge audited source identities without losing the raw attribution.

    `people` is a set, so it is seeded in sorted order: iterating it directly
    let Python's hash randomization reorder the output dict on every run, which
    made a 5.9 MB payload register as changed when nothing about the data had.
    The values were never affected, only the key order.
    """
    histories: dict[str, dict[str, str]] = {
        aliases.get(str(person), str(person)): {} for person in sorted(people)
    }
    for row in vote_rows:
        person_id = aliases.get(str(row["personId"]), str(row["personId"]))
        prior = histories.setdefault(person_id, {}).get(str(row["voteId"]))
        if prior is not None and prior != row["choice"]:
            raise ValueError(
                f"Conflicting alias votes for {person_id} on {row['voteId']}: "
                f"{prior} vs {row['choice']}"
            )
        histories[person_id][str(row["voteId"])] = row["choice"]
    order = {str(vote["id"]): index for index, vote in enumerate(votes)}
    return {
        person_id: [
            [vote_id, choice]
            for vote_id, choice in sorted(
                choices.items(), key=lambda item: order.get(item[0], len(order))
            )
        ]
        for person_id, choices in histories.items()
    }


def build_seat_vote_data(
    seats: list[dict],
    former_members: list[dict],
    histories: dict[str, list[list[str]]],
    votes: list[dict],
    roster_source_url: str,
) -> tuple[dict[str, list[list[str]]], dict[str, list[dict]], list[dict]]:
    """Build a seat history while retaining who actually cast every vote."""
    person_to_seat: dict[str, str] = {}
    seat_members: dict[str, list[dict]] = {seat["id"]: [] for seat in seats}

    def add_member(
        seat_id: str,
        person_id: object,
        name: str,
        party: str,
        role: str,
        source_url: str | None,
    ) -> None:
        if not person_id:
            return
        person = str(person_id)
        previous = person_to_seat.get(person)
        if previous and previous != seat_id:
            raise ValueError(f"Person {person} resolves to both {previous} and {seat_id}")
        person_to_seat[person] = seat_id
        existing = next(
            (member for member in seat_members[seat_id] if member["personId"] == person),
            None,
        )
        if existing:
            return
        seat_members[seat_id].append(
            {
                "personId": person,
                "name": name,
                "party": party,
                "role": role,
                "sourceUrl": source_url,
                "voteCount": len(histories.get(person, [])),
            }
        )

    for seat in seats:
        add_member(
            seat["id"], seat.get("electedPersonId"), seat["electedName"],
            seat["electedParty"], seat["electedNameRole"], INE_INTEGRATION_PUBLIC_URL,
        )
        if seat.get("currentPersonId") != seat.get("electedPersonId"):
            add_member(
                seat["id"], seat.get("currentPersonId"), seat.get("currentName") or "Vacante",
                seat["currentParty"], "suplente", roster_source_url,
            )
    for member in former_members:
        if member.get("seatId"):
            add_member(
                member["seatId"], member["personId"], member["name"], member["party"],
                "suplente", member.get("relationshipSourceUrl"),
            )

    order = {str(vote["id"]): index for index, vote in enumerate(votes)}
    role_by_person = {
        member["personId"]: member["role"]
        for members in seat_members.values()
        for member in members
    }
    member_index = {
        (seat_id, member["personId"]): index
        for seat_id, members in seat_members.items()
        for index, member in enumerate(members)
    }
    seat_histories: dict[str, list[list[str]]] = {seat["id"]: [] for seat in seats}
    candidates: dict[tuple[str, str], list[list[str]]] = {}
    for person_id, history in histories.items():
        seat_id = person_to_seat.get(person_id)
        if not seat_id:
            continue
        for vote_id, choice in history:
            candidates.setdefault((seat_id, vote_id), []).append(
                [vote_id, choice, person_id, role_by_person[person_id]]
            )
    conflicts: list[dict] = []
    for (seat_id, vote_id), entries in candidates.items():
        if len(entries) == 1:
            entry = entries[0]
            seat_histories[seat_id].append(
                [entry[0], entry[1], member_index[(seat_id, entry[2])]]
            )
            continue
        # A constitutional seat cannot cast two votes. Preserve every raw vote
        # in the person histories, but count the titular once in the combined
        # seat history and publish the collision for audit instead of inflating
        # the hemicycle. This currently catches one official Senado page that
        # lists both María Guadalupe Murguía and her suplente Sonia Rocha.
        titular = [entry for entry in entries if entry[3] == "titular"]
        chosen = titular[0] if len(titular) == 1 else entries[0]
        seat_histories[seat_id].append(
            [chosen[0], chosen[1], member_index[(seat_id, chosen[2])]]
        )
        conflicts.append(
            {
                "seatId": seat_id,
                "voteId": vote_id,
                "countedPersonId": chosen[2],
                "reportedPersonIds": [entry[2] for entry in entries],
            }
        )
    for history in seat_histories.values():
        history.sort(key=lambda row: order.get(str(row[0]), len(order)))
    return seat_histories, seat_members, conflicts


def load_chamber_vote_rows(conn: sqlite3.Connection, chamber: str) -> list[dict]:
    """Roll-call rows for one chamber, in the Camara's vote vocabulary.

    The Senado records PRO/CONTRA/ABSTENCIÓN/AUSENTE and the Camara records
    Favor/Contra/Abstención/Ausente. Translating here means a merged history
    reads the same either side of the building. A Senado choice outside the map
    is dropped rather than guessed at.
    """
    vote_rows = rows(conn, CHAMBERS[chamber]["vote_rows"])
    if chamber != "SEN":
        return vote_rows
    translated = []
    for row in vote_rows:
        choice = SENATE_CHOICE.get(row["choice"])
        if choice:
            row["choice"] = choice
            translated.append(row)
    return translated


def person_histories(
    conn: sqlite3.Connection, chamber: str
) -> dict[str, list[list[str]]]:
    """Per-person LXVI vote history, with audited aliases already applied.

    Rebuilt from the fact table on demand rather than stored. The rows are
    already in `fact_gaceta_deputy_vote` / `fact_senador_vote`; the only thing
    resolution adds is the alias substitution, and that mapping is three orders
    of magnitude smaller than the histories it rewrites.

    Seeded from the seats *and* the voters so that a person who holds a seat but
    never cast a vote still appears, with an empty history rather than no entry.
    """
    aliases = {
        row["personId"]: row["canonicalPersonId"]
        for row in rows(
            conn,
            """
            SELECT person_id AS personId, canonical_person_id AS canonicalPersonId
            FROM fact_legislature_66_person_alias
            WHERE chamber = ?
            """,
            (chamber,),
        )
    }
    seated = {
        str(value)
        for row in rows(
            conn,
            """
            SELECT elected_person_id AS elected, current_person_id AS current
            FROM fact_legislature_66_seat_resolved
            WHERE chamber = ?
            """,
            (chamber,),
        )
        for value in (row["elected"], row["current"])
        if value
    }
    voters = {
        aliases.get(str(row["personId"]), str(row["personId"]))
        for row in rows(conn, CHAMBERS[chamber]["voters"])
    }
    votes = rows(conn, CHAMBERS[chamber]["vote_order"])
    return canonical_histories(
        seated | voters, load_chamber_vote_rows(conn, chamber), aliases, votes
    )


def senator_display_name(raw: str | None) -> str:
    """`Sen. Martin del Campo Martin del Campo, Juan Antonio` -> given-name first.

    The Senado roll call prints an honorific and puts surnames before a comma;
    everywhere else a person reads as `Nombre Apellido`. Only the comma is
    trusted to split the name -- the deputy roll call has no comma, and guessing
    where its surnames end would rename people.
    """
    name = str(raw or "").strip()
    if name.lower().startswith("sen."):
        name = name[4:].strip()
    if "," in name:
        surnames, _, given = name.partition(",")
        name = f"{given.strip()} {surnames.strip()}"
    return display_person_name(name)


def resolve_display_names(conn: sqlite3.Connection, chamber: str) -> list[tuple]:
    """Preferred display name for everyone who cast an LXVI ballot.

    The seat tables win where they have an entry, so a legislator is named the
    same way in the hemicycle, the profile search and the per-square tooltips.
    Roll calls reach people the seat tables never held -- substitutes who served
    an interim -- so the chamber's own spelling is the fallback rather than
    dropping the name and leaving a square anonymous.

    `name_source` records which of the two won, so a disagreement between the
    directory and the roll call stays visible instead of being silently absorbed.
    """
    if chamber == "DIP":
        roll_call_sql = (
            "SELECT deputy_id AS personId, deputy_name AS name FROM dim_gaceta_deputy"
        )
        seat_sql = """
            SELECT gaceta_deputy_id AS personId, display_name AS name
            FROM dim_diputados
            WHERE legislature = 66 AND gaceta_deputy_id IS NOT NULL
        """
        format_name = display_person_name
    else:
        roll_call_sql = (
            "SELECT CAST(senador_id AS TEXT) AS personId, senador_name AS name"
            " FROM dim_senador"
        )
        seat_sql = """
            SELECT CAST(senador_id AS TEXT) AS personId, display_name AS name
            FROM dim_senadores
            WHERE legislature = 66
        """
        format_name = senator_display_name

    names = {
        str(row["personId"]): (format_name(row["name"]), "roll_call")
        for row in rows(conn, roll_call_sql)
    }
    names.update(
        {
            str(row["personId"]): (row["name"], "seat_table")
            for row in rows(conn, seat_sql)
        }
    )
    return [
        (chamber, person_id, name, source)
        for person_id, (name, source) in names.items()
        if name
    ]


def resolve_chamber(conn: sqlite3.Connection, chamber: str) -> dict:
    """Everything derived about who occupies which LXVI seat in one chamber.

    The order of these steps is load-bearing. Occupancy has to be applied before
    former members can be identified (a "former" member is precisely one that
    neither seat snapshot names), and aliases have to be reconciled before vote
    histories are merged, or one person's record arrives split in two.
    """
    config = CHAMBERS[chamber]

    seats = rows(conn, config["seats"])
    roster, roster_meta = load_current_roster(conn, chamber)
    add_registered_seat_names(seats)
    apply_roster(seats, roster)

    linked_people = (
        {seat["electedPersonId"] for seat in seats if seat["electedPersonId"]}
        | {seat["currentPersonId"] for seat in seats if seat["currentPersonId"]}
    )
    voter_people = {row["personId"] for row in rows(conn, config["voters"])}

    # A roll-call identity can outlive both seat snapshots available here: the
    # elected result and today's directory. Keep those interim members so the
    # record stays a full LXVI archive rather than a current-roster browser.
    former_members = []
    for member in rows(conn, config["former"]):
        if member["personId"] not in linked_people:
            member["party"] = canonical_party(
                member["party"] or config["default_party"]
            )
            former_members.append(member)

    link_former_members(chamber, former_members, seats, load_substitutes(conn, chamber))
    aliases = reconcile_person_aliases(chamber, seats, former_members)

    people = {
        aliases.get(str(person), str(person))
        for person in (linked_people | voter_people)
    }

    vote_rows = load_chamber_vote_rows(conn, chamber)

    votes = rows(conn, config["vote_order"])
    histories = canonical_histories(people, vote_rows, aliases, votes)
    _, seat_members, conflicts = build_seat_vote_data(
        seats, former_members, histories, votes, roster_meta["sourceUrl"]
    )

    return {
        "chamber": chamber,
        "seats": seats,
        "formerMembers": former_members,
        "aliases": aliases,
        "seatMembers": seat_members,
        "conflicts": conflicts,
        "displayNames": resolve_display_names(conn, chamber),
        "roster": roster_meta,
    }


def write_chamber(conn: sqlite3.Connection, resolved: dict) -> dict[str, int]:
    """Replace one chamber's resolution tables in a single transaction.

    Scoped deletes rather than a bare DELETE: the two chambers are resolved in
    separate passes, and a full wipe would leave the other one missing for as
    long as this run took.
    """
    chamber = resolved["chamber"]
    roster = resolved["roster"]
    seats = resolved["seats"]

    current_by_seat = {
        str(seat["id"]): (
            str(seat["currentPersonId"]) if seat.get("currentPersonId") else None
        )
        for seat in seats
    }

    seat_records = [
        (
            chamber,
            str(seat["id"]),
            str(seat["electedPersonId"]) if seat.get("electedPersonId") else None,
            seat["electedParty"],
            seat.get("titularName"),
            seat.get("substituteName"),
            str(seat["currentPersonId"]) if seat.get("currentPersonId") else None,
            seat.get("currentName"),
            seat["currentParty"],
            seat["currentStatus"],
            roster.get("observedAt"),
            roster.get("sourceUrl"),
        )
        for seat in seats
    ]

    member_records = [
        (
            chamber,
            seat_id,
            index,
            str(member["personId"]),
            member["name"],
            member["party"],
            member["role"],
            int(current_by_seat.get(seat_id) == str(member["personId"])),
            member["voteCount"],
            member.get("sourceUrl"),
        )
        for seat_id, members in resolved["seatMembers"].items()
        for index, member in enumerate(members)
    ]

    former_records = [
        (
            chamber,
            str(member["personId"]),
            member["name"],
            member["party"],
            member.get("seatId"),
            member.get("seatRole"),
            member.get("relationshipSourceUrl"),
        )
        for member in resolved["formerMembers"]
    ]

    alias_records = [
        (chamber, str(source), str(target))
        for source, target in resolved["aliases"].items()
    ]

    conflict_records = [
        (
            chamber,
            conflict["seatId"],
            str(conflict["voteId"]),
            str(conflict["countedPersonId"]),
            ",".join(str(person) for person in conflict["reportedPersonIds"]),
        )
        for conflict in resolved["conflicts"]
    ]

    conn.executescript(SCHEMA)
    with conn:
        for table in (
            "fact_legislature_66_seat_resolved",
            "fact_legislature_66_seat_member",
            "fact_legislature_66_former_member",
            "fact_legislature_66_person_alias",
            "fact_legislature_66_seat_vote_conflict",
        ):
            conn.execute(f"DELETE FROM {table} WHERE chamber = ?", (chamber,))

        conn.executemany(
            """
            INSERT INTO fact_legislature_66_seat_resolved (
                chamber, seat_id, elected_person_id, elected_party, titular_name,
                substitute_name, current_person_id, current_name, current_party,
                current_status, roster_observed_at, roster_source_url
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            seat_records,
        )
        conn.executemany(
            """
            INSERT INTO fact_legislature_66_seat_member (
                chamber, seat_id, member_index, person_id, person_name, party_key,
                seat_role, is_current_occupant, vote_count, relationship_source_url
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            member_records,
        )
        conn.executemany(
            """
            INSERT INTO fact_legislature_66_former_member (
                chamber, person_id, person_name, party_key, seat_id, seat_role,
                relationship_source_url
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            former_records,
        )
        conn.executemany(
            """
            INSERT INTO fact_legislature_66_person_alias (
                chamber, person_id, canonical_person_id
            ) VALUES (?, ?, ?)
            """,
            alias_records,
        )
        conn.execute(
            "DELETE FROM fact_legislature_66_person WHERE chamber = ?", (chamber,)
        )
        conn.executemany(
            """
            INSERT INTO fact_legislature_66_person (
                chamber, person_id, display_name, name_source
            ) VALUES (?, ?, ?, ?)
            """,
            resolved["displayNames"],
        )
        conn.executemany(
            """
            INSERT INTO fact_legislature_66_seat_vote_conflict (
                chamber, seat_id, vote_id, counted_person_id, reported_person_ids
            ) VALUES (?, ?, ?, ?, ?)
            """,
            conflict_records,
        )

    return {
        "seats": len(seat_records),
        "members": len(member_records),
        "former": len(former_records),
        "aliases": len(alias_records),
        "conflicts": len(conflict_records),
        "names": len(resolved["displayNames"]),
    }


def materialize(db_path: Path = DB_PATH) -> None:
    with sqlite3.connect(db_path) as conn:
        for chamber in CHAMBERS:
            resolved = resolve_chamber(conn, chamber)
            counts = write_chamber(conn, resolved)
            print(
                f"  {chamber}: {counts['seats']} seats, {counts['members']} members, "
                f"{counts['former']} former, {counts['aliases']} aliases, "
                f"{counts['conflicts']} conflicts, {counts['names']} names"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DB_PATH))
    args = parser.parse_args()
    materialize(Path(args.db))


if __name__ == "__main__":
    main()
