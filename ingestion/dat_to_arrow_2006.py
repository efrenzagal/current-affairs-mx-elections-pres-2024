from __future__ import annotations

import re
import time
from io import StringIO
from pathlib import Path

import pandas as pd
import requests

BASE_URL = "https://portalanterior.ine.mx/documentos/Estadisticas2006"
CACHE_DIR = Path("data/raw_2006")
OUT = Path("data/clean_2006")
OUT.mkdir(parents=True, exist_ok=True)

REQUEST_DELAY_SECONDS = 1.5
MAX_RETRIES_429 = 4
RETRY_BACKOFF_BASE_SECONDS = 20

# State suffixes used by the 2006 INE text files, based on the URL list.
STATE_SUFFIXES = {
     1: "ags",
     2: "bc",
     3: "bcs",
     4: "camp",
     5: "coah",
     6: "col",
     7: "chis",
     8: "chih",
     9: "df",
    10: "dgo",
    11: "gto",
    12: "gro",
    13: "hgo",
    14: "jal",
    15: "mex",
    16: "mich",
    17: "mor",
    18: "nay",
    19: "nl",
    20: "oax",
    21: "pue",
    22: "qro",
    23: "qroo",
    24: "slp",
    25: "sin",
    26: "son",
    27: "tab",
    28: "tamps",
    29: "tlax",
    30: "ver",
    31: "yuc",
    32: "zac",
}

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

OFFICE_META_2006 = {
    "PRESIDENCIA_2006": {
        "election_id": "PRE_2006",
        "year": 2006,
        "election_type": "PRE",
        "chamber": None,
        "seat_method": "direct",
        "total_seats": 1,
        "term_years": 6,
        "folder": "presidente",
        "prefix": "presidente",
    },
    "DIPUTACIONES_2006": {
        "election_id": "DIP_MR_2006",
        "year": 2006,
        "election_type": "DIP",
        "chamber": "deputies",
        "seat_method": "fptp",
        "total_seats": 300,
        "term_years": 3,
        "folder": "diputadosmr",
        "prefix": "diputados",
    },
    "SENADURIAS_2006": {
        "election_id": "SEN_MR_2006",
        "year": 2006,
        "election_type": "SEN",
        "chamber": "senate",
        "seat_method": "fptp",
        "total_seats": 96,
        "term_years": 6,
        "folder": "senadoresmr",
        "prefix": "senadores",
    },
}

CANDIDATES_CSV_2006 = {
    "PRESIDENCIA_2006": None,
    "DIPUTACIONES_2006": None,
    "SENADURIAS_2006": None,
}

PARTY_META_2006 = {
    "PAN": {"is_coalition": False, "members": []},
    "APM": {"is_coalition": True, "members": ["PRI", "PVEM"]},
    "PBT": {"is_coalition": True, "members": ["PRD", "PT", "CONVERGENCIA"]},
    "NVA_A": {"is_coalition": False, "members": []},
    "ASDC": {"is_coalition": False, "members": []},
}

NON_PARTY_COLS = {
    "ID_ENT",
    "ID_ESTADO",
    "NOMBRE_EDO_M",
    "NOMBRE_ESTADO",
    "DISTRITO_TXT",
    "ID_DISTRITO",
    "CAB_MIN",
    "NOMBRE_DISTRITO",
    "NOM_MIN",
    "MUNICIPIO",
    "TIPO_ELECCION",
    "SECCION",
    "SECC",
    "CASILLA",
    "CASILLA_RAW",
    "TIPO_CASILLA",
    "ID_CASILLA",
    "EXT_CONTIGUA",
    "NO_VOTOS_CAN_NREG",
    "NO_REG",
    "CNR",
    "VALIDOS",
    "NO_VOTOS_NULOS",
    "NULOS",
    "VN",
    "TOTAL",
    "TOTAL_VOTOS",
    "LISTA_NOMINAL",
    "LISTA_NOMINAL_CASILLA",
    "ESTATUS",
    "ESTATUS_ACTA",
    "NOTA",
}


def url_for(meta: dict, suffix: str) -> str:
    return f"{BASE_URL}/{meta['folder']}/txts/{meta['prefix']}_{suffix}.txt"


