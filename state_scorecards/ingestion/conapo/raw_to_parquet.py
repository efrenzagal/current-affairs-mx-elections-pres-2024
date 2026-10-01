"""Normalize the CONAPO population scorecard sources into local Parquet files.

Run from the repository root:
    python3 state_scorecards/ingestion/conapo/raw_to_parquet.py

The original CSV/XLSX files remain in data/raw_conapo, one subfolder per
CONAPO release. This script does not
change the PIBE artifact or the SQLite warehouse. The two archived XLSX files
are read with Python's standard-library ZIP/XML modules so no Excel dependency
is needed for repeatable conversion.
"""

from __future__ import annotations

import argparse
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Iterator

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


ROOT = Path(__file__).resolve().parents[3]
RAW_DIR = ROOT / "state_scorecards" / "data" / "raw_conapo"
CLEAN_DIR = ROOT / "state_scorecards" / "data" / "clean"

FILES = {
    "state_annual": "proyecciones_estatales/05_indicadores_demograficos_proyecciones.csv",
    "state_age": "proyecciones_estatales/00_Pob_Mitad_1950_2070.csv",
    "municipality_annual": "proyecciones_municipales/3_Indicadores_Dem_00_RM.xlsx",
    "municipality_age": "proyecciones_municipales/pobproy_quinq1.csv",
    "municipality_life_stage": "proyecciones_municipales/pobproy_ggrupos.csv",
    "fertility": "proyecciones_estatales/4_Tasas_Especificas_Fecundidad_proyecciones.xlsx",
}

OUTPUTS = {
    "state_annual": "conapo_state_annual.parquet",
    "state_age": "conapo_state_population_age_annual.parquet",
    "municipality_annual": "conapo_municipality_annual.parquet",
    "municipality_age": "conapo_municipality_population_age_annual.parquet",
    "municipality_life_stage": "conapo_municipality_life_stage_annual.parquet",
    "fertility": "conapo_state_fertility_age_annual.parquet",
}

STATE_RENAMES = {
    "POB_MIT_ANIO": "population_total",
    "HOM_MIT_ANIO": "population_men",
    "MUJ_MIT_ANIO": "population_women",
    "NAC": "births",
    "DEF": "deaths",
    "EDAD_MED": "median_age",
    "IND_ENV": "aging_index",
    "RAZ_DEP": "dependency_ratio",
    "RAZ_DEP_INF": "child_dependency_ratio",
    "RAZ_DEP_ADU": "older_dependency_ratio",
    "EV": "life_expectancy",
    "EVH": "life_expectancy_men",
    "EVM": "life_expectancy_women",
    "TGF": "total_fertility_rate",
    "TEF_ADO": "adolescent_fertility_rate",
    "TMI": "infant_mortality_rate",
    "T_CRE_TOT": "total_growth_rate",
}

MUNICIPALITY_RENAMES = {
    "HOM_MIT_AÑO": "population_men",
    "MUJ_MIT_AÑO": "population_women",
    "POB_MIT_MUN": "population_total",
    "POB_MIT_ENT": "state_population_total",
    "EDAD_MED": "median_age",
    "RAZ_DEP": "dependency_ratio",
    "RAZ_DEP_INF": "child_dependency_ratio",
    "RAZ_DEP_ADU": "older_dependency_ratio",
}

AGE_COLUMNS = {
    "POB_00_04": (0, 4),
    "POB_05_09": (5, 9),
    "POB_010_014": (10, 14),
    "POB_015_019": (15, 19),
    "POB_20_24": (20, 24),
    "POB_25_29": (25, 29),
    "POB_30_34": (30, 34),
    "POB_35_39": (35, 39),
    "POB_40_44": (40, 44),
    "POB_45_49": (45, 49),
    "POB_50_54": (50, 54),
    "POB_55_59": (55, 59),
    "POB_60_64": (60, 64),
    "POB_65_69": (65, 69),
    "POB_70_74": (70, 74),
    "POB_75_79": (75, 79),
    "POB_80_84": (80, 84),
    "POB_85_mm": (85, None),
}

MAIN_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
COLUMN_RE = re.compile(r"[A-Z]+")


def _column_index(cell_address: str) -> int:
    match = COLUMN_RE.match(cell_address)
    if not match:
        raise ValueError(f"Invalid XLSX cell address: {cell_address}")
    result = 0
    for char in match.group():
        result = result * 26 + ord(char) - ord("A") + 1
    return result - 1


