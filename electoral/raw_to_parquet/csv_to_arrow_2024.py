## Initialization
import pandas as pd
import numpy as np
from pathlib import Path

NON_PARTY_COLS = {
    "CIRCUNSCRIPCION", "ID_ESTADO", "NOMBRE_ESTADO", "ID_DISTRITO_FEDERAL",
    "CABECERA_DISTRITAL_FEDERAL", "ID_MUNICIPIO", "MUNICIPIO", "SECCION",
    "TIPO_CASILLA", "ID_CASILLA", "EXT_CONTIGUA", "ACTA_CASILLA-MEC",
    "URNA_ELECTRONICA", "NUM_VOTOS_VALIDOS", "NUM_VOTOS_CAN_NREG",
    "NUM_VOTOS_NULOS", "TOTAL_VOTOS", "LISTA_NOMINAL", "ESTATUS_ACTA",
    "TRIBUNAL", "RUTA_ACTA", "OBSERVACIONES",
}

PARTY_META = {
    "MORENA":         {"is_coalition": False, "members": []},
    "PT":             {"is_coalition": False, "members": []},
    "PVEM":           {"is_coalition": False, "members": []},
    "PAN":            {"is_coalition": False, "members": []},
    "PRI":            {"is_coalition": False, "members": []},
    "PRD":            {"is_coalition": False, "members": []},
    "MC":             {"is_coalition": False, "members": []},
    "PAN_PRI_PRD":    {"is_coalition": True,  "members": ["PAN", "PRI", "PRD"]},
    "PAN_PRI":        {"is_coalition": True,  "members": ["PAN", "PRI"]},
    "PAN_PRD":        {"is_coalition": True,  "members": ["PAN", "PRD"]},
    "PRI_PRD":        {"is_coalition": True,  "members": ["PRI", "PRD"]},
    "PVEM_PT_MORENA": {"is_coalition": True,  "members": ["PVEM", "PT", "MORENA"]},
    "PVEM_PT":        {"is_coalition": True,  "members": ["PVEM", "PT"]},
    "PVEM_MORENA":    {"is_coalition": True,  "members": ["PVEM", "MORENA"]},
    "PT_MORENA":      {"is_coalition": True,  "members": ["PT", "MORENA"]},
}

# Maps folder name → election metadata
# NOTE: there is no separate "RP election" — Mexico uses a single ballot for
# diputados/senadores; the same vote counts toward both the local MR seat and
# the party's national PR tally (confirmed: DIPUTACIONES_FED_RP_2024's CAS file
# is the *same* casilla-level votes as DIPUTACIONES_FED_MR_2024's, row-for-row
# identical, plus one extra block of TIPO_CASILLA=='SRP' rows — the transit-voter
# ballots cast at casillas especiales by people away from their home district,
# who can't vote for a specific MR candidate but still get a party-list vote).
# Modeling DIP_RP_2024/SEN_RP_2024 as separate elections double-counted ~170K
# casillas of real votes. Only the extra SRP rows get folded into MR below.
ELECTION_META = {
    "data/electoral_data_raw/raw_2024/PRESIDENCIA_2024":         {"election_id": "PRE_2024",     "year": 2024, "election_type": "PRE", "chamber": None,       "seat_method": "direct", "total_seats": 1,   "term_years": 6},
    "data/electoral_data_raw/raw_2024/DIPUTACIONES_FED_MR_2024": {"election_id": "DIP_MR_2024",  "year": 2024, "election_type": "DIP", "chamber": "deputies", "seat_method": "fptp",   "total_seats": 300, "term_years": 3},
    "data/electoral_data_raw/raw_2024/SENADURIAS_MR_2024":       {"election_id": "SEN_MR_2024",  "year": 2024, "election_type": "SEN", "chamber": "senate",   "seat_method": "fptp",   "total_seats": 96,  "term_years": 6},
}

# MR folder → sibling RP folder, used only to pull the extra SRP-only
# (transit-voter) rows that don't already exist in the MR file.
RP_SUPPLEMENT_FOLDER = {
    "data/electoral_data_raw/raw_2024/DIPUTACIONES_FED_MR_2024": "data/electoral_data_raw/raw_2024/DIPUTACIONES_FED_RP_2024",
    "data/electoral_data_raw/raw_2024/SENADURIAS_MR_2024":       "data/electoral_data_raw/raw_2024/SENADURIAS_RP_2024",
}

