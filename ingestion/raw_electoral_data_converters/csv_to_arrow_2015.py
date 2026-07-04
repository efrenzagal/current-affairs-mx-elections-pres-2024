from __future__ import annotations

from pathlib import Path

import pandas as pd

RAW_DIR = Path("data/electoral_data_raw/raw_2015")
RAW_CSV = RAW_DIR / "diputados.csv"
CANDIDATES_CSV = RAW_DIR / "Cat_Candidatos_Diputado.csv"
OUT = Path("data/electoral_data_clean/clean_2015")
OUT.mkdir(parents=True, exist_ok=True)

ELECTION_META_2015 = {
    "DIPUTACIONES_2015": {
        "election_id": "DIP_MR_2015",
        "year": 2015,
        "election_type": "DIP",
        "chamber": "deputies",
        "seat_method": "fptp",
        "total_seats": 300,
        "term_years": 3,
    },
}

PARTY_META_2015 = {
    "PAN": {"is_coalition": False, "members": []},
    "PRI": {"is_coalition": False, "members": []},
    "PRD": {"is_coalition": False, "members": []},
    "PVEM": {"is_coalition": False, "members": []},
    "PT": {"is_coalition": False, "members": []},
    "MC": {"is_coalition": False, "members": []},
    "PANAL": {"is_coalition": False, "members": []},
    "MORENA": {"is_coalition": False, "members": []},
    "PH": {"is_coalition": False, "members": []},
    "PES": {"is_coalition": False, "members": []},
    "C_PRI_PVEM": {"is_coalition": True, "members": ["PRI", "PVEM"]},
    "C_PRD_PT": {"is_coalition": True, "members": ["PRD", "PT"]},
    "CAND_IND_1": {"is_coalition": False, "members": []},
    "CAND_IND_2": {"is_coalition": False, "members": []},
}

RAW_TO_PARTY_KEY = {
    "MOVIMIENTO_CIUDADANO": "MC",
    "NUEVA_ALIANZA": "PANAL",
    "PS": "PES",
    "NO_REGISTRADOS": "CNR",
    "NULOS": "VN",
    "TOTAL_VOTOS": "TOTAL_VOTOS",
    "LISTA_NOMINAL": "LISTA_NOMINAL_CASILLA",
}

NON_PARTY_COLS = {
    "ESTADO",
    "ID_ESTADO",
    "DISTRITO",
    "ID_DISTRITO",
    "SECCION",
    "ID_CASILLA",
    "TIPO_CASILLA",
    "EXT_CONTIGUA",
    "UBICACION_CASILLA",
    "TIPO_ACTA",
    "NUM_BOLETAS_SOBRANTES",
    "TOTAL_CIUDADANOS_VOTARON",
    "NUM_BOLETAS_EXTRAIDAS",
    "CNR",
    "VN",
    "TOTAL_VOTOS",
    "LISTA_NOMINAL_CASILLA",
    "OBSERVACIONES",
    "CONTABILIZADA",
    "NOMBRE_ESTADO",
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


def load_raw(path: Path) -> tuple[pd.DataFrame, list[str]]:
    df = pd.read_csv(path, low_memory=False, encoding="latin-1", sep="|", skiprows=5)
    df.columns = (
        df.columns
        .str.replace("\ufeff", "", regex=False)
        .str.replace("ï»¿", "", regex=False)
        .str.strip()
    )
    df = df.drop(columns=[c for c in df.columns if c.startswith("Unnamed:")])
    df = df.rename(columns=RAW_TO_PARTY_KEY)
    df = df.rename(columns={"ESTADO": "ID_ESTADO", "DISTRITO": "ID_DISTRITO"})

    numeric_cols = [
        "ID_ESTADO",
        "ID_DISTRITO",
        "SECCION",
        "ID_CASILLA",
        "EXT_CONTIGUA",
        "UBICACION_CASILLA",
        "TIPO_ACTA",
        "NUM_BOLETAS_SOBRANTES",
        "TOTAL_CIUDADANOS_VOTARON",
        "NUM_BOLETAS_EXTRAIDAS",
        "CNR",
        "VN",
        "TOTAL_VOTOS",
        "LISTA_NOMINAL_CASILLA",
        "CONTABILIZADA",
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = coerce_numeric(df[col])

    df["NOMBRE_ESTADO"] = df["ID_ESTADO"].map(ESTADO_NOMBRES)

    candidate_cols = [c for c in df.columns if c not in NON_PARTY_COLS]
    for col in candidate_cols:
        df[col] = coerce_numeric(df[col])

    party_keys = [
        c for c in candidate_cols
        if pd.api.types.is_numeric_dtype(df[c]) and df[c].notna().any()
    ]
    dropped = set(candidate_cols) - set(party_keys)
    if dropped:
        print(f"    Columns dropped from party_keys (all-NaN after coercion): {sorted(dropped)}")

    # The file contains only final counted rows here, but keep this filter so
    # reruns on a different cut still match the LEEME definition.
    df = df[df["CONTABILIZADA"] == 1].copy()
    for col in party_keys:
        df[col] = df[col].fillna(0)

    return df, party_keys


def coerce_numeric(s: pd.Series) -> pd.Series:
    if s.dtype == object:
        s = (
            s.astype(str)
            .str.strip()
            .str.strip("'\"")
            .str.replace(",", "", regex=False)
            .replace({"": None, " ": None, "-": None, "nan": None})
        )
    return pd.to_numeric(s, errors="coerce")


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
        + "A"
        + df["TIPO_ACTA"].astype(int).astype(str)
    )


def build_dim_election(meta: dict) -> pd.DataFrame:
    return pd.DataFrame([meta])


def build_dim_geography(df: pd.DataFrame) -> pd.DataFrame:
    geo_cols = ["ID_ESTADO", "NOMBRE_ESTADO", "SECCION", "ID_DISTRITO"]
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
        "EXT_CONTIGUA",
        "UBICACION_CASILLA",
        "TIPO_ACTA",
        "NUM_BOLETAS_SOBRANTES",
        "TOTAL_CIUDADANOS_VOTARON",
        "NUM_BOLETAS_EXTRAIDAS",
        "LISTA_NOMINAL_CASILLA",
        "OBSERVACIONES",
        "CONTABILIZADA",
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
            "is_coalition": PARTY_META_2015.get(key, {"is_coalition": False})["is_coalition"],
            "members": ",".join(PARTY_META_2015.get(key, {"members": []})["members"]),
        }
        for key in party_keys
    ])