def fetch_state_txt(meta: dict, id_estado: int, suffix: str) -> str:
    cache_path = CACHE_DIR / meta["folder"] / f"{meta['prefix']}_{suffix}.txt"
    if cache_path.exists():
        cached = cache_path.read_text(encoding="utf-8")
        if "\ufffd" not in cached:
            return cached
        print(f"    Refetching {cache_path} due to invalid replacement characters")

    url = url_for(meta, suffix)
    for attempt in range(MAX_RETRIES_429 + 1):
        resp = requests.get(url, timeout=30)
        if resp.status_code == 429:
            wait = RETRY_BACKOFF_BASE_SECONDS * (2 ** attempt)
            print(f"    429 rate limited on {url}, backing off {wait}s (attempt {attempt + 1}/{MAX_RETRIES_429})")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        text = resp.content.decode("latin-1")
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(text, encoding="utf-8")
        time.sleep(REQUEST_DELAY_SECONDS)
        return text

    raise RuntimeError(f"Gave up on {url} after {MAX_RETRIES_429} retries")


def read_2006_text(text: str) -> pd.DataFrame:
    df = pd.read_csv(StringIO(text), sep=";", encoding="utf-8", low_memory=False)
    df.columns = (
        df.columns.astype(str)
        .str.replace("\ufeff", "", regex=False)
        .str.strip()
    )
    drop_cols = [c for c in df.columns if c.startswith("Unnamed:")]
    return df.drop(columns=drop_cols)


def parse_casilla(raw: pd.Series) -> pd.DataFrame:
    s = raw.fillna("").astype(str).str.strip().str.upper()
    tipo = s.str.extract(r"^([A-Z]+)", expand=False).fillna("X")
    tipo = tipo.replace({
        "BASICA": "B",
        "CONTIGUA": "C",
        "EXTRAORDINARIA": "E",
        "ESPECIAL": "S",
        "VOTO": "V",
    })
    tipo = tipo.str[0]
    tipo = tipo.where(~s.str.contains("EXTRANJERO", na=False), "V")

    raw_num = pd.to_numeric(s.str.extract(r"^[A-Z]+(\d+)", expand=False), errors="coerce")
    id_casilla = raw_num.fillna(1).astype(int)
    id_casilla = id_casilla.where(tipo != "V", 0)

    ext_contigua = pd.to_numeric(s.str.extract(r"\bC(\d+)\b", expand=False), errors="coerce")
    ext_contigua = ext_contigua.fillna(0).astype(int)

    return pd.DataFrame({
        "TIPO_CASILLA": tipo,
        "ID_CASILLA": id_casilla,
        "EXT_CONTIGUA": ext_contigua,
    })


def load_office(meta: dict) -> tuple[pd.DataFrame, list[str]]:
    frames = []

    for id_estado, suffix in STATE_SUFFIXES.items():
        text = fetch_state_txt(meta, id_estado, suffix)
        df = read_2006_text(text)
        df["__source_suffix"] = suffix
        frames.append(df)
        print(f"    Estado {id_estado:02d} ({ESTADO_NOMBRES[id_estado]}): {len(df):,} rows")

    df_all = pd.concat(frames, ignore_index=True)
    df_all.columns = [re.sub(r"\s+", " ", c).strip() for c in df_all.columns]

    rename = {
        "ID_ENT": "ID_ESTADO",
        "DISTRITO_TXT": "ID_DISTRITO",
        "CAB_MIN": "NOMBRE_DISTRITO",
        "NOM_MIN": "MUNICIPIO",
        "SECC": "SECCION",
        "CASILLA": "CASILLA_RAW",
        "NO_VOTOS_CAN_NREG": "CNR",
        "NO_REG": "CNR",
        "NO_VOTOS_NULOS": "VN",
        "NULOS": "VN",
        "TOTAL": "TOTAL_VOTOS",
        "LISTA_NOMINAL": "LISTA_NOMINAL_CASILLA",
        "ESTATUS": "ESTATUS_ACTA",
    }
    df_all = df_all.rename(columns=rename)

    df_all["ID_ESTADO"] = pd.to_numeric(df_all["ID_ESTADO"], errors="coerce")
    df_all["NOMBRE_ESTADO"] = df_all["ID_ESTADO"].map(ESTADO_NOMBRES)
    if "NOMBRE_EDO_M" in df_all.columns:
        df_all["NOMBRE_ESTADO_RAW"] = df_all["NOMBRE_EDO_M"]

    if "ID_DISTRITO" in df_all.columns:
        df_all["ID_DISTRITO"] = pd.to_numeric(df_all["ID_DISTRITO"], errors="coerce")

    for col in ["SECCION", "CNR", "VALIDOS", "VN", "TOTAL_VOTOS", "LISTA_NOMINAL_CASILLA"]:
        if col in df_all.columns:
            df_all[col] = pd.to_numeric(df_all[col], errors="coerce")

    casilla_parts = parse_casilla(df_all["CASILLA_RAW"])
    df_all["TIPO_CASILLA"] = casilla_parts["TIPO_CASILLA"]
    df_all["ID_CASILLA"] = casilla_parts["ID_CASILLA"]
    df_all["EXT_CONTIGUA"] = casilla_parts["EXT_CONTIGUA"]
    extranjero = df_all["TIPO_CASILLA"] == "V"
    df_all.loc[extranjero, "ID_CASILLA"] = (
        df_all.loc[extranjero, "ID_DISTRITO"].fillna(0).astype(int)
    )

    candidate_cols = [
        c for c in df_all.columns
        if c not in NON_PARTY_COLS
        and not c.startswith("__")
        and c != "NOMBRE_ESTADO_RAW"
    ]
    for col in candidate_cols:
        df_all[col] = pd.to_numeric(df_all[col], errors="coerce")

    party_keys = [
        c for c in candidate_cols
        if pd.api.types.is_numeric_dtype(df_all[c]) and df_all[c].notna().any()
    ]
    party_keys = sorted(party_keys)

    for col in party_keys:
        df_all[col] = df_all[col].fillna(0)

    df_all["__casilla_id"] = make_casilla_id(df_all)
    dupe_count = df_all["__casilla_id"].duplicated().sum()
    if dupe_count:
        print(f"    Combining {dupe_count:,} duplicate special-casilla MR/RP rows into single totals")
        sum_cols = [
            c for c in party_keys + ["CNR", "VALIDOS", "VN", "TOTAL_VOTOS"]
            if c in df_all.columns
        ]
        first_cols = [c for c in df_all.columns if c not in set(sum_cols + ["__casilla_id"])]
        agg = {c: "sum" for c in sum_cols}
        agg.update({c: "first" for c in first_cols})
        df_all = df_all.groupby("__casilla_id", as_index=False).agg(agg)
        df_all = df_all.drop(columns=["__casilla_id"])
    else:
        df_all = df_all.drop(columns=["__casilla_id"])

    return df_all, party_keys


