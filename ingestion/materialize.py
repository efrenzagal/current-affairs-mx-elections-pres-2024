"""
Election Data Materialize — SQLite → Parquet
==============================================
Single entry point for everything that turns election_data.db into the
Parquet files Streamlit reads from data/materialized/:

  1. Per-election views (casilla / seccion / municipio / estado), the
     winners-only dim_candidatos copy, and the pre-processed municipios
     GeoJSON -- one election_id's worth of rows at a time.
  2. The multi-year (2012/2018/2024), state-granularity timeseries parquet
     used by the "serie de tiempo por partido" section -- coalition votes
     split proportionally to member parties across the whole history.

Both halves used to live in separate scripts (ingestion/pipeline.py's
`materialize` command and root build_timeseries.py); they're merged here
because they're really one step -- "SQLite is ready, now build everything
Streamlit needs" -- and shared the same state-name canonicalization logic
that's easy to let drift out of sync when duplicated across files.

ingestion/pipeline.py keeps only `ingest` (clean parquets -> SQLite).

Usage:
    python ingestion/materialize.py              # views + timeseries
    python ingestion/materialize.py views         # per-election views only
    python ingestion/materialize.py timeseries    # timeseries parquet only
    python ingestion/materialize.py --force       # overwrite existing files
"""

import argparse
import json
import sqlite3
import unicodedata
from pathlib import Path

import pandas as pd

# ── Shared config ──────────────────────────────────────────────────────────────

DB_PATH           = "election_data.db"
MATERIALIZED      = Path("data/materialized")  # output: Streamlit reads these
TIMESERIES_FILE   = "timeseries_estados.parquet"


def _norm(s: str) -> str:
    s = str(s).upper().strip()
    s = unicodedata.normalize("NFD", s)
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


# Canonical id_estado -> state name. Each cycle's source data spells/accents
# state names differently (e.g. "COAHUILA" vs "COAHUILA DE ZARAGOZA",
# "CIUDAD DE MEXICO" vs "CIUDAD DE MÉXICO") even though id_estado (1-32) is
# already consistent everywhere -- grouping by the raw column directly
# silently fragments a single state into multiple rows/dropdown entries
# downstream. Both the per-election views and the timeseries builder key
# off this single mapping so they always agree on state identity.
CANONICAL_ESTADO_NOMBRES = {
     1: "AGUASCALIENTES",                  2: "BAJA CALIFORNIA",
     3: "BAJA CALIFORNIA SUR",             4: "CAMPECHE",
     5: "COAHUILA DE ZARAGOZA",            6: "COLIMA",
     7: "CHIAPAS",                         8: "CHIHUAHUA",
     9: "CIUDAD DE MÉXICO",               10: "DURANGO",
    11: "GUANAJUATO",                     12: "GUERRERO",
    13: "HIDALGO",                        14: "JALISCO",
    15: "MÉXICO",                         16: "MICHOACÁN DE OCAMPO",
    17: "MORELOS",                        18: "NAYARIT",
    19: "NUEVO LEÓN",                     20: "OAXACA",
    21: "PUEBLA",                         22: "QUERÉTARO",
    23: "QUINTANA ROO",                   24: "SAN LUIS POTOSÍ",
    25: "SINALOA",                        26: "SONORA",
    27: "TABASCO",                        28: "TAMAULIPAS",
    29: "TLAXCALA",                       30: "VERACRUZ DE IGNACIO DE LA LLAVE",
    31: "YUCATÁN",                        32: "ZACATECAS",
}


def _sql_quote(name: str) -> str:
    return name.replace("'", "''")


GEOJSON_ESTADO_ALIASES = {
    "COAHUILA": CANONICAL_ESTADO_NOMBRES[5],
    "DISTRITO FEDERAL": CANONICAL_ESTADO_NOMBRES[9],
    "MICHOACAN": CANONICAL_ESTADO_NOMBRES[16],
    "VERACRUZ": CANONICAL_ESTADO_NOMBRES[30],
}