# Folder name → INTEGRACION CSV path
# Same physical file is referenced for every election folder — read once per
# folder for loop symmetry, drop_duplicates() at concat time collapses the repeats.
CANDIDATES_CSV = {
    "data/electoral_data_raw/raw_2024/PRESIDENCIA_2024":         "data/electoral_data_raw/raw_2024/PRESIDENCIA_2024/CSV/INTEGRACION_CARGOS_PEF_2024.csv",
    "data/electoral_data_raw/raw_2024/DIPUTACIONES_FED_MR_2024": "data/electoral_data_raw/raw_2024/PRESIDENCIA_2024/CSV/INTEGRACION_CARGOS_PEF_2024.csv",
    "data/electoral_data_raw/raw_2024/SENADURIAS_MR_2024":       "data/electoral_data_raw/raw_2024/PRESIDENCIA_2024/CSV/INTEGRACION_CARGOS_PEF_2024.csv",
}

OUT = Path("data/electoral_data_clean/clean_2024")
OUT.mkdir(parents=True, exist_ok=True)
## Helper functions
def find_cas_csv(folder: str) -> Path:
    """Find the CAS csv inside <folder>/CSV/"""
    csv_dir = Path(folder) / "CSV"
    matches = [f for f in csv_dir.iterdir() if "CAS" in f.name and f.suffix == ".csv"]
    if not matches:
        raise FileNotFoundError(f"No CAS csv found in {csv_dir}")
    return matches[0]


def load_raw(path: Path) -> tuple[pd.DataFrame, list[str]]:
    """Load csv, coerce numerics, detect party columns."""
    df = pd.read_csv(path, low_memory=False, encoding="latin-1")
    
    df.columns = (
    df.columns
      .str.replace("\ufeff", "", regex=False)
      .str.replace("ï»¿", "", regex=False)
      .str.strip()
    )

    INT_COLS = [
        "ID_ESTADO", "ID_DISTRITO_FEDERAL", "ID_MUNICIPIO",
        "SECCION", "ID_CASILLA", "EXT_CONTIGUA",
        "LISTA_NOMINAL", "TOTAL_VOTOS", "NUM_VOTOS_VALIDOS",
        "NUM_VOTOS_NULOS", "NUM_VOTOS_CAN_NREG", "URNA_ELECTRONICA",
    ]
    for col in INT_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    party_keys = [
        c for c in df.columns
        if c not in NON_PARTY_COLS
        and pd.api.types.is_numeric_dtype(df[c])
    ]
    return df, party_keys


def load_rp_supplement(folder: str, party_keys: list) -> pd.DataFrame:
    """
    Pull only the extra TIPO_CASILLA=='SRP' rows from this MR folder's sibling
    RP file — the transit-voter-only ballots that don't exist in the MR file.
    Everything else in the RP file is a row-for-row duplicate of MR and is
    discarded. Independent-candidate columns (CAND_IND1/2) don't exist in the
    RP file (independents can't appear on a PR-only ballot), so they're added
    back as 0 to align with the MR schema.
    """
    rp_folder = RP_SUPPLEMENT_FOLDER.get(folder)
    if rp_folder is None:
        return pd.DataFrame()

    rp_csv = find_cas_csv(rp_folder)
    df_rp, _ = load_raw(rp_csv)
    srp_rows = df_rp[df_rp["TIPO_CASILLA"] == "SRP"].copy()
    if srp_rows.empty:
        return srp_rows

    for col in party_keys:
        if col not in srp_rows.columns:
            srp_rows[col] = 0
    return srp_rows


def make_geo_id(df):
    return (
        df["ID_ESTADO"].astype(int).astype(str).str.zfill(2)
        + "_"
        + df["SECCION"].astype(int).astype(str).str.zfill(4)
    )

def make_casilla_id(df):
    return (
        df["ID_ESTADO"].astype(int).astype(str).str.zfill(2)
        + "_"
        + df["SECCION"].astype(int).astype(str).str.zfill(4)
        + "_"
        + df["ACTA_CASILLA-MEC"].astype(str).str.strip()
    )


def build_dim_election(meta: dict) -> pd.DataFrame:
    return pd.DataFrame([meta])


def build_dim_geography(df: pd.DataFrame) -> pd.DataFrame:
    GEO_COLS = [
        "ID_ESTADO", "NOMBRE_ESTADO", "SECCION",
        "ID_MUNICIPIO", "MUNICIPIO",
        "ID_DISTRITO_FEDERAL", "CABECERA_DISTRITAL_FEDERAL",
    ]
    if "CIRCUNSCRIPCION" in df.columns:
        GEO_COLS.append("CIRCUNSCRIPCION")

    out = (
        df[GEO_COLS]
        .drop_duplicates()
        .dropna(subset=["ID_ESTADO", "SECCION"])
        .sort_values(["ID_ESTADO", "SECCION"])
        .reset_index(drop=True)
    )
    out["geo_id"] = make_geo_id(out)
    return out[["geo_id"] + [c for c in out.columns if c != "geo_id"]]


