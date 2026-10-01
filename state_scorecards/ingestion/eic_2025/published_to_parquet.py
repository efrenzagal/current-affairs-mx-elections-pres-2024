"""Convert INEGI's published EIC 2025 results to long, validated Parquet.

INEGI's "Principales resultados por localidad de 50 000 y más habitantes" is a
wide Latin-1 CSV: one row per place and statistic (value, standard error, 90%
confidence limits, coefficient of variation) and one column per indicator. This
writes it long, one row per place x indicator with the five statistics side by
side, plus the indicator dictionary (section, name, description) from its UTF-8
descriptor.

These are the official figures, with INEGI's definitions and universes (e.g.
schooling for people aged 15+), so prefer them over `estimaciones` whenever an
indicator is published.

Geography (`geo_level`) follows INEGI's codes:
    nacional    00 000 0000
    estatal     EE 000 0000
    municipal   EE MMM 0000
    localidad   EE MMM LLLL   cities of 50,000+ residents
    resto       EE 997 9997   the state's localities under 50,000, together

`MI` (insufficient sample) and `NA` (not applicable) become nulls with the
code kept in `flag`. A trailing * or ** on a municipio name (censused, or
insufficient sample) moves to `municipio_flag`.

Usage:
    python3 state_scorecards/ingestion/eic_2025/published_to_parquet.py
"""

from __future__ import annotations

import csv
from pathlib import Path

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[3]
SOURCE_DIR = REPO_ROOT / "state_scorecards" / "data" / "eic_2025" / "resultados_publicados"
DATA_CSV = SOURCE_DIR / "conjunto_de_datos" / "conjunto_datos_eic2025_105.csv"
DICTIONARY_CSV = SOURCE_DIR / "diccionario_datos" / "diccionario_datos_eic2025_105.csv"
CLEAN_DIR = REPO_ROOT / "state_scorecards" / "data" / "clean" / "eic_2025"

ID_COLUMNS = ["CVEGEO", "CVE_ENT", "NOM_ENT", "CVE_MUN", "NOM_MUN", "CVE_LOC", "NOM_LOC", "ESTIMADOR"]
STATISTICS = {
    "Valor": "value",
    "Error estándar": "se",
    "Límite inferior de confianza": "li",
    "Límite superior de confianza": "ls",
    "Coeficiente de variación": "cv",
}
FLAGS = {"MI", "NA"}
MUNICIPIO_FLAGS = {"*": "censado", "**": "muestra_insuficiente"}


def read_dictionary() -> pd.DataFrame:
    """Indicator rows of the descriptor, each tagged with the section heading above it."""
    rows, section = [], None
    with DICTIONARY_CSV.open(encoding="utf-8") as handle:
        for record in csv.reader(handle):
            record += [""] * (6 - len(record))
            number, name, description, mnemonic = record[:4]
            if number and not name and not mnemonic:
                section = number  # a heading row: "POBLACIÓN", "EDUCACIÓN"...
            elif number.isdigit() and mnemonic and mnemonic not in ID_COLUMNS:
                rows.append({"indicator": mnemonic, "section": section, "name": name,
                             "description": description, "position": int(number)})
    return pd.DataFrame(rows)


def geo_level(row: pd.Series) -> str:
    if row.CVE_ENT == "00":
        return "nacional"
    if row.CVE_MUN == "000":
        return "estatal"
    if row.CVE_MUN == "997":
        return "resto"
    return "municipal" if row.CVE_LOC == "0000" else "localidad"


def main() -> None:
    wide = pd.read_csv(DATA_CSV, dtype=str, encoding="latin1", keep_default_na=False)
    dictionary = read_dictionary()

    indicators = [column for column in wide.columns if column not in ID_COLUMNS]
    if set(indicators) != set(dictionary.indicator):
        raise ValueError(f"Data and dictionary disagree: {sorted(set(indicators) ^ set(dictionary.indicator))}")
    if set(wide.ESTIMADOR) != set(STATISTICS):
        raise ValueError(f"Unexpected ESTIMADOR values: {sorted(set(wide.ESTIMADOR) - set(STATISTICS))}")
    per_place = wide.groupby("CVEGEO").ESTIMADOR.agg(["size", "nunique"])
    if not (per_place["size"].eq(5) & per_place["nunique"].eq(5)).all():
        raise ValueError("Every place must carry each of the five statistics exactly once")

    long = wide.melt(id_vars=ID_COLUMNS, value_vars=indicators, var_name="indicator", value_name="raw")
    long["number"] = pd.to_numeric(long.raw.str.replace(",", "", regex=False), errors="coerce")
    unparsed = long[long.number.isna() & ~long.raw.isin(FLAGS)]
    if not unparsed.empty:
        raise ValueError(f"Unparsed cells: {unparsed.raw.value_counts().head().to_dict()}")

    keys = ["CVEGEO", "indicator"]
    stats = long.pivot(index=keys, columns="ESTIMADOR", values="number").rename(columns=STATISTICS)
    flag = long[long.ESTIMADOR == "Valor"].set_index(keys).raw.where(lambda raw: raw.isin(FLAGS))
    places = wide.drop_duplicates("CVEGEO")[ID_COLUMNS[:-1]].copy()
    marker = places.NOM_MUN.str.extract(r"(\*+)$")[0]
    places["municipio_flag"] = marker.map(MUNICIPIO_FLAGS)
    places["NOM_MUN"] = places.NOM_MUN.str.rstrip("*")
    places["geo_level"] = places.apply(geo_level, axis=1)

    result = (
        stats[list(STATISTICS.values())].join(flag.rename("flag")).reset_index()
        .merge(places, on="CVEGEO", how="left")
        .merge(dictionary[["indicator", "section", "name"]], on="indicator", how="left")
    )
    result = result[["CVEGEO", "geo_level", "CVE_ENT", "NOM_ENT", "CVE_MUN", "NOM_MUN", "CVE_LOC", "NOM_LOC",
                     "municipio_flag", "section", "indicator", "name", "value", "se", "li", "ls", "cv", "flag"]]

    levels = places.geo_level.value_counts()
    if levels.get("nacional") != 1 or levels.get("estatal") != 32 or levels.get("resto") != 32:
        raise ValueError(f"Unexpected geography: {levels.to_dict()}")
    state = result[(result.geo_level == "estatal") & (result.indicator == "POBTOT")]
    national = result[(result.geo_level == "nacional") & (result.indicator == "POBTOT")].value.iloc[0]
    if abs(state.value.sum() - national) > 1:
        raise ValueError("State populations do not sum to the national total")

    CLEAN_DIR.mkdir(parents=True, exist_ok=True)
    result.to_parquet(CLEAN_DIR / "resultados_publicados.parquet", index=False)
    dictionary.to_parquet(CLEAN_DIR / "indicadores_publicados.parquet", index=False)
    print(f"Wrote {len(result):,} rows: {len(indicators)} indicators x {len(places):,} places "
          f"({', '.join(f'{k} {v}' for k, v in levels.items())})")


if __name__ == "__main__":
    main()