def _xlsx_rows(path: Path) -> Iterator[list[str | None]]:
    """Stream values from the first worksheet of either single-sheet source."""
    with zipfile.ZipFile(path) as archive:
        strings: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            with archive.open("xl/sharedStrings.xml") as stream:
                for _, element in ET.iterparse(stream, events=("end",)):
                    if element.tag == MAIN_NS + "si":
                        strings.append("".join(t.text or "" for t in element.iter(MAIN_NS + "t")))
                        element.clear()

        with archive.open("xl/worksheets/sheet1.xml") as stream:
            context = ET.iterparse(stream, events=("start", "end"))
            _, root = next(context)
            for event, row in context:
                if event != "end" or row.tag != MAIN_NS + "row":
                    continue
                values: list[str | None] = []
                for cell in row.iter(MAIN_NS + "c"):
                    index = _column_index(cell.attrib["r"])
                    if len(values) <= index:
                        values.extend([None] * (index + 1 - len(values)))
                    value = cell.find(MAIN_NS + "v")
                    if cell.attrib.get("t") == "s" and value is not None:
                        values[index] = strings[int(value.text)]
                    elif cell.attrib.get("t") == "inlineStr":
                        values[index] = "".join(
                            t.text or "" for t in cell.iter(MAIN_NS + "t")
                        )
                    elif value is not None:
                        values[index] = value.text
                yield values
                root.clear()


def read_xlsx(path: Path) -> pd.DataFrame:
    rows = _xlsx_rows(path)
    headers = next(rows)
    if any(header is None for header in headers):
        raise ValueError(f"Blank header in {path}")
    records = []
    for row in rows:
        records.append((row + [None] * len(headers))[: len(headers)])
    return pd.DataFrame(records, columns=headers)


def code(series: pd.Series, width: int) -> pd.Series:
    numeric = pd.to_numeric(series, errors="raise")
    if numeric.isna().any() or (numeric % 1 != 0).any():
        raise ValueError("Geography codes must be non-null integers")
    return numeric.astype("int64").astype(str).str.zfill(width)


def integer(series: pd.Series, name: str) -> pd.Series:
    numeric = pd.to_numeric(series, errors="raise")
    if numeric.isna().any() or (numeric % 1 != 0).any():
        raise ValueError(f"{name} must contain non-null integers")
    return numeric.astype("int64")


def unique(data: pd.DataFrame, columns: list[str], label: str) -> None:
    if data[columns].isna().any().any() or data.duplicated(columns).any():
        raise ValueError(f"Null or duplicate {label} key: {columns}")


def write_parquet(data: pd.DataFrame, path: Path) -> None:
    data.to_parquet(path, engine="pyarrow", compression="zstd", index=False)
    print(f"  {path.name}: {len(data):,} rows")


def state_annual(raw_dir: Path, output: Path) -> pd.DataFrame:
    data = pd.read_csv(raw_dir / FILES["state_annual"], low_memory=False)
    required = {"ANIO", "CVE_GEO", "ENTIDAD", "POB_MIT_ANIO", "HOM_MIT_ANIO", "MUJ_MIT_ANIO", "NAC"}
    if not required.issubset(data):
        raise ValueError(f"Missing state indicators: {sorted(required - set(data))}")
    data = data.drop(columns=[c for c in data if c.startswith("fn_") or c in {"RENGLON", "ENTIDAD_FEDERATIVA", "FECHA"}])
    data = data.rename(columns={"ANIO": "year", "CVE_GEO": "state_code", "ENTIDAD": "state_name", **STATE_RENAMES})
    data.columns = [column if column in {"year", "state_code", "state_name"} else column.lower() for column in data]
    data["state_code"] = code(data["state_code"], 2)
    data["year"] = integer(data["year"], "year")
    for column in data.columns.difference(["state_code", "state_name", "year"]):
        data[column] = pd.to_numeric(data[column].replace("ND", pd.NA), errors="coerce")
    for column in ("population_total", "population_men", "population_women", "births"):
        data[column] = integer(data[column], column)
    if not (data.population_men + data.population_women == data.population_total).all():
        raise ValueError("State male and female counts do not sum to total")
    unique(data, ["state_code", "year"], "state annual")
    data["estimate_phase"] = data.year.map(lambda year: "projection" if year >= 2020 else "reconstruction")
    data["source_file"] = Path(FILES["state_annual"]).name
    write_parquet(data, output)
    return data


