## Initialization
import pandas as pd
import numpy as np
from pathlib import Path

NON_PARTY_COLS = {
    "ESTADO", "DISTRITO", "SECCION", "ID_CASILLA", "TIPO_CASILLA", "EXT_CONTIGUA",
    "UBICACION_CASILLA", "TIPO_ACTA",
    "NO_REGISTRADOS", "NULOS", "TOTAL_VOTOS", "LISTA_NOMINAL",
    "OBSERVACIONES", "CONTABILIZADA", "CRYT",
    "HORA_ACOPIO", "HORA_CAPTURA", "HORA_REGISTRO",
}

PARTY_META_2012 = {
    "PAN":         {"is_coalition": False, "members": []},
    "PRI":         {"is_coalition": False, "members": []},
    "PRD":         {"is_coalition": False, "members": []},
    "PVEM":        {"is_coalition": False, "members": []},
    "PT":          {"is_coalition": False, "members": []},
    "MC":          {"is_coalition": False, "members": []},
    "PANAL":       {"is_coalition": False, "members": []},
    # "Compromiso por México"
    "C_PRI_PVEM":  {"is_coalition": True,  "members": ["PRI", "PVEM"]},
    # "Movimiento Progresista"
    "C_PRD_PT_MC": {"is_coalition": True,  "members": ["PRD", "PT", "MC"]},
    "C_PRD_PT":    {"is_coalition": True,  "members": ["PRD", "PT"]},
    "C_PRD_MC":    {"is_coalition": True,  "members": ["PRD", "MC"]},
    "C_PT_MC":     {"is_coalition": True,  "members": ["PT", "MC"]},
}

ELECTION_META_2012 = {
    "PRESIDENCIA_2012":  {"election_id": "PRE_2012",    "year": 2012, "election_type": "PRE", "chamber": None,       "seat_method": "direct", "total_seats": 1,   "term_years": 6},
    "DIPUTACIONES_2012": {"election_id": "DIP_MR_2012", "year": 2012, "election_type": "DIP", "chamber": "deputies", "seat_method": "fptp",   "total_seats": 300, "term_years": 3},
    "SENADURIAS_2012":   {"election_id": "SEN_MR_2012", "year": 2012, "election_type": "SEN", "chamber": "senate",   "seat_method": "fptp",   "total_seats": 96,  "term_years": 6},
}

RAW_CSV_PATHS = {
    "PRESIDENCIA_2012":  "data/electoral_data_raw/raw_2012/presidente.txt",
    "DIPUTACIONES_2012": "data/electoral_data_raw/raw_2012/diputados.txt",
    "SENADURIAS_2012":   "data/electoral_data_raw/raw_2012/senadores.txt",
}

CANDIDATES_CSV_2012 = {
    "PRESIDENCIA_2012":  None,
    "DIPUTACIONES_2012": None,
    "SENADURIAS_2012":   None,
}

# INEGI state ID → state name. 2012 raw files carry only the numeric ESTADO column.
ESTADO_NOMBRES = {
     1: "AGUASCALIENTES",              2: "BAJA CALIFORNIA",
     3: "BAJA CALIFORNIA SUR",         4: "CAMPECHE",
     5: "COAHUILA DE ZARAGOZA",        6: "COLIMA",
     7: "CHIAPAS",                     8: "CHIHUAHUA",
     9: "CIUDAD DE MEXICO",           10: "DURANGO",
    11: "GUANAJUATO",                 12: "GUERRERO",
    13: "HIDALGO",                    14: "JALISCO",
    15: "MEXICO",                     16: "MICHOACAN DE OCAMPO",
    17: "MORELOS",                    18: "NAYARIT",
    19: "NUEVO LEON",                 20: "OAXACA",
    21: "PUEBLA",                     22: "QUERETARO",
    23: "QUINTANA ROO",               24: "SAN LUIS POTOSI",
    25: "SINALOA",                    26: "SONORA",
    27: "TABASCO",                    28: "TAMAULIPAS",
    29: "TLAXCALA",                   30: "VERACRUZ DE IGNACIO DE LA LLAVE",
    31: "YUCATAN",                    32: "ZACATECAS",
}

