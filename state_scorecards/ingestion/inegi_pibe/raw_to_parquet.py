"""Transform INEGI PIBE source files into one clean long-format Parquet.

The detailed release supplies total and industry-level observations for
2003 onward.  The reduced retropolated release supplies aggregate observations
back to 1980.  This script combines them without overlap:

* 1980-2002: reduced retropolated release
* 2003 onward: detailed release

Only the activity-oriented files are read.  INEGI's entity-oriented files are
the same data cube in a different orientation and would duplicate the data.

Usage:
    python3 state_scorecards/ingestion/inegi_pibe/raw_to_parquet.py
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import numpy as np
import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[3]
STATE_SCORECARDS_DIR = REPO_ROOT / "state_scorecards"
RAW_ROOT = STATE_SCORECARDS_DIR / "data" / "raw_inegi"
OUTPUT_PATH = STATE_SCORECARDS_DIR / "data" / "clean" / "pibe_state_annual.parquet"

DETAILED_DIRNAME = "conjunto_de_datos_pibe_csv"
RETRO_DIRNAME = "conjunto_de_datos_piber_csv"

BROAD_ACTIVITY_GROUPS = {
    "Actividades primarias",
    "Actividades secundarias",
    "Actividades terciarias",
}

YEAR_COLUMN_RE = re.compile(r"^\d{4}(?:<[^>]+>)?$")

OBSERVATION_KEY = [
    "geography",
    "activity_id",
    "concept_name",
    "unit",
    "year",
]

OUTPUT_COLUMNS = [
    "dataset",
    "source_file",
    "series_path",
    "series_name",
    "path_level_1",
    "path_level_2",
    "path_level_3",
    "geography",
    "geography_note",
    "activity_id",
    "activity_group",
    "activity_level",
    "activity_code",
    "activity_name",
    "activity_full_name",
    "sector_code",
    "sector_name",
    "subsector_code",
    "subsector_name",
    "unit",
    "concept_code",
    "concept_name",
    "year",
    "revision_status",
    "value",
    "historical_coverage",
]


def _clean_text(value: object) -> str | None:
    """Return normalized text or None for an empty/missing value."""
    if value is None or pd.isna(value):
        return None
    cleaned = " ".join(str(value).split())
    return cleaned or None


def _split_code_name(value: str | None) -> tuple[str | None, str | None]:
    """Split INEGI's ``code---label`` convention."""
    value = _clean_text(value)
    if value is None:
        return None, None
    if "---" not in value:
        return None, value
    code, name = value.split("---", 1)
    return _clean_text(code), _clean_text(name)


def classify_series(series_path: str) -> dict[str, str | None]:
    """Parse one indice.csv hierarchy into normalized activity fields."""
    parts = [_clean_text(part) for part in str(series_path).split("|")]
    if len(parts) > 4:
        raise ValueError(f"Unexpected PIBE hierarchy depth: {series_path!r}")
    parts.extend([None] * (4 - len(parts)))
    series_name, path_level_1, path_level_2, path_level_3 = parts

    if path_level_1 in BROAD_ACTIVITY_GROUPS:
        activity_group = path_level_1
    else:
        activity_group = "Total de la economía"

    sector_raw = None
    if (
        path_level_1 in BROAD_ACTIVITY_GROUPS
        and path_level_2 is not None
        and path_level_2 != "Total"
    ):
        sector_raw = path_level_2

    subsector_raw = None
    if path_level_1 in BROAD_ACTIVITY_GROUPS and path_level_3 is not None:
        subsector_raw = path_level_3

    sector_code, sector_name = _split_code_name(sector_raw)
    subsector_code, subsector_name = _split_code_name(subsector_raw)

    if subsector_raw is not None:
        activity_level = "subsector"
    elif sector_raw is not None:
        activity_level = "sector"
    elif activity_group != "Total de la economía":
        activity_level = "activity_group"
    else:
        activity_level = "total"

    activity_code = subsector_code or sector_code
    if activity_code is not None:
        activity_id = activity_code
    elif activity_group == "Actividades primarias":
        activity_id = "PRIMARY"
    elif activity_group == "Actividades secundarias":
        activity_id = "SECONDARY"
    elif activity_group == "Actividades terciarias":
        activity_id = "TERTIARY"
    else:
        activity_id = "TOTAL"

    activity_name = subsector_name or sector_name or activity_group
    if activity_level == "subsector":
        activity_full_name = " | ".join(
            [activity_group, sector_name or "", subsector_name or ""]
        )
    elif activity_level == "sector":
        activity_full_name = " | ".join([activity_group, sector_name or ""])
    else:
        activity_full_name = activity_group

    return {
        "series_name": series_name,
        "path_level_1": path_level_1,
        "path_level_2": path_level_2,
        "path_level_3": path_level_3,
        "activity_id": activity_id,
        "activity_group": activity_group,
        "activity_level": activity_level,
        "activity_code": activity_code,
        "activity_name": activity_name,
        "activity_full_name": activity_full_name,
        "sector_code": sector_code,
        "sector_name": sector_name,
        "subsector_code": subsector_code,
        "subsector_name": subsector_name,
    }