GEOJSON_MUNICIPIO_ALIASES = {
    ("CHIHUAHUA", "BATOPILAS"): "BATOPILAS DE MANUEL GOMEZ MORIN",
    ("CHIHUAHUA", "CARICHIC"): "CARICHI",
    ("CHIHUAHUA", "CUSIHUIRIACHIC"): "CUSIHUIRIACHI",
    ("CHIHUAHUA", "GUACHOCHIC"): "GUACHOCHI",
    ("CHIHUAHUA", "MAGUARICHIC"): "MAGUARICHI",
    ("CHIHUAHUA", "MATACHIC"): "MATACHI",
    ("CHIHUAHUA", "GENERAL TRIAS"): "SANTA ISABEL",
    ("CHIHUAHUA", "URUACHIC"): "URUACHI",
    ("CIUDAD DE MEXICO", "MAGDALENA CONTRERAS"): "LA MAGDALENA CONTRERAS",
    ("COAHUILA DE ZARAGOZA", "NUEVA ROSITA"): "SAN JUAN DE SABINAS",
    ("DURANGO", "GENERAL SIMON BOLIVAR"): "SIMON BOLIVAR",
    ("DURANGO", "SAN LUIS DE CORDERO"): "SAN LUIS DEL CORDERO",
    ("GUANAJUATO", "ALLENDE"): "SAN MIGUEL DE ALLENDE",
    ("GUANAJUATO", "DOLORES HIDALGO"): "DOLORES HIDALGO CUNA DE LA INDEPENDENCIA NACIONAL",
    ("GUANAJUATO", "SILAO"): "SILAO DE LA VICTORIA",
    ("GUERRERO", "JOSE AZUETA"): "ZIHUATANEJO DE AZUETA",
    ("GUERRERO", "LA UNION"): "LA UNION DE ISIDORO MONTES DE OCA",
    ("JALISCO", "CIUDAD GUZMAN"): "ZAPOTLAN EL GRANDE",
    ("JALISCO", "CIUDAD VENUSTIANO CARRANZA"): "SAN GABRIEL",
    ("JALISCO", "ANTONIO ESCOBEDO"): "SAN JUANITO DE ESCOBEDO",
    ("JALISCO", "CUQUITO"): "CUQUIO",
    ("JALISCO", "MANUEL M. DIEGUEZ"): "SANTA MARIA DEL ORO",
    ("JALISCO", "TLAQUEPAQUE"): "SAN PEDRO TLAQUEPAQUE",
    ("MEXICO", "ACAMBAY"): "ACAMBAY DE RUIZ CASTANEDA",
    ("MEXICO", "JALATLACO"): "XALATLACO",
    ("MEXICO", "SAN MARTIN DE LAS PIRAAMIDES"): "SAN MARTIN DE LAS PIRAMIDES",
    ("MEXICO", "TLALNEPANTLA"): "TLALNEPANTLA DE BAZ",
    ("MEXICO", "ZINACATEPEC"): "ZINACANTEPEC",
    ("MORELOS", "TLALTIZAPAN"): "TLALTIZAPAN DE ZAPATA",
    ("MORELOS", "ZACATEPEC DE HIDALGO"): "ZACATEPEC",
    ("MORELOS", "ZACUALPAN"): "ZACUALPAN DE AMILPAS",
    ("NUEVO LEON", "DOCTOR ARROYO"): "DR. ARROYO",
    ("NUEVO LEON", "DOCTOR COSS"): "DR. COSS",
    ("NUEVO LEON", "DOCTOR GONZALEZ"): "DR. GONZALEZ",
    ("NUEVO LEON", "GENERAL BRAVO"): "GRAL. BRAVO",
    ("NUEVO LEON", "GENERAL ESCOBEDO"): "GRAL. ESCOBEDO",
    ("NUEVO LEON", "GENERAL TERAN"): "GRAL. TERAN",
    ("NUEVO LEON", "GENERAL TREVINO"): "GRAL. TREVINO",
    ("NUEVO LEON", "GENERAL ZARAGOZA"): "GRAL. ZARAGOZA",
    ("NUEVO LEON", "GENERAL ZUAZUA"): "GRAL. ZUAZUA",
    ("SAN LUIS POTOSI", "TANCANHUITZ DE SANTOS"): "TANCANHUITZ",
    ("CHIAPAS", "VILLA COMALTITLAN"): "VILLACOMALTITLAN",
    ("SINALOA", "EL ROSARIO"): "ROSARIO",
    ("TLAXCALA", "ALTZAYANCA"): "ATLTZAYANCA",
    ("TLAXCALA", "YAUHQUEMECAN"): "YAUHQUEMEHCAN",
    ("VERACRUZ DE IGNACIO DE LA LLAVE", "AMATITLAN DE LOS REYES"): "AMATLAN DE LOS REYES",
    ("VERACRUZ DE IGNACIO DE LA LLAVE", "AMATLAN TUXPAN"): "NARANJOS AMATLAN",
    ("VERACRUZ DE IGNACIO DE LA LLAVE", "CAMARON DE TEJADA"): "CAMARON DE TEJEDA",
    ("VERACRUZ DE IGNACIO DE LA LLAVE", "CHOCOMAN"): "CHOCAMAN",
    ("VERACRUZ DE IGNACIO DE LA LLAVE", "COXQUIHI"): "COXQUIHUI",
    ("VERACRUZ DE IGNACIO DE LA LLAVE", "JALANCINGO"): "JALACINGO",
    ("VERACRUZ DE IGNACIO DE LA LLAVE", "MEDELLIN"): "MEDELLIN DE BRAVO",
    ("VERACRUZ DE IGNACIO DE LA LLAVE", "MIHUATLAN"): "MIAHUATLAN",
    ("VERACRUZ DE IGNACIO DE LA LLAVE", "TEMAPACHE"): "ALAMO TEMAPACHE",
    ("VERACRUZ DE IGNACIO DE LA LLAVE", "TLAJOCALPAN"): "TLACOJALPAN",
    ("VERACRUZ DE IGNACIO DE LA LLAVE", "TLAQUILPAN"): "TLAQUILPA",
}


