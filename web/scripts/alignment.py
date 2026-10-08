"""Party and bloc alignment for the chamber explorers (alignment-66.json).

A Python port of camara_de_diputados/votos/legislator_party_agreement.R, which
is where the definitions are argued. The two must produce the same numbers; if
one changes, change the other.

    position   a group's most common choice among Favor, Contra and Abstención
               on one roll call. Absences are not votes; a tie or an empty
               group leaves it without one.
    agreement  the share of a legislator's cast votes that matched a group's
               position, over the roll calls where the group had one. A member
               is taken out of their own group's count first.
    blocs      average-linkage clustering of the major parties on
               1 - pairwise agreement, cut where the mean silhouette is best.
    friction   a roll call where the blocs' majorities differed.
    seated     agreement over every roll call the legislator was seated for:
               a counted absence never matches. Every Cámara absence counts;
               in the Senado only the reconstructed "Sin registro" does (its
               recorded "Ausente" is always an excused official commission).

Each legislator carries one score set per mode (`MODES`): every roll call or
friction only, crossed with votes cast only or absences counted.

Identities are the site's own: the Cámara's audited aliases are applied, so
every legislator id here is a key of that chamber's `histories`.

Scores are published twice: per legislator, and per seat. A seat adds up the
votes of everyone who held it (titular and suplentes), grouped by the
explorer's own `seatMembers`, so a suplente who covered a short licencia
becomes part of a seat's record instead of an outlier of their own.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from itertools import combinations
from pathlib import Path

from lib.canonical import canonical_party

LEGISLATURE = 66
# A bench needs this many distinct legislators to count as a party here.
MIN_PARTY_MEMBERS = 5
# Labels for having no bench; they never form or join a bloc.
NON_PARTIES = {"IND", "SG", "SP"}
# Legislators with fewer votes cast are left off the chart: a suplente with a
# handful of votes sits at 0% or 100% and would own the extremes.
MIN_VOTES = 20

FAVOR, CONTRA, ABSTENCION = 0, 1, 2
# Not a vote, and counted against agreement in the seated modes.
ABSENT = -1
# Not a vote, and not counted at all: an excused absence, or present without voting.
NOT_COUNTED = None
CAMARA_CHOICE = {
    "Favor": FAVOR, "A favor": FAVOR,
    "Contra": CONTRA, "En contra": CONTRA,
    "Abstención": ABSTENCION, "Abstencion": ABSTENCION,
    "Ausente": ABSENT, "Quórum *": NOT_COUNTED,
}
SENADO_CHOICE = {"PRO": FAVOR, "CONTRA": CONTRA, "ABSTENCIÓN": ABSTENCION, "AUSENTE": NOT_COUNTED}
MODES = ("all-cast", "contested-cast", "all-seated", "contested-seated")

# Same reconciliation rule as the R script and gaceta_party_correlations.R:
# keep a roll call only when its official party totals match its deputy rows.
CAMARA_SQL = """
    WITH summary_party_totals AS (
        SELECT gaceta_vote_id, SUM(count) AS summary_party_total
        FROM fact_gaceta_vote_summary
        WHERE vote_choice = 'Total' AND party_key <> 'Total'
        GROUP BY gaceta_vote_id
    ), detail_totals AS (
        SELECT gaceta_vote_id, COUNT(*) AS detail_rows
        FROM fact_gaceta_deputy_vote
        GROUP BY gaceta_vote_id
    )
    SELECT f.gaceta_vote_id, v.vote_date, f.deputy_id, d.deputy_name, f.party_key, f.vote_choice
    FROM fact_gaceta_deputy_vote AS f
    JOIN dim_gaceta_vote AS v ON v.gaceta_vote_id = f.gaceta_vote_id
    JOIN dim_gaceta_deputy AS d ON d.deputy_id = f.deputy_id
    JOIN summary_party_totals AS s ON s.gaceta_vote_id = f.gaceta_vote_id
    JOIN detail_totals AS t ON t.gaceta_vote_id = f.gaceta_vote_id
    WHERE v.legislature = ? AND s.summary_party_total = t.detail_rows
