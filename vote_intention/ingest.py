"""
Load federal vote-intention polls into the warehouse.

Inputs, both one row per poll option with the same columns:

  * aux_scripts/vote_intention/vote_intention_polls.csv, written by
    aux_scripts/vote_intention/scrape_wikipedia_polls.py. Regenerated on every
    scrape, so never edit it by hand.
  * aux_scripts/vote_intention/vote_intention_polls_supplement.csv, polls
    Wikipedia never listed, entered by hand from the press. Each row carries
    its article in source_url and source = "prensa"; poll_ids use a "_sup_"
    infix so they cannot collide with scraped ones.

Populates in election_data.db:

    dim_vote_intention_pollster  one row per polling house (familia matches
                                 dim_approval_pollster.familia where they overlap)
    dim_vote_intention_source    one row per pinned source page revision
    fact_vote_intention_poll     one row per poll, keyed to dim_election
    fact_vote_intention_option   one row per option within a poll
    fact_vote_intention_result   election-day national share for every distinct
                                 party_keys set the options point at
    view_vote_intention_vs_result
                                 options next to the result they forecast

Results are summed from fact_casilla_vote, so they reflect exactly what the
warehouse holds. Two denominators are kept because polls report on both
bases: pct_total counts null ballots and unregistered candidates, pct_valid
leaves out null ballots (INE's "votación válida").

An election still ahead (DIP_MR_2027) has no dim_election row and no
returns. Its polls load all the same, keyed to an election_id that is not in
dim_election; its party_keys go unchecked and it gets no result rows until
electoral/ingest.py loads the returns. Once election day has passed, a
missing dim_election row is an error again.

A party-level option in a coalition year points at that party's own key only.
Ballots marked for several coalition partners at once sit under combination
keys (e.g. PT_MORENA) and are not apportioned back to parties, so a party's
result here is its solo-marked vote, not INE's distributed figure.

Usage:
    /usr/bin/python3 vote_intention/ingest.py
    /usr/bin/python3 vote_intention/ingest.py --force
    /usr/bin/python3 vote_intention/ingest.py --db path/to/other.db

To pick up new 2027 polls, refresh the live Wikipedia pages, reload, and
re-export the web data (/visualizaciones/encuestas):
    /usr/bin/python3 aux_scripts/vote_intention/scrape_wikipedia_polls.py --refresh
    /usr/bin/python3 vote_intention/ingest.py
    /usr/bin/python3 web/scripts/export_vote_intention.py
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "election_data.db"
POLLS_CSV = ROOT / "aux_scripts" / "vote_intention" / "vote_intention_polls.csv"
SUPPLEMENT_CSV = ROOT / "aux_scripts" / "vote_intention" / "vote_intention_polls_supplement.csv"

sys.path.insert(0, str(ROOT / "aux_scripts" / "vote_intention"))
from pollsters import familia  # noqa: E402

SCHEMA = """
CREATE TABLE IF NOT EXISTS dim_vote_intention_pollster (
    pollster_id   INTEGER PRIMARY KEY,
    pollster_name TEXT NOT NULL UNIQUE,
    familia       TEXT
);

CREATE TABLE IF NOT EXISTS dim_vote_intention_source (
    source_id    INTEGER PRIMARY KEY,
    source_kind  TEXT NOT NULL,
    source_ref   TEXT NOT NULL UNIQUE,
    revid        INTEGER,
    retrieved_at TEXT
);

