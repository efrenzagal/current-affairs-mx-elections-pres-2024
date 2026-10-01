"""Export INEGI's state GDP (PIBE) for the economy section of "Conoce tu estado".

Writes web/public/data/estados/economia.json: every geography in one file,
because the state ranking needs all 32 states at once and the whole thing is
smaller than a single CONAPO profile once gzipped.

All values are millions of 2018 pesos. INEGI's constant-price series is
additive, and the exporter refuses to write unless it still is: sectors sum to
their group, groups to total value added, value added plus net product taxes to
GDP, and the 32 states to the national figure.

Coverage differs by level: GDP, value added and the three activity groups run
1980-2024 (INEGI's retropolated series joined to the 2003+ release); the 20
sectors exist only from 2003.

Run from the repository root after the PIBE ingest:
    python3 web/scripts/export_pibe_states.py
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "election_data.db"
OUT_PATH = ROOT / "web" / "public" / "data" / "estados" / "economia.json"

SCHEMA_VERSION = 1
NATIONAL = "00"
UNIT = "Millones de pesos a precios de 2018"
VAB = "Valor agregado bruto"
GDP = "Producto interno bruto, a precios de mercado"
TAXES = "Impuestos sobre los productos, netos"
# Relative tolerance for the additivity checks; the warehouse agrees to 1e-15.
TOLERANCE = 1e-9

# Activity id -> label on the page. Each sector's group comes from the
# warehouse (activity_group), checked against WAREHOUSE_GROUPS.
GROUPS = {
    "PRIMARY": "Primarias",
    "SECONDARY": "Secundarias",
    "TERTIARY": "Terciarias",
}
WAREHOUSE_GROUPS = {
    "Actividades primarias": "PRIMARY",
    "Actividades secundarias": "SECONDARY",
    "Actividades terciarias": "TERTIARY",
}

# SCIAN sector -> short label for the page. INEGI's full names are kept in
# `name` for tooltips and the methodology; these fit a ranked list.
SECTOR_LABELS = {
    "11": "Agropecuario, forestal y pesca",
    "21": "Minería",
    "22": "Electricidad, agua y gas",
    "23": "Construcción",
    "31-33": "Manufacturas",
    "43": "Comercio al por mayor",
    "46": "Comercio al por menor",
    "48-49": "Transportes y almacenamiento",
    "51": "Información en medios masivos",
    "52": "Servicios financieros",
    "53": "Servicios inmobiliarios",
    "54": "Servicios profesionales",
    "55": "Corporativos",
    "56": "Apoyo a los negocios",
    "61": "Educación",
    "62": "Salud y asistencia social",
    "71": "Esparcimiento y cultura",
    "72": "Alojamiento y alimentos",
    "81": "Otros servicios",
    "93": "Gobierno",
}

SOURCE = (
    "INEGI, Producto Interno Bruto por Entidad Federativa (PIBE), base 2018. "
    "Serie retropolada 1980-2002 y serie detallada 2003-2024"
)


def connect() -> sqlite3.Connection:
    return sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)


def close(a: float, b: float) -> bool:
    return abs(a - b) <= TOLERANCE * max(abs(a), abs(b), 1.0)


def load_codes(conn: sqlite3.Connection) -> dict[str, str]:
    """PIBE geography name -> CONAPO state code, so both files share keys."""
    rows = conn.execute("SELECT pibe_geography, state_code FROM dim_conapo_state ORDER BY state_code").fetchall()
    if len(rows) != 33 or rows[0][1] != NATIONAL or any(name is None for name, _ in rows):
        raise ValueError("Expected the national geography plus 32 states, each mapped to a PIBE geography")
    return dict(rows)


def main() -> None:
    with connect() as conn:
        codes = load_codes(conn)
        rows = conn.execute(
            "SELECT geography, year, concept_name, activity_level, activity_id, activity_name, activity_group, value "
            "FROM fact_pibe_state_annual WHERE unit = ? ORDER BY geography, year",
            (UNIT,),
        ).fetchall()

    years = sorted({row[1] for row in rows})
    if years != list(range(years[0], years[-1] + 1)):
        raise ValueError(f"Non-contiguous PIBE years: {years}")
    sector_years = sorted({row[1] for row in rows if row[3] == "sector"})
    if sector_years != list(range(sector_years[0], years[-1] + 1)):
        raise ValueError(f"Sectors do not run contiguously to {years[-1]}")

    # (code, year) -> {"gdp", "taxes", "vab", group ids, sector ids}
    cells: dict[tuple[str, int], dict[str, float]] = {}
    sector_names: dict[str, str] = {}
    sector_groups: dict[str, str] = {}
    for geography, year, concept, level, activity_id, activity_name, activity_group, value in rows:
        if geography not in codes:
            raise ValueError(f"PIBE geography {geography!r} is not in dim_conapo_state")
        if value is None:
            raise ValueError(f"{geography} {year} {activity_id}: missing value")
        cell = cells.setdefault((codes[geography], year), {})
        if concept == GDP:
            key = "gdp"
        elif concept == TAXES:
            key = "taxes"
        elif concept != VAB:
            raise ValueError(f"Unexpected concept {concept!r}")
        elif level == "total":
            key = "vab"
        elif level in ("activity_group", "sector"):
            key = activity_id
        else:
            continue  # subsectors are not published on the page
        if key in cell:
            raise ValueError(f"{geography} {year}: duplicate {key}")
        cell[key] = value
        if level == "sector":
            sector_names[activity_id] = activity_name
            sector_groups[activity_id] = WAREHOUSE_GROUPS[activity_group]
    if set(sector_names) != set(SECTOR_LABELS):
        raise ValueError(f"Sector ids changed: {sorted(set(sector_names) ^ set(SECTOR_LABELS))}")

    sector_ids = list(SECTOR_LABELS)
    state_codes = sorted(set(codes.values()))
    for code in state_codes:
        for year in years:
            cell = cells.get((code, year))
            if cell is None or not {"gdp", "taxes", "vab", *GROUPS} <= cell.keys():
                raise ValueError(f"{code} {year}: incomplete aggregates")
            if not close(cell["vab"] + cell["taxes"], cell["gdp"]):
                raise ValueError(f"{code} {year}: value added plus taxes is not GDP")
            if not close(sum(cell[group] for group in GROUPS), cell["vab"]):
                raise ValueError(f"{code} {year}: activity groups do not sum to value added")
            if year >= sector_years[0]:
                if not set(sector_ids) <= cell.keys():
                    raise ValueError(f"{code} {year}: missing sectors")
                for group in GROUPS:
                    total = sum(cell[s] for s in sector_ids if sector_groups[s] == group)
                    if not close(total, cell[group]):
                        raise ValueError(f"{code} {year}: {group} sectors do not sum to the group")
    for year in years:
        for key in ("gdp", "vab", *GROUPS, *(sector_ids if year >= sector_years[0] else [])):
            summed = sum(cells[(code, year)][key] for code in state_codes if code != NATIONAL)
            if not close(summed, cells[(NATIONAL, year)][key]):
                raise ValueError(f"{year} {key}: states do not sum to the national figure")

    def series(code: str, key: str, span: list[int]) -> list[float]:
        return [round(cells[(code, year)][key], 1) for year in span]

    geographies = {
        code: {
            "gdp": series(code, "gdp", years),
            "valueAdded": series(code, "vab", years),
            "groups": {group: series(code, group, years) for group in GROUPS},
            "sectors": {sector: series(code, sector, sector_years) for sector in sector_ids},
        }
        for code in state_codes
    }
    payload = {
        "schemaVersion": SCHEMA_VERSION,
        "unit": "millones de pesos de 2018",
        "source": SOURCE,
        "years": years,
        "sectorYears": sector_years,
        "groups": [{"id": group, "label": label} for group, label in GROUPS.items()],
        "sectors": [
            {"id": sector, "label": SECTOR_LABELS[sector], "name": sector_names[sector], "group": sector_groups[sector]}
            for sector in sector_ids
        ],
        "geographies": geographies,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {OUT_PATH} ({OUT_PATH.stat().st_size / 1024:.1f} KB): "
          f"{len(geographies)} geographies, {years[0]}-{years[-1]}, sectors {sector_years[0]}-{sector_years[-1]}")


if __name__ == "__main__":
    main()