# SQL CASE expression so GROUP BY collapses duplicate spellings at the
# source, instead of fragmenting one state into several rows.
_ESTADO_CASE_SQL = "CASE g.id_estado " + " ".join(
    f"WHEN {i} THEN '{_sql_quote(name)}'"
    for i, name in CANONICAL_ESTADO_NOMBRES.items()
) + " ELSE g.nombre_estado END"


def get_conn(db_path: str) -> sqlite3.Connection:
    return sqlite3.connect(db_path)


def get_all_elections(conn: sqlite3.Connection) -> list[str]:
    return pd.read_sql_query(
        "SELECT election_id FROM dim_election ORDER BY election_id", conn
    )["election_id"].tolist()


# ══════════════════════════════════════════════════════════════════════════════
# PART 1 — Per-election views (casilla / seccion / municipio / estado)
# ══════════════════════════════════════════════════════════════════════════════

# ── Casilla ────────────────────────────────────────────────────────────────────

def query_casilla(conn, election_id: str) -> pd.DataFrame:
    return pd.read_sql_query(f"""
        SELECT
            f.election_id,
            c.casilla_id,
            c.geo_id,
            g.id_estado,
            {_ESTADO_CASE_SQL} AS nombre_estado,
            g.id_distrito_federal,
            g.cabecera_distrital_federal,
            g.id_municipio,
            g.municipio,
            g.seccion,
            c.tipo_casilla,
            c.id_casilla,
            c.ext_contigua,
            c.lista_nominal,
            c.urna_electronica,
            c.estatus_acta,
            c.ruta_acta,
            c.acta_casilla_mec,
            f.party_key,
            f.votes,
            f.num_votos_validos,
            f.num_votos_nulos,
            f.num_votos_can_nreg,
            f.total_votos
        FROM fact_casilla_vote f
        JOIN dim_casilla  c ON f.casilla_id  = c.casilla_id
                            AND f.election_id = c.election_id
        JOIN dim_geography g ON c.geo_id      = g.geo_id
        WHERE f.election_id = '{election_id}'
        ORDER BY g.id_estado, g.seccion, c.casilla_id, f.party_key
    """, conn)


# ── Sección ────────────────────────────────────────────────────────────────────

def query_seccion(conn, election_id: str) -> pd.DataFrame:
    votes = pd.read_sql_query(f"""
        SELECT
            f.election_id,
            g.id_estado,
            {_ESTADO_CASE_SQL} AS nombre_estado,
            g.id_municipio,
            g.municipio,
            g.seccion,
            f.party_key,
            SUM(f.votes)              AS votes,
            SUM(f.num_votos_validos)  AS num_votos_validos,
            SUM(f.num_votos_nulos)    AS num_votos_nulos,
            SUM(f.num_votos_can_nreg) AS num_votos_can_nreg,
            SUM(f.total_votos)        AS total_votos,
            COUNT(DISTINCT c.casilla_id) AS num_casillas
        FROM fact_casilla_vote f
        JOIN dim_casilla   c ON f.casilla_id  = c.casilla_id
                             AND f.election_id = c.election_id
        JOIN dim_geography g ON c.geo_id       = g.geo_id
        WHERE f.election_id = '{election_id}'
        GROUP BY f.election_id,
                 g.id_estado, {_ESTADO_CASE_SQL},
                 g.id_municipio, g.municipio,
                 g.seccion, f.party_key
        ORDER BY g.id_estado, g.municipio, g.seccion, f.party_key
    """, conn)

    nominal = pd.read_sql_query(f"""
        SELECT
            g.id_estado,
            g.municipio,
            g.seccion,
            SUM(c.lista_nominal) AS lista_nominal_part
        FROM dim_casilla   c
        JOIN dim_geography g ON c.geo_id = g.geo_id
        WHERE c.election_id  = '{election_id}'
          AND c.tipo_casilla != 'S'
          AND g.seccion       > 0
        GROUP BY g.id_estado, g.municipio, g.seccion
    """, conn)

    return votes.merge(nominal, on=["id_estado", "municipio", "seccion"], how="left")


# ── Municipio ──────────────────────────────────────────────────────────────────

