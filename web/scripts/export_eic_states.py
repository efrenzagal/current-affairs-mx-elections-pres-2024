"""Export the Encuesta Intercensal 2025 scorecard behind "Conoce tu estado".

Writes web/public/data/estados/eic2025.json: a curated set of indicators, in
categories, for the nation and the 32 states, each with its value, 90%
confidence interval, coefficient of variation and the state's rank.

Figures are INEGI's published results (`resultados_publicados` in
eic2025.duckdb), with INEGI's definitions and universes. The one exception is
income from work, which INEGI does not publish: it comes from this project's
own estimates over the microdata (`estimaciones`), computed with INEGI's method.

Rank 1 is the highest value, whatever the indicator measures. Sampling error
means close states cannot be told apart, so each rank also carries the range of
ranks the state could hold: `best` counts only states whose interval lies
entirely above this one, `worst` only those entirely below.

Run from the repository root after published_to_parquet.py and build_duckdb.py:
    python3 web/scripts/export_eic_states.py
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb


ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "state_scorecards" / "data" / "eic2025.duckdb"
OUT_PATH = ROOT / "web" / "public" / "data" / "estados" / "eic2025.json"

SCHEMA_VERSION = 1
NATIONAL = "00"
# INEGI's own reliability cut: above this coefficient of variation an
# estimate is imprecise and the page says so.
LOW_PRECISION_CV = 30
INCOME = "INGTRMEN"

# (id, label, detail, unit, decimals). `detail` names the universe whenever it
# is not "all residents" / "all dwellings", because a percentage is only
# readable with its denominator.
CATEGORIES = [
    ("poblacion", "Población", [
        ("MEDIANA_POBTOT", "Edad mediana", None, "años", 1),
        ("INDICE_ENV", "Índice de envejecimiento", "Personas de 65 años y más por cada 100 menores de 15", "", 1),
        ("TGF", "Hijos por mujer", "Tasa global de fecundidad, mujeres de 15 a 49 años", "", 2),
        ("PCN_PNACOE", "Nacieron en otra entidad", None, "%", 1),
        ("PCN_PRESEUA20", "Vivían en Estados Unidos en 2020", "Lugar de residencia en octubre de 2020", "%", 2),
    ]),
    ("identidad", "Identidad", [
        ("PCN_POB_IND", "Se considera indígena", None, "%", 1),
        ("PCN_P3YM_HLI", "Habla una lengua indígena", "Población de 3 años y más", "%", 1),
        ("PCN_POB_AFRO", "Se considera afromexicana", None, "%", 1),
    ]),
    ("educacion", "Educación", [
        ("GRAPROES", "Años de escolaridad", "Promedio, población de 15 años y más", "años", 1),
        ("PCN_P15YM_AN", "No sabe leer ni escribir", "Población de 15 años y más", "%", 1),
        ("PCN_P15YM_ES", "Tiene educación superior", "Población de 15 años y más", "%", 1),
        ("PCN_P6A14_NOA", "Niñas y niños que no van a la escuela", "Población de 6 a 14 años", "%", 1),
    ]),
    ("trabajo", "Trabajo", [
        ("PCN_PEA", "Participa en la economía", "Población de 12 años y más que trabaja o busca trabajo", "%", 1),
        ("PCN_PDESOCUP", "Desocupación", "De la población económicamente activa", "%", 1),
        ("PCN_POCUP_ASA", "Trabaja por un salario", "De la población ocupada", "%", 1),
        ("PCN_POCUP_CPRO", "Trabaja por su cuenta", "De la población ocupada", "%", 1),
        (INCOME, "Ingreso mensual por trabajo", "Promedio de la población ocupada que lo declaró. Cálculo propio con los microdatos", "pesos", 0),
    ]),
    ("movilidad", "Traslado al trabajo", [
        ("PCN_POCUP_2HOR", "Tarda de una a dos horas en llegar", "De la población ocupada", "%", 1),
        ("PCN_POCUP_TROL", "Va en transporte público", "Camión, autobús, combi, colectivo o trolebús; de la población ocupada", "%", 1),
        ("PCN_POCUP_AUTO", "Va en automóvil", "De la población ocupada", "%", 1),
    ]),
    ("salud", "Salud", [
        ("PCN_PSINDER", "Sin afiliación a servicios de salud", None, "%", 1),
        ("PCN_PDER_IMSS", "Afiliada al IMSS", None, "%", 1),
        ("PCN_PDER_IMSSB", "Afiliada o con acceso a IMSS-Bienestar", None, "%", 1),
        ("PCN_PUSU_IPRIV", "Se atiende en servicios privados", None, "%", 1),
        ("PCN_PUSU_CFARM", "Se atiende en consultorios de farmacia", None, "%", 1),
    ]),
    ("hogares", "Ingresos del hogar", [
        ("PCN_HOG_GOB", "Recibe programas de gobierno", "De los hogares", "%", 1),
        ("PCN_HOG_OPAIS", "Recibe dinero del extranjero", "De los hogares", "%", 1),
        ("PCN_HOG_JUB", "Recibe jubilación o pensión", "De los hogares", "%", 1),
    ]),
    ("alimentacion", "Alimentación", [
        ("PCN_HOG_ALIM_N", "Sin acceso a alimentos por falta de dinero", "De los hogares, últimos tres meses", "%", 1),
        ("PCN_ING_ADL1", "Algún adulto sintió hambre y no comió", "De los hogares, últimos tres meses", "%", 1),
        ("PCN_ING_MEN2", "Algún menor se acostó con hambre", "De los hogares, últimos tres meses", "%", 1),
    ]),
    ("desplazamiento", "Desplazamiento forzado", [
        ("PCN_DESP_INSEG", "Dejó su casa por inseguridad o violencia", "Hogares con al menos un integrante desplazado", "%", 2),
        ("PCN_DESP_CATAS", "Dejó su casa por una catástrofe", "Hogares con al menos un integrante desplazado", "%", 2),
    ]),
    ("vivienda", "Vivienda", [
        ("PCN_VPH_C_SERV", "Con luz, agua de la red pública y drenaje", None, "%", 1),
        ("PCN_VPH_AGUADV", "Con agua entubada", "Dentro de la vivienda o del terreno", "%", 1),
        ("PCN_VPH_DRENAJ", "Con drenaje", None, "%", 1),
        ("PCN_VPH_PISOTI", "Con piso de tierra", None, "%", 1),
        ("PCN_VPH_2_5OCU", "Con hacinamiento", "Más de 2.5 personas por cuarto", "%", 1),
        ("PCN_VPH_PROPIA", "Habitada por su dueño", None, "%", 1),
        ("PCN_VPH_ALQUI", "Rentada", None, "%", 1),
        ("PCN_VPH_NOESCRI", "Propia y sin escrituras", "De las viviendas propias", "%", 1),
    ]),
    ("bienes", "Bienes y conectividad", [
        ("PCN_VPH_INTER", "Con internet", None, "%", 1),
        ("PCN_VPH_PC", "Con computadora, laptop o tableta", None, "%", 1),
        ("PCN_VPH_AUTOM", "Con automóvil o camioneta", None, "%", 1),
        ("PCN_VPH_AIRE", "Con aire acondicionado", None, "%", 1),
        ("PCN_VPH_PANEL", "Con panel solar", None, "%", 1),
    ]),
]

SOURCE = (
    "INEGI, Encuesta Intercensal 2025, Principales resultados (levantamiento del 6 de octubre al "
    "14 de noviembre de 2025). Intervalos de confianza al 90%"
)


def load(con: duckdb.DuckDBPyConnection, ids: list[str]) -> dict[str, dict[str, list[float]]]:
    """indicator -> geography code -> [value, li, ls, cv]."""
    rows = con.execute(
        "SELECT indicator, CVE_ENT, value, li, ls, cv FROM resultados_publicados "
        "WHERE geo_level IN ('nacional', 'estatal') AND indicator IN (SELECT unnest(?))",
        [ids],
    ).fetchall()
    rows += con.execute(
        "SELECT ?, CVE_ENT, value, li, ls, cv FROM estimaciones "
        "WHERE table_name = 'personas' AND variable = ? AND statistic = 'promedio' "
        "AND geo_level IN ('nacional', 'estatal')",
        [INCOME, INCOME],
    ).fetchall()
    data: dict[str, dict[str, list[float]]] = {}
    for indicator, code, value, li, ls, cv in rows:
        if None in (value, li, ls, cv):
            raise ValueError(f"{indicator} {code}: missing value or precision (MI/NA)")
        data.setdefault(indicator, {})[code] = [value, li, ls, cv]
    return data


def ranks(values: dict[str, list[float]]) -> dict[str, list[int]]:
    """code -> [rank, best, worst] among the 32 states; 1 is the highest value."""
    states = {code: row for code, row in values.items() if code != NATIONAL}
    result = {}
    for code, (value, li, ls, _) in states.items():
        others = [row for other, row in states.items() if other != code]
        rank = 1 + sum(row[0] > value for row in others)
        best = 1 + sum(row[1] > ls for row in others)     # entirely above
        worst = 32 - sum(row[2] < li for row in others)   # entirely below
        result[code] = [rank, best, worst]
    return result


def main() -> None:
    ids = [indicator for _, _, items in CATEGORIES for indicator, *_ in items]
    if len(ids) != len(set(ids)):
        raise ValueError("An indicator is listed twice")
    with duckdb.connect(str(DB_PATH), read_only=True) as con:
        data = load(con, [i for i in ids if i != INCOME])
        names = dict(con.execute("SELECT CVE_ENT, entidad FROM entidades").fetchall())

    codes = [NATIONAL] + [f"{n:02d}" for n in range(1, 33)]
    for indicator in ids:
        if sorted(data.get(indicator, {})) != codes:
            raise ValueError(f"{indicator}: expected the nation and all 32 states")

    def precise(row: list[float], decimals: int) -> list[float]:
        value, li, ls, cv = row
        return [round(value, decimals + 1), round(li, decimals + 1), round(ls, decimals + 1), round(cv, 1)]

    categories = []
    geographies: dict[str, dict[str, list]] = {code: {} for code in codes}
    for category_id, category_label, items in CATEGORIES:
        indicators = []
        for indicator, label, detail, unit, decimals in items:
            values = data[indicator]
            indicators.append({
                "id": indicator, "label": label, "detail": detail, "unit": unit, "decimals": decimals,
                "source": "propio" if indicator == INCOME else "inegi",
            })
            rank = ranks(values)
            for code in codes:
                row = precise(values[code], decimals)
                geographies[code][indicator] = row if code == NATIONAL else row + rank[code]
        categories.append({"id": category_id, "label": category_label, "indicators": indicators})

    low = sorted({ind for code, rows in geographies.items() for ind, row in rows.items() if row[3] > LOW_PRECISION_CV})
    payload = {
        "schemaVersion": SCHEMA_VERSION,
        "source": SOURCE,
        "lowPrecisionCv": LOW_PRECISION_CV,
        "fields": ["value", "li", "ls", "cv", "rank", "best", "worst"],
        "states": [{"code": code, "name": names.get(code, "Estados Unidos Mexicanos")} for code in codes],
        "categories": categories,
        "geographies": geographies,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {OUT_PATH} ({OUT_PATH.stat().st_size / 1024:.1f} KB): {len(ids)} indicators in "
          f"{len(categories)} categories. Indicators with a state above CV {LOW_PRECISION_CV}: {low or 'none'}")


if __name__ == "__main__":
    main()