OUT = Path("data/electoral_data_clean/clean_2012")
OUT.mkdir(parents=True, exist_ok=True)


## Helper functions

def load_raw(path: str) -> tuple[pd.DataFrame, list[str]]:
    """
    Load a 2012 pipe-delimited .txt, clean, filter, deduplicate, detect party cols.

    Filtering logic:
      - Keep CONTABILIZADA == 1 only. This single condition handles everything:
          - Drops CONTABILIZADA=0 (not counted)
          - Drops CONTABILIZADA=NaN (not yet captured at snapshot time)
          - Keeps SECCION=0 rows (voto en el extranjero) — these are real actas
            confirmed by having valid TOTAL_VOTOS values.
      - Dedup on the casilla composite key after the CONTABILIZADA filter.
        SECCION=0 rows can have duplicate keys with different vote counts (one is
        a correction); break ties by keeping the latest HORA_REGISTRO — the most
        recently captured record is the authoritative one, consistent with how
        PREP systems work.
    """
    df = pd.read_csv(path, low_memory=False, encoding="latin-1", sep="|", skiprows=4)

    df.columns = (
        df.columns
          .str.replace("\ufeff", "", regex=False)
          .str.replace("ï»¿",   "", regex=False)
          .str.strip()
    )

    drop_cols = [c for c in df.columns if c.startswith("Unnamed:")]
    df = df.drop(columns=drop_cols)

    # Rename to canonical names used by downstream functions
    df = df.rename(columns={
        "ESTADO":        "ID_ESTADO",
        "DISTRITO":      "ID_DISTRITO",
        "LISTA_NOMINAL": "LISTA_NOMINAL_CASILLA",
    })

    # Inject NOMBRE_ESTADO — absent in raw 2012 file
    df["NOMBRE_ESTADO"] = df["ID_ESTADO"].map(ESTADO_NOMBRES)
    unmapped = df["NOMBRE_ESTADO"].isna().sum()
    if unmapped > 0:
        bad_ids = df.loc[df["NOMBRE_ESTADO"].isna(), "ID_ESTADO"].unique()
        print(f"    ⚠️  {unmapped} rows with unmapped ID_ESTADO: {sorted(bad_ids)}")

    # Coerce integer identity columns
    for col in ["ID_ESTADO", "ID_DISTRITO", "ID_CASILLA", "EXT_CONTIGUA",
                "SECCION", "LISTA_NOMINAL_CASILLA", "TIPO_ACTA"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Coerce vote/total columns (arrive as object due to whitespace/quotes)
    for col in ["NO_REGISTRADOS", "NULOS", "TOTAL_VOTOS", "CONTABILIZADA"]:
        if col in df.columns and df[col].dtype == object:
            df[col] = (
                df[col].astype(str).str.strip().str.strip("'\"")
                       .str.replace(",", "", regex=False)
            )
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Coerce party/coalition columns
    candidate_cols = [
        c for c in df.columns
        if c not in NON_PARTY_COLS
        and c not in {"NOMBRE_ESTADO", "LISTA_NOMINAL_CASILLA", "ID_ESTADO", "ID_DISTRITO"}
    ]
    for col in candidate_cols:
        if df[col].dtype == object:
            df[col] = (
                df[col].astype(str).str.strip().str.strip("'\"")
                       .str.replace(",", "", regex=False)
            )
        df[col] = pd.to_numeric(df[col], errors="coerce")

    party_keys = [
        c for c in candidate_cols
        if pd.api.types.is_numeric_dtype(df[c]) and df[c].notna().any()
    ]
    dropped = set(candidate_cols) - set(party_keys)
    if dropped:
        print(f"    ⚠️  Columns dropped from party_keys (all-NaN): {sorted(dropped)}")

    # Filter: keep contabilizadas only
    # SECCION=0 (voto en el extranjero) are kept — they are real actas with vote data
    df = df[df["CONTABILIZADA"] == 1.0].copy()

    KEY_COLS = ["ID_ESTADO", "SECCION", "TIPO_CASILLA", "ID_CASILLA", "EXT_CONTIGUA"]

    # Step 1 — dedup *corrections*: per LEEME.txt, TIPO_ACTA distinguishes real
    # ballot types (e.g. 7="acta especial MR", 8="acta especial RP" for
    # diputados) that legitimately coexist for the same physical casilla
    # especial. The original single-pass dedup on KEY_COLS alone (without
    # TIPO_ACTA) silently dropped one of every such MR/RP pair as if it were a
    # duplicate correction — confirmed ~748 special diputado casillas and ~742
    # special senate casillas lost real votes this way. Scoping the
    # latest-HORA_REGISTRO-wins dedup to KEY_COLS+TIPO_ACTA fixes that while
    # still collapsing genuine same-acta-type corrections correctly.
    df = (
        df.sort_values("HORA_REGISTRO", ascending=False, na_position="last")
          .drop_duplicates(subset=KEY_COLS + ["TIPO_ACTA"], keep="first")
    )

    # Step 2 — combine special MR + special RP acta pairs for the same casilla
    # into one record (Mexico uses a single ballot; the RP-only acta exists
    # only for casillas especiales serving transit voters — same real-world
    # case handled for the 2000 cycle in dat_to_arrow_2000.py).
    sum_cols   = party_keys + ["NO_REGISTRADOS", "NULOS", "TOTAL_VOTOS"]
    first_cols = [c for c in df.columns if c not in KEY_COLS and c not in sum_cols]
    agg = {c: "sum" for c in sum_cols}
    agg.update({c: "first" for c in first_cols})
    df = (
        df.groupby(KEY_COLS, as_index=False).agg(agg)
          .sort_values(KEY_COLS)
          .reset_index(drop=True)
    )

    return df, party_keys


def make_geo_id(df: pd.DataFrame) -> pd.Series:
    """
    Format: {ID_ESTADO 2d}_{SECCION 4d}  e.g. "01_0338", "01_0000" (abroad)
    SECCION=0 rows get geo_id like "01_0000" — valid, represents voto en el extranjero
    for that state.
    """
    return (
        df["ID_ESTADO"].astype(int).astype(str).str.zfill(2)
        + "_"
        + df["SECCION"].astype(int).astype(str).str.zfill(4)
    )


def make_casilla_id(df: pd.DataFrame) -> pd.Series:
    """
    Synthesised key: {ID_ESTADO 2d}_{SECCION 4d}_{TIPO_CASILLA}{ID_CASILLA 2d}C{EXT_CONTIGUA 2d}
    e.g. "01_0338_B01C00", "01_0000_B46C00" (abroad casilla)
    Unique after CONTABILIZADA=1 filter and HORA_REGISTRO dedup in load_raw().
    """
    return (
        df["ID_ESTADO"].astype(int).astype(str).str.zfill(2)
        + "_"
        + df["SECCION"].astype(int).astype(str).str.zfill(4)
        + "_"
        + df["TIPO_CASILLA"].astype(str).str.strip()
        + df["ID_CASILLA"].astype(int).astype(str).str.zfill(2)
        + "C"
        + df["EXT_CONTIGUA"].astype(int).astype(str).str.zfill(2)
    )


def build_dim_election(meta: dict) -> pd.DataFrame:
    return pd.DataFrame([meta])


def build_dim_geography(df: pd.DataFrame) -> pd.DataFrame:
    # 2012 has no MUNICIPIO / CIRCUNSCRIPCION; NOMBRE_ESTADO injected in load_raw()
    # SECCION=0 rows produce geo_id like "01_0000" representing abroad votes per state.
    # Derive (ID_MUNICIPIO, MUNICIPIO) from the 2024 SEC lookup — sections are
    # stable enough that coverage is ~98%.
    GEO_COLS = ["ID_ESTADO", "NOMBRE_ESTADO", "SECCION", "ID_DISTRITO"]
    out = (
        df[GEO_COLS]
        .drop_duplicates()
        .dropna(subset=["ID_ESTADO", "SECCION"])
        .sort_values(["ID_ESTADO", "SECCION"])
        .reset_index(drop=True)
    )
    from pathlib import Path
    sec_path = Path("data/electoral_data_raw/raw_2024/PRESIDENCIA_2024/CSV/2024_SEE_PRE_NAL_SEC.csv")
    if sec_path.exists():
        import pandas as _pd
        sec = _pd.read_csv(sec_path, encoding="latin-1")
        sec.columns = [c.replace("﻿", "").strip() for c in sec.columns]
        lookup = (
            sec[["ID_ESTADO", "SECCION", "ID_MUNICIPIO", "MUNICIPIO"]]
            .drop_duplicates(subset=["ID_ESTADO", "SECCION"])
        )
        out = out.merge(lookup, on=["ID_ESTADO", "SECCION"], how="left")
    else:
        out["ID_MUNICIPIO"] = None
        out["MUNICIPIO"]    = None
    out["geo_id"] = make_geo_id(out)
    return out[["geo_id"] + [c for c in out.columns if c != "geo_id"]]


def build_dim_casilla(df: pd.DataFrame, election_id: str) -> pd.DataFrame:
    CASILLA_COLS = [
        "ID_ESTADO", "SECCION", "TIPO_CASILLA", "ID_CASILLA", "EXT_CONTIGUA",
        "UBICACION_CASILLA", "TIPO_ACTA", "LISTA_NOMINAL_CASILLA",
        "OBSERVACIONES", "CONTABILIZADA", "CRYT",
        "HORA_ACOPIO", "HORA_CAPTURA", "HORA_REGISTRO",
    ]
    cols = [c for c in CASILLA_COLS if c in df.columns]
    out = df[cols].copy().reset_index(drop=True)
    out["casilla_id"]  = make_casilla_id(df)
    out["election_id"] = election_id
    out["geo_id"]      = make_geo_id(df)
    front = ["casilla_id", "election_id", "geo_id"]
    return out[front + [c for c in out.columns if c not in front]]


def build_dim_party(party_keys: list) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "party_key":    key,
            "is_coalition": PARTY_META_2012.get(key, {"is_coalition": False})["is_coalition"],
            "members":      ",".join(PARTY_META_2012.get(key, {"members": []})["members"]),
        }
        for key in party_keys
    ])


