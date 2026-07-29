"""
Electoral Ingest — clean parquets → SQLite
==========================================
Loads clean per-cycle parquets into election_data.db.

This file never touches raw CSVs. Per-cycle CSV → Parquet extraction lives in
raw_electoral_data_converters/. This script only reads from
data/electoral_data_clean/ from here on.

Once SQLite is populated, run ingestion/electoral_materialize.py to build the Parquet
files Streamlit reads (per-election views + the multi-year timeseries).

Usage:
    python -m ingestion.electoral_ingest              # full clean rebuild
    python -m ingestion.electoral_ingest --year 2000  # replace one cycle only
"""

import argparse
import sqlite3
import pandas as pd
from pathlib import Path
from typing import Optional

from ingestion.shared import CANONICAL_ESTADO_NOMBRES, DB_PATH, canonical_estado


# ── Election registry ──────────────────────────────────────────────────────────

# Each entry now carries its own clean_dir, since each cycle's notebook writes
# to a separate folder (data/clean_2024, data/clean_2018, ...) and the column
# layouts differ across cycles -- see SCHEMA_MAP below for how ingest_election
# adapts to that per election_id.
# Keyed by election_id so any lookup by ID is unambiguous.
# 2015 and 2021 are midterm cycles — diputados only, no presidente/senadores.
# 2018 MR/RP split is unconfirmed (single combined file, no separate RP source).
ELECTION_META = {
    "PRE_1994": {
        "year": 1994, "election_type": "PRE", "chamber": None,
        "seat_method": "direct", "total_seats": 1, "term_years": 6,
        "clean_dir": Path("data/electoral_data_clean/clean_1994"),
    },
    "PRE_2000": {
        "year": 2000, "election_type": "PRE", "chamber": None,
        "seat_method": "direct", "total_seats": 1, "term_years": 6,
        "clean_dir": Path("data/electoral_data_clean/clean_2000"),
    },
    "DIP_MR_2000": {
        "year": 2000, "election_type": "DIP", "chamber": "deputies",
        "seat_method": "fptp", "total_seats": 300, "term_years": 3,
        "clean_dir": Path("data/electoral_data_clean/clean_2000"),
    },
    "SEN_MR_2000": {
        "year": 2000, "election_type": "SEN", "chamber": "senate",
        "seat_method": "fptp", "total_seats": 96, "term_years": 6,
        "clean_dir": Path("data/electoral_data_clean/clean_2000"),
    },
    "PRE_2006": {
        "year": 2006, "election_type": "PRE", "chamber": None,
        "seat_method": "direct", "total_seats": 1, "term_years": 6,
        "clean_dir": Path("data/electoral_data_clean/clean_2006"),
    },
    "DIP_MR_2006": {
        "year": 2006, "election_type": "DIP", "chamber": "deputies",
        "seat_method": "fptp", "total_seats": 300, "term_years": 3,
        "clean_dir": Path("data/electoral_data_clean/clean_2006"),
    },
    "SEN_MR_2006": {
        "year": 2006, "election_type": "SEN", "chamber": "senate",
        "seat_method": "fptp", "total_seats": 96, "term_years": 6,
        "clean_dir": Path("data/electoral_data_clean/clean_2006"),
    },
    "PRE_2012": {
        "year": 2012, "election_type": "PRE", "chamber": None,
        "seat_method": "direct", "total_seats": 1, "term_years": 6,
        "clean_dir": Path("data/electoral_data_clean/clean_2012"),
    },
    "DIP_MR_2012": {
        "year": 2012, "election_type": "DIP", "chamber": "deputies",
        "seat_method": "fptp", "total_seats": 300, "term_years": 3,
        "clean_dir": Path("data/electoral_data_clean/clean_2012"),
    },
    "SEN_MR_2012": {
        "year": 2012, "election_type": "SEN", "chamber": "senate",
        "seat_method": "fptp", "total_seats": 96, "term_years": 6,
        "clean_dir": Path("data/electoral_data_clean/clean_2012"),
    },
    "DIP_MR_2015": {
        "year": 2015, "election_type": "DIP", "chamber": "deputies",
        "seat_method": "fptp", "total_seats": 300, "term_years": 3,
        "clean_dir": Path("data/electoral_data_clean/clean_2015"),
    },
    "PRE_2018": {
        "year": 2018, "election_type": "PRE", "chamber": None,
        "seat_method": "direct", "total_seats": 1, "term_years": 6,
        "clean_dir": Path("data/electoral_data_clean/clean_2018"),
    },
    "DIP_MR_2018": {
        "year": 2018, "election_type": "DIP", "chamber": "deputies",
        "seat_method": "fptp", "total_seats": 300, "term_years": 3,
        "clean_dir": Path("data/electoral_data_clean/clean_2018"),
    },
    "SEN_MR_2018": {
        "year": 2018, "election_type": "SEN", "chamber": "senate",
        "seat_method": "fptp", "total_seats": 96, "term_years": 6,
        "clean_dir": Path("data/electoral_data_clean/clean_2018"),
    },
    "DIP_MR_2021": {
        "year": 2021, "election_type": "DIP", "chamber": "deputies",
        "seat_method": "fptp", "total_seats": 300, "term_years": 3,
        "clean_dir": Path("data/electoral_data_clean/clean_2021"),
    },
    "PRE_2024": {
        "year": 2024, "election_type": "PRE", "chamber": None,
        "seat_method": "direct", "total_seats": 1, "term_years": 6,
        "clean_dir": Path("data/electoral_data_clean/clean_2024"),
    },
    "DIP_MR_2024": {
        "year": 2024, "election_type": "DIP", "chamber": "deputies",
        "seat_method": "fptp", "total_seats": 300, "term_years": 3,
        "clean_dir": Path("data/electoral_data_clean/clean_2024"),
    },
    "SEN_MR_2024": {
        "year": 2024, "election_type": "SEN", "chamber": "senate",
        "seat_method": "fptp", "total_seats": 96, "term_years": 6,
        "clean_dir": Path("data/electoral_data_clean/clean_2024"),
    },
}

