"""Export the CONAPO state profiles behind "Conoce tu estado".

Writes web/public/data/estados/index.json (state list plus the per-year values
the choropleth needs) and one file per geography, 00.json (national) through
32.json, so the page only downloads the state a reader opens.

Population figures are CONAPO's reconstruction (1970-2019) only; its
projections, which start in 2020, are not published on the site. Remittances
are observed quarterly flows (2013-2024), not projections, so they keep their
full range.

Run from the repository root after the CONAPO and remittance ingests:
    python3 web/scripts/export_conapo_states.py
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "election_data.db"
OUT_DIR = ROOT / "web" / "public" / "data" / "estados"

SCHEMA_VERSION = 1
NATIONAL = "00"
FIRST_YEAR, LAST_YEAR = 1970, 2019
TOP_MUNICIPIOS = 10

# fact_conapo_state_annual column -> exported series key. Rates keep two
# decimals; counts are integers.
SERIES = {
    "population_total": "population",
    "births": "births",
    "deaths": "deaths",
    "cre_nat": "naturalIncrease",
    "t_bru_nat": "birthRate",
    "t_bru_mor": "deathRate",
    "life_expectancy": "lifeExpectancy",
    "life_expectancy_men": "lifeExpectancyMen",
    "life_expectancy_women": "lifeExpectancyWomen",
    "total_fertility_rate": "fertilityRate",
    "adolescent_fertility_rate": "adolescentFertility",
    "infant_mortality_rate": "infantMortality",
    "median_age": "medianAge",
    "child_dependency_ratio": "childDependency",
    "older_dependency_ratio": "olderDependency",
    "dependency_ratio": "dependency",
    "aging_index": "agingIndex",
}
COUNTS = {"population", "births", "deaths", "naturalIncrease"}

# Shown on the map; every one is comparable across states of different size.
MAP_METRICS = {
    "medianAge": {"label": "Edad mediana", "unit": "años", "decimals": 0},
    "lifeExpectancy": {"label": "Esperanza de vida", "unit": "años", "decimals": 1},
    "fertilityRate": {"label": "Hijos por mujer", "unit": "hijos", "decimals": 2},
    "infantMortality": {"label": "Mortalidad infantil", "unit": "por mil nacimientos", "decimals": 1},
    "agingIndex": {"label": "Índice de envejecimiento", "unit": "65+ por cada 100 menores de 15", "decimals": 1},
}

SOURCE = {
    "population": "CONAPO, Conciliación demográfica 1950-2019 y proyecciones 2020-2070 (solo años de conciliación)",
    "remittances": "CONAPO con datos de Banco de México, remesas familiares por municipio, 2013-2024",
}


def connect() -> sqlite3.Connection:
    return sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)


def clean(value: float | int | None, count: bool) -> float | int | None:
    if value is None:
        return None
    return int(value) if count else round(float(value), 2)


def load_states(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT state_code, state_name FROM dim_conapo_state ORDER BY state_code").fetchall()
    if len(rows) != 33 or rows[0][0] != NATIONAL:
        raise ValueError("Expected the national geography plus 32 states in dim_conapo_state")
    return [{"code": code, "name": name, "stateId": int(code)} for code, name in rows]


def load_series(conn: sqlite3.Connection, years: list[int]) -> dict[str, dict[str, list]]:
    columns = ", ".join(SERIES)
    rows = conn.execute(
        f"SELECT state_code, year, {columns} FROM fact_conapo_state_annual "
        "WHERE estimate_phase = 'reconstruction' AND year BETWEEN ? AND ? ORDER BY state_code, year",
        (FIRST_YEAR, LAST_YEAR),
    ).fetchall()
    series: dict[str, dict[str, list]] = {}
    for code, year, *values in rows:
        geography = series.setdefault(code, {key: [] for key in SERIES.values()})
        if len(geography["population"]) != years.index(year):
            raise ValueError(f"Non-contiguous years for {code} at {year}")
        for key, value in zip(SERIES.values(), values):
            geography[key].append(clean(value, key in COUNTS))
    return series


def load_voting_age(conn: sqlite3.Connection) -> dict[str, list[int]]:
    """18+ from single ages: the pyramid's five-year bands cannot cut at 18."""
    rows = conn.execute(
        "SELECT state_code, year, SUM(population) FROM fact_conapo_state_population_age_annual "
        "WHERE estimate_phase = 'reconstruction' AND age >= 18 AND year BETWEEN ? AND ? "
        "GROUP BY state_code, year ORDER BY state_code, year",
        (FIRST_YEAR, LAST_YEAR),
    ).fetchall()
    result: dict[str, list[int]] = {}
    for code, _, total in rows:
        result.setdefault(code, []).append(int(total))
    national = [sum(values[i] for values in result.values()) for i in range(LAST_YEAR - FIRST_YEAR + 1)]
    result[NATIONAL] = national
    return result