def query_municipio(conn, election_id: str) -> pd.DataFrame:
    votes = pd.read_sql_query(f"""
        SELECT
            f.election_id,
            g.id_estado,
            {_ESTADO_CASE_SQL} AS nombre_estado,
            g.id_municipio,
            g.municipio,
            f.party_key,
            SUM(f.votes)              AS votes,
            SUM(f.num_votos_validos)  AS num_votos_validos,
            SUM(f.num_votos_nulos)    AS num_votos_nulos,
            SUM(f.num_votos_can_nreg) AS num_votos_can_nreg,
            SUM(f.total_votos)        AS total_votos,
            COUNT(DISTINCT c.casilla_id) AS num_casillas,
            COUNT(DISTINCT g.seccion)    AS num_secciones
        FROM fact_casilla_vote f
        JOIN dim_casilla   c ON f.casilla_id  = c.casilla_id
                             AND f.election_id = c.election_id
        JOIN dim_geography g ON c.geo_id       = g.geo_id
        WHERE f.election_id = '{election_id}'
        GROUP BY f.election_id,
                 g.id_estado, {_ESTADO_CASE_SQL},
                 g.id_municipio, g.municipio,
                 f.party_key
        ORDER BY g.id_estado, g.municipio, f.party_key
    """, conn)

    nominal = pd.read_sql_query(f"""
        SELECT
            g.id_estado,
            g.municipio,
            SUM(c.lista_nominal) AS lista_nominal_part
        FROM dim_casilla   c
        JOIN dim_geography g ON c.geo_id = g.geo_id
        WHERE c.election_id  = '{election_id}'
          AND c.tipo_casilla != 'S'
          AND g.seccion       > 0
        GROUP BY g.id_estado, g.municipio
    """, conn)

    df = votes.merge(nominal, on=["id_estado", "municipio"], how="left")
    # Pre-compute join key so map rendering needs zero normalization at runtime
    df["_join_key"] = df["nombre_estado"].map(_norm) + "||" + df["municipio"].map(_norm)
    return df


# ── Estado ─────────────────────────────────────────────────────────────────────

def query_estado(conn, election_id: str) -> pd.DataFrame:
    votes = pd.read_sql_query(f"""
        SELECT
            f.election_id,
            g.id_estado,
            {_ESTADO_CASE_SQL} AS nombre_estado,
            f.party_key,
            SUM(f.votes)              AS votes,
            SUM(f.num_votos_validos)  AS num_votos_validos,
            SUM(f.num_votos_nulos)    AS num_votos_nulos,
            SUM(f.num_votos_can_nreg) AS num_votos_can_nreg,
            SUM(f.total_votos)        AS total_votos,
            COUNT(DISTINCT c.casilla_id)          AS num_casillas,
            COUNT(DISTINCT g.municipio)           AS num_municipios,
            COUNT(DISTINCT g.seccion)             AS num_secciones,
            COUNT(DISTINCT g.id_distrito_federal) AS num_distritos
        FROM fact_casilla_vote f
        JOIN dim_casilla   c ON f.casilla_id  = c.casilla_id
                             AND f.election_id = c.election_id
        JOIN dim_geography g ON c.geo_id       = g.geo_id
        WHERE f.election_id = '{election_id}'
        GROUP BY f.election_id, g.id_estado, {_ESTADO_CASE_SQL}, f.party_key
        ORDER BY g.id_estado, f.party_key
    """, conn)

    nominal = pd.read_sql_query(f"""
        SELECT
            g.id_estado,
            SUM(c.lista_nominal) AS lista_nominal_part
        FROM dim_casilla   c
        JOIN dim_geography g ON c.geo_id = g.geo_id
        WHERE c.election_id  = '{election_id}'
          AND c.tipo_casilla != 'S'
          AND g.seccion       > 0
        GROUP BY g.id_estado
    """, conn)

    return votes.merge(nominal, on="id_estado", how="left")


# ── GeoJSON preprocessing ──────────────────────────────────────────────────────

def preprocess_geojson(src: str = "municipios.geojson", out_dir: Path = MATERIALIZED) -> None:
    """
    Normalize the raw municipios GeoJSON once at materialize time:
      - Strip accents, uppercase, build _join_key, set feat["id"]
      - Write to data/materialized/municipios_processed.geojson
    Streamlit then does a plain json.load() with zero per-feature processing.
    """
    src_path = Path(src)
    if not src_path.exists():
        print(f"  ⚠️  {src} not found — GeoJSON preprocessing skipped")
        return

    with open(src_path, encoding="utf-8") as f:
        geo = json.load(f)

    for feat in geo["features"]:
        p          = feat["properties"]
        raw_estado = p.get("NAME_1", "")
        raw_mun    = p.get("NAME_2", "")
        raw_estado = GEOJSON_ESTADO_ALIASES.get(_norm(raw_estado), raw_estado)
        estado_key = _norm(raw_estado)
        mun_key = GEOJSON_MUNICIPIO_ALIASES.get(
            (estado_key, _norm(raw_mun)),
            _norm(raw_mun),
        )
        join_key       = estado_key + "||" + mun_key
        p["_join_key"] = join_key
        feat["id"]     = join_key

    dst = out_dir / "municipios_processed.geojson"
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(geo, f, ensure_ascii=False)
    print(f"  ✓ municipios_processed.geojson  ({dst.stat().st_size/1024/1024:.1f} MB)")


