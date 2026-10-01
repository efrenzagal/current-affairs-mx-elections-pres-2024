"""Pre-compute every Encuesta Intercensal 2025 estimate, with INEGI's precision.

For each variable of viviendas, personas and migrantes, at national, state and
municipio level:

* categorical variables -> for every category, the weighted **total** and the
  **porcentaje** of the variable's universe (records where it is not null;
  "No especificado" counts as a category, as INEGI does);
* numeric variables -> the **promedio** over values inside the descriptor's
  valid range (sentinels such as 999 / 999999 / 98 are excluded), and, when
  the variable has at most 60 distinct values, its categories as well;
* ``_TOTAL`` -> the weighted number of records (POBTOT, VIVPARHAB, migrants).

Every estimate carries its standard error, 90% confidence limits and
coefficient of variation computed exactly as INEGI does. The method was
reverse-engineered from the published "Principales resultados" and reproduces
all 15,052 published value/SE/CI/CV figures tested (see validate_against_published):

1. Ultimate-cluster variance: strata = CVE_ENT+ESTRATO, PSU = UPM,
   factor n_h/(n_h-1), no finite-population correction.
2. Ratios (percentages, means) are linearized: z = (y - R x) / X.
3. Only PSUs containing at least one record of the domain enter the variance
   and the degrees of freedom -- for a total, PSUs where the category occurs;
   for a ratio, PSUs in the universe.
4. Municipios censused (COBERTURA 1) have no sampling error; municipios with
   insufficient sample (COBERTURA 3) report totals without error and their
   ratios are not published ("MI") -- kept here, flagged, without precision.
5. Confidence limits use Student's t(0.95) with dof = PSUs - strata.
6. Percentages get a logit-scale interval; totals and means a symmetric one.

Categories absent from a geography have no row: their total and percentage
are zero.

Multi-response questions (DHSERSAL1/2, MED_TRASLADO_*1-3, SEPARACION1-4,
FINANCIAMIENTO1-3) are estimated column by column, i.e. "first answer",
"second answer"; combining them (e.g. "affiliated to IMSS in any answer") is a
derived indicator, not part of this table.

Usage:
    python3 state_scorecards/ingestion/eic_2025/estimates.py
    python3 state_scorecards/ingestion/eic_2025/estimates.py --tables viviendas --validate
"""

from __future__ import annotations

import argparse
import re
import time
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[3]
CLEAN_DIR = REPO_ROOT / "state_scorecards" / "data" / "clean" / "eic_2025"
OUTPUT_DIR = CLEAN_DIR / "estimaciones"
PUBLISHED_CSV = (
    REPO_ROOT / "state_scorecards" / "data" / "eic_2025" / "resultados_publicados"
    / "conjunto_de_datos" / "conjunto_datos_eic2025_105.csv"
)

TABLES = ("viviendas", "personas", "migrantes")
CLUSTER_KEYS = "CVE_ENT, CVE_MUN, ESTRATO, UPM"

# Identifiers, design fields and pointers to other records: not estimable.
# MUN_* hold a municipio only meaningful paired with ENT_PAIS_*; origin-
# destination flows are a separate product.
SKIP = {
    "CVEGEO", "CVE_ENT", "CVE_MUN", "LOC50K", "ID_VIV", "ID_PERSONA", "ID_MII",
    "TIPO_REG", "COBERTURA", "ESTRATO", "UPM", "FACTOR", "NUMPER",
    "IDENT_MADRE", "IDENT_PADRE", "IDENT_PAREJA", "MPER", "MPERLS",
    "DUE1_NUM", "DUE2_NUM", "MUN_ASI", "MUN_RES_5A", "MUN_TRAB",
}
MAX_NUMERIC_CATEGORIES = 60

LEVELS = {
    "nacional": "'00000'",
    "estatal": "CVE_ENT || '000'",
    "municipal": "CVE_ENT || CVE_MUN",
}

Z = 1.6448536269514722