-- share_basis says what the shares add up to (see scrape_wikipedia_polls.py):
-- efectiva = undecided already removed, bruta = undecided still in the base.
-- reference_date is the best single date the source gives: end of fieldwork
-- when published, else publication, else an unlabelled "Fecha".
CREATE TABLE IF NOT EXISTS fact_vote_intention_poll (
    poll_id                 TEXT PRIMARY KEY,
    election_id             TEXT NOT NULL REFERENCES dim_election(election_id),
    election_date           TEXT NOT NULL,
    pollster_id             INTEGER NOT NULL,
    pollster_raw            TEXT NOT NULL,
    cliente                 TEXT,
    metodo                  TEXT,
    poll_kind               TEXT NOT NULL,
    intention_level         TEXT NOT NULL,
    phase                   TEXT,
    share_basis             TEXT NOT NULL,
    fieldwork_start         TEXT,
    fieldwork_end           TEXT,
    published_date          TEXT,
    reference_date          TEXT,
    reference_date_type     TEXT,
    date_precision          TEXT,
    days_before_election    INTEGER,
    fecha_levantamiento_raw TEXT,
    fecha_publicacion_raw   TEXT,
    fecha_raw               TEXT,
    sample_size             INTEGER,
    margin_of_error         REAL,
    notas                   TEXT,
    -- the poll's own release or the article reporting it, as the source
    -- page's footnote cites it; NULL when the row cites nothing
    poll_source_url         TEXT,
    source_id               INTEGER NOT NULL,
    source_section          TEXT,
    source_table            INTEGER,
    source_row              INTEGER
);

-- party_keys lists the fact_casilla_vote.party_key values ("|"-separated)
-- whose votes make up this option on election day; blank for options with
-- no ballot counterpart (otros, undecided, candidates who never ran).
CREATE TABLE IF NOT EXISTS fact_vote_intention_option (
    poll_id           TEXT NOT NULL REFERENCES fact_vote_intention_poll(poll_id),
    option_order      INTEGER NOT NULL,
    opcion            TEXT NOT NULL,
    option_kind       TEXT NOT NULL,
    candidato         TEXT,
    partido_coalicion TEXT,
    party_keys        TEXT,
    pct               REAL,
    pct_raw           TEXT,
    PRIMARY KEY (poll_id, option_order)
);

CREATE TABLE IF NOT EXISTS fact_vote_intention_result (
    election_id TEXT NOT NULL,
    party_keys  TEXT NOT NULL,
    votes       INTEGER NOT NULL,
    total_votes INTEGER NOT NULL,
    valid_votes INTEGER NOT NULL,
    pct_total   REAL NOT NULL,
    pct_valid   REAL NOT NULL,
    PRIMARY KEY (election_id, party_keys)
);

-- pct_efectiva rescales a ballot option to the poll's ballot + "otros" total,
-- which puts gross and effective polls on the same footing as pct_valid.
CREATE VIEW IF NOT EXISTS view_vote_intention_vs_result AS
WITH ballot AS (
    SELECT poll_id, SUM(pct) AS ballot_pct
    FROM fact_vote_intention_option
    WHERE option_kind IN ('candidato', 'coalicion', 'partido', 'otros')
    GROUP BY poll_id
)
SELECT
    p.poll_id, p.election_id, p.election_date, p.reference_date,
    p.days_before_election, p.date_precision, p.poll_kind, p.intention_level,
    p.phase, p.share_basis, d.pollster_name AS pollster, d.familia, p.cliente,
    p.metodo, p.sample_size,
    o.option_order, o.opcion, o.option_kind, o.candidato, o.partido_coalicion,
    o.party_keys, o.pct,
    ROUND(100.0 * o.pct / NULLIF(b.ballot_pct, 0), 2) AS pct_efectiva,
    r.pct_valid AS result_pct_valid,
    r.pct_total AS result_pct_total,
    ROUND(100.0 * o.pct / NULLIF(b.ballot_pct, 0) - r.pct_valid, 2)
        AS diff_efectiva_vs_valid
FROM fact_vote_intention_option o
JOIN fact_vote_intention_poll p USING (poll_id)
JOIN dim_vote_intention_pollster d USING (pollster_id)
LEFT JOIN ballot b USING (poll_id)
LEFT JOIN fact_vote_intention_result r
       ON r.election_id = p.election_id AND r.party_keys = o.party_keys