def build_dim_casilla(df: pd.DataFrame, election_id: str) -> pd.DataFrame:
    CASILLA_COLS = [
        "ID_ESTADO", "SECCION", "ACTA_CASILLA-MEC",
        "TIPO_CASILLA", "ID_CASILLA", "EXT_CONTIGUA",
        "LISTA_NOMINAL", "URNA_ELECTRONICA", "ESTATUS_ACTA", "RUTA_ACTA",
    ]
    cols = [c for c in CASILLA_COLS if c in df.columns]
    out = (
        df[cols]
        .drop_duplicates(subset=["ID_ESTADO", "SECCION", "ACTA_CASILLA-MEC"])
        .copy()
        .reset_index(drop=True)
    )
    out["election_id"] = election_id
    out["casilla_id"]  = make_casilla_id(out)
    out["geo_id"]      = make_geo_id(out)
    return out[["casilla_id", "election_id", "geo_id"] + [c for c in out.columns if c not in ("casilla_id", "election_id", "geo_id")]]


def build_dim_party(party_keys: list) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "party_key":    key,
            "is_coalition": PARTY_META.get(key, {"is_coalition": False})["is_coalition"],
            "members":      ",".join(PARTY_META.get(key, {"members": []})["members"]),
        }
        for key in party_keys
    ])


def build_dim_candidatos(folder: str) -> pd.DataFrame:
    """
    Read the INTEGRACION CSV referenced by this folder. The same physical file
    is referenced by every folder in CANDIDATES_CSV — drop_duplicates() at
    concat time collapses the repeats, so re-reading per folder is safe (just
    a bit redundant on I/O).

    Output columns: election_type, party_key, id_distrito_federal,
                     candidate_name, candidate_suplente, partido_politico,
                     votacion_ganador, pct_ganador
    """
    csv_rel = CANDIDATES_CSV.get(folder)
    if csv_rel is None:
        print(f"    ⚠️  No candidates CSV mapped for {folder}, skipping")
        return pd.DataFrame()

    csv_path = Path(csv_rel)
    if not csv_path.exists():
        print(f"    ⚠️  Not found: {csv_path}, skipping")
        return pd.DataFrame()

    df = pd.read_csv(csv_path, encoding="latin-1", low_memory=False)
    df.columns = df.columns.str.strip()
    rename = {
        "TIPO_DE_CANDIDATURA":         "election_type",
        "NOMBRE_ACTOR_POLITICO":       "party_key",
        "ID_DISTRITO_FEDERAL":         "id_distrito_federal",
        "PERSONA_CANDIDATA":           "candidate_name",
        "PERSONA_CANDIDATA_SUPLENTE":  "candidate_suplente",
        "PARTIDO_POLITICO":            "partido_politico",
        "VOTACION_GANADOR":            "votacion_ganador",
        "PORCENTAJE_VOTACION_GANADOR": "pct_ganador",
    }
    existing = {k: v for k, v in rename.items() if k in df.columns}
    out = df[list(existing)].rename(columns=existing).copy()
    out["party_key"] = out["party_key"].str.strip()
    return out


def build_fact(df: pd.DataFrame, party_keys: list, election_id: str) -> pd.DataFrame:
    df = df.copy()
    df["casilla_id"] = make_casilla_id(df)

    VOTE_META = ["NUM_VOTOS_VALIDOS", "NUM_VOTOS_NULOS", "NUM_VOTOS_CAN_NREG", "TOTAL_VOTOS"]
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


def sanity_check(df_raw, fact, dim_casilla, dim_party, dim_geography, election_id):
    print(f"\n{'─'*55}")
    print(f"  {election_id}")
    print(f"{'─'*55}")

    totals = (
        fact.groupby("party_key")["votes"]
        .sum()
        .sort_values(ascending=False)
        .reset_index()
    )
    print(totals.to_string(index=False))

    orphan_casillas = set(fact["casilla_id"]) - set(dim_casilla["casilla_id"])
    orphan_parties  = set(fact["party_key"])  - set(dim_party["party_key"])
    orphan_geos     = set(dim_casilla["geo_id"]) - set(dim_geography["geo_id"])

    check = df_raw.copy()
    check["_expected"] = (
        check["NUM_VOTOS_VALIDOS"] + check["NUM_VOTOS_NULOS"] + check["NUM_VOTOS_CAN_NREG"]
    )
    mismatches = (check["_expected"] != check["TOTAL_VOTOS"]).sum()

    print(f"\nOrphan casilla_ids : {len(orphan_casillas)}")
    print(f"Orphan party_keys  : {len(orphan_parties)}")
    print(f"Orphan geo_ids     : {len(orphan_geos)}")
    print(f"TOTAL_VOTOS errors : {mismatches:,}")
    print(f"\ndim_geography      : {len(dim_geography):>10,} rows")
    print(f"dim_casilla        : {len(dim_casilla):>10,} rows")
    print(f"dim_party          : {len(dim_party):>10,} rows")
    print(f"fact_casilla_vote  : {len(fact):>10,} rows")