# Per-cycle column mapping: canonical SQLite column -> source parquet column
# name for that cycle, or None if the field doesn't exist in that cycle's
# source data (NULL gets inserted). Keyed by year since both election types
# within a year share the same notebook/schema.
SCHEMA_MAP = {
    1994: {
        "geography": {
            "id_municipio":               None,
            "municipio":                  "MUNICIPIO",
            "id_distrito_federal":        "ID_DISTRITO",
            "cabecera_distrital_federal": "CABECERA",
            "circunscripcion":            None,
        },
        "casilla": {
            "acta_casilla_mec": "casilla_id",
            "urna_electronica": None,
            "lista_nominal":    None,
        },
        "fact": {
            "num_votos_validos":  None,
            "num_votos_nulos":    "VN",
            "num_votos_can_nreg": "CNR",
            "total_votos":        "TOTAL",
        },
    },
    2015: {
        "geography": {
            # No municipio/circunscripcion in the 2015 diputados file
            "id_municipio":               None,
            "municipio":                  None,
            "id_distrito_federal":        "ID_DISTRITO",
            "cabecera_distrital_federal": None,
            "circunscripcion":            None,
        },
        "casilla": {
            "acta_casilla_mec": "casilla_id",  # synthesised key (includes TIPO_ACTA), same approach as 2012
            "urna_electronica": None,          # not present in 2015
            "lista_nominal":    "LISTA_NOMINAL_CASILLA",
        },
        "fact": {
            "num_votos_validos":  None,        # no valid/invalid split in 2015
            "num_votos_nulos":    "VN",
            "num_votos_can_nreg": "CNR",
            "total_votos":        "TOTAL_VOTOS",
        },
    },
    2021: {
        "geography": {
            "id_municipio":               None,  # not present in 2021 diputados file
            "municipio":                  None,
            "id_distrito_federal":        "ID_DISTRITO",
            "cabecera_distrital_federal": "NOMBRE_DISTRITO",
            "circunscripcion":            None,
        },
        "casilla": {
            "acta_casilla_mec": "CLAVE_ACTA",  # real acta-level key from source, same approach as 2018
            "urna_electronica": None,          # not present in 2021
            "lista_nominal":    "LISTA_NOMINAL_CASILLA",
        },
        "fact": {
            "num_votos_validos":  None,        # no valid/invalid split in 2021
            "num_votos_nulos":    "VN",
            "num_votos_can_nreg": "CNR",
            "total_votos":        "TOTAL_VOTOS_CALCULADOS",
        },
    },
    2000: {
        "geography": {
            # Municipio is reconstructed by build_2000_municipio_crosswalk.py
            # from the 1994/2006 dimensions, with 2024 IDs where names match.
            "id_municipio":               "ID_MUNICIPIO",
            "municipio":                  "MUNICIPIO",
            "id_distrito_federal":        "ID_DISTRITO",
            "cabecera_distrital_federal": None,
            "circunscripcion":            None,
        },
        "casilla": {
            "acta_casilla_mec": "casilla_id",  # synthesised key, same approach as 2012
            "urna_electronica": None,          # not present in 2000
            "lista_nominal":    None,          # not present in the 2000 .dat columns
        },
        "fact": {
            "num_votos_validos":  None,          # no valid/invalid split in 2000
            "num_votos_nulos":    "NULOS",
            "num_votos_can_nreg": "CAND_NO_REGIS",
            "total_votos":        "TOTAL",
        },
    },
    2006: {
        "geography": {
            "id_municipio":               None,
            "municipio":                  "MUNICIPIO",
            "id_distrito_federal":        "ID_DISTRITO",
            "cabecera_distrital_federal": "NOMBRE_DISTRITO",
            "circunscripcion":            None,
        },
        "casilla": {
            "acta_casilla_mec": "casilla_id",
            "urna_electronica": None,
            "lista_nominal":    "LISTA_NOMINAL_CASILLA",
        },
        "fact": {
            "num_votos_validos":  "VALIDOS",
            "num_votos_nulos":    "VN",
            "num_votos_can_nreg": "CNR",
            "total_votos":        "TOTAL_VOTOS",
        },
    },
    2024: {
        "geography": {
            "id_municipio":              "ID_MUNICIPIO",
            "municipio":                 "MUNICIPIO",
            "id_distrito_federal":       "ID_DISTRITO_FEDERAL",
            "cabecera_distrital_federal":"CABECERA_DISTRITAL_FEDERAL",
            "circunscripcion":           "CIRCUNSCRIPCION",
        },
        "casilla": {
            "acta_casilla_mec": "ACTA_CASILLA-MEC",
            "urna_electronica": "URNA_ELECTRONICA",
            "lista_nominal":    "LISTA_NOMINAL",
        },
        "fact": {
            "num_votos_validos":  "NUM_VOTOS_VALIDOS",
            "num_votos_nulos":    "NUM_VOTOS_NULOS",
            "num_votos_can_nreg": "NUM_VOTOS_CAN_NREG",
            "total_votos":        "TOTAL_VOTOS",
        },
    },
    2018: {
        "geography": {
            # municipio derived from 2024 SEC lookup in csv_to_arrow_2018.py
            "id_municipio":               "ID_MUNICIPIO",
            "municipio":                  "MUNICIPIO",
            "id_distrito_federal":        "ID_DISTRITO",
            "cabecera_distrital_federal": "NOMBRE_DISTRITO",
            "circunscripcion":            None,
        },
        "casilla": {
            "acta_casilla_mec": "CLAVE_ACTA",
            "urna_electronica": None,
            "lista_nominal":    "LISTA_NOMINAL_CASILLA",
        },
        "fact": {
            # No valid-votes column in 2018; only nulos (VN), no-registrados
            # (CNR), and a pre-calculated total. Inserted as NULL per the
            # "insert NULL for missing fields" decision rather than derived.
            "num_votos_validos":  None,
            "num_votos_nulos":    "VN",
            "num_votos_can_nreg": "CNR",
            "total_votos":        "TOTAL_VOTOS_CALCULADOS",
        },
    },
    2012: {
        "geography": {
            # municipio derived from 2024 SEC lookup in csv_to_arrow_2012.py
            "id_municipio":               "ID_MUNICIPIO",
            "municipio":                  "MUNICIPIO",
            "id_distrito_federal":        "ID_DISTRITO",
            "cabecera_distrital_federal": None,  # no NOMBRE_DISTRITO in 2012
            "circunscripcion":            None,
        },
        "casilla": {
            # casilla_id is the synthesised key; used directly as acta identifier
            "acta_casilla_mec": "casilla_id",
            "urna_electronica": None,            # not present in 2012
            "lista_nominal":    "LISTA_NOMINAL_CASILLA",
        },
        "fact": {
            "num_votos_validos":  None,          # not present in 2012
            "num_votos_nulos":    "VN",          # renamed from NULOS in build_fact()
            "num_votos_can_nreg": "CNR",         # renamed from NO_REGISTRADOS in build_fact()
            "total_votos":        "TOTAL_VOTOS", # same column name as 2024
        },
    }
}


