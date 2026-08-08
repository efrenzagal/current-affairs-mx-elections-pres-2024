"""Resolve official current Congreso rosters onto stable INE seat identities.

This pipeline is deliberately independent from vote ingestion.  It preserves
the election-time identity and party in ``dim_diputados`` / ``dim_senadores``
and appends dated current-roster snapshots for the hemicycle.

Usage::

    python -m ingestion.congress_roster_ingest
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import unicodedata
from pathlib import Path

import pandas as pd

from ui.person_names import match_person_name, person_name_similarity, person_name_tokens


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "election_data.db"
ROSTER_DIR = ROOT / "data" / "clean_congress_rosters"
REVIEW_PATH = ROOT / "data" / "current_congress_review.csv"
LEGISLATURE = 66

SCHEMA = """
CREATE TABLE IF NOT EXISTS dim_congress_roster_snapshot (
    snapshot_id       TEXT PRIMARY KEY,
    chamber           TEXT NOT NULL CHECK (chamber IN ('DIP', 'SEN')),
    legislature       INTEGER NOT NULL,
    observed_at       TEXT NOT NULL,
    source_url        TEXT NOT NULL,
    source_sha256     TEXT NOT NULL,
    roster_row_count  INTEGER NOT NULL,
    constitutional_seats INTEGER NOT NULL,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS fact_congress_roster_seat (
    snapshot_id       TEXT NOT NULL,
    chamber           TEXT NOT NULL CHECK (chamber IN ('DIP', 'SEN')),
    seat_id           TEXT NOT NULL,
    member_source_id  TEXT,
    current_name      TEXT,
    current_party     TEXT,
    member_status     TEXT NOT NULL,
    vote_person_id    TEXT,
    election_name     TEXT,
    election_party    TEXT NOT NULL,
    seat_type         TEXT NOT NULL,
    id_estado         INTEGER,
    nombre_estado     TEXT,
    id_distrito_federal INTEGER,
    circunscripcion   INTEGER,
    numero_lista      INTEGER,
    match_method      TEXT NOT NULL,
    match_score       REAL,
    profile_url       TEXT,
    PRIMARY KEY (snapshot_id, seat_id),
    FOREIGN KEY (snapshot_id) REFERENCES dim_congress_roster_snapshot(snapshot_id)
);

CREATE INDEX IF NOT EXISTS idx_congress_roster_latest
    ON dim_congress_roster_snapshot(chamber, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_congress_roster_member
    ON fact_congress_roster_seat(chamber, member_source_id);
"""

PARTY_ALIASES = {"MRN": "MORENA", "CAND_INDEPENDIENTE": "IND", "SIN GRUPO": "SG"}

# Official profile IDs mapped to stable INE seats where the directory shortens
# or corrects the registered name enough to fall below the conservative global
# matcher.  These are deliberately seat-specific and must remain audited.
AUDITED_CURRENT_SEAT_OVERRIDES = {
    # SITL corrects INE's "ARIÑANO" typo to "Artiñano".
    ("DIP", "391"): "DIP_55F71017CA0A",
    # SITL omits Francisco/Federico from Francisco Arturo Federico Ávila Anaya.
    ("DIP", "343"): "DIP_B6B0C4B44745",
    # SITL renders María de Jesús Rosete Sánchez simply as "Rosete María".
    ("DIP", "437"): "DIP_2EAFAA11EFF1",
    # SITL shortens Olga del Carmen Sánchez Cordero Dávila.
    ("DIP", "431"): "DIP_0440365B05A3",
    # Senado profile 1511 shortens Susana del Carmen Zatarain García.
    ("SEN", "1511"): "SEN_6B8192E5D164",
}


def canonical_party(value: object) -> str:
    party = str(value or "").strip().upper()
    return PARTY_ALIASES.get(party, party)


def state_key(value: object) -> str:
    text = (
        unicodedata.normalize("NFKD", str(value or ""))
        .encode("ascii", "ignore")
        .decode("ascii")
        .upper()
    )
    text = re.sub(r"[^A-Z]+", " ", text).strip()
    aliases = {
        "CDMX": "CIUDAD DE MEXICO",
        "COAHUILA": "COAHUILA DE ZARAGOZA",
        "MICHOACAN": "MICHOACAN DE OCAMPO",
        "ESTADO DE MEXICO": "MEXICO",
        "VERACRUZ": "VERACRUZ DE IGNACIO DE LA LLAVE",
    }
    return aliases.get(text, text)


def _candidate_lookup(seats: pd.DataFrame, subset: pd.DataFrame | None = None) -> dict[str, tuple[int, str]]:
    frame = seats if subset is None else subset
    lookup: dict[str, tuple[int, str]] = {}
    for index, row in frame.iterrows():
        for role, column in (("titular", "ine_candidate_name"), ("suplente", "ine_substitute_name")):
            value = row.get(column)
            name = str(value).strip() if pd.notna(value) else ""
            if name:
                lookup[f"{index}|{role}"] = (index, name)
    return lookup


def _match_candidate(name: str, lookup: dict[str, tuple[int, str]]) -> tuple[int | None, str, float | None]:
    names = [value[1] for value in lookup.values()]
    matched, quality = match_person_name(name, names)
    if matched is None:
        return None, "unmatched", None
    matches = [(key, value) for key, value in lookup.items() if value[1] == matched]
    if len(matches) != 1:
        return None, "ambiguous_name", None
    key, (index, _) = matches[0]
    role = key.rsplit("|", 1)[-1]
    method = f"{role}_{'exact_tokens' if quality == 'exact' else 'approximate_tokens'}"
    return index, method, person_name_similarity(name, matched)


def _load_deputy_vote_roster(conn: sqlite3.Connection) -> dict[str, str]:
    roster = pd.read_sql_query(
        """
        SELECT DISTINCT d.deputy_id, d.deputy_name
        FROM dim_gaceta_deputy d
        JOIN fact_gaceta_deputy_vote f USING(deputy_id)
        JOIN dim_gaceta_vote v USING(gaceta_vote_id)
        WHERE v.legislature = ?
        """,
        conn,
        params=(LEGISLATURE,),
    )
    return dict(zip(roster["deputy_name"], roster["deputy_id"]))


def _resolve_vote_person(name: str, roster_by_name: dict[str, str]) -> str | None:
    matched, _ = match_person_name(name, roster_by_name)
    return roster_by_name.get(matched) if matched else None


def resolve_diputados(conn: sqlite3.Connection, roster: pd.DataFrame) -> tuple[pd.DataFrame, list[dict]]:
    seats = pd.read_sql_query(
        """
        SELECT diputado_id AS seat_id, party_key AS election_party, seat_type,
               id_estado, nombre_estado, id_distrito_federal, circunscripcion,
               numero_lista, ine_candidate_name, ine_substitute_name,
               display_name AS election_name
        FROM dim_diputados WHERE legislature = ? ORDER BY diputado_id
        """,
        conn,
        params=(LEGISLATURE,),
    )
    seats["_state_key"] = seats["nombre_estado"].map(state_key)
    vote_roster = _load_deputy_vote_roster(conn)
    assignments: dict[int, dict] = {}
    review: list[dict] = []

    for _, member in roster.iterrows():
        seat_index: int | None = None
        method = "unmatched"
        score: float | None = None
        override_id = AUDITED_CURRENT_SEAT_OVERRIDES.get(
            ("DIP", str(member["member_source_id"]))
        )
        if override_id is not None:
            candidates = seats[seats["seat_id"] == override_id]
            if len(candidates) == 1:
                seat_index = int(candidates.index[0])
                method = "audited_seat_override"
                score = person_name_similarity(member["current_name"], candidates.iloc[0]["election_name"])
        elif pd.notna(member.get("district")):
            candidates = seats[
                (seats["seat_type"] == "MR")
                & (seats["_state_key"] == state_key(member["state"]))
                & (seats["id_distrito_federal"] == int(member["district"]))
            ]
            if len(candidates) == 1:
                seat_index = int(candidates.index[0])
                method, score = "district_key", 1.0
        else:
            candidates = seats[
                (seats["seat_type"] == "RP")
                & (seats["circunscripcion"] == int(member["circunscripcion"]))
            ]
            seat_index, method, score = _match_candidate(
                str(member["current_name"]), _candidate_lookup(seats, candidates)
            )

        if seat_index is None or seat_index in assignments:
            review.append(
                {
                    "chamber": "DIP",
                    "member_source_id": member["member_source_id"],
                    "current_name": member["current_name"],
                    "current_party": member["current_party"],
                    "reason": "duplicate_seat" if seat_index in assignments else method,
                }
            )
            continue
        assignments[seat_index] = {
            **member.to_dict(),
            "vote_person_id": _resolve_vote_person(str(member["current_name"]), vote_roster),
            "match_method": method,
            "match_score": score,
        }

    return _combine_seats(seats, assignments, "DIP"), review


def resolve_senadores(conn: sqlite3.Connection, roster: pd.DataFrame) -> tuple[pd.DataFrame, list[dict]]:
    seats = pd.read_sql_query(
        """
        SELECT senador_seat_id AS seat_id, party_key AS election_party, seat_type,
               id_estado, nombre_estado, NULL AS id_distrito_federal,
               NULL AS circunscripcion, numero_lista, ine_candidate_name,
               ine_substitute_name, display_name AS election_name, senador_id
        FROM dim_senadores WHERE legislature = ? ORDER BY senador_seat_id
        """,
        conn,
        params=(LEGISLATURE,),
    )
    by_vote_id = {
        str(int(row.senador_id)): int(index)
        for index, row in seats.dropna(subset=["senador_id"]).iterrows()
    }
    all_candidates = _candidate_lookup(seats)
    assignments: dict[int, dict] = {}
    review: list[dict] = []

    for _, member in roster.iterrows():
        source_id = str(member["member_source_id"])
        override_id = AUDITED_CURRENT_SEAT_OVERRIDES.get(("SEN", source_id))
        if override_id is not None:
            candidates = seats[seats["seat_id"] == override_id]
            seat_index = int(candidates.index[0]) if len(candidates) == 1 else None
            method, score = "audited_seat_override", person_name_similarity(
                member["current_name"], candidates.iloc[0]["election_name"]
            ) if len(candidates) == 1 else None
        else:
            seat_index = by_vote_id.get(source_id)
            method, score = ("existing_senado_id", 1.0) if seat_index is not None else ("unmatched", None)
        if seat_index is None:
            seat_index, method, score = _match_candidate(str(member["current_name"]), all_candidates)
        if seat_index is None or seat_index in assignments:
            review.append(
                {
                    "chamber": "SEN",
                    "member_source_id": source_id,
                    "current_name": member["current_name"],
                    "current_party": member["current_party"],
                    "reason": "duplicate_seat" if seat_index in assignments else method,
                }
            )
            continue
        assignments[seat_index] = {
            **member.to_dict(),
            "vote_person_id": source_id if conn.execute(
                "SELECT 1 FROM dim_senador WHERE senador_id = ?", (int(source_id),)
            ).fetchone() else None,
            "match_method": method,
            "match_score": score,
        }

    return _combine_seats(seats, assignments, "SEN"), review


def _combine_seats(seats: pd.DataFrame, assignments: dict[int, dict], chamber: str) -> pd.DataFrame:
    rows: list[dict] = []
    for index, seat in seats.iterrows():
        member = assignments.get(int(index))
        rows.append(
            {
                "snapshot_id": member["snapshot_id"] if member else None,
                "chamber": chamber,
                "seat_id": seat["seat_id"],
                "member_source_id": member.get("member_source_id") if member else None,
                "current_name": member.get("current_name") if member else None,
                "current_party": canonical_party(member.get("current_party")) if member else "VACANTE",
                "member_status": member.get("status", "vacante") if member else "vacante",
                "vote_person_id": member.get("vote_person_id") if member else None,
                "election_name": seat["election_name"],
                "election_party": canonical_party(seat["election_party"]),
                "seat_type": seat["seat_type"],
                "id_estado": seat["id_estado"],
                "nombre_estado": seat["nombre_estado"],
                "id_distrito_federal": seat["id_distrito_federal"],
                "circunscripcion": seat["circunscripcion"],
                "numero_lista": seat["numero_lista"],
                "match_method": member.get("match_method", "vacant_unassigned") if member else "vacant_unassigned",
                "match_score": member.get("match_score") if member else None,
                "profile_url": member.get("profile_url") if member else None,
            }
        )
    return pd.DataFrame(rows)


def _insert_snapshot(conn: sqlite3.Connection, roster: pd.DataFrame, resolved: pd.DataFrame, chamber: str) -> None:
    snapshot_id = str(roster.iloc[0]["snapshot_id"])
    resolved = resolved.copy()
    resolved["snapshot_id"] = snapshot_id
    meta = pd.DataFrame(
        [{
            "snapshot_id": snapshot_id,
            "chamber": chamber,
            "legislature": LEGISLATURE,
            "observed_at": roster.iloc[0]["observed_at"],
            "source_url": (
                "https://sitl.diputados.gob.mx/LXVI_leg/info_diputados.php"
                if chamber == "DIP"
                else roster.iloc[0]["source_url"]
            ),
            "source_sha256": roster.iloc[0]["source_sha256"],
            "roster_row_count": len(roster),
            "constitutional_seats": 500 if chamber == "DIP" else 128,
        }]
    )
    # Re-running ingestion for the exact same parsed snapshot is idempotent,
    # while new observation timestamps remain append-only history.
    conn.execute("DELETE FROM fact_congress_roster_seat WHERE snapshot_id = ?", (snapshot_id,))
    conn.execute("DELETE FROM dim_congress_roster_snapshot WHERE snapshot_id = ?", (snapshot_id,))
    meta.to_sql("dim_congress_roster_snapshot", conn, if_exists="append", index=False)
    resolved.to_sql("fact_congress_roster_seat", conn, if_exists="append", index=False)


def materialize(db_path: Path = DB_PATH, roster_dir: Path = ROSTER_DIR) -> tuple[pd.DataFrame, pd.DataFrame]:
    diputados_roster = pd.read_csv(roster_dir / "diputados_current.csv", dtype={"member_source_id": str})
    senadores_roster = pd.read_csv(roster_dir / "senadores_current.csv", dtype={"member_source_id": str})
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA)
        diputados, dip_review = resolve_diputados(conn, diputados_roster)
        senadores, sen_review = resolve_senadores(conn, senadores_roster)
        review = pd.DataFrame([*dip_review, *sen_review])
        if not review.empty:
            review.to_csv(REVIEW_PATH, index=False)
            raise ValueError(
                f"Current roster has {len(review)} unresolved/duplicate members; review {REVIEW_PATH}"
            )
        if diputados["member_source_id"].notna().sum() != 500:
            raise ValueError("Camara snapshot did not resolve all 500 seats")
        if senadores["member_source_id"].notna().sum() != len(senadores_roster):
            raise ValueError("Senate snapshot did not resolve every in-office member")
        if senadores["member_status"].eq("vacante").sum() != 128 - len(senadores_roster):
            raise ValueError("Senate vacancy count does not reconcile to the official in-office directory")
        _insert_snapshot(conn, diputados_roster, diputados, "DIP")
        _insert_snapshot(conn, senadores_roster, senadores, "SEN")
        conn.commit()
    if REVIEW_PATH.exists():
        REVIEW_PATH.unlink()
    return diputados, senadores


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest current official Congreso rosters")
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--roster-dir", type=Path, default=ROSTER_DIR)
    args = parser.parse_args()
    diputados, senadores = materialize(args.db, args.roster_dir)
    print(f"Resolved Camara: {diputados['member_source_id'].notna().sum()}/500")
    print(
        "Resolved Senate: "
        f"{senadores['member_source_id'].notna().sum()}/128 in office; "
        f"{senadores['member_status'].eq('vacante').sum()} vacant"
    )


if __name__ == "__main__":
    main()