WHERE o.option_kind IN ('candidato', 'coalicion', 'partido');
"""

TABLES = [
    "fact_vote_intention_result", "fact_vote_intention_option",
    "fact_vote_intention_poll", "dim_vote_intention_source",
    "dim_vote_intention_pollster",
]


def none_if_blank(value: str):
    value = (value or "").strip()
    return value or None


def to_float(value: str) -> float | None:
    value = none_if_blank(value)
    return float(value) if value is not None else None


def to_int(value: str) -> int | None:
    value = none_if_blank(value)
    return int(float(value)) if value is not None else None


def read_rows() -> list[dict]:
    if not POLLS_CSV.exists():
        raise FileNotFoundError(
            f"{POLLS_CSV} not found — run "
            "aux_scripts/vote_intention/scrape_wikipedia_polls.py first"
        )
    rows: list[dict] = []
    for path in (POLLS_CSV, SUPPLEMENT_CSV):
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as handle:
            rows.extend(csv.DictReader(handle))
    return rows


def upcoming_elections(conn: sqlite3.Connection, rows: list[dict]) -> set[str]:
    """Elections not yet held: no dim_election row and election day still ahead."""
    elections = {r[0] for r in conn.execute("SELECT election_id FROM dim_election")}
    return {
        row["election_id"] for row in rows
        if row["election_id"] not in elections
        and date.fromisoformat(row["election_date"]) > date.today()
    }


def check_inputs(conn: sqlite3.Connection, rows: list[dict]) -> list[str]:
    """Every election and party key the CSV names must exist in the warehouse,
    except for elections not yet held."""
    problems: list[str] = []
    elections = {r[0] for r in conn.execute("SELECT election_id FROM dim_election")}
    upcoming = upcoming_elections(conn, rows)
    for election_id in sorted({row["election_id"] for row in rows}):
        if election_id in upcoming:
            continue
        if election_id not in elections:
            problems.append(f"{election_id} is not in dim_election")
            continue
        known = {
            r[0] for r in conn.execute(
                "SELECT DISTINCT party_key FROM fact_casilla_vote WHERE election_id = ?",
                (election_id,),
            )
        }
        named = {
            key
            for row in rows if row["election_id"] == election_id
            for key in row["party_keys"].split("|") if key
        }
        for key in sorted(named - known):
            problems.append(f"{election_id}: party_key {key!r} not in fact_casilla_vote")

    # Poll-level fields repeat on every option row and must agree.
    by_poll: dict[str, tuple] = {}
    poll_fields = ("election_id", "pollster", "reference_date", "share_basis", "source_url")
    for row in rows:
        signature = tuple(row[f] for f in poll_fields)
        previous = by_poll.setdefault(row["poll_id"], signature)
        if previous != signature:
            problems.append(f"{row['poll_id']}: poll fields differ between option rows")
    return problems


def upsert_dims(conn, rows) -> tuple[dict[str, int], dict[str, int]]:
    for name in sorted({row["pollster"] for row in rows}):
        conn.execute(
            "INSERT OR IGNORE INTO dim_vote_intention_pollster (pollster_name, familia) "
            "VALUES (?, ?)",
            (name, familia(name)),
        )
    retrieved = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for url, revid, source in sorted({
        (row["source_url"], row["source_revid"], row["source"]) for row in rows
    }):
        conn.execute(
            "INSERT OR IGNORE INTO dim_vote_intention_source "
            "(source_kind, source_ref, revid, retrieved_at) VALUES (?, ?, ?, ?)",
            (source, url, to_int(revid), retrieved),
        )
    pollsters = dict(conn.execute(
        "SELECT pollster_name, pollster_id FROM dim_vote_intention_pollster"
    ).fetchall())
    sources = dict(conn.execute(
        "SELECT source_ref, source_id FROM dim_vote_intention_source"
    ).fetchall())
    return pollsters, sources


def load_polls(conn, rows, pollsters, sources) -> tuple[int, int]:
    conn.execute("DELETE FROM fact_vote_intention_option")
    conn.execute("DELETE FROM fact_vote_intention_poll")
    seen: set[str] = set()
    for row in rows:
        if row["poll_id"] in seen:
            continue
        seen.add(row["poll_id"])
        reference = none_if_blank(row["reference_date"])
        days_before = (
            (date.fromisoformat(row["election_date"]) - date.fromisoformat(reference)).days
            if reference else None
        )
        conn.execute(
            """INSERT INTO fact_vote_intention_poll VALUES
               (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                row["poll_id"], row["election_id"], row["election_date"],
                pollsters[row["pollster"]], row["pollster_raw"],
                none_if_blank(row["cliente"]), none_if_blank(row["metodo"]),
                row["poll_kind"], row["intention_level"], none_if_blank(row["phase"]),
                row["share_basis"],
                none_if_blank(row["fieldwork_start"]), none_if_blank(row["fieldwork_end"]),
                none_if_blank(row["published_date"]), reference,
                none_if_blank(row["reference_date_type"]),
                none_if_blank(row["date_precision"]), days_before,
                none_if_blank(row["fecha_levantamiento_raw"]),
                none_if_blank(row["fecha_publicacion_raw"]),
                none_if_blank(row["fecha_raw"]),
                to_int(row["sample_size"]), to_float(row["margin_of_error"]),
                none_if_blank(row["notas"]),
                none_if_blank(row.get("poll_source_url"))
                or (row["source_url"] if row["source"] == "prensa" else None),
                sources[row["source_url"]], row["source_section"],
                to_int(row["source_table"]), to_int(row["source_row"]),
            ),
        )
    for row in rows:
        conn.execute(
            "INSERT INTO fact_vote_intention_option VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row["poll_id"], int(row["option_order"]),
                row["opcion"] or row["partido_coalicion"], row["option_kind"],
                none_if_blank(row["candidato"]), none_if_blank(row["partido_coalicion"]),
                none_if_blank(row["party_keys"]), to_float(row["pct"]),
                row["pct_raw"],
            ),
        )
    return len(seen), len(rows)