def materialize_views(db_path: str = DB_PATH, out_dir: Path = MATERIALIZED,
                       geojson: str = "municipios.geojson", force: bool = False):
    print("=" * 55)
    print("PER-ELECTION VIEWS: SQLite → parquet views + aux")
    print("=" * 55)
    if force:
        print("  (--force: overwriting existing files)\n")

    out_dir.mkdir(parents=True, exist_ok=True)
    conn = get_conn(db_path)

    try:
        elections = get_all_elections(conn)
        print(f"Found {len(elections)} election(s): {elections}\n")

        for eid in elections:
            print(f"Processing {eid}...")
            for name, fn in [
                ("casilla",   query_casilla),
                ("seccion",   query_seccion),
                ("municipio", query_municipio),
                ("estado",    query_estado),
            ]:
                path = out_dir / f"view_{name}_{eid}.parquet"
                if path.exists() and not force:
                    mb = path.stat().st_size / 1024 / 1024
                    print(f"  → {name}... ⏭  already exists ({mb:.2f} MB), skipping")
                    continue
                print(f"  → {name}...", end=" ", flush=True)
                df = fn(conn, eid)
                df.to_parquet(path, index=False)
                mb = path.stat().st_size / 1024 / 1024
                print(f"✓  {len(df):>10,} rows  ({mb:.2f} MB)")
            print()

        print("Building auxiliary files...")

        # dim_candidatos: straight copy-through from SQLite (no CSV access here).
        # SQLite is the source of truth post-ingest; we dump it back to parquet
        # so Streamlit's read path stays "parquet only", same as every other view.
        cand_path = out_dir / "dim_candidatos.parquet"
        if cand_path.exists() and not force:
            print(f"  ⏭  dim_candidatos already exists, skipping")
        else:
            df_cand = pd.read_sql_query("SELECT * FROM dim_candidatos", conn)
            if df_cand.empty:
                print("  ⚠️  dim_candidatos is empty in SQLite — skipping parquet write")
            else:
                df_cand.to_parquet(cand_path, index=False)
                print(f"  ✓ dim_candidatos  ({len(df_cand):,} rows  →  {cand_path.stat().st_size/1024:.0f} KB)")

        geo_path = out_dir / "municipios_processed.geojson"
        if geo_path.exists() and not force:
            print(f"  ⏭  municipios_processed.geojson already exists, skipping")
        else:
            preprocess_geojson(src=geojson, out_dir=out_dir)

        print(f"\nAll files in {out_dir.resolve()}\n")
        for f in sorted(out_dir.glob("*.parquet")):
            mb = f.stat().st_size / 1024 / 1024
            print(f"  {f.name:<50}  {mb:>6.2f} MB")

    finally:
        conn.close()

    print("\n✓ Views materialize complete")


# ══════════════════════════════════════════════════════════════════════════════
# PART 2 — Timeseries (multi-year, state granularity, by party)
# ══════════════════════════════════════════════════════════════════════════════
# Reads from election_data.db and writes a single flat Parquet file:
#     data/materialized/timeseries_estados.parquet
#
# Each row is one (election_id, year, election_type, nombre_estado, party_key)
# combination with:
#   - votes_raw    : votes as recorded (coalition rows kept intact)
#   - votes_split  : coalition votes split proportionally to member parties
#                    (direct votes for non-coalition parties are unchanged)
#   - total_votos  : total votes cast in that election x state
#   - lista_nominal: registered voters (from non-special casillas)
#   - pct_raw      : votes_raw / total_votos
#   - pct_split    : votes_split / total_votos
#   - is_coalition : whether this party_key is a coalition
#
# Coalition splitting logic (mirrors the R script):
#   For each (election, state, coalition_key), the coalition's votes are
#   distributed to member parties proportionally to those members' own
#   direct-vote counts in the same (election, state). If a member has 0
#   direct votes (e.g. didn't run solo that cycle), weight falls back to
#   equal share across members.

# Canonical party normalisation across cycles — each cycle's ingestion script
# names the same real party differently (2018 spells out full names; 2006 used
# its own short codes). Coalitions are NOT touched here: a coalition is a
# genuinely distinct entity each cycle (different member combinations), so
# only same-party single-party aliases belong in this dict.
PARTY_ALIASES = {
    "MOVIMIENTO CIUDADANO": "MC",
    "NUEVA ALIANZA":        "PANAL",       # 2018 spelling -> 2012/2015's code
    "NVA_A":                "PANAL",       # 2006 spelling -> 2012/2015's code
    "ENCUENTRO SOCIAL":     "PES",         # 2018 spelling -> 2015/2021's code
    "CAND_IND_01":          "CAND_IND_1",  # 2018 numbering -> 2015's convention
    "CAND_IND_02":          "CAND_IND_2",
    "CAND_IND1":            "CAND_IND_1",  # 2024 numbering -> 2015's convention
    "CAND_IND2":            "CAND_IND_2",
}

