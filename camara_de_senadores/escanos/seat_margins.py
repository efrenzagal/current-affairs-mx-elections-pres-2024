"""Attach each LXVI Senado seat to the margin it was won by in memory.

The INE integration CSV reports, per contested seat, who won it and by how much.
That file is raw source under the gitignored `data/electoral_data_raw/` tree,
and the static web exporter used to parse it directly at build time -- meaning
the published site could be built from a file the warehouse had never seen or
validated.

The website exporter calls :func:`attach_election_results` while assembling
``senate-66.json``. No derived margin table is created in SQLite. Only seats
actually won in a contest receive values: proportional-representation seats
have no winning margin, represented by ``None`` rather than zero.

A Senado seat is identified by its position on the state list, and both MR and
first-minority seats carry a result. The Cámara has a separate implementation
because it keys contested seats on federal district number instead.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

INTEGRATION_PATH = (
    ROOT
    / "data"
    / "electoral_data_raw"
    / "raw_2024"
    / "PRESIDENCIA_2024"
    / "CSV"
    / "INTEGRACION_CARGOS_PEF_2024.csv"
)
CANDIDATURE = "SEN_MR"


def load_results(path: Path = INTEGRATION_PATH) -> dict[tuple, dict]:
    """`(id_estado, numero_lista)` -> the winning margin the INE reported."""
    results: dict[tuple, dict] = {}
    with path.open(encoding="utf-8-sig", newline="") as source:
        for row in csv.DictReader(source):
            if row["TIPO_DE_CANDIDATURA"] != CANDIDATURE:
                continue
            pct = row["PORCENTAJE_VOTACION_GANADOR"].replace("%", "").strip()
            results[(int(row["ID_ESTADO"]), int(row["NUMERO_LISTA"]))] = {
                "electionActor": row["NOMBRE_ACTOR_POLITICO"].strip() or None,
                "winningVotes": int(row["VOTACION_GANADOR"]),
                "winningPct": float(pct),
            }
    return results


def attach_election_results(
    seats: list[dict], path: Path = INTEGRATION_PATH
) -> list[dict]:
    """Add the website's margin fields to already-resolved Senate seats."""
    results = load_results(path)
    matched = 0
    for seat in seats:
        result = results.get((seat.get("stateId"), seat.get("listNumber")))
        seat["districtSeat"] = None
        seat["electionActor"] = result["electionActor"] if result else None
        seat["winningVotes"] = result["winningVotes"] if result else None
        seat["winningPct"] = result["winningPct"] if result else None
        matched += int(result is not None)
    if matched != 96:
        raise ValueError(f"Expected margins for 96 Senate MR/FM seats; matched {matched}")
    return seats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", default=str(INTEGRATION_PATH))
    args = parser.parse_args()
    results = load_results(Path(args.csv))
    if len(results) != 96:
        raise SystemExit(f"Expected 96 Senate MR/FM results; found {len(results)}")
    print(f"Validated {len(results)} Senate MR/FM election results; no database writes.")


if __name__ == "__main__":
    main()
