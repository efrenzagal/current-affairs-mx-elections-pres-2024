"""Normalize CONAPO quarterly remittances by municipio into a local Parquet file.

Run from the repository root, after raw_to_parquet.py:
    python3 state_scorecards/ingestion/conapo/remesas_raw_to_parquet.py

The source truncates its text fields (state names at 14 characters, the state
migration-intensity grade at 4, municipio names at 25), so geography is keyed
on codes only. Municipio names come from the clean CONAPO municipal table and
the truncated state grade is dropped. Each state has one "No identificado"
municipio (code ending in 999) for remittances not assigned to a municipio.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from raw_to_parquet import CLEAN_DIR, OUTPUTS, RAW_DIR, code, integer, unique, write_parquet


SOURCE = "remesas/remesas_2013-2024.csv"
OUTPUT = "conapo_remittances_municipality_quarterly.parquet"

QUARTERS = {"Ene-Mar": 1, "Abr-Jun": 2, "Jul-Sep": 3, "Oct-Dic": 4}
MIGRATION_REGIONS = {"Tradicional", "Norte", "Centro", "Sur-Sureste"}
INTENSITY_GRADES = {"Muy alto", "Alto", "Medio", "Bajo", "Muy bajo", "Nulo", "No aplica"}


def remittances(raw_dir: Path, clean_dir: Path, output: Path) -> None:
    data = pd.read_csv(raw_dir / SOURCE, dtype={"cve_ent": str, "cve_mun": str})
    required = {"cve_ent", "cve_mun", "trim", "aaaa", "rem", "reg_mig", "giim_mun"}
    if not required.issubset(data):
        raise ValueError(f"Missing remittance fields: {sorted(required - set(data))}")
    data = data.rename(columns={
        "cve_ent": "state_code", "cve_mun": "municipality_code", "aaaa": "year",
        "rem": "remittances_usd_millions", "reg_mig": "migration_region",
        "giim_mun": "migration_intensity_grade",
    })
    data.state_code = code(data.state_code, 2)
    data.municipality_code = code(data.municipality_code, 5)
    data.year = integer(data.year, "year")
    data["quarter"] = data.trim.map(QUARTERS)
    if data.quarter.isna().any():
        raise ValueError(f"Unexpected quarter labels: {sorted(set(data.trim) - set(QUARTERS))}")
    data.quarter = data.quarter.astype("int64")
    data.remittances_usd_millions = pd.to_numeric(data.remittances_usd_millions, errors="raise")
    if data.remittances_usd_millions.isna().any() or (data.remittances_usd_millions < 0).any():
        raise ValueError("Remittances must be non-null and non-negative")
    if not (data.municipality_code.str[:2] == data.state_code).all():
        raise ValueError("Municipal code prefix differs from state code")
    if not set(data.migration_region).issubset(MIGRATION_REGIONS):
        raise ValueError(f"Unexpected migration regions: {sorted(set(data.migration_region) - MIGRATION_REGIONS)}")
    if not set(data.migration_intensity_grade).issubset(INTENSITY_GRADES):
        raise ValueError(f"Unexpected intensity grades: {sorted(set(data.migration_intensity_grade) - INTENSITY_GRADES)}")
    if data.groupby("state_code").migration_region.nunique().max() != 1:
        raise ValueError("A state has more than one migration region")
    if data.groupby("municipality_code").migration_intensity_grade.nunique().max() != 1:
        raise ValueError("A municipio has more than one migration intensity grade")
    unique(data, ["municipality_code", "year", "quarter"], "remittances")
    periods = data[["year", "quarter"]].drop_duplicates()
    if len(data) != data.municipality_code.nunique() * len(periods):
        raise ValueError("Remittances do not cover every municipio in every quarter")

    names = pd.read_parquet(
        clean_dir / OUTPUTS["municipality_annual"], columns=["municipality_code", "municipality_name"]
    ).drop_duplicates()
    if names.municipality_code.duplicated().any():
        raise ValueError("CONAPO municipal names are not unique per code")
    data = data.merge(names, on="municipality_code", how="left", validate="many_to_one")
    unidentified = data.municipality_code.str.endswith("999")
    if data.loc[~unidentified, "municipality_name"].isna().any():
        missing = sorted(data.loc[~unidentified & data.municipality_name.isna(), "municipality_code"].unique())
        raise ValueError(f"Municipio codes absent from CONAPO municipal table: {missing}")
    data.loc[unidentified, "municipality_name"] = "No identificado"

    data["source_file"] = Path(SOURCE).name
    data = data[[
        "municipality_code", "state_code", "municipality_name", "year", "quarter",
        "remittances_usd_millions", "migration_region", "migration_intensity_grade", "source_file",
    ]].sort_values(["municipality_code", "year", "quarter"], ignore_index=True)
    write_parquet(data, output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=RAW_DIR)
    parser.add_argument("--output-dir", type=Path, default=CLEAN_DIR)
    args = parser.parse_args()
    raw_dir = args.raw_dir.resolve()
    output_dir = args.output_dir.resolve()
    if not (raw_dir / SOURCE).is_file():
        raise FileNotFoundError(raw_dir / SOURCE)
    if not (output_dir / OUTPUTS["municipality_annual"]).is_file():
        raise FileNotFoundError(f"{output_dir / OUTPUTS['municipality_annual']}; run raw_to_parquet.py first")
    pending = output_dir / (OUTPUT + ".next")
    print(f"Remittances raw: {raw_dir / SOURCE}")
    print(f"Clean output: {output_dir}")
    remittances(raw_dir, output_dir, pending)
    pending.replace(output_dir / OUTPUT)
    print("Remittance Parquet file validated and published.")


if __name__ == "__main__":
    main()