## Main loop — one pass per election folder
all_elections  = []
all_geography  = []
all_casillas   = []
all_parties    = []
all_candidatos = []
all_facts      = []

for folder, meta in ELECTION_META.items():
    election_id = meta["election_id"]
    print(f"\nProcessing {folder}...")

    csv_path = find_cas_csv(folder)
    print(f"  Found: {csv_path}")

    df_raw, party_keys = load_raw(csv_path)

    rp_extra = load_rp_supplement(folder, party_keys)
    if not rp_extra.empty:
        print(f"  + {len(rp_extra)} transit-voter SRP rows merged in from sibling RP file")
        df_raw = pd.concat([df_raw, rp_extra], ignore_index=True)

    dim_election   = build_dim_election(meta)
    dim_geography  = build_dim_geography(df_raw)
    dim_casilla    = build_dim_casilla(df_raw, election_id)
    dim_party      = build_dim_party(party_keys)
    dim_candidatos = build_dim_candidatos(folder)
    fact           = build_fact(df_raw, party_keys, election_id)

    sanity_check(df_raw, fact, dim_casilla, dim_party, dim_geography, election_id)

    all_elections.append(dim_election)
    all_geography.append(dim_geography)
    all_casillas.append(dim_casilla)
    all_parties.append(dim_party)
    all_candidatos.append(dim_candidatos)
    all_facts.append(fact)

print("\n✓ All elections processed")
## Concat + dedup across elections
# Elections: one row per election, no dedup needed
dim_election_final = pd.concat(all_elections, ignore_index=True)

# Geography: same sections appear in all 5 elections — deduplicate
dim_geography_final = (
    pd.concat(all_geography, ignore_index=True)
    .drop_duplicates(subset=["geo_id"])
    .sort_values(["ID_ESTADO", "SECCION"])
    .reset_index(drop=True)
)

# Casillas: scoped by election_id so no cross-election collision
dim_casilla_final = pd.concat(all_casillas, ignore_index=True)

# Parties: deduplicate across elections (same parties appear in all)
dim_party_final = (
    pd.concat(all_parties, ignore_index=True)
    .drop_duplicates(subset=["party_key"])
    .reset_index(drop=True)
)

# Candidatos: same physical CSV referenced by every folder — deduplicate
dim_candidatos_final = (
    pd.concat(all_candidatos, ignore_index=True)
    .drop_duplicates(subset=["election_type", "party_key", "id_distrito_federal", "candidate_name"])
    .reset_index(drop=True)
)

# Facts: all rows, no dedup
fact_final = pd.concat(all_facts, ignore_index=True)

print(f"dim_election      : {len(dim_election_final):>10,} rows")
print(f"dim_geography     : {len(dim_geography_final):>10,} rows")
print(f"dim_casilla       : {len(dim_casilla_final):>10,} rows")
print(f"dim_party         : {len(dim_party_final):>10,} rows")
print(f"dim_candidatos    : {len(dim_candidatos_final):>10,} rows")
print(f"fact_casilla_vote : {len(fact_final):>10,} rows")
## Write out
dim_election_final.to_parquet(OUT / "dim_election.parquet",     index=False)
dim_geography_final.to_parquet(OUT / "dim_geography.parquet",   index=False)
dim_casilla_final.to_parquet(OUT / "dim_casilla.parquet",       index=False)
dim_party_final.to_parquet(OUT / "dim_party.parquet",           index=False)
dim_candidatos_final.to_parquet(OUT / "dim_candidatos.parquet", index=False)

# delete_matching clears each partition before writing. Without it pyarrow
# defaults to overwrite_or_ignore, which drops a second randomly-named copy
# of every row into the existing partition dir on each re-run.
fact_final.to_parquet(
    OUT / "fact_casilla_vote.parquet",
    index=False,
    partition_cols=["election_id"],
    existing_data_behavior="delete_matching",
)

print("Written to", OUT.resolve())
for f in sorted(OUT.rglob("*.parquet")):
    print(f"  {f.relative_to(OUT)}")