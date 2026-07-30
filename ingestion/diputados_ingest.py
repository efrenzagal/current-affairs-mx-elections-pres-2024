"""Build the official-deputy to Gaceta identity bridge.

``dim_diputados`` has one row per final 2024 Cámara de Diputados seat
assignment published by INE. It preserves the official seat attributes and
maps the elected candidate to ``dim_gaceta_deputy`` only when the normalized
name match is reliable.

Usage:
    python -m ingestion.diputados_ingest
    python -m ingestion.diputados_ingest --db path/to/election_data.db
"""

from __future__ import annotations

import argparse
import hashlib
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
ELECTION_ID = "DIP_MR_2024"
LEGISLATURE = 66


TABLE_SCHEMA = """
CREATE TABLE {table_name} (
    diputado_id                 TEXT PRIMARY KEY,
    election_id                 TEXT NOT NULL,
    legislature                 INTEGER NOT NULL,
    seat_type                   TEXT NOT NULL CHECK (seat_type IN ('MR', 'RP')),
    party_key                   TEXT NOT NULL,
    id_estado                   INTEGER,
    nombre_estado               TEXT,
    id_distrito_federal         INTEGER,
    circunscripcion             INTEGER,
    numero_lista                INTEGER,
    identidad_sexo_generica     INTEGER,
    ine_candidate_name          TEXT,
    ine_substitute_name         TEXT,
    source_name_role            TEXT NOT NULL,
    display_name                TEXT,
    normalized_name_key         TEXT,
    gaceta_deputy_id            TEXT,
    gaceta_deputy_name          TEXT,
    match_method                TEXT NOT NULL,
    match_score                 REAL,
    source_file                 TEXT NOT NULL,
    created_at                  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (gaceta_deputy_id) REFERENCES dim_gaceta_deputy(deputy_id)
)
"""

INDEX_SCHEMA = """
CREATE INDEX idx_dim_diputados_gaceta
    ON dim_diputados(gaceta_deputy_id);

CREATE INDEX idx_dim_diputados_seat
    ON dim_diputados(election_id, seat_type, party_key);
"""


def _integer_or_none(value: object) -> int | None:
    return int(value) if pd.notna(value) else None


def _source_file_label(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path.resolve())


def diputado_seat_key(row: pd.Series) -> str:
    """Stable natural seat key that does not depend on candidate spelling."""
    candidature_type = str(row["TIPO_DE_CANDIDATURA"])
    if candidature_type == "DIP_MR":
        return "|".join([
            ELECTION_ID,
            "MR",
            str(_integer_or_none(row["ID_ESTADO"]) or 0),
            str(_integer_or_none(row["ID_DISTRITO_FEDERAL"]) or 0),
        ])
    if candidature_type == "DIP_RP":
        return "|".join([
            ELECTION_ID,
            "RP",
            str(_integer_or_none(row["CIRCUNSCRIPCION"]) or 0),
            str(row["PARTIDO_POLITICO"]).strip(),
            str(_integer_or_none(row["NUMERO_LISTA"]) or 0),
            str(_integer_or_none(row["IDENTIDAD_SEXO_GENERICA"]) or 0),
        ])
    raise ValueError(f"Unsupported candidature type: {candidature_type}")


def diputado_id_for_row(row: pd.Series) -> str:
    digest = hashlib.sha1(diputado_seat_key(row).encode("utf-8")).hexdigest()[:12].upper()
    return f"DIP_{digest}"


def load_official_deputy_rows(path: Path = INTEGRACION_PATH) -> pd.DataFrame:
    df = pd.read_csv(path, encoding="utf-8-sig")
    return df[df["TIPO_DE_CANDIDATURA"].isin(["DIP_MR", "DIP_RP"])].copy()


def load_gaceta_roster(conn: sqlite3.Connection, legislature: int = LEGISLATURE) -> pd.DataFrame:
    return pd.read_sql_query(
        """
        SELECT DISTINCT f.deputy_id, d.deputy_name
        FROM fact_gaceta_deputy_vote AS f
        JOIN dim_gaceta_vote AS v ON v.gaceta_vote_id = f.gaceta_vote_id
        JOIN dim_gaceta_deputy AS d ON d.deputy_id = f.deputy_id
        WHERE v.legislature = ?
        ORDER BY d.deputy_name
        """,
        conn,
        params=(int(legislature),),
    )


