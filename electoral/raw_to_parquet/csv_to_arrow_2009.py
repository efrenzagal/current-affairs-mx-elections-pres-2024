"""Convert 2009 federal-deputy casilla results into clean Parquet inputs.

The INE exports the same ordinary casilla votes in both the MR and RP files.
The RP export additionally has 778 ``SRP`` rows: special-casilla ballots cast
by transit voters who could vote for a party list but not a district candidate.
This converter stores one combined vote fact for ``DIP_MR_2009``: all MR rows
plus only those RP-only SRP rows.  It does not create a duplicate RP contest.

Run from the repository root:

    python -m electoral.raw_to_parquet.csv_to_arrow_2009
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd


RAW_DIR = Path("data/electoral_data_raw/raw_2009")
MR_CSV = RAW_DIR / "DIPUTACIONES_FED_MR_2009" / "2009_SEE_DIP_FED_MR_NAL_CAS.csv"
RP_CSV = RAW_DIR / "DIPUTACIONES_FED_RP_2009" / "2009_SEE_DIP_FED_RP_NAL_CAS.csv"
OUT = Path("data/electoral_data_clean/clean_2009")

ELECTION_META_2009 = {
    "election_id": "DIP_MR_2009",
    "year": 2009,
    "election_type": "DIP",
    "chamber": "deputies",
    "seat_method": "fptp",
    "total_seats": 300,
    "term_years": 3,
}

RAW_TO_PARTY_KEY = {
    "CONV": "MC",
    "NVA_ALIANZA": "PANAL",
    "PRIMERO_MEXICO": "C_PRI_PVEM",
    "SALVEMOS_MEXICO": "C_PT_MC",
    "NUM_VOTOS_CAN_NREG": "CNR",
    "NUM_VOTOS_NULOS": "VN",
}

PARTY_META_2009 = {
    "PAN": {"is_coalition": False, "members": []},
    "PRI": {"is_coalition": False, "members": []},
    "PRD": {"is_coalition": False, "members": []},
    "PVEM": {"is_coalition": False, "members": []},
    "PT": {"is_coalition": False, "members": []},
    "MC": {"is_coalition": False, "members": []},
    "PANAL": {"is_coalition": False, "members": []},
    "PSD": {"is_coalition": False, "members": []},
    "C_PRI_PVEM": {"is_coalition": True, "members": ["PRI", "PVEM"]},
    "C_PT_MC": {"is_coalition": True, "members": ["PT", "MC"]},
}

KEY_COLS = ["ID_ESTADO", "ID_DISTRITO", "SECCION", "CASILLA"]
PARTY_KEYS = list(PARTY_META_2009)
VOTE_META = ["CNR", "VN", "TOTAL_VOTOS"]
NUMERIC_COLS = [
    "CIRCUNSCRIPCION", "ID_ESTADO", "ID_DISTRITO", "ID_MUNICIPIO", "SECCION",
    *PARTY_KEYS, *VOTE_META, "TOTAL_VOTOS", "LISTA_NOMINAL",
]
REQUIRED_COLUMNS = {
    "CIRCUNSCRIPCION", "ID_ESTADO", "NOMBRE_ESTADO", "ID_DISTRITO",
    "CABECERA_DISTRITAL", "ID_MUNICIPIO", "MUNICIPIO", "SECCION", "CASILLA",
    *RAW_TO_PARTY_KEY, "TOTAL_VOTOS", "LISTA_NOMINAL", "ESTATUS_ACTA", "TEPJF",
    "RUTA_ACTA",
}


def load_source(path: Path) -> pd.DataFrame:
    """Read one 2009 casilla export and normalize numeric/vote columns."""
    if not path.exists():
        raise FileNotFoundError(f"Missing 2009 source file: {path}")

    df = pd.read_csv(path, encoding="latin-1", low_memory=False)
    df.columns = (
        df.columns.str.replace("\ufeff", "", regex=False)
        .str.replace("ï»¿", "", regex=False)
        .str.strip()
    )
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"[{path.name}] missing required columns: {sorted(missing)}")

    df = df.rename(columns=RAW_TO_PARTY_KEY)
    for col in NUMERIC_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df["CASILLA"] = df["CASILLA"].astype(str).str.strip()
    return df


def source_key_index(df: pd.DataFrame) -> pd.MultiIndex:
    return pd.MultiIndex.from_frame(df[KEY_COLS])


def validate_source_relationship(mr: pd.DataFrame, rp: pd.DataFrame) -> pd.DataFrame:
    """Prove that RP is an MR duplicate plus the SRP-only supplement."""
    if mr.duplicated(KEY_COLS).any() or rp.duplicated(KEY_COLS).any():
        raise ValueError("2009 MR or RP source has duplicate state/district/section/casilla keys")

    mr_keys = source_key_index(mr)
    rp_keys = source_key_index(rp)
    if not mr_keys.isin(rp_keys).all():
        raise ValueError("Some 2009 MR rows are missing from the RP source")

    common = rp[rp_keys.isin(mr_keys)].set_index(KEY_COLS).sort_index()
    expected = mr.set_index(KEY_COLS).sort_index()
    common = common[expected.columns]
    if not common.equals(expected):
        raise ValueError("MR and shared RP rows differ; refusing to double-count or discard results")

    supplement = rp[~rp_keys.isin(mr_keys)].copy()
    if supplement.empty or not supplement["CASILLA"].str.fullmatch(r"SRP\d+").all():
        raise ValueError("RP-only rows are not exclusively SRP special-casilla ballots")
    return supplement


def classify_casilla(casilla: pd.Series) -> pd.Series:
    """Keep enough type detail to distinguish RP-only SRP from normal casillas."""
    return casilla.str.extract(r"^(SRP|B|C|E)", expand=False).fillna("OTRA")


def make_geo_id(df: pd.DataFrame) -> pd.Series:
    return (
        df["ID_ESTADO"].astype(int).astype(str).str.zfill(2)
        + "_"
        + df["SECCION"].astype(int).astype(str).str.zfill(4)
    )


def make_casilla_id(df: pd.DataFrame) -> pd.Series:
    """Stable identity based on the authoritative compact source casilla code."""
    return make_geo_id(df) + "_" + df["CASILLA"].astype(str).str.strip()


def build_dim_election() -> pd.DataFrame:
    return pd.DataFrame([ELECTION_META_2009])


def build_dim_geography(df: pd.DataFrame) -> pd.DataFrame:
    cols = [
        "ID_ESTADO", "NOMBRE_ESTADO", "SECCION", "ID_MUNICIPIO", "MUNICIPIO",
        "ID_DISTRITO", "CABECERA_DISTRITAL", "CIRCUNSCRIPCION",
    ]
    out = (
        df[cols].drop_duplicates(subset=["ID_ESTADO", "SECCION"])
        .sort_values(["ID_ESTADO", "SECCION"]).reset_index(drop=True)
    )
    out["geo_id"] = make_geo_id(out)
    return out[["geo_id"] + [col for col in out if col != "geo_id"]]


def build_dim_casilla(df: pd.DataFrame) -> pd.DataFrame:
    out = df[[
        "ID_ESTADO", "SECCION", "CASILLA", "LISTA_NOMINAL", "ESTATUS_ACTA", "TEPJF",
        "RUTA_ACTA",
    ]].copy()
    out["TIPO_CASILLA"] = classify_casilla(out["CASILLA"])
    out["election_id"] = ELECTION_META_2009["election_id"]
    out["casilla_id"] = make_casilla_id(df)
    out["geo_id"] = make_geo_id(df)
    if out.duplicated(["election_id", "casilla_id"]).any():
        raise ValueError("Generated 2009 casilla IDs are not unique")
    front = ["casilla_id", "election_id", "geo_id"]
    return out[front + [col for col in out if col not in front]]


def build_dim_party() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "party_key": key,
            "is_coalition": meta["is_coalition"],
            "members": ",".join(meta["members"]),
        }
        for key, meta in PARTY_META_2009.items()
    ])


def build_dim_candidatos() -> pd.DataFrame:
    """The supplied 2009 district file gives two names but no party linkage."""
    return pd.DataFrame(columns=[
        "election_type", "party_key", "id_estado", "nombre_estado", "id_distrito_federal",
        "candidate_name", "candidate_suplente", "partido_politico", "votacion_ganador",
        "pct_ganador",
    ])


def build_fact(df: pd.DataFrame) -> pd.DataFrame:
    work = df.copy()
    work["casilla_id"] = make_casilla_id(work)
    fact = work[["casilla_id", *PARTY_KEYS, *VOTE_META]].melt(
        id_vars=["casilla_id", *VOTE_META],
        value_vars=PARTY_KEYS,
        var_name="party_key",
        value_name="votes",
    )
    fact["election_id"] = ELECTION_META_2009["election_id"]
    fact["votes"] = fact["votes"].fillna(0).astype(int)
    return fact[["election_id", "casilla_id", "party_key", "votes", *VOTE_META]]


def sanity_check(
    raw: pd.DataFrame,
    supplement: pd.DataFrame,
    fact: pd.DataFrame,
    dim_casilla: pd.DataFrame,
    dim_party: pd.DataFrame,
    dim_geography: pd.DataFrame,
) -> None:
    """Fail on relationships that would silently distort national or MR totals."""
    expected_votes = raw[PARTY_KEYS + ["CNR", "VN"]].fillna(0).sum(axis=1)
    total_errors = int((expected_votes != raw["TOTAL_VOTOS"].fillna(0)).sum())
    if total_errors:
        raise ValueError(f"2009 TOTAL_VOTOS reconciliation failed for {total_errors:,} rows")
    if len(raw) != 139_959 or len(supplement) != 778:
        raise ValueError(
            f"Unexpected 2009 row counts: combined={len(raw):,}, SRP supplement={len(supplement):,}"
        )
    if raw["ID_ESTADO"].nunique() != 32 or raw[KEY_COLS[:2]].drop_duplicates().shape[0] != 300:
        raise ValueError("2009 coverage is not exactly 32 states and 300 federal districts")
    if fact.duplicated(["election_id", "casilla_id", "party_key"]).any():
        raise ValueError("Duplicate 2009 election/casilla/party fact rows")
    if set(fact["casilla_id"]) != set(dim_casilla["casilla_id"]):
        raise ValueError("2009 fact and casilla identities do not match")
    if not set(fact["party_key"]).issubset(set(dim_party["party_key"])):
        raise ValueError("2009 fact has party keys absent from dim_party")
    if not set(dim_casilla["geo_id"]).issubset(set(dim_geography["geo_id"])):
        raise ValueError("2009 casilla geography has orphan geo IDs")
    if int((dim_casilla["TIPO_CASILLA"] == "SRP").sum()) != len(supplement):
        raise ValueError("2009 SRP rows were not retained as distinct special-casilla records")

    print("\n2009 source and output checks passed")
    print(f"  MR base rows             : {len(raw) - len(supplement):,}")
    print(f"  RP-only SRP rows         : {len(supplement):,}")
    print(f"  combined casilla rows    : {len(raw):,}")
    print(f"  geography sections       : {len(dim_geography):,}")
    print(f"  party vote fact rows     : {len(fact):,}")
    print(f"  SRP total votes          : {int(supplement['TOTAL_VOTOS'].sum()):,}")


def main() -> None:
    print(f"Reading MR base: {MR_CSV}")
    mr = load_source(MR_CSV)
    print(f"Reading RP comparison/supplement: {RP_CSV}")
    rp = load_source(RP_CSV)
    supplement = validate_source_relationship(mr, rp)
    raw = pd.concat([mr, supplement], ignore_index=True)

    dim_election = build_dim_election()
    dim_geography = build_dim_geography(raw)
    dim_casilla = build_dim_casilla(raw)
    dim_party = build_dim_party()
    dim_candidatos = build_dim_candidatos()
    fact = build_fact(raw)
    sanity_check(raw, supplement, fact, dim_casilla, dim_party, dim_geography)

    OUT.mkdir(parents=True, exist_ok=True)
    dim_election.to_parquet(OUT / "dim_election.parquet", index=False)
    dim_geography.to_parquet(OUT / "dim_geography.parquet", index=False)
    dim_casilla.to_parquet(OUT / "dim_casilla.parquet", index=False)
    dim_party.to_parquet(OUT / "dim_party.parquet", index=False)
    dim_candidatos.to_parquet(OUT / "dim_candidatos.parquet", index=False)
    # delete_matching clears each partition before writing. Without it pyarrow
    # defaults to overwrite_or_ignore, which drops a second randomly-named copy
    # of every row into the existing partition dir on each re-run.
    fact.to_parquet(OUT / "fact_casilla_vote.parquet", index=False, partition_cols=["election_id"],
                    existing_data_behavior="delete_matching")

    print(f"\nWritten clean 2009 inputs to {OUT.resolve()}")
    for path in sorted(OUT.rglob("*.parquet")):
        print(f"  {path.relative_to(OUT)}")


if __name__ == "__main__":
    main()
