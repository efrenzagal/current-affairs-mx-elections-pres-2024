"""Pre-build the Congreso composition assets consumed by Streamlit.

This performs all database queries, seat attribution, and Plotly construction
outside the web app. It writes one Plotly JSON figure and one HTML summary per
election, plus a manifest used by ``ine_explorer_v2.py``.

Run from the repository root:

    python3 aux_scripts/build_hemicycle_cache.py
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import plotly.io as pio


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = REPO_ROOT / "election_data.db"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "data" / "cache" / "hemicycles"

# ``python aux_scripts/build_hemicycle_cache.py`` puts aux_scripts/ on
# sys.path, while the allocation modules are imported from the repo package.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def load_winners(election_id: str, conn: sqlite3.Connection):
    """Use the same source priority as the standalone hemicycle explorer."""
    from aux_scripts.seat_allocations.hemicycle_explorer import (
        dip_winners_from_votes,
        load_composicion_dip,
        load_composicion_sen,
        sen_winners_from_votes,
    )

    if election_id.startswith("DIP_MR_"):
        winners = load_composicion_dip(election_id, conn)
        return winners if winners is not None and not winners.empty else dip_winners_from_votes(conn, election_id)

    winners = load_composicion_sen(election_id, conn)
    return winners if winners is not None and not winners.empty else sen_winners_from_votes(conn, election_id)


def write_text(path: Path, content: str) -> None:
    """Avoid leaving a partially-written artifact if the builder is interrupted."""
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text(content, encoding="utf-8")
    temporary_path.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build cached Congreso hemicycle assets.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="SQLite warehouse path")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DIR, help="Asset output directory")
    args = parser.parse_args()

    db_path = args.db.resolve()
    output_dir = args.output.resolve()
    if not db_path.exists():
        raise SystemExit(f"No se encontró la base de datos: {db_path}")

    # The seat-allocation module refers to data/ paths relative to the repo.
    os.chdir(REPO_ROOT)

    from aux_scripts.seat_allocations.hemicycle_explorer import (
        build_figure,
        build_summary_html,
        get_elections,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        election_ids = (
            get_elections(conn, "DIP_MR")
            + get_elections(conn, "SEN_MR")
        )
        completed_ids: list[str] = []

        for election_id in election_ids:
            print(f"Building {election_id}...")
            winners = load_winners(election_id, conn)
            if winners is None or winners.empty:
                print("  Skipped: no seat data.")
                continue

            figure = build_figure(winners, election_id)
            write_text(
                output_dir / f"{election_id}.figure.json",
                pio.to_json(figure, validate=False, pretty=False),
            )
            write_text(
                output_dir / f"{election_id}.summary.html",
                build_summary_html(winners, election_id),
            )
            completed_ids.append(election_id)
            print(f"  Saved {len(winners)} seats.")

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_database": str(db_path),
        "elections": completed_ids,
    }
    write_text(output_dir / "manifest.json", json.dumps(manifest, indent=2) + "\n")
    print(f"Built {len(completed_ids)} hemicycles in {output_dir}")


if __name__ == "__main__":
    main()