def build_dim_diputados(
    conn: sqlite3.Connection,
    integration_path: Path = INTEGRACION_PATH,
) -> pd.DataFrame:
    official = load_official_deputy_rows(integration_path)
    roster = load_gaceta_roster(conn)
    roster_by_name = dict(zip(roster["deputy_name"], roster["deputy_id"]))
    roster_names = list(roster_by_name)

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
        identity_name = candidate_name or substitute_name
        source_name_role = "titular" if candidate_name else "suplente"
        matched_name = None
        matched_id = None
        match_score = None

        if not identity_name:
            match_method = "missing_source_name"
        else:
            matched_name, quality = match_person_name(identity_name, roster_names)
            if matched_name is None:
                match_method = "unmatched"
            else:
                matched_id = roster_by_name[matched_name]
                match_score = person_name_similarity(identity_name, matched_name)
                match_method = "exact_tokens" if quality == "exact" else "approximate_tokens"

        is_mr = source["TIPO_DE_CANDIDATURA"] == "DIP_MR"
        rows.append({
            "diputado_id": diputado_id_for_row(source),
            "election_id": ELECTION_ID,
            "legislature": LEGISLATURE,
            "seat_type": "MR" if is_mr else "RP",
            "party_key": str(source["PARTIDO_POLITICO"]).strip(),
            "id_estado": _integer_or_none(source["ID_ESTADO"]),
            "nombre_estado": (
                str(source["NOMBRE_ESTADO"]).strip()
                if pd.notna(source["NOMBRE_ESTADO"])
                else None
            ),
            "id_distrito_federal": _integer_or_none(source["ID_DISTRITO_FEDERAL"]),
            "circunscripcion": _integer_or_none(source["CIRCUNSCRIPCION"]),
            "numero_lista": _integer_or_none(source["NUMERO_LISTA"]),
            "identidad_sexo_generica": _integer_or_none(source["IDENTIDAD_SEXO_GENERICA"]),
            "ine_candidate_name": candidate_name or None,
            "ine_substitute_name": substitute_name or None,
            "source_name_role": source_name_role,
            "display_name": display_person_name(identity_name) or None,
            "normalized_name_key": (
                "|".join(person_name_tokens(identity_name))
                if identity_name
                else None
            ),
            "gaceta_deputy_id": matched_id,
            "gaceta_deputy_name": matched_name,
            "match_method": match_method,
            "match_score": match_score,
            "source_file": _source_file_label(integration_path),
        })
    return pd.DataFrame(rows)


def validate_dim_diputados(
    conn: sqlite3.Connection,
    table_name: str = "dim_diputados",
) -> None:
    if table_name not in {"dim_diputados", "dim_diputados_next"}:
        raise ValueError(f"Unexpected table name: {table_name}")

    row_count = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
    if row_count != 500:
        raise ValueError(f"{table_name} must contain 500 seats; found {row_count}")

    duplicate_ids = conn.execute(
        f"""
        SELECT COUNT(*) FROM (
            SELECT diputado_id
            FROM {table_name}
            GROUP BY diputado_id
            HAVING COUNT(*) > 1
        )
        """
    ).fetchone()[0]
    if duplicate_ids:
        raise ValueError(f"dim_diputados contains {duplicate_ids} duplicate IDs")

    orphan_matches = conn.execute(
        f"""
        SELECT COUNT(*)
        FROM {table_name} AS d
        WHERE d.gaceta_deputy_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1
              FROM dim_gaceta_deputy AS g
              WHERE g.deputy_id = d.gaceta_deputy_id
          )
        """
    ).fetchone()[0]
    if orphan_matches:
        raise ValueError(f"dim_diputados contains {orphan_matches} orphan Gaceta mappings")

    seat_counts = dict(
        conn.execute(
            f"SELECT seat_type, COUNT(*) FROM {table_name} GROUP BY seat_type"
        ).fetchall()
    )
    if seat_counts != {"MR": 300, "RP": 200}:
        raise ValueError(f"Unexpected seat split: {seat_counts}")

    duplicate_mappings = conn.execute(
        f"""
        SELECT COUNT(*) FROM (
            SELECT gaceta_deputy_id
            FROM {table_name}
            WHERE gaceta_deputy_id IS NOT NULL
            GROUP BY gaceta_deputy_id
            HAVING COUNT(*) > 1
        )
        """
    ).fetchone()[0]
    if duplicate_mappings:
        raise ValueError(f"{duplicate_mappings} Gaceta identities map to multiple seats")

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


def materialize_dim_diputados(
    conn: sqlite3.Connection,
    integration_path: Path = INTEGRACION_PATH,
) -> pd.DataFrame:
    if not integration_path.exists():
        raise FileNotFoundError(f"Official integration file not found: {integration_path}")

    dim = build_dim_diputados(conn, integration_path)
    if len(dim) != 500 or not dim["diputado_id"].is_unique:
        raise ValueError("Candidate dimension failed pre-insert row/ID validation")

    next_table = "dim_diputados_next"
    conn.execute(f"DROP TABLE IF EXISTS {next_table}")
    conn.execute(TABLE_SCHEMA.format(table_name=next_table))
    try:
        dim.to_sql(next_table, conn, if_exists="append", index=False)
        validate_dim_diputados(conn, next_table)
    except Exception:
        conn.execute(f"DROP TABLE IF EXISTS {next_table}")
        conn.commit()
        raise

    conn.execute("DROP TABLE IF EXISTS dim_diputados")
    conn.execute(f"ALTER TABLE {next_table} RENAME TO dim_diputados")
    for statement in INDEX_SCHEMA.strip().split(";"):
        if statement.strip():
            conn.execute(statement)
    validate_dim_diputados(conn)
    conn.commit()
    return dim


def main() -> None:
    parser = argparse.ArgumentParser(description="Build dim_diputados identity mappings.")
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--integration", type=Path, default=INTEGRACION_PATH)
    args = parser.parse_args()

    with sqlite3.connect(args.db) as conn:
        dim = materialize_dim_diputados(conn, args.integration)

    counts = dim["match_method"].value_counts().to_dict()
    print(f"Built dim_diputados: {len(dim)} official seats")
    print(f"Mapping QA: {counts}")


if __name__ == "__main__":
    main()