def build_dim_candidatos(election_type: str) -> pd.DataFrame:
    csv_rel = CANDIDATES_CSV_2012.get(election_type)
    if csv_rel is None:
        print(f"    ⚠️  No candidates CSV mapped for {election_type}, skipping")
        return pd.DataFrame()
    csv_path = Path(csv_rel)
    if not csv_path.exists():
        print(f"    ⚠️  Not found: {csv_path}, skipping")
        return pd.DataFrame()
    df = pd.read_csv(csv_path, encoding="latin-1", low_memory=False)
    df.columns = df.columns.str.strip()
    return df


def build_fact(df: pd.DataFrame, party_keys: list, election_id: str) -> pd.DataFrame:
    """
    Vote metadata mapping (2012 → canonical names for ingestion/electoral_ingest.py SCHEMA_MAP):
      NO_REGISTRADOS → CNR        (NUM_VOTOS_CAN_NREG in 2024, CNR in 2018)
      NULOS          → VN         (NUM_VOTOS_NULOS in 2024, VN in 2018)
      TOTAL_VOTOS    → TOTAL_VOTOS (same as 2024; TOTAL_VOTOS_CALCULADOS in 2018)
      NUM_VOTOS_VALIDOS: NULL — not present in 2012
    """
    df = df.copy()
    df["casilla_id"] = make_casilla_id(df)

    dupe_count = df["casilla_id"].duplicated().sum()
    if dupe_count > 0:
        raise ValueError(
            f"[{election_id}] casilla_id not unique: {dupe_count:,} duplicates remain "
            f"after load_raw() filtering. Investigate key formula."
        )

    df = df.rename(columns={"NO_REGISTRADOS": "CNR", "NULOS": "VN"})

    VOTE_META = ["CNR", "VN", "TOTAL_VOTOS"]
    fact = (
        df[["casilla_id"] + party_keys + VOTE_META]
        .melt(
            id_vars=["casilla_id"] + VOTE_META,
            value_vars=party_keys,
            var_name="party_key",
            value_name="votes",
        )
    )
    fact["election_id"] = election_id
    fact["votes"]       = fact["votes"].fillna(0).astype(int)
    return fact[["election_id", "casilla_id", "party_key", "votes"] + VOTE_META]