def load_index(data_dir: Path) -> pd.DataFrame:
    """Read the headerless INEGI file-to-series index."""
    index_path = data_dir / "indice.csv"
    if not index_path.exists():
        raise FileNotFoundError(f"Missing INEGI index: {index_path}")

    index = pd.read_csv(
        index_path,
        header=None,
        names=["source_file", "series_path"],
        dtype="string",
    )
    index = index[
        index["source_file"].str.contains(
            r"_actividad_.*\.csv$", regex=True, na=False
        )
    ].copy()

    if index["source_file"].duplicated().any():
        duplicates = index.loc[index["source_file"].duplicated(), "source_file"]
        raise ValueError(f"Duplicate filenames in {index_path}: {duplicates.tolist()}")
    return index


def transform_activity_file(
    source_path: Path,
    series_path: str,
    dataset_name: str,
) -> pd.DataFrame:
    """Convert one activity-oriented INEGI file from wide to long format."""
    raw = pd.read_csv(source_path, low_memory=False)
    if "Descriptores" not in raw.columns:
        raise ValueError(f"Missing Descriptores column in {source_path}")

    year_columns = [
        column for column in raw.columns if YEAR_COLUMN_RE.fullmatch(str(column))
    ]
    if not year_columns:
        raise ValueError(f"No annual columns found in {source_path}")

    descriptor_parts = raw["Descriptores"].astype("string").str.split(
        "|", n=2, expand=True, regex=False
    )
    if descriptor_parts.shape[1] != 3:
        raise ValueError(f"Unexpected Descriptores layout in {source_path}")

    wide = raw[year_columns].copy()
    wide.insert(0, "geography_raw", descriptor_parts[2])
    wide.insert(0, "concept_raw", descriptor_parts[1])
    wide.insert(0, "unit", descriptor_parts[0].str.strip())

    long_data = wide.melt(
        id_vars=["unit", "concept_raw", "geography_raw"],
        value_vars=year_columns,
        var_name="year_raw",
        value_name="value",
    )

    long_data["year"] = long_data["year_raw"].str.extract(r"^(\d{4})")[0].astype(int)
    long_data["revision_status"] = np.where(
        long_data["year_raw"].str.contains("<R>", regex=False),
        "revised",
        "not_marked",
    )

    long_data["geography_note"] = long_data["geography_raw"].str.extract(
        r"<([^>]+)>$", expand=False
    )
    long_data["geography"] = (
        long_data["geography_raw"]
        .str.replace(r"<[^>]+>$", "", regex=True)
        .str.strip()
    )

    concept_has_code = long_data["concept_raw"].str.contains(
        "---", regex=False, na=False
    )
    concept_parts = long_data["concept_raw"].str.split(
        "---", n=1, expand=True, regex=False
    )
    long_data["concept_code"] = concept_parts[0].str.strip().where(concept_has_code)
    if concept_parts.shape[1] == 2:
        long_data["concept_name"] = concept_parts[1].where(
            concept_has_code, concept_parts[0]
        )
    else:
        long_data["concept_name"] = concept_parts[0]
    long_data["concept_name"] = long_data["concept_name"].str.strip()

    numeric_value = pd.to_numeric(long_data["value"], errors="coerce")
    invalid_numeric = long_data["value"].notna() & numeric_value.isna()
    if invalid_numeric.any():
        samples = long_data.loc[invalid_numeric, "value"].astype(str).unique()[:5]
        raise ValueError(f"Non-numeric values in {source_path}: {samples.tolist()}")
    long_data["value"] = numeric_value

    series_fields = classify_series(series_path)
    long_data["dataset"] = dataset_name
    long_data["source_file"] = source_path.name
    long_data["series_path"] = series_path
    for column, value in series_fields.items():
        long_data[column] = value

    return long_data[
        [column for column in OUTPUT_COLUMNS if column != "historical_coverage"]
    ]


def transform_release(data_dir: Path, dataset_name: str) -> pd.DataFrame:
    """Transform every activity file in one INEGI release."""
    index = load_index(data_dir)
    index_lookup = dict(zip(index["source_file"], index["series_path"]))
    activity_files = sorted(data_dir.glob("*_actividad_*.csv"))

    disk_names = {path.name for path in activity_files}
    index_names = set(index_lookup)
    if disk_names != index_names:
        missing_on_disk = sorted(index_names - disk_names)
        missing_in_index = sorted(disk_names - index_names)
        raise ValueError(
            f"Activity/index mismatch in {data_dir}: "
            f"missing_on_disk={missing_on_disk}, missing_in_index={missing_in_index}"
        )

    print(f"  {dataset_name}: transforming {len(activity_files)} activity files")
    frames = [
        transform_activity_file(path, str(index_lookup[path.name]), dataset_name)
        for path in activity_files
    ]
    result = pd.concat(frames, ignore_index=True)
    print(f"  {dataset_name}: {len(result):,} long rows")
    return result