def t95(dof: pd.Series) -> pd.Series:
    """Upper 95% Student t quantile (Cornish-Fisher, A&S 26.7.5).

    Agrees with R's qt() to 1e-8 for dof >= 30; the smallest design here has
    dof well above that except for tiny domains, where it is still within 1e-3.
    """
    d = dof.where(dof > 0).astype(float)
    g = [
        (Z**3 + Z) / 4,
        (5 * Z**5 + 16 * Z**3 + 3 * Z) / 96,
        (3 * Z**7 + 19 * Z**5 + 17 * Z**3 - 15 * Z) / 384,
        (79 * Z**9 + 776 * Z**7 + 1482 * Z**5 - 1920 * Z**3 - 945 * Z) / 92160,
    ]
    return Z + sum(c / d ** (i + 1) for i, c in enumerate(g))


# Variable plan ------------------------------------------------------------------


def valid_numeric_condition(column: str, categories: pd.DataFrame) -> str:
    """SQL predicate keeping values inside the descriptor's numeric ranges.

    Ranges are written "lo..hi"; a lone "0" ("Ninguno", "Menos de un año") is a
    real zero. Every other single code (98, 99, 999, 999998, 999999) is a
    sentinel. A variable without categories (NUMPERS) is valid when not null.
    """
    parts = []
    for code in categories.code:
        match = re.fullmatch(r"(\d+)\.\.(\d+)", code.strip())
        if match:
            parts.append(f"{column} BETWEEN {int(match[1])} AND {int(match[2])}")
        elif code.strip() == "0":
            parts.append(f"{column} = 0")
    return "(" + " OR ".join(parts) + ")" if parts else f"{column} IS NOT NULL"


def variable_plan(con: duckdb.DuckDBPyConnection, table: str) -> list[dict]:
    variables = con.execute(
        "SELECT variable, data_type FROM codebook_variables WHERE table_name = ? ORDER BY position", [table]
    ).df()
    categories = con.execute("SELECT * FROM codebook_categories WHERE table_name = ?", [table]).df()
    plan = [{"variable": "_TOTAL", "kind": "total_records"}]
    for row in variables.itertuples():
        if row.variable in SKIP:
            continue
        if row.data_type == "Numérico":
            condition = valid_numeric_condition(row.variable, categories[categories.variable == row.variable])
            distinct = con.execute(f"SELECT count(DISTINCT {row.variable}) FROM {table}").fetchone()[0]
            plan.append({"variable": row.variable, "kind": "numeric", "valid": condition})
            if distinct <= MAX_NUMERIC_CATEGORIES:
                plan.append({"variable": row.variable, "kind": "categorical"})
        else:
            plan.append({"variable": row.variable, "kind": "categorical"})
    return plan


# Estimation ---------------------------------------------------------------------


