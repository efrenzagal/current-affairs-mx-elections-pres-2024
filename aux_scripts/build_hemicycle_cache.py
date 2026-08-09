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
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import plotly.io as pio


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = REPO_ROOT / "data" / "cache" / "hemicycles"
DB_PATH = REPO_ROOT / "election_data.db"

# ``python aux_scripts/build_hemicycle_cache.py`` puts aux_scripts/ on
# sys.path, while the allocation modules are imported from the repo package.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def load_winners(election_id: str, view: str = "electoral", as_of: str | None = None):
    """Load stable INE seats, optionally overlaid by the latest roster."""
    from aux_scripts.seat_allocations.hemicycle_explorer import (
        INTEGRACION_PATHS,
        load_from_integracion,
    )

    source_path = REPO_ROOT / INTEGRACION_PATHS[election_id]
    chamber = "DIP" if election_id.startswith("DIP_") else "SEN"
    winners = load_from_integracion(str(source_path), chamber=chamber)
    winners["election_candidate_name"] = winners["candidate_name"]
    winners["election_party"] = winners["canonical_party"]
    # Both current and electoral figures must place each constitutional seat
    # at the same coordinate. Current affiliation changes only its color/text.
    winners["layout_party"] = winners["election_party"]
    winners["roster_status"] = "electoral"
    if view == "electoral":
        winners["vote_person_id"] = ""
        return winners
    if view != "current":
        raise ValueError(f"Unknown hemicycle view: {view}")

    id_column = "diputado_id" if chamber == "DIP" else "senador_seat_id"
    with sqlite3.connect(DB_PATH) as conn:
        current = pd.read_sql_query(
            """
            SELECT r.seat_id, r.current_name, r.current_party, r.member_status,
                   r.vote_person_id, s.observed_at, s.source_url
            FROM fact_congress_roster_seat r
            JOIN dim_congress_roster_snapshot s USING(snapshot_id)
            WHERE r.chamber = ?
              AND s.observed_at = (
                  SELECT MAX(observed_at) FROM dim_congress_roster_snapshot
                  WHERE chamber = ? AND (? IS NULL OR observed_at < ?)
              )
            """,
            conn,
            params=(chamber, chamber, as_of, f"{as_of}T23:59:59.999999+00:00" if as_of else None),
        )
    if len(current) != len(winners):
        raise ValueError(
            f"{election_id}: current roster overlay has {len(current)} seats; expected {len(winners)}"
        )
    winners = winners.merge(current, left_on=id_column, right_on="seat_id", validate="one_to_one")
    winners["candidate_name"] = winners["current_name"].fillna("Vacante")
    winners["reported_current_party"] = winners["current_party"]
    display_party = winners["current_party"].copy()
    display_party = display_party.mask(winners["member_status"].eq("licencia"), "LICENCIA")
    display_party = display_party.mask(winners["member_status"].eq("vacante"), "VACANTE")
    winners["party_key"] = display_party
    winners["canonical_party"] = display_party
    winners["roster_status"] = winners["member_status"]
    winners["vote_person_id"] = winners["vote_person_id"].fillna("")
    return winners


def load_composition_dates() -> list[str]:
    """Dates for which both chambers have an official directory snapshot."""
    if not DB_PATH.exists():
        return []
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT chamber, substr(observed_at, 1, 10) AS observed_date
            FROM dim_congress_roster_snapshot
            GROUP BY chamber, observed_date
            """
        ).fetchall()
    by_chamber = {
        chamber: {date for row_chamber, date in rows if row_chamber == chamber}
        for chamber in ("DIP", "SEN")
    }
    return sorted(by_chamber["DIP"] & by_chamber["SEN"])


def load_temporal_history() -> dict:
    """Serialize compact observed seat and vote-party histories for UI drill-down."""
    with sqlite3.connect(DB_PATH) as conn:
        occupancy = pd.read_sql_query(
            """
            SELECT chamber, seat_id, occupant_name, party_key, status,
                   valid_from, valid_to, vote_person_id
            FROM fact_congress_seat_occupancy
            ORDER BY chamber, seat_id, valid_from
            """,
            conn,
        )
        membership = pd.read_sql_query(
            """
            SELECT chamber, person_id, party_key, valid_from, valid_to,
                   observations, conflicting_observations
            FROM fact_congress_party_membership
            WHERE source_type = 'vote_reported'
            ORDER BY chamber, person_id, valid_from
            """,
            conn,
        )
        electoral_people = pd.read_sql_query(
            """
            SELECT 'DIP' AS chamber, diputado_id AS seat_id,
                   gaceta_deputy_id AS person_id
            FROM dim_diputados
            WHERE gaceta_deputy_id IS NOT NULL
            UNION ALL
            SELECT 'SEN' AS chamber, senador_seat_id AS seat_id,
                   CAST(senador_id AS TEXT) AS person_id
            FROM dim_senadores
            WHERE senador_id IS NOT NULL
            """,
            conn,
        )
    occupancy_by_seat: dict[str, dict[str, list[dict]]] = {"DIP": {}, "SEN": {}}
    for (chamber, seat_id), rows in occupancy.groupby(["chamber", "seat_id"], sort=False):
        occupancy_by_seat[str(chamber)][str(seat_id)] = rows.drop(
            columns=["chamber", "seat_id"]
        ).where(pd.notna(rows.drop(columns=["chamber", "seat_id"])), None).to_dict("records")
    party_by_person: dict[str, dict[str, list[dict]]] = {"DIP": {}, "SEN": {}}
    for (chamber, person_id), rows in membership.groupby(["chamber", "person_id"], sort=False):
        party_by_person[str(chamber)][str(person_id)] = rows.drop(
            columns=["chamber", "person_id"]
        ).where(pd.notna(rows.drop(columns=["chamber", "person_id"])), None).to_dict("records")
    electoral_person_by_seat: dict[str, dict[str, str]] = {"DIP": {}, "SEN": {}}
    for record in electoral_people.to_dict("records"):
        electoral_person_by_seat[str(record["chamber"])][str(record["seat_id"])] = str(
            record["person_id"]
        )
    return {
        "occupancy_by_seat": occupancy_by_seat,
        "party_by_person": party_by_person,
        "electoral_person_by_seat": electoral_person_by_seat,
    }


def load_roster_manifest() -> dict:
    if not DB_PATH.exists():
        return {}
    with sqlite3.connect(DB_PATH) as conn:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='dim_congress_roster_snapshot'"
        ).fetchone()
        if not exists:
            return {}
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT chamber, observed_at, source_url, source_sha256, roster_row_count,
                   constitutional_seats
            FROM dim_congress_roster_snapshot s
            WHERE observed_at = (
                SELECT MAX(observed_at) FROM dim_congress_roster_snapshot WHERE chamber = s.chamber
            )
            ORDER BY chamber
            """
        ).fetchall()
    return {row["chamber"]: dict(row) for row in rows}