# When ALL coalition members had 0 direct votes (i.e. they only ran under the
# coalition banner), the proportional-split fallback would otherwise assign
# equal shares to every member. For well-known presidential coalitions where
# the candidacy clearly belonged to one party, attribute 100% to that party.
COALITION_LEAD_PARTY = {
    "A. CAM.": "PAN",   # 2000: Fox (PAN) ran under Alianza por el Cambio
    "A. MEX.": "PRD",   # 2000: Cárdenas (PRD) ran under Alianza por México
    "APM":     "PRI",   # 2006: Madrazo (PRI) ran under Alianza por México
    "PBT":     "PRD",   # 2006: AMLO (PRD) ran under Por el Bien de Todos
}

# Full Spanish names for standalone (non-coalition) parties, keyed by the
# post-alias canonical code. Coalitions don't need an entry here — their
# label is built from member codes instead (see build_party_labels below).
# Anything not listed here (e.g. UNO_PDM, whose exact historical meaning
# isn't confidently known) just falls back to showing its raw code, which is
# strictly safer than guessing a name that might be wrong.
PARTY_FULL_NAMES = {
    "PAN":      "Acción Nacional",
    "PRI":      "Revolucionario Institucional",
    "PRD":      "Revolución Democrática",
    "PVEM":     "Verde Ecologista de México",
    "PT":       "Trabajo",
    "MC":       "Movimiento Ciudadano",
    "MORENA":   "Movimiento Regeneración Nacional",
    "PANAL":    "Nueva Alianza",
    "PES":      "Encuentro Social",
    "PH":       "Humanista",
    "RSP":      "Redes Sociales Progresistas",
    "FXM":      "Fuerza por México",
    "ASDC":     "Alternativa Socialdemócrata y Campesina",
    "PARM":     "Auténtico de la Revolución Mexicana",
    "PFCRN":    "Frente Cardenista de Reconstrucción Nacional",
    "PPS":      "Popular Socialista",
    "PCD":      "Centro Democrático",
    "DSPPN":    "Democracia Social",
    "A. CAM.":  "Alianza por el Cambio",
    "A. MEX.":  "Alianza por México",
    "CI":               "Candidatura Independiente",
    "CAND_IND_1":       "Candidatura Independiente 1",
    "CAND_IND_2":       "Candidatura Independiente 2",
}


def build_party_labels(party_keys: list[str], coalitions: pd.DataFrame) -> dict[str, str]:
    """
    party_key -> human-readable label.
      - Coalitions: "{code} (PARTY+PARTY+...)" built from actual members.
      - Known standalone parties: "{code} — Full Name".
      - Unknown codes: left as-is (no guessing).
    """
    members_by_coalition = (
        coalitions.groupby("coalition_key")["member_key"]
        .apply(lambda s: "+".join(sorted(s)))
        .to_dict()
    )
    labels = {}
    for key in party_keys:
        if key in members_by_coalition:
            labels[key] = f"{key} ({members_by_coalition[key]})"
        elif key in PARTY_FULL_NAMES:
            labels[key] = f"{key} — {PARTY_FULL_NAMES[key]}"
        else:
            labels[key] = key
    return labels


def load_raw_votes(conn: sqlite3.Connection) -> pd.DataFrame:
    """Pull state-level vote totals for all elections, all parties."""
    return pd.read_sql_query("""
        SELECT
            e.election_id,
            e.year,
            e.election_type,
            g.id_estado,
            g.nombre_estado,
            f.party_key,
            SUM(f.votes)       AS votes_raw,
            SUM(f.total_votos) AS total_votos
        FROM fact_casilla_vote f
        JOIN dim_casilla   c ON  f.casilla_id  = c.casilla_id
                             AND f.election_id  = c.election_id
        JOIN dim_geography g ON  c.geo_id       = g.geo_id
        JOIN dim_election  e ON  f.election_id  = e.election_id
        WHERE c.tipo_casilla != 'S'
          AND g.seccion        > 0
        GROUP BY
            e.election_id, e.year, e.election_type,
            g.id_estado,
            f.party_key
        ORDER BY e.year, g.id_estado, f.party_key
    """, conn)


def load_lista_nominal(conn: sqlite3.Connection) -> pd.DataFrame:
    """State-level lista nominal per election (non-special casillas only)."""
    return pd.read_sql_query("""
        SELECT
            c.election_id,
            g.id_estado,
            SUM(c.lista_nominal) AS lista_nominal
        FROM dim_casilla   c
        JOIN dim_geography g ON c.geo_id = g.geo_id
        WHERE c.tipo_casilla != 'S'
          AND g.seccion        > 0
        GROUP BY c.election_id, g.id_estado
    """, conn)


def load_coalitions(conn: sqlite3.Connection) -> pd.DataFrame:
    """Return a table of coalition_key → member_key pairs."""
    raw = pd.read_sql_query("""
        SELECT party_key, members
        FROM dim_party
        WHERE is_coalition = 1
          AND members IS NOT NULL
          AND members != ''
    """, conn)

    rows = []
    for _, r in raw.iterrows():
        for member in r["members"].split(","):
            member = member.strip()
            member = PARTY_ALIASES.get(member, member)
            rows.append({"coalition_key": r["party_key"], "member_key": member})
    return pd.DataFrame(rows)


