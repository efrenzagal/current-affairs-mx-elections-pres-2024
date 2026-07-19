#!/usr/bin/env python3
"""Build an auditable municipio crosswalk for the 2000 election sections.

The 2000 result files do not identify municipios.  This script joins their
(state, section) keys to the geography dimensions from 1994, 2006, and 2024.
It writes both a complete crosswalk and a smaller review file containing all
missing or conflicting sections. Pass --apply-to-dim-geography to copy the
resolved municipio fields into the canonical 2000 geography parquet.
"""

from __future__ import annotations

import argparse
import re
import unicodedata
from pathlib import Path

import pandas as pd


DEFAULT_CLEAN_ROOT = Path("data/electoral_data_clean")
DEFAULT_OUTPUT = DEFAULT_CLEAN_ROOT / "clean_2000/municipio_crosswalk_2000.csv"
DEFAULT_ISSUES = DEFAULT_CLEAN_ROOT / "clean_2000/municipio_crosswalk_2000_issues.csv"


def normalize_name(value: object) -> str | None:
    """Normalize names for comparison without changing the displayed value."""
    if pd.isna(value):
        return None
    text = unicodedata.normalize("NFKD", str(value))
    text = text.encode("ascii", "ignore").decode("ascii").upper()
    text = re.sub(r"[^A-Z0-9]+", " ", text).strip()
    return text or None