def state_age(raw_dir: Path, output: Path, annual: pd.DataFrame) -> None:
    data = pd.read_csv(raw_dir / FILES["state_age"])
    required = {"AÑO", "CVE_GEO", "ENTIDAD", "EDAD", "SEXO", "POBLACION"}
    if not required.issubset(data):
        raise ValueError(f"Missing state population fields: {sorted(required - set(data))}")
    data = data.rename(columns={"AÑO": "year", "CVE_GEO": "state_code", "ENTIDAD": "state_name", "EDAD": "age", "SEXO": "sex", "POBLACION": "population"})
    data["state_code"] = code(data.state_code, 2)
    for column in ("year", "age", "population"):
        data[column] = integer(data[column], column)
    if set(data.sex) != {"Hombres", "Mujeres"} or not data.age.between(0, 109).all():
        raise ValueError("Unexpected state age or sex values")
    unique(data, ["state_code", "year", "sex", "age"], "state age")
    summed = data.groupby(["state_code", "year", "sex"], as_index=False).population.sum()
    expected = annual.melt(id_vars=["state_code", "year"], value_vars=["population_men", "population_women"], var_name="sex", value_name="expected")
    expected.sex = expected.sex.map({"population_men": "Hombres", "population_women": "Mujeres"})
    checked = summed.merge(expected, on=["state_code", "year", "sex"], validate="one_to_one", indicator=True)
    if len(checked) != len(summed) or not (checked._merge == "both").all() or not (checked.population == checked.expected).all():
        raise ValueError("State age/sex population does not reconcile to annual indicators")
    data["estimate_phase"] = data.year.map(lambda year: "projection" if year >= 2020 else "reconstruction")
    data["source_file"] = Path(FILES["state_age"]).name
    write_parquet(data, output)


def municipality_annual(raw_dir: Path, output: Path) -> pd.DataFrame:
    data = read_xlsx(raw_dir / FILES["municipality_annual"])
    required = {"CLAVE", "CLAVE_ENT", "NOM_ENT", "NOM_MUN", "AÑO", "POB_MIT_MUN", "HOM_MIT_AÑO", "MUJ_MIT_AÑO"}
    if not required.issubset(data):
        raise ValueError(f"Missing municipal indicators: {sorted(required - set(data))}")
    data = data.rename(columns={"CLAVE": "municipality_code", "CLAVE_ENT": "state_code", "NOM_ENT": "state_name", "NOM_MUN": "municipality_name", "AÑO": "year", **MUNICIPALITY_RENAMES})
    data.columns = [column if column in {"municipality_code", "state_code", "state_name", "municipality_name", "year"} else column.lower() for column in data]
    data["municipality_code"] = code(data.municipality_code, 5)
    data["state_code"] = code(data.state_code, 2)
    data["year"] = integer(data.year, "year")
    for column in data.columns.difference(["municipality_code", "state_code", "state_name", "municipality_name", "year"]):
        data[column] = pd.to_numeric(data[column], errors="coerce")
    for column in ("population_total", "population_men", "population_women"):
        data[column] = integer(data[column], column)
    if not (data.population_men + data.population_women == data.population_total).all():
        raise ValueError("Municipal male and female counts do not sum to total")
    if not (data.municipality_code.str[:2] == data.state_code).all():
        raise ValueError("Municipal code prefix differs from state code")
    unique(data, ["municipality_code", "year"], "municipality annual")
    data["estimate_phase"] = data.year.map(lambda year: "projection" if year >= 2021 else "reconstruction")
    data["source_file"] = Path(FILES["municipality_annual"]).name
    write_parquet(data, output)
    return data