def split_coalitions(
    state_raw: pd.DataFrame,
    coalitions: pd.DataFrame,
) -> pd.DataFrame:
    """
    For each (election_id, id_estado, coalition_key), distribute coalition
    votes to member parties proportional to each member's own direct votes.
    Fallback when all members have 0 direct votes: 100% to the lead party
    if the coalition is in COALITION_LEAD_PARTY, otherwise equal weight.

    Returns a long DataFrame with columns:
        election_id, id_estado, party_key, votes_split
    where only NON-coalition rows are included (coalitions are dissolved).
    """
    # normalise party aliases in raw data
    state_raw = state_raw.copy()
    state_raw["party_key"] = state_raw["party_key"].replace(PARTY_ALIASES)

    coalition_keys = set(coalitions["coalition_key"].unique())

    direct = state_raw[~state_raw["party_key"].isin(coalition_keys)][
        ["election_id", "id_estado", "party_key", "votes_raw"]
    ].rename(columns={"votes_raw": "direct_votes"})

    coalition_rows = state_raw[state_raw["party_key"].isin(coalition_keys)][
        ["election_id", "id_estado", "party_key", "votes_raw"]
    ].rename(columns={"party_key": "coalition_key", "votes_raw": "coalition_votes"})

    # Join coalition → members
    attributed = coalition_rows.merge(coalitions, on="coalition_key", how="inner")

    # Join member direct votes (for weighting)
    attributed = attributed.merge(
        direct.rename(columns={"party_key": "member_key", "direct_votes": "member_indiv"}),
        on=["election_id", "id_estado", "member_key"],
        how="left",
    )
    attributed["member_indiv"] = attributed["member_indiv"].fillna(0.0)

    # Compute weights within each (election, state, coalition)
    grp = attributed.groupby(["election_id", "id_estado", "coalition_key"])
    attributed["total_member_indiv"] = grp["member_indiv"].transform("sum")
    attributed["n_members"]          = grp["member_key"].transform("count")

    attributed["weight"] = attributed.apply(
        lambda r: (
            r["member_indiv"] / r["total_member_indiv"]
            if r["total_member_indiv"] > 0
            else (
                1.0 if r["member_key"] == COALITION_LEAD_PARTY.get(r["coalition_key"])
                else 0.0
            ) if r["coalition_key"] in COALITION_LEAD_PARTY
            else 1.0 / r["n_members"]
        ),
        axis=1,
    )
    attributed["attributed_votes"] = attributed["coalition_votes"] * attributed["weight"]

    # Sum attributed votes to member parties
    split_attributed = (
        attributed
        .groupby(["election_id", "id_estado", "member_key"], as_index=False)["attributed_votes"]
        .sum()
        .rename(columns={"member_key": "party_key", "attributed_votes": "votes_split_from_coalitions"})
    )

    # Combine: direct votes + attributed votes
    combined = direct.merge(
        split_attributed,
        on=["election_id", "id_estado", "party_key"],
        how="outer",
    )
    combined["direct_votes"]                 = combined["direct_votes"].fillna(0.0)
    combined["votes_split_from_coalitions"]  = combined["votes_split_from_coalitions"].fillna(0.0)
    combined["votes_split"] = combined["direct_votes"] + combined["votes_split_from_coalitions"]

    return combined[["election_id", "id_estado", "party_key", "votes_split"]]


