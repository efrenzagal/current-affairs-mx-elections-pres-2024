from __future__ import annotations

from pathlib import Path

import pandas as pd

RAW_DIR = Path("data/electoral_data_raw/raw_2021/20210611_1000_CW_diputaciones")
RAW_CSV = RAW_DIR / "diputaciones.csv"
CANDIDATES_CSV = RAW_DIR / "diputaciones_candidaturas_2021.csv"
OUT = Path("data/electoral_data_clean/clean_2021")
OUT.mkdir(parents=True, exist_ok=True)

ELECTION_META_2021 = {
    "DIPUTACIONES_2021": {
        "election_id": "DIP_MR_2021",
        "year": 2021,
        "election_type": "DIP",
        "chamber": "deputies",
        "seat_method": "fptp",
        "total_seats": 300,
        "term_years": 3,
    },
}

RAW_TO_PARTY_KEY = {
    "PAN-PRI-PRD": "PAN_PRI_PRD",
    "PAN-PRI": "PAN_PRI",
    "PAN-PRD": "PAN_PRD",
    "PRI-PRD": "PRI_PRD",
    "PVEM-PT-MORENA": "PVEM_PT_MORENA",
    "PVEM-PT": "PVEM_PT",
    "PVEM-MORENA": "PVEM_MORENA",
    "PT-MORENA": "PT_MORENA",
    "CANDIDATO/A NO REGISTRADO/A": "CNR",
    "VOTOS NULOS": "VN",
}

PARTY_META_2021 = {
    "PAN": {"is_coalition": False, "members": []},
    "PRI": {"is_coalition": False, "members": []},
    "PRD": {"is_coalition": False, "members": []},
    "PVEM": {"is_coalition": False, "members": []},
    "PT": {"is_coalition": False, "members": []},
    "MC": {"is_coalition": False, "members": []},
    "MORENA": {"is_coalition": False, "members": []},
    "PES": {"is_coalition": False, "members": []},
    "RSP": {"is_coalition": False, "members": []},
    "FXM": {"is_coalition": False, "members": []},
    "CI": {"is_coalition": False, "members": []},
    "PAN_PRI_PRD": {"is_coalition": True, "members": ["PAN", "PRI", "PRD"]},
    "PAN_PRI": {"is_coalition": True, "members": ["PAN", "PRI"]},
    "PAN_PRD": {"is_coalition": True, "members": ["PAN", "PRD"]},
    "PRI_PRD": {"is_coalition": True, "members": ["PRI", "PRD"]},
    "PVEM_PT_MORENA": {"is_coalition": True, "members": ["PVEM", "PT", "MORENA"]},
    "PVEM_PT": {"is_coalition": True, "members": ["PVEM", "PT"]},
    "PVEM_MORENA": {"is_coalition": True, "members": ["PVEM", "MORENA"]},
    "PT_MORENA": {"is_coalition": True, "members": ["PT", "MORENA"]},
}

NON_PARTY_COLS = {
    "CLAVE_CASILLA",
    "CLAVE_ACTA",
    "ID_ESTADO",
    "NOMBRE_ESTADO",
    "ID_DISTRITO",
    "NOMBRE_DISTRITO",
    "SECCION",
    "ID_CASILLA",
    "TIPO_CASILLA",
    "EXT_CONTIGUA",
    "CASILLA",
    "NUM_ACTA_IMPRESO",
    "CNR",
    "VN",
    "TOTAL_VOTOS_CALCULADOS",
    "LISTA_NOMINAL_CASILLA",
    "OBSERVACIONES",
    "MECANISMOS_TRASLADO",
    "FECHA_HORA",
}


def load_raw(path: Path) -> tuple[pd.DataFrame, list[str]]:
    df = pd.read_csv(path, low_memory=False, encoding="latin-1", sep="|", skiprows=6)
    df.columns = (
        df.columns
        .str.replace("\ufeff", "", regex=False)
        .str.replace("ï»¿", "", regex=False)
        .str.strip()
    )
    df = df.drop(columns=[c for c in df.columns if c.startswith("Unnamed:")])
    df = df.rename(columns=RAW_TO_PARTY_KEY)

    df["CLAVE_CASILLA"] = df["CLAVE_CASILLA"].astype(str).str.strip().str.strip("'")
    df["CLAVE_ACTA"] = df["CLAVE_ACTA"].astype(str).str.strip().str.strip("'")

    int_cols = [
        "ID_ESTADO",
        "ID_DISTRITO",
        "SECCION",
        "ID_CASILLA",
        "EXT_CONTIGUA",
        "NUM_ACTA_IMPRESO",
        "LISTA_NOMINAL_CASILLA",
        "TOTAL_VOTOS_CALCULADOS",
        "CNR",
        "VN",
    ]
    for col in int_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    candidate_cols = [c for c in df.columns if c not in NON_PARTY_COLS]
    for col in candidate_cols:
        if df[col].dtype == object:
            df[col] = (
                df[col].astype(str)
                .str.strip()
                .str.strip("'\"")
                .str.replace(",", "", regex=False)
            )
        df[col] = pd.to_numeric(df[col], errors="coerce")

    party_keys = [
        c for c in candidate_cols
        if pd.api.types.is_numeric_dtype(df[c]) and df[c].notna().any()
    ]
    dropped = set(candidate_cols) - set(party_keys)
    if dropped:
        print(f"    Columns dropped from party_keys (all-NaN after coercion): {sorted(dropped)}")

    return df, party_keys