def municipality_age(raw_dir: Path, output: Path, annual: pd.DataFrame) -> None:
    source = raw_dir / FILES["municipality_age"]
    columns = list(pd.read_csv(source, nrows=0))
    missing = set(AGE_COLUMNS) | {"CLAVE", "CLAVE_ENT", "NOM_MUN", "SEXO", "ANO", "POB_TOTAL"}
    if not missing.issubset(columns):
        raise ValueError(f"Missing municipal age fields: {sorted(missing - set(columns))}")
    expected = annual.set_index(["municipality_code", "year"])[["population_men", "population_women"]]
    writer: pq.ParquetWriter | None = None
    seen: set[tuple[str, int, str]] = set()
    row_count = 0
    try:
        for chunk in pd.read_csv(source, chunksize=20_000):
            chunk = chunk.rename(columns={"CLAVE": "municipality_code", "CLAVE_ENT": "state_code", "SEXO": "sex", "ANO": "year", "POB_TOTAL": "population_total"})
            chunk.municipality_code = code(chunk.municipality_code, 5)
            chunk.state_code = code(chunk.state_code, 2)
            chunk.year = integer(chunk.year, "year")
            chunk.sex = chunk.sex.map({"HOMBRES": "Hombres", "MUJERES": "Mujeres"})
            if chunk.sex.isna().any() or not (chunk.municipality_code.str[:2] == chunk.state_code).all():
                raise ValueError("Unexpected municipal age geography or sex")
            if not (chunk[list(AGE_COLUMNS)].sum(axis=1) == chunk.population_total).all():
                raise ValueError("Municipal five-year ages do not sum to their source total")
            keys = set(map(tuple, chunk[["municipality_code", "year", "sex"]].itertuples(index=False, name=None)))
            if len(keys) != len(chunk) or seen.intersection(keys):
                raise ValueError("Duplicate municipal population age source key")
            seen.update(keys)
            lookup = expected.reindex(pd.MultiIndex.from_frame(chunk[["municipality_code", "year"]]))
            if lookup.isna().any().any():
                raise ValueError("Municipal age row lacks an annual indicator row")
            expected_total = pd.Series(
                [men if sex == "Hombres" else women for men, women, sex in zip(lookup.population_men, lookup.population_women, chunk.sex)],
                index=chunk.index,
            )
            if not (chunk.population_total == expected_total).all():
                raise ValueError("Municipal age/sex population differs from annual indicators")
            long = chunk.melt(id_vars=["municipality_code", "state_code", "year", "sex"], value_vars=list(AGE_COLUMNS), var_name="source_age_column", value_name="population")
            long["age_start"] = long.source_age_column.map(lambda name: AGE_COLUMNS[name][0]).astype("int64")
            long["age_end"] = pd.Series(long.source_age_column.map(lambda name: AGE_COLUMNS[name][1]), dtype="Int64")
            long["age_band"] = long.source_age_column.map(lambda name: f"{AGE_COLUMNS[name][0]}+" if AGE_COLUMNS[name][1] is None else f"{AGE_COLUMNS[name][0]}-{AGE_COLUMNS[name][1]}")
            long = long.drop(columns="source_age_column")
            long["population"] = integer(long.population, "population")
            long["estimate_phase"] = long.year.map(lambda year: "projection" if year >= 2021 else "reconstruction")
            long["source_file"] = Path(FILES["municipality_age"]).name
            table = pa.Table.from_pandas(long, preserve_index=False)
            if writer is None:
                writer = pq.ParquetWriter(output, table.schema, compression="zstd")
            writer.write_table(table)
            row_count += len(long)
    finally:
        if writer is not None:
            writer.close()
    if len(seen) != len(annual) * 2 or row_count != len(seen) * len(AGE_COLUMNS):
        raise ValueError("Municipal age file does not cover every municipality-year and sex")
    print(f"  {output.name}: {row_count:,} rows")


