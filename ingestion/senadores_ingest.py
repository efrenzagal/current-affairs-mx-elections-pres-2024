"""
Build the official-senator to Senado.gob.mx identity bridge.

``dim_senadores`` has one row per final 2024 Senado de la Republica seat
assignment published by INE. It preserves the official seat attributes and
maps the titular -- or the officially listed substitute who actually appears
in the roll calls -- to ``dim_senador`` (the roster scraped from
senado.gob.mx by camara_de_senadores/votos/crawl_senado_votes.py) only when
the identity is reliable.

Usage:
    python -m ingestion.senadores_ingest
    python -m ingestion.senadores_ingest --db path/to/election_data.db
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sqlite3
from pathlib import Path

import pandas as pd

from ui.person_names import (
    display_person_name,
    match_person_name,
    person_name_similarity,
    person_name_tokens,
)


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "election_data.db"
INTEGRACION_PATH = (
    ROOT
    / "data"
    / "electoral_data_raw"
    / "raw_2024"
    / "PRESIDENCIA_2024"
    / "CSV"
    / "INTEGRACION_CARGOS_PEF_2024.csv"
)
ELECTION_ID = "SEN_MR_2024"
LEGISLATURE = 66

SENADO_TITLE_PREFIX_RE = re.compile(r"^\s*Sen\.\s*", re.IGNORECASE)

# Manually verified aliases where the official INE integration and the
# senado.gob.mx roster refer to the same person but the conservative token
# matcher cannot prove it (INE spells out middle names/second surnames that
# senado.gob.mx omits, dropping the token-set Jaccard score just under the
# 0.67 threshold). Keep these explicit: loosening the global fuzzy-match
# threshold risks linking two different senators with similar names.
AUDITED_SENADO_NAME_OVERRIDES: dict[str, str] = {
    # Roster omits middle names "Dora Luz" (score 4/6 = 0.667, just under threshold).
    "SASIL DORA LUZ DE LEON VILLARD": "De León Villard, Sasil",
    # Roster omits "Del Carmen" (score 3/5 = 0.60).
    "VERONICA DEL CARMEN DIAZ ROBLES": "Díaz Robles, Verónica",
    # Roster omits "Jose" and "Rodolfo" (score 3/5 = 0.60).
    "JOSE GERARDO RODOLFO FERNANDEZ NOROÑA": "Fernández Noroña, Gerardo",
    # Roster omits "Maria", "Del Carmen", and second surname "Garcia" (score 2/6 = 0.33).
    "MARIA LILLY DEL CARMEN TELLEZ GARCIA": "Téllez , Lilly",
}


TABLE_SCHEMA = """
CREATE TABLE {table_name} (
    senador_seat_id      TEXT PRIMARY KEY,
    election_id          TEXT NOT NULL,
    legislature           INTEGER NOT NULL,
    seat_type             TEXT NOT NULL CHECK (seat_type IN ('MR', 'FM', 'RP')),
    party_key             TEXT NOT NULL,
    id_estado             INTEGER,
    nombre_estado          TEXT,
    numero_lista           INTEGER,
    ine_candidate_name     TEXT,
    ine_substitute_name    TEXT,
    source_name_role       TEXT NOT NULL,
    display_name           TEXT,
    normalized_name_key    TEXT,
    senador_id             INTEGER,
    senador_name           TEXT,
    match_method           TEXT NOT NULL,
    match_score            REAL,
    source_file            TEXT NOT NULL,
    created_at             TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (senador_id) REFERENCES dim_senador(senador_id)
)
"""

INDEX_SCHEMA = """
CREATE INDEX idx_dim_senadores_senador
    ON dim_senadores(senador_id);

CREATE INDEX idx_dim_senadores_seat
    ON dim_senadores(election_id, seat_type, party_key);
"""


def _integer_or_none(value: object) -> int | None:
    return int(value) if pd.notna(value) else None


def _source_file_label(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path.resolve())


def strip_senado_title(name: str) -> str:
    """Drop the "Sen. " title so it doesn't pollute token matching."""
    return SENADO_TITLE_PREFIX_RE.sub("", name or "").strip()


def senador_seat_type(row: pd.Series) -> str:
    """SEN_MR list position 3 is the primera minoria seat; 1-2 are mayoria."""
    if row["TIPO_DE_CANDIDATURA"] == "SEN_RP":
        return "RP"
    return "FM" if _integer_or_none(row["NUMERO_LISTA"]) == 3 else "MR"