def make_geo_id(df: pd.DataFrame) -> pd.Series:
    return (
        df["ID_ESTADO"].astype(int).astype(str).str.zfill(2)
        + "_"
        + df["SECCION"].astype(int).astype(str).str.zfill(4)
    )


def make_casilla_id(df: pd.DataFrame) -> pd.Series:
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
    keep = {k: v for k, v in meta.items() if k not in {"folder", "prefix"}}
    return pd.DataFrame([keep])


def build_dim_geography(df: pd.DataFrame) -> pd.DataFrame:
    cols = ["ID_ESTADO", "NOMBRE_ESTADO", "SECCION", "ID_DISTRITO", "NOMBRE_DISTRITO", "MUNICIPIO"]
    out = (
        df[cols]
        .drop_duplicates()
        .dropna(subset=["ID_ESTADO", "SECCION"])
        .sort_values(["ID_ESTADO", "SECCION"])
        .reset_index(drop=True)
    )
    out["geo_id"] = make_geo_id(out)
    return out[["geo_id"] + [c for c in out.columns if c != "geo_id"]]


def build_dim_casilla(df: pd.DataFrame, election_id: str) -> pd.DataFrame:
    cols = [
        "ID_ESTADO",
        "SECCION",
        "TIPO_CASILLA",
        "ID_CASILLA",
        "EXT_CONTIGUA",
        "CASILLA_RAW",
        "LISTA_NOMINAL_CASILLA",
        "TIPO_ELECCION",
        "ESTATUS_ACTA",
        "NOTA",
    ]
    cols = [c for c in cols if c in df.columns]
    out = df[cols].copy().reset_index(drop=True)
    out["casilla_id"] = make_casilla_id(df)
    out["election_id"] = election_id
    out["geo_id"] = make_geo_id(df)
    out = out.drop_duplicates(subset=["election_id", "casilla_id"]).reset_index(drop=True)
    front = ["casilla_id", "election_id", "geo_id"]
    return out[front + [c for c in out.columns if c not in front]]


def build_dim_party(party_keys: list[str]) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "party_key": key,
            "is_coalition": PARTY_META_2006.get(key, {"is_coalition": False})["is_coalition"],
            "members": ",".join(PARTY_META_2006.get(key, {"members": []})["members"]),
        }
        for key in party_keys
    ])