def write_text(path: Path, content: str) -> None:
    """Avoid leaving a partially-written artifact if the builder is interrupted."""
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text(content, encoding="utf-8")
    temporary_path.replace(path)


def seat_coordinates(figure, chamber: str) -> dict[str, tuple[float, float]]:
    """Return stable-seat coordinates from the figure's click metadata."""
    seat_index = 5 if chamber == "DIP" else 6
    coordinates: dict[str, tuple[float, float]] = {}
    for trace in figure.data:
        for x, y, customdata in zip(trace.x, trace.y, trace.customdata):
            seat_id = str(customdata[seat_index])
            if not seat_id:
                raise ValueError(f"{chamber}: figure point is missing its stable seat ID")
            coordinates[seat_id] = (float(x), float(y))
    return coordinates


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
    roster_manifest = load_roster_manifest()
    views = ["current", "electoral"] if set(roster_manifest) == {"DIP", "SEN"} else ["electoral"]
    composition_dates = load_composition_dates() if "current" in views else []
    temporal_history = load_temporal_history() if "current" in views else {
        "occupancy_by_seat": {}, "party_by_person": {}, "electoral_person_by_seat": {}
    }
    write_text(
        output_dir / "temporal_history.json",
        json.dumps(temporal_history, ensure_ascii=False, separators=(",", ":")),
    )

    for election_id in sorted(INTEGRACION_PATHS):
        print(f"Building {election_id}...")
        expected_seats = 500 if election_id.startswith("DIP_") else 128
        chamber = "DIP" if election_id.startswith("DIP_") else "SEN"
        figures_by_view = {}
        view_specs = [(view, view, None) for view in views]
        view_specs.extend(
            (f"asof-{observed_date}", "current", observed_date)
            for observed_date in composition_dates
        )
        for asset_view, data_view, observed_date in view_specs:
            winners = load_winners(election_id, view=data_view, as_of=observed_date)
            if len(winners) != expected_seats:
                raise SystemExit(
                    f"{election_id}: expected {expected_seats} official seats, found {len(winners)}"
                )
            figure = build_figure(winners, election_id)
            figures_by_view[asset_view] = figure
            write_text(
                output_dir / f"{election_id}.{asset_view}.figure.json",
                pio.to_json(figure, validate=False, pretty=False),
            )
            write_text(
                output_dir / f"{election_id}.{asset_view}.summary.html",
                build_summary_html(winners, election_id),
            )
            # Preserve the original cache contract as the electoral baseline.
            if asset_view == "electoral":
                write_text(output_dir / f"{election_id}.figure.json", pio.to_json(figure, validate=False))
                write_text(output_dir / f"{election_id}.summary.html", build_summary_html(winners, election_id))
        if {"current", "electoral"}.issubset(figures_by_view):
            current_coordinates = seat_coordinates(figures_by_view["current"], chamber)
            electoral_coordinates = seat_coordinates(figures_by_view["electoral"], chamber)
            if current_coordinates != electoral_coordinates:
                changed = sum(
                    current_coordinates.get(seat_id) != coordinate
                    for seat_id, coordinate in electoral_coordinates.items()
                )
                raise ValueError(
                    f"{election_id}: {changed} seats move between current and electoral views"
                )
            for asset_view, historical_figure in figures_by_view.items():
                if not asset_view.startswith("asof-"):
                    continue
                historical_coordinates = seat_coordinates(historical_figure, chamber)
                if historical_coordinates != electoral_coordinates:
                    raise ValueError(f"{election_id}: seats move in {asset_view}")
        completed_ids.append(election_id)
        print(f"  Saved {expected_seats} seats in {', '.join(views)} view(s).")

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "methodology": (
            "Stable seats and electoral origin from INE; current occupants and parliamentary "
            "groups from dated official chamber directories."
        ),
        "source": {
            "name": "INE · Integración de diputaciones y senadurías, PEF 2023–2024",
            "url": "https://ine.mx/integracion-de-diputaciones-y-senadurias-pef-2023-2024/",
            "local_file": INTEGRACION_PATHS["DIP_MR_2024"],
        },
        "views": views,
        "composition_dates": composition_dates,
        "temporal_history": "temporal_history.json",
        "rosters": roster_manifest,
        "elections": completed_ids,
    }
    write_text(output_dir / "manifest.json", json.dumps(manifest, indent=2) + "\n")
    print(f"Built {len(completed_ids)} hemicycles in {output_dir}")


if __name__ == "__main__":
    main()