## Main loop
all_elections  = []
all_geography  = []
all_casillas   = []
all_parties    = []
all_candidatos = []
all_facts      = []

for election_type, meta in ELECTION_META_2012.items():
    election_id = meta["election_id"]
    print(f"\nProcessing {election_type}...")
    print(f"  Reading: {RAW_CSV_PATHS[election_type]}")

    df_raw, party_keys = load_raw(RAW_CSV_PATHS[election_type])

    dim_election   = build_dim_election(meta)
    dim_geography  = build_dim_geography(df_raw)
    dim_casilla    = build_dim_casilla(df_raw, election_id)
    dim_party      = build_dim_party(party_keys)
    dim_candidatos = build_dim_candidatos(election_type)
    fact           = build_fact(df_raw, party_keys, election_id)

    print(f"  dim_geography     : {len(dim_geography):>10,} rows")
    print(f"  dim_casilla       : {len(dim_casilla):>10,} rows")
    print(f"  dim_party         : {len(dim_party):>10,} rows")
    print(f"  fact_casilla_vote : {len(fact):>10,} rows")

    all_elections.append(dim_election)
    all_geography.append(dim_geography)
    all_casillas.append(dim_casilla)
    all_parties.append(dim_party)
    all_candidatos.append(dim_candidatos)
    all_facts.append(fact)