"""

SENADO_SQL = """
    SELECT
        CAST(f.votacion_id AS TEXT), v.vote_date, CAST(f.senador_id AS TEXT),
        s.senador_name, COALESCE(NULLIF(TRIM(f.grupo_parlamentario), ''), 'SG'), f.voto
    FROM fact_senador_vote AS f
    JOIN dim_senado_vote AS v ON v.votacion_id = f.votacion_id
    LEFT JOIN dim_senador AS s ON s.senador_id = f.senador_id
    WHERE v.legislature = ? AND f.voto IS NOT NULL
"""


def load_records(conn: sqlite3.Connection, chamber: str) -> list[tuple]:
    """(vote_id, date, person_id, raw_name, party, choice) for one chamber.

    `choice` is FAVOR / CONTRA / ABSTENCION, ABSENT or NOT_COUNTED. The Senado
    gets its unrecorded absences added (see `senado_unrecorded`).
    """
    sql, choices, code = (
        (CAMARA_SQL, CAMARA_CHOICE, "DIP") if chamber == "diputados" else (SENADO_SQL, SENADO_CHOICE, "SEN")
    )
    aliases = dict(conn.execute(
        "SELECT person_id, canonical_person_id FROM fact_legislature_66_person_alias WHERE chamber = ?",
        (code,),
    ).fetchall())
    records = []
    for vote_id, date, person_id, raw_name, party, choice in conn.execute(sql, (LEGISLATURE,)):
        if choice not in choices:
            raise ValueError(f"Unmapped {chamber} vote choice {choice!r}")
        records.append((
            vote_id,
            date or "",
            aliases.get(person_id, person_id),
            (raw_name or "").removeprefix("Sen. "),
            canonical_party(party),
            choices[choice],
        ))
    if chamber == "senado":
        records += senado_unrecorded(conn, records)
    return records


def senado_unrecorded(conn: sqlite3.Connection, records: list[tuple]) -> list[tuple]:
    """Absences the Senado leaves out of its roll calls, as ABSENT records.

    The R script's "occupant" rule (SENATE_GAP_RULE): a roll call strictly
    between a senator's own first and last recorded vote, when no member of
    their seat voted in it, and only for the member who last voted from that
    seat before it. The website's history fill (fill_no_registro_gaps) skips
    that last condition and charges a suplente for the titular's absences.
    """
    order = {
        vote_id: index
        for index, (_, vote_id) in enumerate(sorted({(r[1], r[0]) for r in records}, key=lambda t: (t[0], int(t[1]))))
    }
    dates = {r[0]: r[1] for r in records}
    by_position = {index: vote_id for vote_id, index in order.items()}
    seat_of = dict(conn.execute(
        "SELECT person_id, seat_id FROM fact_legislature_66_seat_member WHERE chamber = 'SEN'"
    ).fetchall())

    held: dict[str, dict[int, tuple[str, str]]] = defaultdict(dict)  # person -> position -> (party, name)
    seat_rows: dict[str, dict[int, set[str]]] = defaultdict(lambda: defaultdict(set))  # seat -> position -> people
    for vote_id, _, person, raw_name, party, _ in records:
        held[person][order[vote_id]] = (party, raw_name)
        if person in seat_of:
            seat_rows[seat_of[person]][order[vote_id]].add(person)
    seat_positions = {seat: sorted(rows) for seat, rows in seat_rows.items()}

    added = []
    for person, rows in held.items():
        positions = sorted(rows)
        seat = seat_of.get(person)
        for position in range(positions[0] + 1, positions[-1]):
            if position in rows:
                continue
            if seat:
                if position in seat_rows[seat]:
                    continue
                before = [p for p in seat_positions[seat] if p < position]
                if person not in seat_rows[seat][before[-1]]:
                    continue
            previous = max(p for p in positions if p < position)
            party, raw_name = rows[previous]
            vote_id = by_position[position]
            added.append((vote_id, dates[vote_id], person, raw_name, party, ABSENT))
    return added


def majority(counts: list[int]) -> int | None:
    top = max(counts)
    if top == 0 or counts.count(top) > 1:
        return None
    return counts.index(top)


def without(counts: list[int], choice: int) -> list[int]:
    reduced = list(counts)
    reduced[choice] -= 1
    return reduced


def average_linkage(parties: list[str], distance: dict[tuple[str, str], float]) -> list[list[list[str]]]:
    """Agglomerative clustering, average linkage (R's hclust "average").

    Returns `partitions[k]`, the cut into k clusters, for every k; each
    cluster lists its parties in the tree's leaf order.
    """
    clusters = [[party] for party in parties]
    partitions = {len(clusters): [list(c) for c in clusters]}

    def linkage(a: list[str], b: list[str]) -> float:
        return sum(distance[x, y] for x in a for y in b) / (len(a) * len(b))

    while len(clusters) > 1:
        i, j = min(
            combinations(range(len(clusters)), 2),
            key=lambda pair: linkage(clusters[pair[0]], clusters[pair[1]]),
        )
        merged = clusters[i] + clusters[j]
        clusters = [c for k, c in enumerate(clusters) if k not in (i, j)] + [merged]
        partitions[len(clusters)] = [list(c) for c in clusters]
    return partitions


def mean_silhouette(partition: list[list[str]], distance: dict[tuple[str, str], float]) -> float:
    """cluster::silhouette's definition; a singleton scores 0."""
    scores = []
    for cluster in partition:
        for party in cluster:
            if len(cluster) == 1:
                scores.append(0.0)
                continue
            a = sum(distance[party, other] for other in cluster if other != party) / (len(cluster) - 1)
            b = min(
                sum(distance[party, other] for other in rival) / len(rival)
                for rival in partition if rival is not cluster
            )
            scores.append((b - a) / max(a, b) if max(a, b) > 0 else 0.0)
    return sum(scores) / len(scores)


def chamber_alignment(
    records: list[tuple], names: dict[str, str], seat_members: dict[str, list[dict]]
) -> dict:
    members: dict[str, set[str]] = defaultdict(set)
    latest: dict[str, tuple[str, str, str, str]] = {}
    counts: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0, 0]))
    for vote_id, date, person, raw_name, party, choice in records:
        members[party].add(person)
        if person not in latest or (date, vote_id) >= latest[person][:2]:
            latest[person] = (date, vote_id, party, raw_name)
        if choice is not None and choice >= 0:
            counts[vote_id][party][choice] += 1

    size = {party: len(people) for party, people in members.items()}
    majors = sorted(p for p, n in size.items() if n >= MIN_PARTY_MEMBERS and p not in NON_PARTIES)

    positions = {
        vote_id: {p: majority(by_party[p]) for p in majors if p in by_party}
        for vote_id, by_party in counts.items()
    }
    def pair_agreement(vote_ids) -> tuple[dict, dict]:
        agreement, roll_calls = {}, {}
        for a in majors:
            for b in majors:
                both = [
                    (positions[v][a], positions[v][b]) for v in vote_ids
                    if positions[v].get(a) is not None and positions[v].get(b) is not None
                ]
                agreement[a, b] = sum(x == y for x, y in both) / len(both) if both else 0.0
                roll_calls[a, b] = len(both)
        return agreement, roll_calls

    agreement, pair_roll_calls = pair_agreement(positions)

    distance = {pair: 1 - value for pair, value in agreement.items()}
    partitions = average_linkage(majors, distance)
    k = max(range(2, len(majors)), key=lambda n: (mean_silhouette(partitions[n], distance), -n))
    # Blocs numbered by size, members largest bench first.
    blocs = sorted(
        (sorted(cluster, key=lambda p: -size[p]) for cluster in partitions[k]),
        key=lambda cluster: -sum(size[p] for p in cluster),
    )
    bloc_of = {party: index for index, bloc in enumerate(blocs) for party in bloc}
    # Heatmap order: bloc by bloc, each bloc in its tree's leaf order.
    leaf_order = partitions[1][0]
    order = [p for bloc in blocs for p in sorted(bloc, key=leaf_order.index)]

    bloc_counts = {
        vote_id: [
            [sum(by_party[p][c] for p in bloc if p in by_party) for c in range(3)]
            for bloc in blocs
        ]
        for vote_id, by_party in counts.items()
    }
    # Friction: the blocs' majorities took different positions. A party
    # breaking from its own bloc while the blocs agree does not count.
    contested = {
        vote_id for vote_id, totals in bloc_counts.items()
        if len({majority(total) for total in totals} - {None}) > 1
    }
    agreement_contested, pair_roll_calls_contested = pair_agreement(contested)
    # tallies[person][mode][bloc index or "own"] = [agreed, counted]
    tallies: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: [0, 0])))
    cast: dict[str, int] = defaultdict(int)
    absences: dict[str, int] = defaultdict(int)

    def tally(person: str, vote_id: str, key, agreed: bool, is_cast: bool) -> None:
        modes = ["all-seated"] + (["all-cast"] if is_cast else [])
        if vote_id in contested:
            modes += ["contested-" + mode.split("-")[1] for mode in modes]
        for mode in modes:
            tallies[person][mode][key][0] += agreed
            tallies[person][mode][key][1] += 1

    for vote_id, _, person, _, party, choice in records:
        if choice is NOT_COUNTED or vote_id not in counts:
            continue
        is_cast = choice != ABSENT
        if is_cast:
            cast[person] += 1
        else:
            absences[person] += 1
        # A voter leaves their own group's count before its position is read;
        # an absentee was never in it.
        if party in majors:
            party_total = counts[vote_id][party]
            own = majority(without(party_total, choice) if is_cast else party_total)
            if own is not None:
                tally(person, vote_id, "own", is_cast and own == choice, is_cast)
        for index, bloc_total in enumerate(bloc_counts[vote_id]):
            member = is_cast and bloc_of.get(party) == index
            position = majority(without(bloc_total, choice) if member else bloc_total)
            if position is not None:
                tally(person, vote_id, index, is_cast and position == choice, is_cast)

    def share(counted: list[int]) -> float | None:
        return round(counted[0] / counted[1], 3) if counted[1] else None

    def scores(people: list[str]) -> dict[str, list[float | None]]:
        def summed(mode: str, key) -> list[int]:
            return [sum(tallies[p][mode][key][i] for p in people if p in tallies) for i in (0, 1)]
        return {
            mode: [share(summed(mode, index)) for index in range(len(blocs))] + [share(summed(mode, "own"))]
            for mode in MODES
        }

    seats = []
    for seat_id, seat_people in seat_members.items():
        people = [member["personId"] for member in seat_people]
        votes = sum(cast[p] for p in people)
        if votes < MIN_VOTES:
            continue
        # The seat reads as whoever cast its latest vote, under that bench.
        holder = max((p for p in people if p in latest), key=lambda p: latest[p][:2])
        seats.append({
            "id": seat_id,
            "holder": holder,
            "holderName": names.get(holder) or latest[holder][3],
            "party": latest[holder][2],
            "occupants": sum(1 for p in people if cast[p] > 0),
            "votes": votes,
            "absences": sum(absences[p] for p in people),
            "scores": scores(people),
        })

    legislators = []
    for person, votes in cast.items():
        if votes < MIN_VOTES:
            continue
        _, _, party, raw_name = latest[person]
        legislators.append({
            "id": person,
            "name": names.get(person) or raw_name,
            "party": party,
            "votes": votes,
            "absences": absences[person],
            # Per mode: agreement with each bloc in `blocs` order, then with
            # their own bench (null without one).
            "scores": scores([person]),
        })
    legislators.sort(key=lambda row: row["id"])

    return {
        "rollCalls": len(counts),
        "contestedRollCalls": len(contested),
        "parties": order,
        "blocs": [{"label": " + ".join(bloc), "parties": bloc} for bloc in blocs],
        "agreement": [[round(agreement[a, b], 4) for b in order] for a in order],
        "pairRollCalls": [[pair_roll_calls[a, b] for b in order] for a in order],
        "agreementContested": [[round(agreement_contested[a, b], 4) for b in order] for a in order],
        "pairRollCallsContested": [[pair_roll_calls_contested[a, b] for b in order] for a in order],
        "legislators": legislators,
        "seats": sorted(seats, key=lambda row: row["id"]),
    }


def export_alignment(
    conn: sqlite3.Connection,
    names: dict[str, dict[str, str]],
    seat_members: dict[str, dict[str, list[dict]]],
    path: Path,
) -> None:
    payload = {
        "manifest": {
            "schemaVersion": 3,
            "modes": list(MODES),
            "legislature": LEGISLATURE,
            "minVotes": MIN_VOTES,
            "minPartyMembers": MIN_PARTY_MEMBERS,
        },
        **{
            chamber: chamber_alignment(load_records(conn, chamber), names[chamber], seat_members[chamber])
            for chamber in ("diputados", "senado")
        },
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {path} ({path.stat().st_size / 1024:.0f} KB)")