def categorical_estimates(con: duckdb.DuckDBPyConnection, table: str, variable: str) -> pd.DataFrame:
    """Totals and percentages for every category of one variable, all levels."""
    category = "'total'" if variable == "_TOTAL" else f"CAST({variable} AS VARCHAR)"
    where = "" if variable == "_TOTAL" else f"WHERE {variable} IS NOT NULL"
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE cc AS
        SELECT {CLUSTER_KEYS}, CVE_ENT || ESTRATO AS h, {category} AS category,
               SUM(FACTOR)::DOUBLE AS y, count(*) AS nrec
        FROM {table} {where} GROUP BY ALL
    """)
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE cu AS
        SELECT {CLUSTER_KEYS}, h, SUM(y) AS x FROM cc GROUP BY ALL
    """)
    frames = []
    for level, geo in LEVELS.items():
        totals = con.execute(f"""
            WITH by_stratum AS (
                SELECT {geo} AS geo, category, h, count(*) AS n, SUM(y) AS sy, SUM(y * y) AS syy, SUM(nrec) AS nrec
                FROM cc GROUP BY ALL
            )
            SELECT geo, category, SUM(sy) AS value,
                   SUM(CASE WHEN n > 1 THEN n / (n - 1) * (syy - sy * sy / n) ELSE 0 END) AS var,
                   SUM(n) AS psu, count(*) AS strata, SUM(nrec) AS n_obs
            FROM by_stratum GROUP BY ALL
        """).df()
        totals["statistic"] = "total"

        percentages = con.execute(f"""
            WITH universe_h AS (
                SELECT {geo} AS geo, h, count(*) AS n, SUM(x) AS sx, SUM(x * x) AS sxx FROM cu GROUP BY ALL
            ),
            universe AS (
                SELECT geo, SUM(sx) AS X,
                       SUM(CASE WHEN n > 1 THEN n / (n - 1) * (sxx - sx * sx / n) ELSE 0 END) AS bx,
                       SUM(n) AS psu, count(*) AS strata
                FROM universe_h GROUP BY ALL
            ),
            cat_h AS (
                SELECT {geo.replace('CVE_', 'cc.CVE_')} AS geo, cc.category, cc.h,
                       SUM(cc.y) AS sy, SUM(cc.y * cc.y) AS syy, SUM(cc.y * cu.x) AS sxy, SUM(cc.nrec) AS nrec
                FROM cc JOIN cu USING ({CLUSTER_KEYS}) GROUP BY ALL
            ),
            cat AS (
                SELECT c.geo, c.category, SUM(c.sy) AS Y, SUM(c.nrec) AS n_obs,
                       -- y-part of the linearized variance, strata where the category occurs
                       list(struct_pack(n := u.n, sy := c.sy, syy := c.syy, sxy := c.sxy, sx := u.sx)) AS parts
                FROM cat_h c JOIN universe_h u USING (geo, h) GROUP BY ALL
            )
            SELECT c.geo, c.category, c.Y / u.X AS ratio, c.n_obs, u.X, u.bx, u.psu, u.strata,
                   list_sum(list_transform(c.parts, p -> CASE WHEN p.n > 1 THEN
                       p.n / (p.n - 1) * (p.syy - 2 * (c.Y / u.X) * p.sxy
                                         - (p.sy * p.sy - 2 * (c.Y / u.X) * p.sy * p.sx) / p.n)
                       ELSE 0 END)) AS ypart
            FROM cat c JOIN universe u USING (geo)
        """).df()
        percentages["var"] = (percentages.ratio**2 * percentages.bx + percentages.ypart) / percentages.X**2
        percentages["value"] = percentages.ratio
        percentages["statistic"] = "porcentaje"

        columns = ["geo", "category", "statistic", "value", "var", "psu", "strata", "n_obs"]
        both = pd.concat([totals[columns], percentages[columns]])
        both["geo_level"] = level
        frames.append(both)
    out = pd.concat(frames, ignore_index=True)
    out["variable"] = variable
    return out