print("\n✓ All 2012 elections processed")


## Concat + dedup
dim_election_final = pd.concat(all_elections, ignore_index=True)

dim_geography_final = (
    pd.concat(all_geography, ignore_index=True)
    .drop_duplicates(subset=["geo_id"])
    .sort_values(["ID_ESTADO", "SECCION"])
    .reset_index(drop=True)
)

dim_casilla_final = pd.concat(all_casillas, ignore_index=True)

dim_party_final = (
    pd.concat(all_parties, ignore_index=True)
    .drop_duplicates(subset=["party_key"])
    .reset_index(drop=True)
)

all_candidatos_nonempty = [df for df in all_candidatos if not df.empty]
dim_candidatos_final = (
    pd.concat(all_candidatos_nonempty, ignore_index=True)
    if all_candidatos_nonempty else pd.DataFrame()
)

fact_final = pd.concat(all_facts, ignore_index=True)

print(f"\ndim_election      : {len(dim_election_final):>10,} rows")
print(f"dim_geography     : {len(dim_geography_final):>10,} rows")
print(f"dim_casilla       : {len(dim_casilla_final):>10,} rows")
print(f"dim_party         : {len(dim_party_final):>10,} rows")
print(f"dim_candidatos    : {len(dim_candidatos_final):>10,} rows")
print(f"fact_casilla_vote : {len(fact_final):>10,} rows")


## Write out
dim_election_final.to_parquet(OUT / "dim_election.parquet",   index=False)
dim_geography_final.to_parquet(OUT / "dim_geography.parquet", index=False)
dim_casilla_final.to_parquet(OUT / "dim_casilla.parquet",     index=False)
dim_party_final.to_parquet(OUT / "dim_party.parquet",         index=False)

if not dim_candidatos_final.empty:
    dim_candidatos_final.to_parquet(OUT / "dim_candidatos.parquet", index=False)
else:
    print("  ⚠️  dim_candidatos is empty — skipping parquet write")

fact_final.to_parquet(
    OUT / "fact_casilla_vote.parquet",
    index=False,
    partition_cols=["election_id"],
)

print("\nWritten to", OUT.resolve())
for f in sorted(OUT.rglob("*.parquet")):
    print(f"  {f.relative_to(OUT)}")