def municipality_life_stage(raw_dir: Path, output: Path, annual: pd.DataFrame) -> None:
    data = pd.read_csv(raw_dir / FILES["municipality_life_stage"])
    required = {"CLAVE", "CLAVE_ENT", "SEXO", "ANO", "POB_TOTAL", "POB_00_011", "POB_012_29", "POB_30_59", "POB_60_mm"}
    if not required.issubset(data):
        raise ValueError(f"Missing municipal life-stage fields: {sorted(required - set(data))}")
    data = data[list(required)].rename(columns={
        "CLAVE": "municipality_code", "CLAVE_ENT": "state_code", "SEXO": "sex",
        "ANO": "year", "POB_TOTAL": "population_total", "POB_00_011": "population_0_11",
        "POB_012_29": "population_12_29", "POB_30_59": "population_30_59",
        "POB_60_mm": "population_60_plus",
    })
    data.municipality_code = code(data.municipality_code, 5)
    data.state_code = code(data.state_code, 2)
    data.year = integer(data.year, "year")
    data.sex = data.sex.map({"HOMBRES": "Hombres", "MUJERES": "Mujeres"})
    if data.sex.isna().any() or not (data.municipality_code.str[:2] == data.state_code).all():
        raise ValueError("Unexpected municipal life-stage geography or sex")
    for column in ("population_total", "population_0_11", "population_12_29", "population_30_59", "population_60_plus"):
        data[column] = integer(data[column], column)
    groups = ["population_0_11", "population_12_29", "population_30_59", "population_60_plus"]
    if not (data[groups].sum(axis=1) == data.population_total).all():
        raise ValueError("Municipal life stages do not sum to total population")
    unique(data, ["municipality_code", "year", "sex"], "municipality life-stage")
    summed = data.groupby(["municipality_code", "year"], as_index=False).population_total.sum()
    checked = summed.merge(annual[["municipality_code", "year", "population_total"]], on=["municipality_code", "year"], validate="one_to_one", suffixes=("_stage", "_annual"))
    if len(checked) != len(annual) or not (checked.population_total_stage == checked.population_total_annual).all():
        raise ValueError("Municipal life-stage totals differ from annual indicators")
    data["estimate_phase"] = data.year.map(lambda year: "projection" if year >= 2021 else "reconstruction")
    data["source_file"] = Path(FILES["municipality_life_stage"]).name
    write_parquet(data, output)


def fertility(raw_dir: Path, output: Path, annual: pd.DataFrame) -> None:
    data = read_xlsx(raw_dir / FILES["fertility"])
    required = {"AÑO", "CVE_GEO", "ENTIDAD", "GPO_EDAD", "TASAS", "NACIMIENTOS"}
    if not required.issubset(data):
        raise ValueError(f"Missing fertility fields: {sorted(required - set(data))}")
    data = data[list(required)].rename(columns={"AÑO": "year", "CVE_GEO": "state_code", "ENTIDAD": "state_name", "GPO_EDAD": "mother_age_band", "TASAS": "fertility_rate_per_1000", "NACIMIENTOS": "births"})
    data.state_code = code(data.state_code, 2)
    data.year = integer(data.year, "year")
    data.births = integer(data.births, "births")
    data.fertility_rate_per_1000 = pd.to_numeric(data.fertility_rate_per_1000, errors="raise")
    ages = data.mother_age_band.str.extract(r"^(\d+)-(\d+)$")
    if ages.isna().any().any():
        raise ValueError("Unexpected maternal age band")
    data["mother_age_start"] = ages[0].astype("int64")
    data["mother_age_end"] = ages[1].astype("int64")
    unique(data, ["state_code", "year", "mother_age_band"], "fertility")
    summed = data.groupby(["state_code", "year"], as_index=False).births.sum()
    checked = summed.merge(annual[["state_code", "year", "births"]], on=["state_code", "year"], validate="one_to_one", suffixes=("_age", "_annual"), indicator=True)
    if len(checked) != len(annual) or not (checked._merge == "both").all() or not (checked.births_age == checked.births_annual).all():
        raise ValueError("Fertility births do not reconcile to annual indicators")
    data["estimate_phase"] = data.year.map(lambda year: "projection" if year >= 2020 else "reconstruction")
    data["source_file"] = Path(FILES["fertility"]).name
    write_parquet(data, output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=RAW_DIR)
    parser.add_argument("--output-dir", type=Path, default=CLEAN_DIR)
    args = parser.parse_args()
    raw_dir = args.raw_dir.resolve()
    output_dir = args.output_dir.resolve()
    for filename in FILES.values():
        if not (raw_dir / filename).is_file():
            raise FileNotFoundError(raw_dir / filename)
    output_dir.mkdir(parents=True, exist_ok=True)
    pending = {name: output_dir / (filename + ".next") for name, filename in OUTPUTS.items()}
    print(f"CONAPO raw: {raw_dir}")
    print(f"Clean output: {output_dir}")
    state = state_annual(raw_dir, pending["state_annual"])
    state_age(raw_dir, pending["state_age"], state)
    municipality = municipality_annual(raw_dir, pending["municipality_annual"])
    municipality_age(raw_dir, pending["municipality_age"], municipality)
    municipality_life_stage(raw_dir, pending["municipality_life_stage"], municipality)
    fertility(raw_dir, pending["fertility"], state)
    for name, filename in OUTPUTS.items():
        pending[name].replace(output_dir / filename)
    print("CONAPO Parquet files validated and published.")


if __name__ == "__main__":
    main()