def make_geo_id(df: pd.DataFrame) -> pd.Series:
    return (
        df["ID_ESTADO"].astype(int).astype(str).str.zfill(2)
        + "_"
        + df["SECCION"].astype(int).astype(str).str.zfill(4)
    )


def make_casilla_id(df: pd.DataFrame) -> pd.Series:
    return df["CLAVE_ACTA"].astype(str).str.strip()


def build_dim_election(meta: dict) -> pd.DataFrame:
    return pd.DataFrame([meta])


def build_dim_geography(df: pd.DataFrame) -> pd.DataFrame:
    geo_cols = [
        "ID_ESTADO",
        "NOMBRE_ESTADO",
        "SECCION",
        "ID_DISTRITO",
        "NOMBRE_DISTRITO",
    ]
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
        "CLAVE_CASILLA",
        "CLAVE_ACTA",
        "TIPO_CASILLA",
        "ID_CASILLA",
        "EXT_CONTIGUA",
        "CASILLA",
        "NUM_ACTA_IMPRESO",
        "LISTA_NOMINAL_CASILLA",
        "OBSERVACIONES",
        "MECANISMOS_TRASLADO",
        "FECHA_HORA",
    ]
    out = (
        df[casilla_cols]
        .drop_duplicates(subset=["CLAVE_ACTA"])
        .copy()
        .reset_index(drop=True)
    )
    out["election_id"] = election_id
    out["casilla_id"] = make_casilla_id(out)
    out["geo_id"] = make_geo_id(out)
    front = ["casilla_id", "election_id", "geo_id"]
    return out[front + [c for c in out.columns if c not in front]]


def build_dim_party(party_keys: list[str]) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "party_key": key,
            "is_coalition": PARTY_META_2021.get(key, {"is_coalition": False})["is_coalition"],
            "members": ",".join(PARTY_META_2021.get(key, {"members": []})["members"]),
        }
        for key in party_keys
    ])


def normalize_candidate_party_key(value: object) -> str:
    key = str(value).strip()
    replacements = {
        "MOVIMIENTO CIUDADANO": "MC",
        "FS X MÉXICO": "FXM",
        "FS X MEXICO": "FXM",
    }
    return replacements.get(key, key)


def build_dim_candidatos(path: Path) -> pd.DataFrame:
    if not path.exists():
        print(f"    Not found: {path}, skipping")
        return pd.DataFrame()

    df = pd.read_csv(path, encoding="latin-1", sep="|", skiprows=1, low_memory=False)
    df.columns = df.columns.str.strip()
    out = pd.DataFrame({
        "election_type": "DIP",
        "party_key": df["PARTIDO_CI"].map(normalize_candidate_party_key),
        "id_estado": pd.to_numeric(df["ESTADO"], errors="coerce"),
        "nombre_estado": None,
        "id_distrito_federal": pd.to_numeric(df["DISTRITO"], errors="coerce"),
        "candidate_name": df["CANDIDATURA_PROPIETARIA"].astype(str).str.strip(),
        "candidate_suplente": df["CANDIDATURA_SUPLENTE"].astype(str).str.strip(),
        "partido_politico": df["PARTIDO_CI"].astype(str).str.strip(),
        "votacion_ganador": None,
        "pct_ganador": None,
    })
    return out


def build_fact(df: pd.DataFrame, party_keys: list[str], election_id: str) -> pd.DataFrame:
    df = df.copy()
    df["casilla_id"] = make_casilla_id(df)

    dupe_count = df["casilla_id"].duplicated().sum()
    if dupe_count:
        raise ValueError(f"[{election_id}] casilla_id is not unique: {dupe_count:,} duplicate rows")

    vote_meta = ["CNR", "VN", "TOTAL_VOTOS_CALCULADOS"]
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
    total_errors = (expected != df_raw["TOTAL_VOTOS_CALCULADOS"]).sum()

    print(f"\nOrphan casilla_ids : {len(set(fact['casilla_id']) - set(dim_casilla['casilla_id']))}")
    print(f"Orphan party_keys  : {len(set(fact['party_key']) - set(dim_party['party_key']))}")
    print(f"Orphan geo_ids     : {len(set(dim_casilla['geo_id']) - set(dim_geography['geo_id']))}")
    print(f"TOTAL_VOTOS errors : {total_errors:,}")
    print(f"\ndim_geography      : {len(dim_geography):>10,} rows")
    print(f"dim_casilla        : {len(dim_casilla):>10,} rows")
    print(f"dim_party          : {len(dim_party):>10,} rows")
    print(f"fact_casilla_vote  : {len(fact):>10,} rows")


def main() -> None:
    election_type, meta = next(iter(ELECTION_META_2021.items()))
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

    print("\nAll 2021 elections processed")
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