def senador_seat_key(row: pd.Series) -> str:
    """Stable natural seat key that does not depend on candidate spelling."""
    candidature_type = str(row["TIPO_DE_CANDIDATURA"])
    if candidature_type == "SEN_MR":
        return "|".join([
            ELECTION_ID,
            "MR",
            str(_integer_or_none(row["ID_ESTADO"]) or 0),
            str(_integer_or_none(row["NUMERO_LISTA"]) or 0),
        ])
    if candidature_type == "SEN_RP":
        return "|".join([
            ELECTION_ID,
            "RP",
            str(row["PARTIDO_POLITICO"]).strip(),
            str(_integer_or_none(row["NUMERO_LISTA"]) or 0),
        ])
    raise ValueError(f"Unsupported candidature type: {candidature_type}")


def senador_id_for_row(row: pd.Series) -> str:
    digest = hashlib.sha1(senador_seat_key(row).encode("utf-8")).hexdigest()[:12].upper()
    return f"SEN_{digest}"


def load_official_senador_rows(path: Path = INTEGRACION_PATH) -> pd.DataFrame:
    df = pd.read_csv(path, encoding="utf-8-sig")
    return df[df["TIPO_DE_CANDIDATURA"].isin(["SEN_MR", "SEN_RP"])].copy()


def load_senado_roster(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT senador_id, senador_name FROM dim_senador ORDER BY senador_name", conn
    )


def resolve_senado_identity(
    candidate_name: str,
    substitute_name: str,
    roster_names: list[str],
) -> tuple[str | None, str | None, str, float | None]:
    """Resolve an official seat to the person appearing in the Senado roster.

    Try the elected titular first, then the officially listed substitute. This
    matters when the substitute actually took the seat and therefore owns the
    roll-call history. Audited aliases cover source typos without weakening
    the conservative matcher for everybody else.
    """
    roster_set = set(roster_names)
    identities = (("titular", candidate_name), ("suplente", substitute_name))

    for role, identity_name in identities:
        if not identity_name:
            continue

        audited_name = AUDITED_SENADO_NAME_OVERRIDES.get(identity_name.upper())
        if audited_name is not None:
            if audited_name not in roster_set:
                raise ValueError(
                    f"Audited Senado identity not found in legislature {LEGISLATURE} "
                    f"roster: {identity_name!r} -> {audited_name!r}"
                )
            return (
                audited_name,
                role,
                "audited_override",
                person_name_similarity(identity_name, audited_name),
            )

        matched_name, quality = match_person_name(identity_name, roster_names)
        if matched_name is not None:
            method = "exact_tokens" if quality == "exact" else "approximate_tokens"
            return (
                matched_name,
                role,
                method,
                person_name_similarity(identity_name, matched_name),
            )

    return None, None, "unmatched", None


def build_dim_senadores(
    conn: sqlite3.Connection,
    integration_path: Path = INTEGRACION_PATH,
) -> pd.DataFrame:
    official = load_official_senador_rows(integration_path)
    roster = load_senado_roster(conn)
    roster_by_stripped_name: dict[str, tuple[str, int]] = {}
    for senador_id, senador_name in zip(roster["senador_id"], roster["senador_name"]):
        roster_by_stripped_name[strip_senado_title(senador_name)] = (senador_name, senador_id)
    roster_names = list(roster_by_stripped_name)

    rows = []
    for _, source in official.iterrows():
        candidate_name = (
            str(source["PERSONA_CANDIDATA"]).strip()
            if pd.notna(source["PERSONA_CANDIDATA"])
            else ""
        )
        substitute_name = (
            str(source["PERSONA_CANDIDATA_SUPLENTE"]).strip()
            if pd.notna(source["PERSONA_CANDIDATA_SUPLENTE"])
            else ""
        )
        if not candidate_name and not substitute_name:
            identity_name = ""
            source_name_role = "suplente"
            matched_name = None
            matched_id = None
            match_score = None
            match_method = "missing_source_name"
        else:
            matched_name, matched_role, match_method, match_score = resolve_senado_identity(
                candidate_name,
                substitute_name,
                roster_names,
            )
            if matched_name is None:
                identity_name = candidate_name or substitute_name
                source_name_role = "titular" if candidate_name else "suplente"
                matched_id = None
            else:
                source_name_role = str(matched_role)
                identity_name = candidate_name if matched_role == "titular" else substitute_name
                matched_id = roster_by_stripped_name[matched_name][1]
                matched_name = roster_by_stripped_name[matched_name][0]

        rows.append({
            "senador_seat_id": senador_id_for_row(source),
            "election_id": ELECTION_ID,
            "legislature": LEGISLATURE,
            "seat_type": senador_seat_type(source),
            "party_key": str(source["PARTIDO_POLITICO"]).strip(),
            "id_estado": _integer_or_none(source["ID_ESTADO"]),
            "nombre_estado": (
                str(source["NOMBRE_ESTADO"]).strip()
                if pd.notna(source["NOMBRE_ESTADO"])
                else None
            ),
            "numero_lista": _integer_or_none(source["NUMERO_LISTA"]),
            "ine_candidate_name": candidate_name or None,
            "ine_substitute_name": substitute_name or None,
            "source_name_role": source_name_role,
            "display_name": display_person_name(identity_name) or None,
            "normalized_name_key": (
                "|".join(person_name_tokens(identity_name))
                if identity_name
                else None
            ),
            "senador_id": matched_id,
            "senador_name": matched_name,
            "match_method": match_method,
            "match_score": match_score,
            "source_file": _source_file_label(integration_path),
        })
    return pd.DataFrame(rows)