def load_pyramids(conn: sqlite3.Connection) -> tuple[list[str], dict[str, dict[str, list[list[int]]]]]:
    rows = conn.execute(
        "SELECT state_code, year, sex, age_start, age_band, population "
        "FROM view_conapo_population_pyramid ORDER BY state_code, year, age_start"
    ).fetchall()
    bands: list[str] = []
    pyramids: dict[str, dict[str, list[list[int]]]] = {}
    for code, year, sex, age_start, band, population in rows:
        if code == NATIONAL and year == FIRST_YEAR and sex == "Hombres":
            bands.append(band)
        key = "men" if sex == "Hombres" else "women"
        geography = pyramids.setdefault(code, {"men": [], "women": []})
        index = year - FIRST_YEAR
        while len(geography[key]) <= index:
            geography[key].append([])
        geography[key][index].append(int(population))
    return bands, pyramids


def load_remittances(conn: sqlite3.Connection) -> tuple[list[str], dict[str, dict]]:
    periods = [
        f"{year}-T{quarter}" for year, quarter in conn.execute(
            "SELECT DISTINCT year, quarter FROM fact_conapo_remittances_municipality_quarterly ORDER BY year, quarter"
        )
    ]
    rows = conn.execute(
        "SELECT state_code, year, quarter, SUM(remittances_usd_millions), MAX(migration_region) "
        "FROM fact_conapo_remittances_municipality_quarterly GROUP BY state_code, year, quarter "
        "ORDER BY state_code, year, quarter"
    ).fetchall()
    result: dict[str, dict] = {}
    for code, _, _, total, region in rows:
        entry = result.setdefault(code, {"region": region, "quarterly": []})
        entry["quarterly"].append(round(total, 2))
    national = [round(sum(entry["quarterly"][i] for entry in result.values()), 2) for i in range(len(periods))]
    result[NATIONAL] = {"region": None, "quarterly": national}

    latest = int(periods[-1][:4])
    tops = conn.execute(
        "SELECT state_code, municipality_code, municipality_name, migration_intensity_grade, "
        "SUM(remittances_usd_millions) AS total "
        "FROM fact_conapo_remittances_municipality_quarterly "
        "WHERE year = ? AND municipality_code NOT LIKE '__999' "
        "GROUP BY municipality_code ORDER BY total DESC",
        (latest,),
    ).fetchall()
    for code, entry in result.items():
        scoped = [row for row in tops if code == NATIONAL or row[0] == code][:TOP_MUNICIPIOS]
        entry["topYear"] = latest
        entry["topMunicipios"] = [
            {"code": municipio, "name": name, "grade": grade, "usdMillions": round(total, 2)}
            for _, municipio, name, grade, total in scoped
        ]
    return periods, result


def main() -> None:
    years = list(range(FIRST_YEAR, LAST_YEAR + 1))
    with connect() as conn:
        states = load_states(conn)
        series = load_series(conn, years)
        voting_age = load_voting_age(conn)
        bands, pyramids = load_pyramids(conn)
        periods, remittances = load_remittances(conn)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    written = 0
    for state in states:
        code = state["code"]
        geography = series[code]
        pyramid = pyramids[code]
        if len(geography["population"]) != len(years) or len(pyramid["men"]) != len(years):
            raise ValueError(f"{code}: incomplete year coverage")
        for index, total in enumerate(geography["population"]):
            if sum(pyramid["men"][index]) + sum(pyramid["women"][index]) != total:
                raise ValueError(f"{code} {years[index]}: pyramid does not sum to population_total")
        if len(remittances[code]["quarterly"]) != len(periods):
            raise ValueError(f"{code}: incomplete remittance quarters")
        payload = {
            "schemaVersion": SCHEMA_VERSION,
            "code": code,
            "name": state["name"],
            "years": years,
            "series": {**geography, "votingAge": voting_age[code]},
            "pyramid": {"bands": bands, **pyramid},
            "remittances": {"periods": periods, **remittances[code]},
        }
        (OUT_DIR / f"{code}.json").write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
        )
        written += 1

    index = {
        "schemaVersion": SCHEMA_VERSION,
        "years": years,
        "source": SOURCE,
        "states": states,
        "metrics": MAP_METRICS,
        "map": {
            key: {state["code"]: series[state["code"]][key] for state in states if state["code"] != NATIONAL}
            for key in MAP_METRICS
        },
        "national": {key: series[NATIONAL][key] for key in MAP_METRICS},
    }
    (OUT_DIR / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    sizes = sorted((path.stat().st_size for path in OUT_DIR.glob("[0-9]*.json")), reverse=True)
    print(f"Wrote {written} geography files to {OUT_DIR} (largest {sizes[0] / 1024:.1f} KB)")
    print(f"Wrote {OUT_DIR / 'index.json'} ({(OUT_DIR / 'index.json').stat().st_size / 1024:.1f} KB)")


if __name__ == "__main__":
    main()