def load_results(conn, rows) -> int:
    """National shares for each party_keys set the options point at."""
    conn.execute("DELETE FROM fact_vote_intention_result")
    upcoming = upcoming_elections(conn, rows)
    wanted: dict[str, set[str]] = {}
    for row in rows:
        if row["party_keys"] and row["election_id"] not in upcoming:
            wanted.setdefault(row["election_id"], set()).add(row["party_keys"])

    loaded = 0
    for election_id, key_sets in sorted(wanted.items()):
        by_key = dict(conn.execute(
            "SELECT party_key, SUM(votes) FROM fact_casilla_vote "
            "WHERE election_id = ? GROUP BY party_key",
            (election_id,),
        ).fetchall())
        # Null and unregistered-candidate counts repeat on every party row of a
        # casilla, so they are taken once per casilla.
        nulos, no_registrados = conn.execute(
            """SELECT SUM(n), SUM(c) FROM (
                   SELECT MAX(COALESCE(num_votos_nulos, 0)) AS n,
                          MAX(COALESCE(num_votos_can_nreg, 0)) AS c
                   FROM fact_casilla_vote WHERE election_id = ?
                   GROUP BY casilla_id)""",
            (election_id,),
        ).fetchone()
        valid = sum(by_key.values()) + no_registrados
        total = valid + nulos
        for key_set in sorted(key_sets):
            votes = sum(by_key.get(key, 0) for key in key_set.split("|"))
            conn.execute(
                "INSERT INTO fact_vote_intention_result VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    election_id, key_set, votes, total, valid,
                    round(100.0 * votes / total, 3), round(100.0 * votes / valid, 3),
                ),
            )
            loaded += 1
    return loaded


