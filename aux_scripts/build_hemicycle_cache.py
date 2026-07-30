"""Pre-build the Congreso composition assets consumed by Streamlit.

This reads INE's final seat integration and performs all Plotly construction
outside the web app. It writes one Plotly JSON figure and one HTML summary per
officially supported election, plus a manifest used by ``ine_explorer_v2.py``.

Run from the repository root:

    python3 aux_scripts/build_hemicycle_cache.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import plotly.io as pio


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = REPO_ROOT / "data" / "cache" / "hemicycles"

# ``python aux_scripts/build_hemicycle_cache.py`` puts aux_scripts/ on
# sys.path, while the allocation modules are imported from the repo package.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def load_winners(election_id: str):
    """Load final seats from the official INE integration file."""
    from aux_scripts.seat_allocations.hemicycle_explorer import (
        INTEGRACION_PATHS,
        load_from_integracion,
    )

    source_path = REPO_ROOT / INTEGRACION_PATHS[election_id]
    chamber = "DIP" if election_id.startswith("DIP_") else "SEN"
    return load_from_integracion(str(source_path), chamber=chamber)


def write_text(path: Path, content: str) -> None:
    """Avoid leaving a partially-written artifact if the builder is interrupted."""
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text(content, encoding="utf-8")
    temporary_path.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build cached Congreso hemicycle assets.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DIR, help="Asset output directory")
    args = parser.parse_args()

    output_dir = args.output.resolve()

    os.chdir(REPO_ROOT)

    from aux_scripts.seat_allocations.hemicycle_explorer import (
        INTEGRACION_PATHS,
        build_figure,
        build_summary_html,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    completed_ids: list[str] = []

    for election_id in sorted(INTEGRACION_PATHS):
        print(f"Building {election_id}...")
        winners = load_winners(election_id)
        expected_seats = 500 if election_id.startswith("DIP_") else 128
        if len(winners) != expected_seats:
            raise SystemExit(
                f"{election_id}: expected {expected_seats} official seats, found {len(winners)}"
            )

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
        "methodology": "Final seat assignments published by INE; no inferred or simulated seats.",
        "source": {
            "name": "INE · Integración de diputaciones y senadurías, PEF 2023–2024",
            "url": "https://ine.mx/integracion-de-diputaciones-y-senadurias-pef-2023-2024/",
            "local_file": INTEGRACION_PATHS["DIP_MR_2024"],
        },
        "elections": completed_ids,
    }
    write_text(output_dir / "manifest.json", json.dumps(manifest, indent=2) + "\n")
    print(f"Built {len(completed_ids)} hemicycles in {output_dir}")


if __name__ == "__main__":
    main()