def load_geography(path: Path, year: int) -> pd.DataFrame:
    df = pd.read_parquet(path)
    df.columns = [column.upper() for column in df.columns]
    required = {"ID_ESTADO", "SECCION"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{path} lacks required columns: {sorted(missing)}")

    columns = ["ID_ESTADO", "SECCION"]
    if "MUNICIPIO" in df.columns:
        columns.append("MUNICIPIO")
    if "ID_MUNICIPIO" in df.columns:
        columns.append("ID_MUNICIPIO")
    out = df[columns].copy()
    out["ID_ESTADO"] = pd.to_numeric(out["ID_ESTADO"], errors="raise").astype("int64")
    out["SECCION"] = pd.to_numeric(out["SECCION"], errors="raise").astype("int64")

    key = ["ID_ESTADO", "SECCION"]
    value_columns = [column for column in out.columns if column not in key]
    duplicates = out.drop_duplicates().groupby(key, dropna=False).size()
    if (duplicates > 1).any():
        examples = duplicates[duplicates > 1].head().index.tolist()
        raise ValueError(f"{path} has conflicting rows for section keys, e.g. {examples}")
    out = out.drop_duplicates(key)
    return out.rename(columns={column: f"{column}_{year}" for column in value_columns})


def classify(row: pd.Series) -> pd.Series:
    names = {year: row.get(f"MUNICIPIO_{year}") for year in (1994, 2006, 2024)}
    normalized = {year: normalize_name(value) for year, value in names.items()}
    adjacent = [normalized[1994], normalized[2006]]
    adjacent_present = [value for value in adjacent if value]
    all_present = [value for value in normalized.values() if value]

    if not all_present:
        issue = "missing_all_sources"
        resolved_year = None
        confidence = "unresolved"
    elif (
        len(adjacent_present) == 2
        and adjacent_present[0] != adjacent_present[1]
        and row["PAIR_RELATION_1994_2006"] == "one_to_one_name_variant"
    ):
        # Every shared section of the 1994 municipio maps to this one 2006
        # municipio and vice versa. This is strong evidence of a renamed or
        # differently formatted municipio, rather than a section transfer.
        issue = "name_variant_1994_2006"
        resolved_year = 2006
        confidence = "high"
    elif len(adjacent_present) == 2 and adjacent_present[0] != adjacent_present[1]:
        # A current match cannot determine whether a municipal change happened
        # before or after the 2000 election, so retain this for manual review.
        issue = "conflict_1994_2006"
        resolved_year = None
        confidence = "unresolved"
    elif len(adjacent_present) == 2:
        resolved_year = 2006
        confidence = "high"
        issue = "current_conflict" if normalized[2024] and normalized[2024] != adjacent_present[0] else ""
    elif len(adjacent_present) == 1:
        resolved_year = 1994 if normalized[1994] else 2006
        confidence = "medium"
        issue = "current_conflict" if normalized[2024] and normalized[2024] != adjacent_present[0] else ""
    else:
        resolved_year = 2024
        confidence = "low"
        issue = "missing_adjacent_sources"

    resolved_name = names[resolved_year] if resolved_year else None
    resolved_norm = normalize_name(resolved_name)
    current_id = row.get("ID_MUNICIPIO_2024")
    # A 2024 ID is safe to attach only when its municipio name matches the
    # resolved historical name. Historical IDs are unavailable in 1994/2006.
    resolved_id = current_id if resolved_norm and resolved_norm == normalized[2024] else None
    source = str(resolved_year) if resolved_year else None

    return pd.Series(
        {
            "ID_MUNICIPIO_RESOLVED": resolved_id,
            "MUNICIPIO_RESOLVED": resolved_name,
            "MUNICIPIO_SOURCE": source,
            "MUNICIPIO_CONFIDENCE": confidence,
            "ISSUE": issue,
        }
    )


def add_pair_relation(df: pd.DataFrame) -> pd.DataFrame:
    """Classify the relationship between the two adjacent-cycle names.

    A differing name is considered a name variant only when the mapping is
    one-to-one in both directions across all shared 2000 section keys. If an
    old municipio maps to multiple new municipios (or vice versa), the rows
    remain unresolved because that indicates a split, creation, or transfer.
    """
    out = df.copy()
    out["_NORM_1994"] = out["MUNICIPIO_1994"].map(normalize_name)
    out["_NORM_2006"] = out["MUNICIPIO_2006"].map(normalize_name)
    shared = out.dropna(subset=["_NORM_1994", "_NORM_2006"])

    forward = shared.groupby(["ID_ESTADO", "_NORM_1994"])["_NORM_2006"].nunique()
    reverse = shared.groupby(["ID_ESTADO", "_NORM_2006"])["_NORM_1994"].nunique()

    def relation(row: pd.Series) -> str:
        old = row["_NORM_1994"]
        new = row["_NORM_2006"]
        if not old or not new:
            return "missing_adjacent_name"
        if old == new:
            return "same_normalized_name"
        if forward.loc[(row["ID_ESTADO"], old)] == 1 and reverse.loc[(row["ID_ESTADO"], new)] == 1:
            return "one_to_one_name_variant"
        return "boundary_or_creation_conflict"

    out["PAIR_RELATION_1994_2006"] = out.apply(relation, axis=1)
    return out.drop(columns=["_NORM_1994", "_NORM_2006"])


def build_crosswalk(clean_root: Path) -> pd.DataFrame:
    paths = {
        year: clean_root / f"clean_{year}/dim_geography.parquet"
        for year in (1994, 2000, 2006, 2024)
    }
    for path in paths.values():
        if not path.exists():
            raise FileNotFoundError(path)

    base = load_geography(paths[2000], 2000)[["ID_ESTADO", "SECCION"]]
    result = base
    for year in (1994, 2006, 2024):
        result = result.merge(
            load_geography(paths[year], year),
            on=["ID_ESTADO", "SECCION"],
            how="left",
            validate="one_to_one",
        )

    result = add_pair_relation(result)

    result.insert(
        0,
        "geo_id",
        result["ID_ESTADO"].astype(str).str.zfill(2)
        + "_"
        + result["SECCION"].astype(str).str.zfill(4),
    )
    resolved = result.apply(classify, axis=1)
    result = pd.concat([result, resolved], axis=1)

    # Resolve a modern municipio ID by the chosen municipio name anywhere in
    # its state. A section may have moved by 2024, so its row-level 2024 ID is
    # not necessarily the ID of the historical municipio selected above.
    current_names = result.dropna(subset=["MUNICIPIO_2024", "ID_MUNICIPIO_2024"]).copy()
    current_names["_NORM"] = current_names["MUNICIPIO_2024"].map(normalize_name)
    id_counts = current_names.groupby(["ID_ESTADO", "_NORM"])["ID_MUNICIPIO_2024"].nunique()
    unambiguous = id_counts[id_counts == 1].index
    current_names = current_names.set_index(["ID_ESTADO", "_NORM"])
    id_lookup = {
        key: int(current_names.loc[key, "ID_MUNICIPIO_2024"].iloc[0])
        if isinstance(current_names.loc[key, "ID_MUNICIPIO_2024"], pd.Series)
        else int(current_names.loc[key, "ID_MUNICIPIO_2024"])
        for key in unambiguous
    }
    result["ID_MUNICIPIO_RESOLVED"] = result.apply(
        lambda row: id_lookup.get(
            (row["ID_ESTADO"], normalize_name(row["MUNICIPIO_RESOLVED"]))
        ),
        axis=1,
    ).astype("Int64")
    return result.sort_values(["ID_ESTADO", "SECCION"])


def apply_to_dim_geography(crosswalk: pd.DataFrame, clean_root: Path) -> Path:
    """Write resolved municipio fields into clean_2000/dim_geography.parquet."""
    path = clean_root / "clean_2000/dim_geography.parquet"
    geography = pd.read_parquet(path)
    geography = geography.drop(columns=["ID_MUNICIPIO", "MUNICIPIO"], errors="ignore")
    values = crosswalk[
        ["ID_ESTADO", "SECCION", "ID_MUNICIPIO_RESOLVED", "MUNICIPIO_RESOLVED"]
    ].rename(
        columns={
            "ID_MUNICIPIO_RESOLVED": "ID_MUNICIPIO",
            "MUNICIPIO_RESOLVED": "MUNICIPIO",
        }
    )
    geography = geography.merge(
        values,
        on=["ID_ESTADO", "SECCION"],
        how="left",
        validate="one_to_one",
    )
    geography.to_parquet(path, index=False)
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clean-root", type=Path, default=DEFAULT_CLEAN_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--issues-output", type=Path, default=DEFAULT_ISSUES)
    parser.add_argument(
        "--apply-to-dim-geography",
        action="store_true",
        help="Add resolved ID_MUNICIPIO/MUNICIPIO fields to clean_2000/dim_geography.parquet",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    crosswalk = build_crosswalk(args.clean_root)
    issues = crosswalk[crosswalk["ISSUE"] != ""].copy()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.issues_output.parent.mkdir(parents=True, exist_ok=True)
    crosswalk.to_csv(args.output, index=False)
    issues.to_csv(args.issues_output, index=False)

    if args.apply_to_dim_geography:
        applied_path = apply_to_dim_geography(crosswalk, args.clean_root)
        print(f"Applied resolved municipios to: {applied_path}")

    print(f"Crosswalk: {args.output} ({len(crosswalk):,} sections)")
    print(f"Review CSV: {args.issues_output} ({len(issues):,} sections)")
    print("\nResolution confidence:")
    print(crosswalk["MUNICIPIO_CONFIDENCE"].value_counts(dropna=False).to_string())
    print("\nReview reasons:")
    print(issues["ISSUE"].value_counts(dropna=False).to_string())


if __name__ == "__main__":
    main()