def materialize_timeseries(db_path: str = DB_PATH, out_dir: Path = MATERIALIZED):
    print("=" * 55)
    print("TIMESERIES: SQLite → multi-year state-level parquet")
    print("=" * 55)

    print(f"Connecting to {db_path}...")
    conn = sqlite3.connect(db_path)

    print("Loading raw votes...")
    state_raw = load_raw_votes(conn)
    state_raw["party_key"] = state_raw["party_key"].replace(PARTY_ALIASES)
    # Override with the canonical name from id_estado — don't trust any one
    # cycle's spelling/accenting (see CANONICAL_ESTADO_NOMBRES above).
    state_raw["nombre_estado"] = state_raw["id_estado"].map(CANONICAL_ESTADO_NOMBRES)

    print("Loading lista nominal...")
    lista = load_lista_nominal(conn)

    print("Loading coalition definitions...")
    coalitions = load_coalitions(conn)

    print("Splitting coalition votes...")
    split = split_coalitions(state_raw, coalitions)

    # ── Assemble final table ───────────────────────────────────────────────────

    # Base: raw votes per (election, state, party)
    # We keep coalition rows in votes_raw; they won't appear in votes_split
    election_meta = pd.read_sql_query(
        "SELECT election_id, year, election_type FROM dim_election", conn
    )
    conn.close()

    # total_votos per (election, state) — sum across all parties (deduplicated)
    # total_votos in our raw is per-party row, so we want it as a state-level
    # total votes cast — use the max across parties since it's a repeated
    # value (same casilla total on every party row).
    total_per_state = (
        state_raw
        .groupby(["election_id", "id_estado"], as_index=False)["total_votos"]
        .max()
        .rename(columns={"total_votos": "total_votos_estado"})
    )

    # Identify coalition party_keys
    coalition_keys = set(coalitions["coalition_key"].unique())

    # Merge split votes back onto base — OUTER, not left: some coalition
    # members (e.g. 2006's PRD/PT/CONVERGENCIA inside PBT) never have their
    # own raw row at all — the source only ever recorded the coalition-level
    # total, never an individual member tally — so a left join starting from
    # state_raw would silently drop their split-derived share entirely.
    df = state_raw.merge(
        split,
        on=["election_id", "id_estado", "party_key"],
        how="outer",
    )
    # For coalition rows, votes_split is the dissolved total (not applicable);
    # for direct rows, votes_split = direct + attributed from coalitions
    # Coalition rows themselves get votes_split = NaN which is correct —
    # they are dissolved into members in the split view.

    # Backfill identity columns for member-only rows introduced by the outer
    # merge above (they exist in `split` but had no native state_raw row).
    member_only = df["nombre_estado"].isna()
    if member_only.any():
        elec_lookup = (
            state_raw[["election_id", "year", "election_type"]]
            .drop_duplicates()
            .set_index("election_id")
        )
        df.loc[member_only, "nombre_estado"]  = df.loc[member_only, "id_estado"].map(CANONICAL_ESTADO_NOMBRES)
        df.loc[member_only, "year"]           = df.loc[member_only, "election_id"].map(elec_lookup["year"])
        df.loc[member_only, "election_type"]  = df.loc[member_only, "election_id"].map(elec_lookup["election_type"])
        df.loc[member_only, "votes_raw"]      = 0.0
        print(f"  + {member_only.sum()} coalition-member rows added (never had a native row, only a split-derived share)")

    print("Building party display labels...")
    party_labels = build_party_labels(df["party_key"].unique().tolist(), coalitions)
    df["party_label"] = df["party_key"].map(party_labels)

    # Mark each row (covers member-only rows too — they are real standalone
    # parties, just never recorded directly in the source for that cycle)
    df["is_coalition"] = df["party_key"].isin(coalition_keys)

    # Merge lista nominal
    df = df.merge(lista, on=["election_id", "id_estado"], how="left")

    # Merge state totals
    df = df.merge(total_per_state, on=["election_id", "id_estado"], how="left")

    # Percentages
    df["pct_raw"]   = df["votes_raw"]   / df["total_votos_estado"] * 100
    df["pct_split"] = df["votes_split"] / df["total_votos_estado"] * 100

    # Clean up column names and types
    df["year"]           = df["year"].astype(int)
    df["id_estado"]      = df["id_estado"].astype(int)
    df["votes_raw"]      = df["votes_raw"].astype(float)
    df["votes_split"]    = df["votes_split"].astype(float)
    df["lista_nominal"]  = df["lista_nominal"].astype(float)

    # Drop total_votos (redundant with total_votos_estado)
    df = df.drop(columns=["total_votos"], errors="ignore")

    # Sort
    df = df.sort_values(["year", "election_type", "id_estado", "party_key"]).reset_index(drop=True)

    # Write
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / TIMESERIES_FILE
    df.to_parquet(out_path, index=False)

    mb = out_path.stat().st_size / 1024 / 1024
    print(f"\n✓ Written: {out_path}  ({len(df):,} rows · {mb:.2f} MB)")
    print(f"\nColumns: {list(df.columns)}")
    print(f"\nElections: {sorted(df['election_id'].unique())}")
    print(f"States:    {df['nombre_estado'].nunique()}")
    print(f"Parties:   {sorted(df['party_key'].unique())}")
    print("\n✓ Timeseries materialize complete")


# ══════════════════════════════════════════════════════════════════════════════
# CLI entry point
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Materialize Streamlit parquets from SQLite")
    parser.add_argument(
        "command",
        choices=["views", "timeseries", "all"],
        nargs="?",
        default="all",
        help=(
            "views       — per-election casilla/seccion/municipio/estado parquets + aux\n"
            "timeseries  — multi-year state-level timeseries parquet\n"
            "all         — both (default)"
        ),
    )
    parser.add_argument("--db",      default=DB_PATH,              help="SQLite path")
    parser.add_argument("--mat-dir", default=str(MATERIALIZED),    help="Output parquet dir (data/materialized)")
    parser.add_argument("--geojson", default="municipios.geojson", help="Raw GeoJSON path to pre-process (views only)")
    parser.add_argument("--force",   action="store_true",          help="Overwrite existing materialized view files")
    args = parser.parse_args()

    mat = Path(args.mat_dir)

    if args.command in ("views", "all"):
        materialize_views(db_path=args.db, out_dir=mat, geojson=args.geojson, force=args.force)

    if args.command in ("timeseries", "all"):
        materialize_timeseries(db_path=args.db, out_dir=mat)
