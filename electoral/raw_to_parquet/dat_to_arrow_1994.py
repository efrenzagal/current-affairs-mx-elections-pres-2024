from __future__ import annotations

import re
import time
from io import StringIO
from pathlib import Path

import pandas as pd
import requests

BASE_URL = "https://portalanterior.ine.mx/documentos/RESELEC/nuevo_1994/pres_94/dto_cas"
CACHE_DIR = Path("data/electoral_data_raw/raw_1994/presidente")
OUT = Path("data/electoral_data_clean/clean_1994")
OUT.mkdir(parents=True, exist_ok=True)

REQUEST_DELAY_SECONDS = 1.5
MAX_RETRIES_429 = 4
RETRY_BACKOFF_BASE_SECONDS = 20

ELECTION_META_1994 = {
    "PRESIDENCIA_1994": {
        "election_id": "PRE_1994",
        "year": 1994,
        "election_type": "PRE",
        "chamber": None,
        "seat_method": "direct",
        "total_seats": 1,
        "term_years": 6,
    },
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

PARTY_META_1994 = {
    "PAN": {"is_coalition": False, "members": []},
    "PRI": {"is_coalition": False, "members": []},
    "PPS": {"is_coalition": False, "members": []},
    "PRD": {"is_coalition": False, "members": []},
    "PFCRN": {"is_coalition": False, "members": []},
    "PARM": {"is_coalition": False, "members": []},
    "UNO_PDM": {"is_coalition": False, "members": []},
    "PT": {"is_coalition": False, "members": []},
    "PVEM": {"is_coalition": False, "members": []},
}

NON_PARTY_COLS = {
    "ENTIDAD",
    "CABECERA",
    "DISTRITO",
    "ID_DISTRITO",
    "MUNICIPIO",
    "SECCION",
    "CASILLA",
    "CASILLA_RAW",
    "TIPO_CASILLA",
    "ID_CASILLA",
    "NO_REG",
    "CNR",
    "NULOS",
    "VN",
    "TOTAL",
    "STATUS CASILLA",
    "STATUS_CASILLA",
    "ID_ESTADO",
    "NOMBRE_ESTADO",
}


def url_for(id_estado: int) -> str:
    return f"{BASE_URL}/{id_estado}_pre_cas_94.txt"


def fetch_state_txt(id_estado: int) -> str:
    cache_path = CACHE_DIR / f"{id_estado:02d}_pre_cas_94.txt"
    if cache_path.exists():
        return cache_path.read_text(encoding="latin-1")

    url = url_for(id_estado)
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
        cache_path.write_text(text, encoding="latin-1")
        time.sleep(REQUEST_DELAY_SECONDS)
        return text

    raise RuntimeError(f"Gave up on {url} after {MAX_RETRIES_429} retries")


def parse_casilla(raw: pd.Series) -> pd.DataFrame:
    s = raw.fillna("").astype(str).str.strip().str.upper()
    tipo = s.str.extract(r"^([A-Z]+)", expand=False).fillna("X")
    tipo = tipo.replace({
        "BASICA": "B",
        "CONTIGUA": "C",
        "EXTRAORDINARIA": "E",
        "ESPECIAL": "S",
    })
    tipo = tipo.str[0]

    raw_num = pd.to_numeric(s.str.extract(r"^[A-Z]+(\d+)", expand=False), errors="coerce")
    id_casilla = raw_num.fillna(1).astype(int)
    return pd.DataFrame({"TIPO_CASILLA": tipo, "ID_CASILLA": id_casilla})


def load_raw() -> tuple[pd.DataFrame, list[str]]:
    frames = []
    for id_estado in range(1, 33):
        text = fetch_state_txt(id_estado)
        df = pd.read_csv(StringIO(text), sep=";", encoding="latin-1", low_memory=False)
        df.columns = [
            re.sub(r"\s+", " ", c).strip().replace("\ufeff", "")
            for c in df.columns.astype(str)
        ]
        df["ID_ESTADO"] = id_estado
        frames.append(df)
        print(f"    Estado {id_estado:02d} ({ESTADO_NOMBRES[id_estado]}): {len(df):,} rows")

    df_all = pd.concat(frames, ignore_index=True)
    df_all = df_all.rename(columns={
        "DISTRITO": "ID_DISTRITO",
        "CASILLA": "CASILLA_RAW",
        "NO_REG": "CNR",
        "NULOS": "VN",
        "STATUS CASILLA": "STATUS_CASILLA",
    })
    df_all["NOMBRE_ESTADO"] = df_all["ID_ESTADO"].map(ESTADO_NOMBRES)
    if "ENTIDAD" in df_all.columns:
        df_all["NOMBRE_ESTADO_RAW"] = df_all["ENTIDAD"]

    casilla_parts = parse_casilla(df_all["CASILLA_RAW"])
    df_all["TIPO_CASILLA"] = casilla_parts["TIPO_CASILLA"]
    df_all["ID_CASILLA"] = casilla_parts["ID_CASILLA"]

    for col in ["ID_DISTRITO", "SECCION", "CNR", "VN", "TOTAL"]:
        if col in df_all.columns:
            df_all[col] = pd.to_numeric(df_all[col], errors="coerce")

    candidate_cols = [
        c for c in df_all.columns
        if c not in NON_PARTY_COLS
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
        print(f"    Combining {dupe_count:,} duplicate casilla rows into single totals")
        sum_cols = party_keys + ["CNR", "VN", "TOTAL"]
        first_cols = [c for c in df_all.columns if c not in set(sum_cols + ["__casilla_id"])]
        agg = {c: "sum" for c in sum_cols}
        agg.update({c: "first" for c in first_cols})
        df_all = df_all.groupby("__casilla_id", as_index=False).agg(agg)
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
    )


def build_dim_election(meta: dict) -> pd.DataFrame:
    return pd.DataFrame([meta])


def build_dim_geography(df: pd.DataFrame) -> pd.DataFrame:
    geo_cols = ["ID_ESTADO", "NOMBRE_ESTADO", "SECCION", "ID_DISTRITO", "CABECERA", "MUNICIPIO"]
    out = (
        df[geo_cols]
        .drop_duplicates()
        .dropna(subset=["ID_ESTADO", "SECCION"])
        .sort_values(["ID_ESTADO", "SECCION"])
        .reset_index(drop=True)
    )
    out["geo_id"] = make_geo_id(out)
    return out[["geo_id"] + [c for c in out.columns if c != "geo_id"]]


def build_dim_casilla(df: pd.DataFrame, election_id: str) -> pd.DataFrame:
    casilla_cols = [
        "ID_ESTADO",
        "SECCION",
        "TIPO_CASILLA",
        "ID_CASILLA",
        "CASILLA_RAW",
        "STATUS_CASILLA",
    ]
    out = df[casilla_cols].copy().reset_index(drop=True)
    out["election_id"] = election_id
    out["casilla_id"] = make_casilla_id(df)
    out["geo_id"] = make_geo_id(df)
    out = out.drop_duplicates(subset=["election_id", "casilla_id"]).reset_index(drop=True)
    front = ["casilla_id", "election_id", "geo_id"]
    return out[front + [c for c in out.columns if c not in front]]


def build_dim_party(party_keys: list[str]) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "party_key": key,
            "is_coalition": PARTY_META_1994.get(key, {"is_coalition": False})["is_coalition"],
            "members": ",".join(PARTY_META_1994.get(key, {"members": []})["members"]),
        }
        for key in party_keys
    ])


