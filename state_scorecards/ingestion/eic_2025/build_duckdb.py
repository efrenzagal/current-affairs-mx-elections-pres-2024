"""Build a queryable DuckDB database over the Encuesta Intercensal 2025 Parquet.

The microdata stays in Parquet; ``personas``, ``viviendas`` and ``migrantes``
are views that read it, so the database file itself is a few hundred KB and
nothing is duplicated. The small codebook tables are copied in, and every view
column carries its description and question as a comment, which DuckDB clients
(and ``python3 -m query_console --db <this file>``) show alongside the schema.

Rows are respondent units: one sampled person, dwelling or migrant. Every
estimate must weight by FACTOR -- SUM(FACTOR) reproduces INEGI's published
totals exactly.

The views point at absolute paths, so rebuild after moving the repository or
after rerunning raw_to_parquet.py with new states (it takes seconds).

Usage:
    python3 state_scorecards/ingestion/eic_2025/build_duckdb.py
"""

from __future__ import annotations

from pathlib import Path

import duckdb


REPO_ROOT = Path(__file__).resolve().parents[3]
CLEAN_DIR = REPO_ROOT / "state_scorecards" / "data" / "clean" / "eic_2025"
DB_PATH = REPO_ROOT / "state_scorecards" / "data" / "eic2025.duckdb"

TABLE_COMMENTS = {
    "viviendas": "EIC 2025 microdata: one row per sampled private dwelling. Weight by FACTOR.",
    "personas": "EIC 2025 microdata: one row per resident of a sampled dwelling. Weight by FACTOR. Joins viviendas on ID_VIV.",
    "migrantes": "EIC 2025 microdata: one row per household member who left for another country since 2020. Weight by FACTOR. Joins viviendas on ID_VIV.",
    "codebook_variables": "One row per microdata variable: section, description, full question, type, valid range, and the classifier it is coded against.",
    "codebook_categories": "Category codes and labels for coded variables, from INEGI's file descriptor (eic2025_micro_fd.xlsx).",
    "codebook_classifiers": "Long classifiers (industry, occupation, country, municipio...) referenced by codebook_variables.classifier.",
    "entidades": "The 32 states: CVE_ENT and name.",
    "municipios": "Municipios keyed by CVEGEO (2-digit state + 3-digit municipio), with their state.",
}


def quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def build(con: duckdb.DuckDBPyConnection) -> None:
    for table in ("codebook_variables", "codebook_categories", "codebook_classifiers"):
        source = CLEAN_DIR / f"{table}.parquet"
        con.execute(f"CREATE TABLE {table} AS SELECT * FROM read_parquet({quote(str(source))})")

    for table in ("viviendas", "personas", "migrantes"):
        files = sorted((CLEAN_DIR / table).glob("*.parquet"))
        if not files:
            raise SystemExit(f"No Parquet under {CLEAN_DIR / table}; run raw_to_parquet.py first.")
        pattern = str(CLEAN_DIR / table / "*.parquet")
        con.execute(f"CREATE VIEW {table} AS SELECT * FROM read_parquet({quote(pattern)})")

    con.execute("""
        CREATE VIEW entidades AS
        SELECT code AS CVE_ENT, label AS entidad
        FROM codebook_classifiers WHERE classifier = 'entidad'
    """)
    con.execute("""
        CREATE VIEW municipios AS
        SELECT code AS CVEGEO, label AS municipio, parent_code AS CVE_ENT, parent_label AS entidad
        FROM codebook_classifiers
        WHERE classifier = 'municipio' AND length(code) = 5
    """)

    estimates = sorted((CLEAN_DIR / "estimaciones").glob("*.parquet"))
    if estimates:
        pattern = str(CLEAN_DIR / "estimaciones" / "*.parquet")
        con.execute(f"CREATE VIEW estimaciones AS SELECT * FROM read_parquet({quote(pattern)})")
        # Labels: a category is either a descriptor code or, for classified
        # variables (industry, country...), a classifier code. Numeric ranges
        # ("1..130") never match a category, so those keep their raw value.
        con.execute("""
            CREATE VIEW estimaciones_etiquetadas AS
            SELECT e.*,
                   v.description AS variable_descripcion,
                   v.question_full AS pregunta,
                   coalesce(k.label, c.label) AS category_label,
                   coalesce(m.municipio, n.entidad, 'Estados Unidos Mexicanos') AS lugar,
                   n.entidad
            FROM estimaciones e
            LEFT JOIN codebook_variables v ON v.table_name = e.table_name AND v.variable = e.variable
            LEFT JOIN codebook_categories c
                   ON c.table_name = e.table_name AND c.variable = e.variable AND c.code = e.category
            LEFT JOIN codebook_classifiers k ON k.classifier = v.classifier AND k.code = e.category
            LEFT JOIN municipios m ON m.CVEGEO = e.CVEGEO AND e.geo_level = 'municipal'
            LEFT JOIN entidades n ON n.CVE_ENT = e.CVE_ENT
        """)
        TABLE_COMMENTS["estimaciones"] = (
            "Every EIC 2025 estimate at national (CVEGEO 00000), state (EE000) and municipio (EEMMM) level: "
            "per category a total and a porcentaje of the question's universe; per numeric variable a promedio. "
            "se, li/ls (90%) and cv computed exactly as INEGI publishes them. Absent categories mean zero. "
            "flag: censado (no sampling error) or muestra_insuficiente (INEGI does not publish its ratios)."
        )
        TABLE_COMMENTS["estimaciones_etiquetadas"] = (
            "estimaciones with the question, the category label and the place name joined in."
        )

    published = CLEAN_DIR / "resultados_publicados.parquet"
    if published.exists():
        con.execute(f"CREATE VIEW resultados_publicados AS SELECT * FROM read_parquet({quote(str(published))})")
        dictionary = CLEAN_DIR / "indicadores_publicados.parquet"
        con.execute(f"CREATE TABLE indicadores_publicados AS SELECT * FROM read_parquet({quote(str(dictionary))})")
        TABLE_COMMENTS["resultados_publicados"] = (
            "INEGI's published EIC 2025 results, one row per place x indicator: value, se, li/ls (90%) and cv. "
            "geo_level: nacional, estatal, municipal, localidad (50,000+) or resto (a state's localities under "
            "50,000). flag: MI (insufficient sample) or NA (not applicable), value null. Official definitions "
            "and universes: prefer these over estimaciones when an indicator is published."
        )
        TABLE_COMMENTS["indicadores_publicados"] = (
            "The 341 published indicators: mnemonic, section, name and INEGI's description."
        )

    for name, comment in TABLE_COMMENTS.items():
        kind = "TABLE" if name.startswith("codebook_") or name == "indicadores_publicados" else "VIEW"
        con.execute(f"COMMENT ON {kind} {name} IS {quote(comment)}")

    # Column comments: "Description — ¿full question?" so the schema browser
    # answers "what was actually asked" without opening the codebook.
    variables = con.execute(
        "SELECT table_name, variable, description, question_full, classifier FROM codebook_variables"
    ).fetchall()
    for table, variable, description, question, classifier in variables:
        parts = [description or ""]
        if question and question != description:
            parts.append(question)
        if classifier:
            parts.append(f"[clasificador: {classifier}]")
        con.execute(f'COMMENT ON COLUMN {table}."{variable}" IS {quote(" — ".join(p for p in parts if p))}')


def main() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    staging = DB_PATH.with_suffix(".duckdb.tmp")
    staging.unlink(missing_ok=True)
    con = duckdb.connect(str(staging))
    try:
        build(con)
        counts = {
            table: con.execute(f"SELECT count(*), sum(FACTOR) FROM {table}").fetchone()
            for table in ("viviendas", "personas", "migrantes")
        }
    finally:
        con.close()
    staging.replace(DB_PATH)

    for table, (rows, weighted) in counts.items():
        print(f"{table:10} {rows:>12,} rows  {weighted:>14,} weighted")
    print(f"Wrote {DB_PATH} ({DB_PATH.stat().st_size / 1024:,.0f} KB)")


if __name__ == "__main__":
    main()