def build_dim_candidatos(election_type: str) -> pd.DataFrame:
    csv_rel = CANDIDATES_CSV_2006.get(election_type)
    if csv_rel is None:
        print(f"    No candidates CSV mapped for {election_type}, skipping")
        return pd.DataFrame()
    csv_path = Path(csv_rel)
    if not csv_path.exists():
        print(f"    Not found: {csv_path}, skipping")
        return pd.DataFrame()
    df = pd.read_csv(csv_path, encoding="utf-8", low_memory=False)
    df.columns = df.columns.str.strip()
    return df


def build_fact(df: pd.DataFrame, party_keys: list[str], election_id: str) -> pd.DataFrame:
    df = df.copy()
    df["casilla_id"] = make_casilla_id(df)

    dupe_count = df["casilla_id"].duplicated().sum()
    if dupe_count:
        sample = df.loc[df["casilla_id"].duplicated(keep=False), ["casilla_id", "CASILLA_RAW"]].head(10)
        raise ValueError(
            f"[{election_id}] casilla_id not unique: {dupe_count:,} duplicates.\n"
            f"{sample.to_string(index=False)}"
        )

    vote_meta = ["CNR", "VALIDOS", "VN", "TOTAL_VOTOS"]
    fact = (
        df[["casilla_id"] + party_keys + vote_meta]
        .melt(
            id_vars=["casilla_id"] + vote_meta,
            value_vars=party_keys,
            var_name="party_key",
            value_name="votes",
        )
    )
    fact["election_id"] = election_id
    fact["votes"] = fact["votes"].fillna(0).astype(int)
    return fact[["election_id", "casilla_id", "party_key", "votes"] + vote_meta]


def sanity_check(
    fact: pd.DataFrame,
    dim_casilla: pd.DataFrame,
    dim_party: pd.DataFrame,
    dim_geography: pd.DataFrame,
    election_id: str,
) -> None:
    print(f"\n{'-' * 55}")
    print(f"  {election_id}")
    print(f"{'-' * 55}")
    totals = fact.groupby("party_key")["votes"].sum().sort_values(ascending=False).reset_index()
    print(totals.to_string(index=False))
    print(f"\nOrphan casilla_ids : {len(set(fact['casilla_id']) - set(dim_casilla['casilla_id']))}")
    print(f"Orphan party_keys  : {len(set(fact['party_key']) - set(dim_party['party_key']))}")
    print(f"Orphan geo_ids     : {len(set(dim_casilla['geo_id']) - set(dim_geography['geo_id']))}")
    print(f"\ndim_geography      : {len(dim_geography):>10,} rows")
    print(f"dim_casilla        : {len(dim_casilla):>10,} rows")
    print(f"dim_party          : {len(dim_party):>10,} rows")
    print(f"fact_casilla_vote  : {len(fact):>10,} rows")


def main() -> None:
    all_elections = []
    all_geography = []
    all_casillas = []
    all_parties = []
    all_candidatos = []
    all_facts = []

    for election_type, meta in OFFICE_META_2006.items():
        election_id = meta["election_id"]
        print(f"\nProcessing {election_type}...")

        df_raw, party_keys = load_office(meta)

        dim_election = build_dim_election(meta)
        dim_geography = build_dim_geography(df_raw)
        dim_casilla = build_dim_casilla(df_raw, election_id)
        dim_party = build_dim_party(party_keys)
        dim_candidatos = build_dim_candidatos(election_type)
        fact = build_fact(df_raw, party_keys, election_id)

        sanity_check(fact, dim_casilla, dim_party, dim_geography, election_id)

        all_elections.append(dim_election)
        all_geography.append(dim_geography)
        all_casillas.append(dim_casilla)
        all_parties.append(dim_party)
        all_candidatos.append(dim_candidatos)
        all_facts.append(fact)

    print("\nAll 2006 elections processed")

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

    dim_election_final.to_parquet(OUT / "dim_election.parquet", index=False)
    dim_geography_final.to_parquet(OUT / "dim_geography.parquet", index=False)
    dim_casilla_final.to_parquet(OUT / "dim_casilla.parquet", index=False)
    dim_party_final.to_parquet(OUT / "dim_party.parquet", index=False)

    if not dim_candidatos_final.empty:
        dim_candidatos_final.to_parquet(OUT / "dim_candidatos.parquet", index=False)
    else:
        print("  dim_candidatos is empty - skipping parquet write")

    fact_final.to_parquet(
        OUT / "fact_casilla_vote.parquet",
        index=False,
        partition_cols=["election_id"],
    )

    print("\nWritten to", OUT.resolve())
    for f in sorted(OUT.rglob("*.parquet")):
        print(f"  {f.relative_to(OUT)}")


if __name__ == "__main__":
    main()
