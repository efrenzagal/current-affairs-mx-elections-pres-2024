"""Attach each LXVI seat to the margin it was won by.

The INE integration CSV reports, per contested seat, who won it and by how much.
That file is raw source under the gitignored `data/electoral_data_raw/` tree,
and the static web exporter used to parse it directly at build time -- meaning
the published site could be built from a file the warehouse had never seen or
validated.

This resolves those rows onto seat ids once, so consumers join on `seat_id`
rather than re-deriving the chamber-specific key. Only seats actually won in a
contest get a row: proportional-representation seats have no winning margin,
and a seat with no row is reported as having no result rather than a zero one.
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DB_PATH = ROOT / "election_data.db"
INTEGRATION_PATH = (
    ROOT
    / "data"
    / "electoral_data_raw"
    / "raw_2024"
    / "PRESIDENCIA_2024"
    / "CSV"
    / "INTEGRACION_CARGOS_PEF_2024.csv"
)
INTEGRATION_PUBLIC_URL = (
    "https://ine.mx/integracion-de-diputaciones-y-senadurias-pef-2023-2024/"
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS fact_legislature_66_seat_election_result (
    chamber        TEXT NOT NULL CHECK (chamber IN ('DIP', 'SEN')),
    seat_id        TEXT NOT NULL,
    district_seat  TEXT,
    election_actor TEXT,
    winning_votes  INTEGER NOT NULL,
    winning_pct    REAL NOT NULL,
    source_url     TEXT NOT NULL,
    created_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (chamber, seat_id)
);
"""

# A Camara district is identified by its federal district number and only MR
# seats have one; a Senado seat is identified by its position on the state list,
# and both MR and first-minority seats carry a result.
CHAMBER_KEYS = {
    "DIP": {
        "candidature": "DIP_MR",
        "csv_key": ("ID_ESTADO", "ID_DISTRITO_FEDERAL"),
        "seat_sql": """
            SELECT diputado_id AS seat_id, id_estado, id_distrito_federal AS number
            FROM dim_diputados
            WHERE legislature = 66 AND seat_type = 'MR'
        """,
    },
    "SEN": {
        "candidature": "SEN_MR",
        "csv_key": ("ID_ESTADO", "NUMERO_LISTA"),
        "seat_sql": """
            SELECT senador_seat_id AS seat_id, id_estado, numero_lista AS number
            FROM dim_senadores
            WHERE legislature = 66 AND seat_type IN ('MR', 'FM')
        """,
    },
}


def load_results(chamber: str, path: Path = INTEGRATION_PATH) -> dict[tuple, dict]:
    """`(id_estado, number)` -> the winning margin the INE reported for it."""
    config = CHAMBER_KEYS[chamber]
    state_column, number_column = config["csv_key"]
    results: dict[tuple, dict] = {}
    with path.open(encoding="utf-8-sig", newline="") as source:
        for row in csv.DictReader(source):
            if row["TIPO_DE_CANDIDATURA"] != config["candidature"]:
                continue
            pct = row["PORCENTAJE_VOTACION_GANADOR"].replace("%", "").strip()
            results[(int(row[state_column]), int(row[number_column]))] = {
                # Only the Camara names a district seat; the Senado has none.
                "districtSeat": (
                    row["CABECERA_DISTRITAL_FEDERAL"].strip() or None
                    if chamber == "DIP"
                    else None
                ),
                "electionActor": row["NOMBRE_ACTOR_POLITICO"].strip() or None,
                "winningVotes": int(row["VOTACION_GANADOR"]),
                "winningPct": float(pct),
            }
    return results


def materialize(db_path: Path = DB_PATH, path: Path = INTEGRATION_PATH) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA)
        conn.row_factory = sqlite3.Row
        for chamber, config in CHAMBER_KEYS.items():
            results = load_results(chamber, path)
            records = []
            unmatched = 0
            for seat in conn.execute(config["seat_sql"]):
                result = results.get((seat["id_estado"], seat["number"]))
                if result is None:
                    unmatched += 1
                    continue
                records.append(
                    (
                        chamber,
                        seat["seat_id"],
                        result["districtSeat"],
                        result["electionActor"],
                        result["winningVotes"],
                        result["winningPct"],
                        INTEGRATION_PUBLIC_URL,
                    )
                )
            with conn:
                conn.execute(
                    "DELETE FROM fact_legislature_66_seat_election_result"
                    " WHERE chamber = ?",
                    (chamber,),
                )
                conn.executemany(
                    """
                    INSERT INTO fact_legislature_66_seat_election_result (
                        chamber, seat_id, district_seat, election_actor,
                        winning_votes, winning_pct, source_url
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    records,
                )
            note = f", {unmatched} contested seats unmatched" if unmatched else ""
            print(f"  {chamber}: {len(records)} seat results{note}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--csv", default=str(INTEGRATION_PATH))
    args = parser.parse_args()
    materialize(Path(args.db), Path(args.csv))


if __name__ == "__main__":
    main()