class ElectionWarehouse:
    """Manages SQLite schema and ingestion for election data."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.conn    = None
        self.cursor  = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.conn:
            if exc_type is None:
                self.conn.commit()
            else:
                self.conn.rollback()
            self.close()
        return False

    def connect(self):
        self.conn   = sqlite3.connect(self.db_path)
        self.cursor = self.conn.cursor()
        self.cursor.execute("PRAGMA journal_mode=WAL")
        self.cursor.execute("PRAGMA synchronous=NORMAL")
        print(f"✓ Connected to {self.db_path}")

    def close(self):
        if self.conn:
            self.conn.close()
            print(f"✓ Closed {self.db_path}")

    # ── Schema ─────────────────────────────────────────────────────────────────

    def create_schema(self):
        print("\n📋 Creating schema...")

        self.cursor.executescript("""
            CREATE TABLE IF NOT EXISTS dim_election (
                election_id    TEXT PRIMARY KEY,
                year           INTEGER NOT NULL,
                election_type  TEXT NOT NULL,
                chamber        TEXT,
                seat_method    TEXT NOT NULL,
                total_seats    INTEGER NOT NULL,
                term_years     INTEGER NOT NULL,
                created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS dim_geography (
                geo_id                    TEXT NOT NULL,
                election_id               TEXT NOT NULL,
                id_estado                 INTEGER NOT NULL,
                nombre_estado             TEXT NOT NULL,
                seccion                   INTEGER NOT NULL,
                id_municipio              INTEGER,
                municipio                 TEXT,
                id_distrito_federal       INTEGER,
                cabecera_distrital_federal TEXT,
                circunscripcion           INTEGER,
                created_at                TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (geo_id, election_id),
                FOREIGN KEY (election_id) REFERENCES dim_election(election_id)
            );

            CREATE TABLE IF NOT EXISTS dim_casilla (
                casilla_id       TEXT,
                election_id      TEXT NOT NULL,
                geo_id           TEXT NOT NULL,
                id_estado        INTEGER NOT NULL,
                seccion          INTEGER NOT NULL,
                acta_casilla_mec TEXT,
                tipo_casilla     TEXT,
                id_casilla       INTEGER,
                ext_contigua     INTEGER,
                lista_nominal    INTEGER,
                urna_electronica INTEGER,
                estatus_acta     TEXT,
                ruta_acta        TEXT,
                created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (election_id, casilla_id),
                FOREIGN KEY (election_id)          REFERENCES dim_election(election_id),
                FOREIGN KEY (geo_id, election_id)  REFERENCES dim_geography(geo_id, election_id)
            );

            CREATE TABLE IF NOT EXISTS dim_party (
                party_key    TEXT PRIMARY KEY,
                is_coalition BOOLEAN NOT NULL,
                members      TEXT,
                created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS dim_candidatos (
                candidato_id        INTEGER PRIMARY KEY AUTOINCREMENT,
                election_type       TEXT NOT NULL,
                party_key           TEXT NOT NULL,
                id_estado           INTEGER,
                nombre_estado       TEXT,
                id_distrito_federal INTEGER,
                candidate_name      TEXT,
                candidate_suplente  TEXT,
                partido_politico    TEXT,
                votacion_ganador    INTEGER,
                pct_ganador         REAL,
                created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(election_type, party_key, id_estado, id_distrito_federal, candidate_name)
            );

            CREATE TABLE IF NOT EXISTS dim_municipio_map_crosswalk (
                municipio_map_id       INTEGER PRIMARY KEY AUTOINCREMENT,
                election_id            TEXT NOT NULL,
                id_estado              INTEGER NOT NULL,
                nombre_estado          TEXT,
                source_municipio_id    INTEGER,
                source_municipio       TEXT NOT NULL,
                municipio_key          TEXT NOT NULL,
                inegi_cvegeo           TEXT,
                map_feature_id         TEXT,
                match_method           TEXT NOT NULL,
                review_status          TEXT,
                suggested_cvegeo       TEXT,
                suggested_municipio    TEXT,
                similarity             REAL,
                created_at             TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(election_id, id_estado, municipio_key, source_municipio_id),
                FOREIGN KEY (election_id) REFERENCES dim_election(election_id)
            );

            CREATE TABLE IF NOT EXISTS fact_casilla_vote (
                vote_id             INTEGER PRIMARY KEY AUTOINCREMENT,
                election_id         TEXT NOT NULL,
                casilla_id          TEXT NOT NULL,
                party_key           TEXT NOT NULL,
                votes               INTEGER NOT NULL DEFAULT 0,
                num_votos_validos   INTEGER,
                num_votos_nulos     INTEGER,
                num_votos_can_nreg  INTEGER,
                total_votos         INTEGER,
                created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (election_id) REFERENCES dim_election(election_id),
                FOREIGN KEY (party_key)   REFERENCES dim_party(party_key),
                UNIQUE(election_id, casilla_id, party_key)
            );

            CREATE INDEX IF NOT EXISTS idx_fact_election  ON fact_casilla_vote(election_id);
            CREATE INDEX IF NOT EXISTS idx_fact_party     ON fact_casilla_vote(party_key);
            CREATE INDEX IF NOT EXISTS idx_fact_geo       ON dim_casilla(geo_id);
            CREATE INDEX IF NOT EXISTS idx_casilla_elec   ON dim_casilla(election_id);
            CREATE INDEX IF NOT EXISTS idx_geo_state      ON dim_geography(id_estado);
            CREATE INDEX IF NOT EXISTS idx_candidatos_type ON dim_candidatos(election_type, id_estado, party_key);
            CREATE INDEX IF NOT EXISTS idx_mun_map_election ON dim_municipio_map_crosswalk(election_id, id_estado);
            CREATE INDEX IF NOT EXISTS idx_mun_map_cvegeo ON dim_municipio_map_crosswalk(inegi_cvegeo);
        """)

        self.conn.commit()
        print("✓ Schema created with indexes")

    # ── Ingestion ──────────────────────────────────────────────────────────────

    def delete_elections(self, election_ids: list[str]) -> None:
        """Delete selected elections without touching any other cycle."""
        for election_id in election_ids:
            print(f"  ♻️  Removing existing {election_id} rows...")
            # Delete children before parents; the schema intentionally does
            # not rely on ON DELETE CASCADE.
            self.cursor.execute(
                "DELETE FROM fact_casilla_vote WHERE election_id = ?", (election_id,)
            )
            self.cursor.execute(
                "DELETE FROM dim_casilla WHERE election_id = ?", (election_id,)
            )
            self.cursor.execute(
                "DELETE FROM dim_geography WHERE election_id = ?", (election_id,)
            )
            self.cursor.execute(
                "DELETE FROM dim_election WHERE election_id = ?", (election_id,)
            )
        self.conn.commit()

    def ingest_election(self, election_id: str, election_meta: dict, parquet_dir: Path = None):
        print(f"\n🗳️  Ingesting {election_id}...")

        self.cursor.execute(
            "SELECT election_id FROM dim_election WHERE election_id = ?", (election_id,)
        )
        dim_exists = self.cursor.fetchone() is not None

        self.cursor.execute(
            "SELECT 1 FROM fact_casilla_vote WHERE election_id = ? LIMIT 1", (election_id,)
        )
        facts_exist = self.cursor.fetchone() is not None

        if dim_exists and facts_exist:
            print(f"  ⚠️  {election_id} already exists, skipping...")
            return
        if dim_exists or facts_exist:
            # Partial prior ingest (e.g. a run that crashed or was killed
            # between dim_election and fact_casilla_vote, or across separate
            # invocations rather than one atomic transaction). Clear out
            # whatever's there for this election_id so the re-run starts clean
            # instead of hitting a UNIQUE collision on fact_casilla_vote.
            print(f"  ⚠️  {election_id} found in a partial state — clearing before re-ingest...")
            self.cursor.execute("DELETE FROM fact_casilla_vote WHERE election_id = ?", (election_id,))
            self.cursor.execute("DELETE FROM dim_casilla       WHERE election_id = ?", (election_id,))
            self.cursor.execute("DELETE FROM dim_election      WHERE election_id = ?", (election_id,))

        # Each election now carries its own clean_dir (cycles write to separate
        # folders); an explicit parquet_dir argument still overrides this if
        # passed, for backwards compatibility / manual reruns.
        parquet_dir = parquet_dir or election_meta["clean_dir"]

        year = election_meta["year"]
        if year not in SCHEMA_MAP:
            raise ValueError(
                f"[{election_id}] No SCHEMA_MAP entry for year={year}. "
                f"Add one before ingesting this cycle."
            )
        geo_map     = SCHEMA_MAP[year]["geography"]
        casilla_map = SCHEMA_MAP[year]["casilla"]
        fact_map    = SCHEMA_MAP[year]["fact"]

        def get_mapped(row, canonical_col, col_map, cast=None):
            """Look up the source column for `canonical_col` via col_map; None
            in the map (or a missing/NaN source value) yields a NULL insert."""
            src_col = col_map.get(canonical_col)
            if src_col is None:
                return None
            val = row.get(src_col)
            if pd.isna(val):
                return None
            return cast(val) if cast else val

        # 1. dim_election
        self.cursor.execute(
            """INSERT INTO dim_election
               (election_id, year, election_type, chamber, seat_method, total_seats, term_years)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                election_id,
                election_meta["year"],
                election_meta["election_type"],
                election_meta.get("chamber"),
                election_meta["seat_method"],
                election_meta["total_seats"],
                election_meta["term_years"],
            ),
        )
        print("  ✓ Election metadata")

        # 2. dim_geography — one row per (geo_id, election_id).
        # Some converters emit multiple rows for geo_id XX_0000 (seccion=0 state
        # placeholder, one per district). Keep the first occurrence.
        df_geo = pd.read_parquet(parquet_dir / "dim_geography.parquet")
        df_geo = df_geo.drop_duplicates(subset=["geo_id"])
        geo_rows = [
            (
                row["geo_id"],
                election_id,
                int(row["ID_ESTADO"]),
                canonical_estado(row["ID_ESTADO"], row["NOMBRE_ESTADO"]),
                int(row["SECCION"]),
                get_mapped(row, "id_municipio", geo_map, int),
                get_mapped(row, "municipio", geo_map),
                get_mapped(row, "id_distrito_federal", geo_map, int),
                get_mapped(row, "cabecera_distrital_federal", geo_map),
                get_mapped(row, "circunscripcion", geo_map, int),
            )
            for _, row in df_geo.iterrows()
        ]
        self.cursor.executemany(
            """INSERT INTO dim_geography
               (geo_id, election_id, id_estado, nombre_estado, seccion, id_municipio,
                municipio, id_distrito_federal, cabecera_distrital_federal, circunscripcion)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            geo_rows,
        )
        print(f"  ✓ Geography ({len(df_geo):,} sections)")

        # 3. dim_casilla — bulk insert
        df_casilla = pd.read_parquet(parquet_dir / "dim_casilla.parquet")
        df_casilla = df_casilla[df_casilla["election_id"] == election_id]
        casilla_rows = [
            (
                row["casilla_id"],
                row["election_id"],
                row["geo_id"],
                int(row["ID_ESTADO"]),
                int(row["SECCION"]),
                get_mapped(row, "acta_casilla_mec", casilla_map),
                row.get("TIPO_CASILLA"),
                int(row["ID_CASILLA"])      if pd.notna(row.get("ID_CASILLA"))   else None,
                int(row["EXT_CONTIGUA"])    if pd.notna(row.get("EXT_CONTIGUA")) else None,
                get_mapped(row, "lista_nominal", casilla_map, int),
                get_mapped(row, "urna_electronica", casilla_map, int),
                row.get("ESTATUS_ACTA"),
                row.get("RUTA_ACTA"),
            )
            for _, row in df_casilla.iterrows()
        ]
        self.cursor.executemany(
            """INSERT INTO dim_casilla
               (casilla_id, election_id, geo_id, id_estado, seccion, acta_casilla_mec,
                tipo_casilla, id_casilla, ext_contigua, lista_nominal, urna_electronica,
                estatus_acta, ruta_acta)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            casilla_rows,
        )
        print(f"  ✓ Casillas ({len(df_casilla):,} rows)")

        # 4. dim_party — bulk insert
        df_party = pd.read_parquet(parquet_dir / "dim_party.parquet")
        party_rows = [
            (row["party_key"], bool(row["is_coalition"]), row.get("members"))
            for _, row in df_party.iterrows()
        ]
        self.cursor.executemany(
            "INSERT OR IGNORE INTO dim_party (party_key, is_coalition, members) VALUES (?, ?, ?)",
            party_rows,
        )
        print(f"  ✓ Parties ({len(df_party):,} entries)")

        # 5. fact_casilla_vote — bulk insert (largest table; executemany is critical here)
        fact_path = parquet_dir / "fact_casilla_vote.parquet"
        if fact_path.is_dir():
            election_fact_path = fact_path / f"election_id={election_id}"
            if not election_fact_path.exists():
                print(f"  ⚠️  No partition found for {election_id}")
                return
            df_fact = pd.read_parquet(election_fact_path)
            df_fact["election_id"] = election_id
            df_fact = df_fact.drop_duplicates(subset=["casilla_id", "party_key"])
        else:
            df_fact = pd.read_parquet(fact_path)
            df_fact = df_fact[df_fact["election_id"] == election_id]

        fact_rows = [
            (
                row["election_id"],
                row["casilla_id"],
                row["party_key"],
                int(row["votes"]),
                get_mapped(row, "num_votos_validos", fact_map, int),
                get_mapped(row, "num_votos_nulos", fact_map, int),
                get_mapped(row, "num_votos_can_nreg", fact_map, int),
                get_mapped(row, "total_votos", fact_map, int),
            )
            for _, row in df_fact.iterrows()
        ]
        self.cursor.executemany(
            """INSERT INTO fact_casilla_vote
               (election_id, casilla_id, party_key, votes, num_votos_validos,
                num_votos_nulos, num_votos_can_nreg, total_votos)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            fact_rows,
        )
        print(f"  ✓ Votes ({len(df_fact):,} rows)")

        self.conn.commit()

    def ingest_candidatos(self, clean_dirs: list[Path]):
        """
        Load dim_candidatos.parquet (built once per cycle by the notebook) into
        SQLite. Not election-scoped — call once per ingest run, scanning every
        distinct clean_dir across ELECTION_META so each cycle's candidatos file
        gets a chance to load. No FK to dim_election, since election_type is a
        one-to-many relationship to election_id (e.g. 'PRE' could span several
        years' worth of dim_election rows).

        Idempotency is per-row via the UNIQUE(election_type, party_key,
        id_estado, id_distrito_federal, candidate_name) constraint + INSERT OR
        IGNORE, NOT a single "table already has rows -> skip everything" check
        -- that check used to silently block every cycle after the first one
        ever populated the table.
        """
        print(f"\n🧑‍💼 Ingesting candidatos...")

        for parquet_dir in clean_dirs:
            path = parquet_dir / "dim_candidatos.parquet"
            if not path.exists():
                print(f"  ⚠️  {path} not found, skipping")
                continue

            df = pd.read_parquet(path)
            if df.empty:
                print(f"  ⚠️  {path} is empty, skipping")
                continue

            rows = [
                (
                    row.get("election_type"),
                    row.get("party_key"),
                    int(row["id_estado"]) if pd.notna(row.get("id_estado")) else None,
                    row.get("nombre_estado"),
                    int(row["id_distrito_federal"]) if pd.notna(row.get("id_distrito_federal")) else None,
                    row.get("candidate_name"),
                    row.get("candidate_suplente"),
                    row.get("partido_politico"),
                    int(row["votacion_ganador"]) if pd.notna(row.get("votacion_ganador")) else None,
                    float(str(row["pct_ganador"]).replace("%", "").strip())
                    if pd.notna(row.get("pct_ganador")) else None,
                )
                for _, row in df.iterrows()
            ]
            before = self.cursor.execute("SELECT COUNT(*) FROM dim_candidatos").fetchone()[0]
            self.cursor.executemany(
                """INSERT OR IGNORE INTO dim_candidatos
                   (election_type, party_key, id_estado, nombre_estado, id_distrito_federal,
                    candidate_name, candidate_suplente, partido_politico, votacion_ganador, pct_ganador)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
            after = self.cursor.execute("SELECT COUNT(*) FROM dim_candidatos").fetchone()[0]
            print(f"  ✓ {path.parent.name}: {len(df):,} rows read, {after - before:,} new")

        self.conn.commit()

    def query(self, sql: str, params=()) -> pd.DataFrame:
        return pd.read_sql_query(sql, self.conn, params=params)

    def stats(self):
        print("\n📊 Database Statistics")
        print("─" * 50)
        for label, sql in {
            "Elections":     "SELECT COUNT(*) FROM dim_election",
            "States":        "SELECT COUNT(DISTINCT id_estado) FROM dim_geography",
            "Sections":      "SELECT COUNT(*) FROM dim_geography",
            "Polling booths":"SELECT COUNT(*) FROM dim_casilla",
            "Parties":       "SELECT COUNT(*) FROM dim_party",
            "Candidatos":    "SELECT COUNT(*) FROM dim_candidatos",
            "Vote records":  "SELECT COUNT(*) FROM fact_casilla_vote",
            "Total votes":   "SELECT SUM(votes) FROM fact_casilla_vote",
        }.items():
            result = self.cursor.execute(sql).fetchone()[0] or 0
            print(f"  {label:<20} {result:>15,}")

        df = self.query(
            "SELECT election_id, COUNT(*) as votes FROM fact_casilla_vote "
            "GROUP BY election_id ORDER BY election_id DESC"
        )
        print("\n  By election:")
        for _, row in df.iterrows():
            print(f"    {row['election_id']:<15} {row['votes']:>15,}")


def run_ingest(
    db_path: str = DB_PATH,
    parquet_dir: Path = None,
    year: Optional[int] = None,
):
    import os
    print("=" * 55)
    print("STEP 1 — INGEST: parquets → SQLite")
    print("=" * 55)
    if parquet_dir is not None:
        print(f"  (--clean-dir override: {parquet_dir} used for ALL elections)\n")

    selected = {
        election_id: meta
        for election_id, meta in ELECTION_META.items()
        if year is None or meta["year"] == year
    }
    if not selected:
        valid_years = sorted({meta["year"] for meta in ELECTION_META.values()})
        raise ValueError(f"No elections registered for year {year}; choose from {valid_years}")

    if year is None:
        # Full mode starts from a clean slate so normalization changes cannot
        # leave stale rows in other cycles.
        for suffix in ("", "-wal", "-shm"):
            p = db_path + suffix
            if os.path.exists(p):
                os.remove(p)
                print(f"  Removed existing {p}")
    else:
        print(f"  Targeted cycle refresh: {year} ({', '.join(selected)})")

    with ElectionWarehouse(db_path=db_path) as wh:
        wh.create_schema()
        if year is not None:
            wh.delete_elections(list(selected))
        for election_id, meta in selected.items():
            wh.ingest_election(election_id, meta, parquet_dir)


        # Collect every distinct clean_dir referenced by ELECTION_META (or just
        # the override, if one was passed) so candidatos get a chance to load
        # from each cycle's folder, not just the first one.
        if parquet_dir is not None:
            clean_dirs = [parquet_dir]
        else:
            seen = set()
            clean_dirs = []
            for meta in selected.values():
                d = meta["clean_dir"]
                if d not in seen:
                    seen.add(d)
                    clean_dirs.append(d)

        wh.ingest_candidatos(clean_dirs)
        wh.stats()
    print("\n✓ Ingest complete")



# ══════════════════════════════════════════════════════════════════════════════
# CLI entry point
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Election data ingest: clean parquets -> SQLite")
    parser.add_argument(
        "--clean-dir", default=None,
        help=(
            "Override: use this single parquet dir for ALL elections instead "
            "of each election's per-cycle clean_dir from ELECTION_META. "
            "Leave unset for normal multi-cycle ingestion."
        ),
    )
    parser.add_argument("--db", default=DB_PATH, help="SQLite path")
    parser.add_argument(
        "--year",
        type=int,
        default=None,
        choices=sorted({meta["year"] for meta in ELECTION_META.values()}),
        help="Replace only elections in this cycle; omit for a full clean rebuild",
    )
    args = parser.parse_args()

    run_ingest(
        db_path=args.db,
        parquet_dir=Path(args.clean_dir) if args.clean_dir else None,
        year=args.year,
    )
