"""
Municipio ternary classification — Base / Contenciosa / Empate for every
municipio x presidential election (1994-2024).

Reuses the exact same L/R/C bloc aggregation as the Trayectoria tab
(ui.trajectory.load_trajectory_data) and the same majority-rule classifier
(ui.common.classify_ternary), so the table matches what's on screen.

Category rule (see ui/common.py for the full rationale):
  - Base <bloc>        : that bloc cleared 50% (no two-way coalition of the
                          other blocs could have beaten it)
  - Plural <bloc>       : no majority; that bloc leads by more than
                          tie_radius, but 2nd and 3rd place are themselves
                          within tie_radius of each other (no real top-two
                          race, just a lone leader)
  - Contenciosa X-Y     : no majority; blocs X and Y are the top two and are
                          within tie_radius of each other
  - Empate              : no majority AND all three blocs within tie_radius
                          points of 33.33%

Usage:
    python aux_scripts/ternary_classification/build_municipio_ternary_classification.py
    python aux_scripts/ternary_classification/build_municipio_ternary_classification.py --tie-radius 6
"""

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from ui.common import MATERIALIZED_DIR, classify_ternary  # noqa: E402
from ui.trajectory import load_trajectory_data  # noqa: E402

OUT_BASENAME = "municipio_ternary_classification"


def build(tie_radius: float):
    df = load_trajectory_data()
    if df.empty:
        raise SystemExit(
            "No trajectory data found. Run the ingestion/materialize pipeline first "
            "(python ingestion/electoral_materialize.py)."
        )

    df = df.copy()
    df["election_id"] = "PRE_" + df["year"].astype(int).astype(str)
    df["category"] = df.apply(
        lambda r: classify_ternary(r["pct_L"], r["pct_R"], r["pct_C"], tie_radius=tie_radius),
        axis=1,
    )
    df["tie_radius"] = tie_radius

    out = df[[
        "id_estado", "nombre_estado", "municipio", "municipio_key",
        "year", "election_id",
        "L", "R", "C", "total_votos",
        "pct_L", "pct_R", "pct_C",
        "category", "tie_radius",
    ]].sort_values(["id_estado", "municipio_key", "year"]).reset_index(drop=True)

    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tie-radius", type=float, default=8.0,
        help="Max deviation (pct points) from 33.33%% on every axis to call it "
             "a tie within the no-majority zone. Default matches the Trayectoria "
             "tab's default slider value.",
    )
    parser.add_argument(
        "--out-dir", type=Path, default=MATERIALIZED_DIR,
        help="Output directory (default: data/materialized, same as the rest of "
             "the app's materialized tables).",
    )
    args = parser.parse_args()

    out = build(args.tie_radius)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    parquet_path = args.out_dir / f"{OUT_BASENAME}.parquet"
    csv_path = args.out_dir / f"{OUT_BASENAME}.csv"
    out.to_parquet(parquet_path, index=False)
    out.to_csv(csv_path, index=False)

    print(f"Wrote {len(out):,} rows ({out['municipio_key'].nunique():,} municipios, "
          f"{out['year'].nunique()} elections) to:")
    print(f"  {parquet_path}")
    print(f"  {csv_path}")
    print()
    print(out["category"].value_counts())


if __name__ == "__main__":
    main()
