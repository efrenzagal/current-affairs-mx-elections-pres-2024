"""Build the compact municipality -> federal-district browser index."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "election_data.db"
DEFAULT_OUTPUT = ROOT / "web/public/data/federal-district-lookup.json"


def build_index(db_path: Path) -> dict:
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        rows = conn.execute(
            """
            SELECT
                id_estado,
                nombre_estado,
                id_municipio,
                municipio,
                id_distrito_federal
            FROM dim_geography
            WHERE election_id = 'DIP_MR_2024'
              AND municipio IS NOT NULL
              AND id_distrito_federal > 0
            GROUP BY id_estado, nombre_estado, id_municipio, municipio,
                     id_distrito_federal
            ORDER BY id_estado, municipio, id_distrito_federal
            """
        ).fetchall()

    states: dict[int, dict] = {}
    municipalities: dict[tuple[int, int | None, str], dict] = {}
    for state_id, state_name, municipality_id, municipality_name, district in rows:
        state = states.setdefault(
            state_id,
            {"id": state_id, "name": state_name, "municipalities": []},
        )
        key = (state_id, municipality_id, municipality_name)
        municipality = municipalities.get(key)
        if municipality is None:
            municipality = {
                "id": municipality_id,
                "name": municipality_name,
                "districts": [],
            }
            municipalities[key] = municipality
            state["municipalities"].append(municipality)
        municipality["districts"].append(district)

    return {
        "schemaVersion": 1,
        "electionId": "DIP_MR_2024",
        "source": "INE election geography loaded in dim_geography",
        "states": list(states.values()),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    payload = build_index(args.db)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    municipality_count = sum(len(state["municipalities"]) for state in payload["states"])
    print(f"Wrote {args.output} ({len(payload['states'])} states, {municipality_count} municipalities)")


if __name__ == "__main__":
    main()