def numeric_estimates(con: duckdb.DuckDBPyConnection, table: str, variable: str, valid: str) -> pd.DataFrame:
    """Weighted mean over valid values, all levels (a ratio estimator)."""
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE cm AS
        SELECT {CLUSTER_KEYS}, CVE_ENT || ESTRATO AS h,
               SUM(FACTOR * {variable})::DOUBLE AS y, SUM(FACTOR)::DOUBLE AS x, count(*) AS nrec
        FROM {table} WHERE {valid} GROUP BY ALL
    """)
    frames = []
    for level, geo in LEVELS.items():
        df = con.execute(f"""
            WITH by_h AS (
                SELECT {geo} AS geo, h, count(*) AS n, SUM(y) AS sy, SUM(x) AS sx, SUM(y * y) AS syy,
                       SUM(x * x) AS sxx, SUM(x * y) AS sxy, SUM(nrec) AS nrec
                FROM cm GROUP BY ALL
            ),
            dom AS (SELECT geo, SUM(sy) / SUM(sx) AS R, SUM(sx) AS X FROM by_h GROUP BY ALL)
            SELECT b.geo, d.R AS value,
                   SUM(CASE WHEN b.n > 1 THEN b.n / (b.n - 1) * (
                         (b.syy - 2 * d.R * b.sxy + d.R * d.R * b.sxx)
                         - (b.sy - d.R * b.sx) * (b.sy - d.R * b.sx) / b.n) ELSE 0 END) / (d.X * d.X) AS var,
                   SUM(b.n) AS psu, count(*) AS strata, SUM(b.nrec) AS n_obs
            FROM by_h b JOIN dom d USING (geo) GROUP BY b.geo, d.R, d.X
        """).df()
        df["geo_level"] = level
        frames.append(df)
    out = pd.concat(frames, ignore_index=True)
    out["category"] = None
    out["statistic"] = "promedio"
    out["variable"] = variable
    return out


def finish(raw: pd.DataFrame, table: str, coverage: pd.Series) -> pd.DataFrame:
    """Apply INEGI's coverage rules and compute SE, confidence limits and CV."""
    df = raw.copy()
    df["se"] = np.sqrt(df["var"].clip(lower=0))
    df["dof"] = df.psu - df.strata
    municipal = df.geo_level == "municipal"
    cob = df.geo.map(coverage).where(municipal)
    df["flag"] = None
    censused = cob == "1"
    insufficient = cob == "3"
    no_error = censused | (insufficient & (df.statistic == "total"))
    df.loc[no_error, "se"] = 0.0
    df.loc[censused, "flag"] = "censado"
    df.loc[insufficient, "flag"] = "muestra_insuficiente"
    df.loc[insufficient & (df.statistic != "total"), "se"] = np.nan

    t = t95(df.dof)
    df["li"] = df.value - t * df.se
    df["ls"] = df.value + t * df.se
    # Percentages: interval on the logit scale, back-transformed.
    pct = (df.statistic == "porcentaje") & (df.se > 0) & df.value.between(0, 1, inclusive="neither")
    p, se = df.value[pct], df.se[pct]
    logit, half = np.log(p / (1 - p)), t[pct] * se / (p * (1 - p))
    df.loc[pct, "li"] = 1 / (1 + np.exp(-(logit - half)))
    df.loc[pct, "ls"] = 1 / (1 + np.exp(-(logit + half)))
    exact = df.se == 0
    df.loc[exact, ["li", "ls"]] = df.loc[exact, ["value", "value"]].values
    df["cv"] = np.where(df.value != 0, 100 * df.se / df.value.abs(), np.nan)

    is_pct = df.statistic == "porcentaje"
    for column in ("value", "se", "li", "ls"):
        df.loc[is_pct, column] = df.loc[is_pct, column] * 100

    for column in ("n_obs", "psu", "strata"):
        df[column] = df[column].astype("int64")
    df["table_name"] = table
    df["CVEGEO"] = df.geo
    df["CVE_ENT"] = df.geo.str[:2]
    df["CVE_MUN"] = df.geo.str[2:]
    columns = [
        "table_name", "variable", "category", "statistic", "geo_level", "CVEGEO", "CVE_ENT", "CVE_MUN",
        "value", "se", "li", "ls", "cv", "n_obs", "psu", "strata", "flag",
    ]
    return df[columns]


def estimate_table(con: duckdb.DuckDBPyConnection, table: str) -> pd.DataFrame:
    coverage = con.execute(
        f"SELECT CVE_ENT || CVE_MUN AS geo, any_value(COBERTURA) FROM {table} GROUP BY 1"
    ).df().set_index("geo").iloc[:, 0]
    frames = []
    for item in variable_plan(con, table):
        started = time.time()
        if item["kind"] == "numeric":
            raw = numeric_estimates(con, table, item["variable"], item["valid"])
        else:
            raw = categorical_estimates(con, table, item["variable"])
        frames.append(finish(raw, table, coverage))
        print(f"  {table}.{item['variable']:<18} {item['kind']:<13} {len(frames[-1]):>9,} rows  {time.time() - started:5.1f}s")
    return pd.concat(frames, ignore_index=True)


# Validation ---------------------------------------------------------------------