def validate_unique_observations(data: pd.DataFrame, label: str) -> None:
    duplicates = data.duplicated(OBSERVATION_KEY, keep=False)
    if duplicates.any():
        sample = data.loc[duplicates, OBSERVATION_KEY].head(10).to_dict("records")
        raise ValueError(f"{label} has duplicate observation keys: {sample}")


def validate_overlap(detailed: pd.DataFrame, retro: pd.DataFrame) -> None:
    """Confirm that comparable aggregate observations agree in 2003-2024."""
    detailed_overlap = detailed[detailed["activity_level"].isin(["total", "activity_group"])]
    retro_overlap = retro[retro["year"] >= 2003]

    shared = detailed_overlap.merge(
        retro_overlap,
        on=OBSERVATION_KEY,
        how="inner",
        suffixes=("_detailed", "_retro"),
        validate="one_to_one",
    )
    if shared.empty:
        raise ValueError("No comparable detailed/retro observations found in 2003-2024")

    detailed_missing = shared["value_detailed"].isna()
    retro_missing = shared["value_retro"].isna()
    boundary_gap = (shared["year"] == 2003) & detailed_missing & ~retro_missing
    unexpected_null_gap = (detailed_missing ^ retro_missing) & ~boundary_gap
    if unexpected_null_gap.any():
        sample_columns = OBSERVATION_KEY + ["value_detailed", "value_retro"]
        sample = shared.loc[unexpected_null_gap, sample_columns].head(10).to_dict("records")
        raise ValueError(
            f"Detailed and retro overlap has {unexpected_null_gap.sum():,} unexpected "
            f"missing-value differences: {sample}"
        )

    jointly_observed = ~detailed_missing & ~retro_missing
    matching = np.isclose(
        shared.loc[jointly_observed, "value_detailed"],
        shared.loc[jointly_observed, "value_retro"],
        rtol=1e-10,
        atol=1e-6,
        equal_nan=True,
    )
    if not matching.all():
        sample_columns = OBSERVATION_KEY + ["value_detailed", "value_retro"]
        mismatched = shared.loc[jointly_observed].loc[~matching, sample_columns]
        sample = mismatched.head(10).to_dict("records")
        raise ValueError(
            f"Detailed and retro overlap differs in {(~matching).sum():,} rows: {sample}"
        )
    print(
        f"  QA: {jointly_observed.sum():,} shared 2003-2024 values agree; "
        f"{boundary_gap.sum():,} expected 2003 growth-rate gaps"
    )


def build_combined(raw_root: Path) -> pd.DataFrame:
    detailed_dir = raw_root / DETAILED_DIRNAME / "conjunto_de_datos"
    retro_dir = raw_root / RETRO_DIRNAME / "conjunto_de_datos"

    detailed = transform_release(detailed_dir, "pibe_detailed")
    retro = transform_release(retro_dir, "pibe_retro")

    validate_unique_observations(detailed, "Detailed release")
    validate_unique_observations(retro, "Retro release")
    validate_overlap(detailed, retro)

    detailed = detailed.copy()
    detailed["historical_coverage"] = "detailed"

    retro_historical = retro[retro["year"] < 2003].copy()
    retro_historical["historical_coverage"] = "retropolated_aggregate"

    combined = pd.concat([retro_historical, detailed], ignore_index=True)
    combined = combined[OUTPUT_COLUMNS].sort_values(
        ["geography", "activity_id", "concept_name", "unit", "year"],
        kind="stable",
    )
    combined = combined.reset_index(drop=True)

    validate_unique_observations(combined, "Combined release")
    if combined["series_path"].isna().any():
        raise ValueError("Combined data contains rows without series_path")
    if combined["activity_id"].isna().any():
        raise ValueError("Combined data contains rows without activity_id")
    if not (combined.loc[combined["dataset"] == "pibe_retro", "year"] < 2003).all():
        raise ValueError("Combined data retains overlapping retro observations")

    return combined


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Transform detailed and retropolated INEGI PIBE files to one Parquet."
    )
    parser.add_argument("--raw-root", type=Path, default=RAW_ROOT)
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    args = parser.parse_args()

    raw_root = args.raw_root.resolve()
    output_path = args.output.resolve()
    print(f"Raw data: {raw_root}")
    print(f"Output:   {output_path}")

    combined = build_combined(raw_root)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(output_path.suffix + ".tmp")
    combined.to_parquet(
        temporary_path,
        index=False,
        engine="pyarrow",
        compression="zstd",
    )
    temporary_path.replace(output_path)

    print("\n── Clean data summary ─────────────────────────────────")
    print(f"  rows:        {len(combined):,}")
    print(f"  years:       {combined['year'].min()}-{combined['year'].max()}")
    print(f"  geographies: {combined['geography'].nunique():,}")
    print(f"  activities:  {combined['activity_id'].nunique():,}")
    for (dataset, coverage), count in combined.groupby(
        ["dataset", "historical_coverage"], dropna=False
    ).size().items():
        print(f"  {dataset} / {coverage}: {count:,}")
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