def validate_dim_senadores(
    conn: sqlite3.Connection,
    table_name: str = "dim_senadores",
) -> None:
    if table_name not in {"dim_senadores", "dim_senadores_next"}:
        raise ValueError(f"Unexpected table name: {table_name}")

    row_count = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
    if row_count != 128:
        raise ValueError(f"{table_name} must contain 128 seats; found {row_count}")

    duplicate_ids = conn.execute(
        f"""
        SELECT COUNT(*) FROM (
            SELECT senador_seat_id
            FROM {table_name}
            GROUP BY senador_seat_id
            HAVING COUNT(*) > 1
        )
        """
    ).fetchone()[0]
    if duplicate_ids:
        raise ValueError(f"dim_senadores contains {duplicate_ids} duplicate IDs")

    orphan_matches = conn.execute(
        f"""
        SELECT COUNT(*)
        FROM {table_name} AS d
        WHERE d.senador_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1
              FROM dim_senador AS g
              WHERE g.senador_id = d.senador_id
          )
        """
    ).fetchone()[0]
    if orphan_matches:
        raise ValueError(f"dim_senadores contains {orphan_matches} orphan Senado mappings")

    unmatched = conn.execute(
        f"SELECT COUNT(*) FROM {table_name} WHERE senador_id IS NULL"
    ).fetchone()[0]
    if unmatched:
        raise ValueError(f"dim_senadores contains {unmatched} seats without Senado mappings")

    seat_counts = dict(
        conn.execute(
            f"SELECT seat_type, COUNT(*) FROM {table_name} GROUP BY seat_type"
        ).fetchall()
    )
    if seat_counts != {"MR": 64, "FM": 32, "RP": 32}:
        raise ValueError(f"Unexpected seat split: {seat_counts}")

    duplicate_mappings = conn.execute(
        f"""
        SELECT COUNT(*) FROM (
            SELECT senador_id
            FROM {table_name}
            WHERE senador_id IS NOT NULL
            GROUP BY senador_id
            HAVING COUNT(*) > 1
        )
        """
    ).fetchone()[0]
    if duplicate_mappings:
        raise ValueError(f"{duplicate_mappings} Senado identities map to multiple seats")

    weak_matches = conn.execute(
        f"""
        SELECT COUNT(*)
        FROM {table_name}
        WHERE match_method = 'approximate_tokens'
          AND (match_score IS NULL OR match_score < 0.67)
        """
    ).fetchone()[0]
    if weak_matches:
        raise ValueError(f"{weak_matches} approximate mappings fall below the threshold")


def materialize_dim_senadores(
    conn: sqlite3.Connection,
    integration_path: Path = INTEGRACION_PATH,
) -> pd.DataFrame:
    if not integration_path.exists():
        raise FileNotFoundError(f"Official integration file not found: {integration_path}")

    dim = build_dim_senadores(conn, integration_path)
    if len(dim) != 128 or not dim["senador_seat_id"].is_unique:
        raise ValueError("Candidate dimension failed pre-insert row/ID validation")

    next_table = "dim_senadores_next"
    conn.execute(f"DROP TABLE IF EXISTS {next_table}")
    conn.execute(TABLE_SCHEMA.format(table_name=next_table))
    try:
        dim.to_sql(next_table, conn, if_exists="append", index=False)
        validate_dim_senadores(conn, next_table)
    except Exception:
        conn.execute(f"DROP TABLE IF EXISTS {next_table}")
        conn.commit()
        raise

    conn.execute("DROP TABLE IF EXISTS dim_senadores")
    conn.execute(f"ALTER TABLE {next_table} RENAME TO dim_senadores")
    for statement in INDEX_SCHEMA.strip().split(";"):
        if statement.strip():
            conn.execute(statement)
    validate_dim_senadores(conn)
    conn.commit()
    return dim


def main() -> None:
    parser = argparse.ArgumentParser(description="Build dim_senadores identity mappings.")
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--integration", type=Path, default=INTEGRACION_PATH)
    args = parser.parse_args()

    with sqlite3.connect(args.db) as conn:
        dim = materialize_dim_senadores(conn, args.integration)

    counts = dim["match_method"].value_counts().to_dict()
    print(f"Built dim_senadores: {len(dim)} official seats")
    print(f"Mapping QA: {counts}")


if __name__ == "__main__":
    main()