# Published mnemonic -> (table, variable, category, statistic). Each is a direct
# category of one variable over that variable's universe.
PUBLISHED_EQUIVALENTS = {
    "POBTOT": ("personas", "_TOTAL", "total", "total"),
    "POBFEM": ("personas", "SEXO", "3", "total"),
    "POBMAS": ("personas", "SEXO", "1", "total"),
    "VIVPARHAB": ("viviendas", "_TOTAL", "total", "total"),
    "PCN_VPH_INTER": ("viviendas", "INTERNET", "7", "porcentaje"),
    "PCN_VPH_PISOTI": ("viviendas", "PISOS", "1", "porcentaje"),
    "PCN_VPH_C_ELEC": ("viviendas", "ELECTRICIDAD", "1", "porcentaje"),
    "PCN_VPH_REFRI": ("viviendas", "REFRIGERADOR", "1", "porcentaje"),
    "PCN_VPH_LAVAD": ("viviendas", "LAVADORA", "3", "porcentaje"),
    "PCN_VPH_CEL": ("viviendas", "CELULAR", "5", "porcentaje"),
    "PCN_VPH_ALQUI": ("viviendas", "TENENCIA", "3", "porcentaje"),
}


def validate_against_published(estimates: pd.DataFrame) -> pd.DataFrame:
    pub = pd.read_csv(PUBLISHED_CSV, dtype=str, encoding="latin1")
    pub = pub[pub.NOM_LOC.isin(["Total nacional", "Total de la entidad", "Total del municipio"])]
    pub = pub.assign(CVEGEO=pub.CVE_ENT + pub.CVE_MUN)
    stats = {"Valor": "value", "Error estándar": "se", "Límite inferior de confianza": "li",
             "Límite superior de confianza": "ls", "Coeficiente de variación": "cv"}
    rows = []
    for mnemonic, (table, variable, category, statistic) in PUBLISHED_EQUIVALENTS.items():
        if mnemonic not in pub.columns:
            continue
        ours = estimates[(estimates.table_name == table) & (estimates.variable == variable)
                         & (estimates.category == category) & (estimates.statistic == statistic)].set_index("CVEGEO")
        if ours.empty:
            continue
        theirs = pub.pivot_table(index="CVEGEO", columns="ESTIMADOR", values=mnemonic, aggfunc="first")
        theirs = theirs.apply(pd.to_numeric, errors="coerce").dropna(subset=["Error estándar"])
        joined = theirs.join(ours[list(stats.values())], how="inner")
        ok = pd.concat({ours_col: (joined[ours_col].round(2) - joined[pub_col]).abs() <= 0.011
                        for pub_col, ours_col in stats.items()}, axis=1)
        rows.append({"indicator": mnemonic, "estimates": len(joined),
                     **{f"{c}_match": round(ok[c].mean(), 4) for c in stats.values()},
                     "all_match": round(ok.all(axis=1).mean(), 4)})
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tables", nargs="+", choices=TABLES, default=list(TABLES))
    parser.add_argument("--validate", action="store_true", help="Compare against INEGI's published results")
    args = parser.parse_args()

    con = duckdb.connect()
    for name in ("codebook_variables", "codebook_categories"):
        con.execute(f"CREATE VIEW {name} AS SELECT * FROM read_parquet('{CLEAN_DIR / name}.parquet')")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    all_estimates = []
    for table in args.tables:
        con.execute(f"CREATE OR REPLACE VIEW {table} AS SELECT * FROM read_parquet('{CLEAN_DIR / table}/*.parquet')")
        started = time.time()
        estimates = estimate_table(con, table)
        out_path = OUTPUT_DIR / f"{table}.parquet"
        estimates.to_parquet(out_path.with_suffix(".parquet.tmp"), index=False, compression="zstd")
        out_path.with_suffix(".parquet.tmp").replace(out_path)
        print(f"{table}: {len(estimates):,} estimates in {time.time() - started:.0f}s -> {out_path}")
        all_estimates.append(estimates)

    if args.validate:
        report = validate_against_published(pd.concat(all_estimates, ignore_index=True))
        print("\nAgainst INEGI's published results (share of estimates matching to 2 decimals):")
        print(report.to_string(index=False))


if __name__ == "__main__":
    main()