def normalize_candidate_party_key(value: object) -> str:
    key = str(value).strip()
    replacements = {
        "MOVIMIENTO CIUDADANO": "MC",
        "NUEVA ALIANZA": "PANAL",
        "PARTIDO HUMANISTA": "PH",
        "ENCUENTRO SOCIAL": "PES",
        "PRI-PVEM": "C_PRI_PVEM",
        "PRD-PT": "C_PRD_PT",
        "CI_1": "CAND_IND_1",
        "CI_2": "CAND_IND_2",
    }
    return replacements.get(key, key)


def build_dim_candidatos(path: Path) -> pd.DataFrame:
    if not path.exists():
        print(f"    Not found: {path}, skipping")
        return pd.DataFrame()

    df = pd.read_csv(path, encoding="latin-1", sep="|", skiprows=1, low_memory=False)
    df.columns = df.columns.str.strip()
    return pd.DataFrame({
        "election_type": "DIP",
        "party_key": df["PARTIDO"].map(normalize_candidate_party_key),
        "id_estado": pd.to_numeric(df["ESTADO"], errors="coerce"),
        "nombre_estado": None,
        "id_distrito_federal": pd.to_numeric(df["DISTRITO"], errors="coerce"),
        "candidate_name": df["CANDIDATO_PROPIETARIO"].astype(str).str.strip(),
        "candidate_suplente": df["CANDIDATO_SUPLENTE"].astype(str).str.strip(),
        "partido_politico": df["PARTIDO"].astype(str).str.strip(),
        "votacion_ganador": None,
        "pct_ganador": None,
    })


def build_fact(df: pd.DataFrame, party_keys: list[str], election_id: str) -> pd.DataFrame:
    df = df.copy()
    df["casilla_id"] = make_casilla_id(df)

    dupe_count = df["casilla_id"].duplicated().sum()
    if dupe_count:
        raise ValueError(f"[{election_id}] casilla_id is not unique: {dupe_count:,} duplicate rows")

    vote_meta = ["CNR", "VN", "TOTAL_VOTOS"]
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
    total_errors = (expected != df_raw["TOTAL_VOTOS"]).sum()

    print(f"\nOrphan casilla_ids : {len(set(fact['casilla_id']) - set(dim_casilla['casilla_id']))}")
    print(f"Orphan party_keys  : {len(set(fact['party_key']) - set(dim_party['party_key']))}")
    print(f"Orphan geo_ids     : {len(set(dim_casilla['geo_id']) - set(dim_geography['geo_id']))}")
    print(f"TOTAL_VOTOS errors : {total_errors:,}")
    print(f"\ndim_geography      : {len(dim_geography):>10,} rows")
    print(f"dim_casilla        : {len(dim_casilla):>10,} rows")
    print(f"dim_party          : {len(dim_party):>10,} rows")
    print(f"fact_casilla_vote  : {len(fact):>10,} rows")


def main() -> None:
    election_type, meta = next(iter(ELECTION_META_2015.items()))
    election_id = meta["election_id"]

    print(f"\nProcessing {election_type}...")
    print(f"  Reading: {RAW_CSV}")
    df_raw, party_keys = load_raw(RAW_CSV)

    dim_election = build_dim_election(meta)
    dim_geography = build_dim_geography(df_raw)
    dim_casilla = build_dim_casilla(df_raw, election_id)
    dim_party = build_dim_party(party_keys)
    dim_candidatos = build_dim_candidatos(CANDIDATES_CSV)
    fact = build_fact(df_raw, party_keys, election_id)

    sanity_check(df_raw, fact, dim_casilla, dim_party, dim_geography, election_id)

    print("\nAll 2015 elections processed")
    print(f"\ndim_election      : {len(dim_election):>10,} rows")
    print(f"dim_geography     : {len(dim_geography):>10,} rows")
    print(f"dim_casilla       : {len(dim_casilla):>10,} rows")
    print(f"dim_party         : {len(dim_party):>10,} rows")
    print(f"dim_candidatos    : {len(dim_candidatos):>10,} rows")
    print(f"fact_casilla_vote : {len(fact):>10,} rows")

    dim_election.to_parquet(OUT / "dim_election.parquet", index=False)
    dim_geography.to_parquet(OUT / "dim_geography.parquet", index=False)
    dim_casilla.to_parquet(OUT / "dim_casilla.parquet", index=False)
    dim_party.to_parquet(OUT / "dim_party.parquet", index=False)
    dim_candidatos.to_parquet(OUT / "dim_candidatos.parquet", index=False)
    fact.to_parquet(
        OUT / "fact_casilla_vote.parquet",
        index=False,
        partition_cols=["election_id"],
    )

    print("\nWritten to", OUT.resolve())
    for f in sorted(OUT.rglob("*.parquet")):
        print(f"  {f.relative_to(OUT)}")


if __name__ == "__main__":
    main()