def build_fact(df: pd.DataFrame, party_keys: list[str], election_id: str) -> pd.DataFrame:
    df = df.copy()
    df["casilla_id"] = make_casilla_id(df)

    dupe_count = df["casilla_id"].duplicated().sum()
    if dupe_count:
        raise ValueError(f"[{election_id}] casilla_id is not unique: {dupe_count:,} duplicate rows")

    vote_meta = ["CNR", "VN", "TOTAL"]
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
    df_raw: pd.DataFrame,
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

    expected = df_raw[dim_party["party_key"].tolist() + ["CNR", "VN"]].sum(axis=1)
    total_errors = (expected != df_raw["TOTAL"]).sum()

    print(f"\nOrphan casilla_ids : {len(set(fact['casilla_id']) - set(dim_casilla['casilla_id']))}")
    print(f"Orphan party_keys  : {len(set(fact['party_key']) - set(dim_party['party_key']))}")
    print(f"Orphan geo_ids     : {len(set(dim_casilla['geo_id']) - set(dim_geography['geo_id']))}")
    print(f"TOTAL errors       : {total_errors:,}")
    print(f"\ndim_geography      : {len(dim_geography):>10,} rows")
    print(f"dim_casilla        : {len(dim_casilla):>10,} rows")
    print(f"dim_party          : {len(dim_party):>10,} rows")
    print(f"fact_casilla_vote  : {len(fact):>10,} rows")


def main() -> None:
    election_type, meta = next(iter(ELECTION_META_1994.items()))
    election_id = meta["election_id"]
    print(f"\nProcessing {election_type}...")

    df_raw, party_keys = load_raw()
    dim_election = build_dim_election(meta)
    dim_geography = build_dim_geography(df_raw)
    dim_casilla = build_dim_casilla(df_raw, election_id)
    dim_party = build_dim_party(party_keys)
    fact = build_fact(df_raw, party_keys, election_id)

    sanity_check(df_raw, fact, dim_casilla, dim_party, dim_geography, election_id)

    print("\nAll 1994 presidential data processed")
    print(f"\ndim_election      : {len(dim_election):>10,} rows")
    print(f"dim_geography     : {len(dim_geography):>10,} rows")
    print(f"dim_casilla       : {len(dim_casilla):>10,} rows")
    print(f"dim_party         : {len(dim_party):>10,} rows")
    print(f"fact_casilla_vote : {len(fact):>10,} rows")

    dim_election.to_parquet(OUT / "dim_election.parquet", index=False)
    dim_geography.to_parquet(OUT / "dim_geography.parquet", index=False)
    dim_casilla.to_parquet(OUT / "dim_casilla.parquet", index=False)
    dim_party.to_parquet(OUT / "dim_party.parquet", index=False)
    # delete_matching clears each partition before writing. Without it pyarrow
    # defaults to overwrite_or_ignore, which drops a second randomly-named copy
    # of every row into the existing partition dir on each re-run.
    fact.to_parquet(
        OUT / "fact_casilla_vote.parquet",
        index=False,
        partition_cols=["election_id"],
        existing_data_behavior="delete_matching",
    )

    print("\nWritten to", OUT.resolve())
    for f in sorted(OUT.rglob("*.parquet")):
        print(f"  {f.relative_to(OUT)}")


if __name__ == "__main__":
    main()