def validate(conn: sqlite3.Connection, expected_polls: int, expected_options: int) -> bool:
    print("\n── QA ──────────────────────────────────────────────────")
    ok = True

    def check(condition: bool, good: str, bad: str) -> None:
        nonlocal ok
        if condition:
            print(f"  OK: {good}")
        else:
            print(f"  ERROR:   {bad}")
            ok = False

    polls = conn.execute("SELECT COUNT(*) FROM fact_vote_intention_poll").fetchone()[0]
    options = conn.execute("SELECT COUNT(*) FROM fact_vote_intention_option").fetchone()[0]
    check(
        (polls, options) == (expected_polls, expected_options),
        f"{polls:,} polls and {options:,} options stored, as read",
        f"stored {polls:,}/{options:,} but read {expected_polls:,}/{expected_options:,}",
    )
    out_of_range = conn.execute(
        "SELECT COUNT(*) FROM fact_vote_intention_option WHERE pct < 0 OR pct > 100"
    ).fetchone()[0]
    check(out_of_range == 0, "all shares within 0-100",
          f"{out_of_range} option share(s) outside 0-100")
    after = conn.execute(
        "SELECT COUNT(*) FROM fact_vote_intention_poll WHERE days_before_election < 0"
    ).fetchone()[0]
    check(after == 0, "no poll dated after its election",
          f"{after} poll(s) dated after election day")
    unmatched = conn.execute(
        """SELECT COUNT(*) FROM fact_vote_intention_option o
           JOIN fact_vote_intention_poll p USING (poll_id)
           LEFT JOIN fact_vote_intention_result r
                  ON r.election_id = p.election_id AND r.party_keys = o.party_keys
           WHERE o.party_keys IS NOT NULL AND r.party_keys IS NULL
             AND p.election_id IN (SELECT election_id FROM dim_election)"""
    ).fetchone()[0]
    check(unmatched == 0, "every option with party_keys has a result (held elections)",
          f"{unmatched} option(s) with party_keys but no result row")
    for election_id, n in conn.execute(
        """SELECT election_id, COUNT(*) FROM fact_vote_intention_poll
           WHERE election_id NOT IN (SELECT election_id FROM dim_election)
           GROUP BY 1 ORDER BY 1"""
    ):
        print(f"  NOTE: {election_id}: {n} polls loaded with no results yet (election ahead)")

    # The winner's share is a fixed, well-known number; a wrong key set or
    # denominator shows up here first.
    print("\n  Winner check (pct of total votes, incl. null ballots):")
    for election_id, candidato in [
        ("PRE_1994", "Ernesto Zedillo"), ("PRE_2000", "Vicente Fox"),
        ("PRE_2006", "Felipe Calderón"), ("PRE_2012", "Enrique Peña Nieto"),
        ("PRE_2018", "Andrés Manuel López Obrador"), ("PRE_2024", "Claudia Sheinbaum"),
    ]:
        row = conn.execute(
            """SELECT DISTINCT r.pct_total, r.pct_valid
               FROM fact_vote_intention_option o
               JOIN fact_vote_intention_poll p USING (poll_id)
               JOIN fact_vote_intention_result r
                 ON r.election_id = p.election_id AND r.party_keys = o.party_keys
               WHERE p.election_id = ? AND o.candidato = ?
                 AND o.option_kind IN ('candidato', 'partido')""",
            (election_id, candidato),
        ).fetchall()
        shown = ", ".join(f"{t:.2f}% total / {v:.2f}% valid" for t, v in row)
        print(f"    {election_id} {candidato}: {shown}")

    basis = conn.execute(
        "SELECT share_basis, COUNT(*) FROM fact_vote_intention_poll GROUP BY 1 ORDER BY 2 DESC"
    ).fetchall()
    print("\n  share_basis: " + ", ".join(f"{b}={n}" for b, n in basis))
    print("────────────────────────────────────────────────────────")
    return ok


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--force", action="store_true",
                        help="Drop and recreate the vote-intention tables first")
    args = parser.parse_args()

    print(f"Warehouse: {args.db}")
    # Other sessions read the warehouse for minutes at a time; wait them out
    # rather than failing on the first shared lock.
    conn = sqlite3.connect(args.db, timeout=600)
    if args.force:
        print("--force: dropping existing vote-intention tables")
        conn.execute("DROP VIEW IF EXISTS view_vote_intention_vs_result")
        for table in TABLES:
            conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.executescript(SCHEMA)

    rows = read_rows()
    problems = check_inputs(conn, rows)
    if problems:
        for problem in problems:
            print(f"  ERROR:   {problem}")
        conn.close()
        sys.exit(1)

    try:
        pollsters, sources = upsert_dims(conn, rows)
        n_polls, n_options = load_polls(conn, rows, pollsters, sources)
        n_results = load_results(conn, rows)
        conn.commit()
    except Exception as exc:
        conn.rollback()
        print(f"  ERROR: {exc}")
        conn.close()
        sys.exit(1)

    print(f"  polls={n_polls:,}  options={n_options:,}  result rows={n_results}")
    print("\n── Row counts ──────────────────────────────────────────")
    for table in reversed(TABLES):
        n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {table}: {n:,}")

    ok = validate(conn, n_polls, n_options)
    conn.close()
    if not ok:
        print("Ingest completed with QA errors — see ERROR lines above.")
        sys.exit(1)
    print("Done.")


if __name__ == "__main__":
    main()
