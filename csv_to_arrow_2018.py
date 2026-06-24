## Initialization
import pandas as pd
import numpy as np
from pathlib import Path

NON_PARTY_COLS = {
    "CLAVE_CASILLA", "CLAVE_ACTA", "ID_ESTADO", "NOMBRE_ESTADO", "ID_DISTRITO",
    "NOMBRE_DISTRITO", "SECCION", "ID_CASILLA", "TIPO_CASILLA", "EXT_CONTIGUA",
    "CASILLA", "NUM_ACTA_IMPRESO", "CNR", "VN", "TOTAL_VOTOS_CALCULADOS",
    "LISTA_NOMINAL_CASILLA", "OBSERVACIONES", "MECANISMOS_TRASLADO",
    "FECHA_HORA", "Unnamed: 42", "Unnamed: 43",
}

# Cycle-scoped: every party_key here is unique to 2018 and will be reconciled
# against 2024's PARTY_META later via a separate alias/equivalence step —
# NOT renamed in place, so 2018 facts stay historically accurate on their own.
PARTY_META_2018 = {
    "PAN":                  {"is_coalition": False, "members": []},
    "PRI":                  {"is_coalition": False, "members": []},
    "PRD":                  {"is_coalition": False, "members": []},
    "PVEM":                 {"is_coalition": False, "members": []},
    "PT":                   {"is_coalition": False, "members": []},
    "MOVIMIENTO CIUDADANO": {"is_coalition": False, "members": []},
    "NUEVA ALIANZA":        {"is_coalition": False, "members": []},
    "MORENA":               {"is_coalition": False, "members": []},
    "ENCUENTRO SOCIAL":     {"is_coalition": False, "members": []},
    "PAN_PRD_MC":           {"is_coalition": True,  "members": ["PAN", "PRD", "MOVIMIENTO CIUDADANO"]},
    "PAN_PRD":              {"is_coalition": True,  "members": ["PAN", "PRD"]},
    "PAN_MC":               {"is_coalition": True,  "members": ["PAN", "MOVIMIENTO CIUDADANO"]},
    "PRD_MC":               {"is_coalition": True,  "members": ["PRD", "MOVIMIENTO CIUDADANO"]},
    "PRI_PVEM_NA":          {"is_coalition": True,  "members": ["PRI", "PVEM", "NUEVA ALIANZA"]},
    "PRI_PVEM":             {"is_coalition": True,  "members": ["PRI", "PVEM"]},
    "PRI_NA":               {"is_coalition": True,  "members": ["PRI", "NUEVA ALIANZA"]},
    "PVEM_NA":              {"is_coalition": True,  "members": ["PVEM", "NUEVA ALIANZA"]},
    "PT_MORENA_PES":        {"is_coalition": True,  "members": ["PT", "MORENA", "ENCUENTRO SOCIAL"]},
    "PT_MORENA":            {"is_coalition": True,  "members": ["PT", "MORENA"]},
    "PT_PES":               {"is_coalition": True,  "members": ["PT", "ENCUENTRO SOCIAL"]},
    "MORENA_PES":           {"is_coalition": True,  "members": ["MORENA", "ENCUENTRO SOCIAL"]},
    "CAND_IND_01":          {"is_coalition": False, "members": []},
    "CAND_IND_02":          {"is_coalition": False, "members": []},
}

# Maps election type -> election metadata
# NOTE: 2018 files (as provided) do not split MR/RP into separate CSVs the
# way 2024 does -- diputaciones.csv and senadurias.csv appear to carry MR
# results only. Confirm before treating these as the full picture; if PR/RP
# seats were allocated from these same vote totals via a separate formula
# (rather than a separate ballot/column set), we may not need DIP_RP_2018 /
# SEN_RP_2018 rows here at all.
ELECTION_META_2018 = {
    "PRESIDENCIA_2018":   {"election_id": "PRE_2018",     "year": 2018, "election_type": "PRE", "chamber": None,       "seat_method": "direct", "total_seats": 1,   "term_years": 6},
    "DIPUTACIONES_2018":  {"election_id": "DIP_MR_2018",  "year": 2018, "election_type": "DIP", "chamber": "deputies", "seat_method": "fptp",   "total_seats": 300, "term_years": 3},
    "SENADURIAS_2018":    {"election_id": "SEN_MR_2018",  "year": 2018, "election_type": "SEN", "chamber": "senate",   "seat_method": "fptp",   "total_seats": 96,  "term_years": 6},
}

# Election type -> raw CSV path (hardcoded; 2018 has exactly one file per type,
# unlike 2024 which searches a folder for a "CAS"-named file)
RAW_CSV_PATHS = {
    "PRESIDENCIA_2018":  "data/20180708_2130_CW/20180708_2130_CW_presidencia/presidencia.csv",
    "DIPUTACIONES_2018": "data/20180708_2130_CW/20180708_2130_CW_diputaciones/diputaciones.csv",
    "SENADURIAS_2018":   "data/20180708_2130_CW/20180708_2130_CW_senadurias/senadurias.csv",
}

# Election type -> candidates/winners CSV path.
# No equivalent of 2024's INTEGRACION_CARGOS file has been identified for 2018
# yet -- build_dim_candidatos() will return an empty frame until one is mapped.
CANDIDATES_CSV_2018 = {
    "PRESIDENCIA_2018":  None,
    "DIPUTACIONES_2018": None,
    "SENADURIAS_2018":   None,
}

OUT = Path("data/clean_2018")
OUT.mkdir(parents=True, exist_ok=True)

## Helper functions
def load_raw(path: str) -> tuple[pd.DataFrame, list[str]]:
    """Load a 2018 pipe-delimited CSV, coerce numerics, detect party columns."""
    df = pd.read_csv(path, low_memory=False, encoding="latin-1", sep="|", skiprows=6)

    df.columns = (
        df.columns
          .str.replace("\ufeff", "", regex=False)
          .str.replace("ï»¿", "", regex=False)
          .str.strip()
    )

    # Drop fully-empty trailing artifact columns from the pipe-delimited format
    drop_cols = [c for c in df.columns if c.startswith("Unnamed:")]
    df = df.drop(columns=drop_cols)

    # CLAVE_CASILLA arrives wrapped in stray literal quote characters, e.g. '010000M0100'
    df["CLAVE_CASILLA"] = df["CLAVE_CASILLA"].astype(str).str.strip().str.strip("'")

    INT_COLS = [
        "ID_ESTADO", "ID_DISTRITO", "ID_CASILLA", "EXT_CONTIGUA",
        "LISTA_NOMINAL_CASILLA", "TOTAL_VOTOS_CALCULADOS", "CNR", "VN",
        "NUM_ACTA_IMPRESO",
    ]
    for col in INT_COLS:
        if col in df.columns:
            # Some metadata columns (e.g. NUM_ACTA_IMPRESO) use a literal '-'
            # as a placeholder for "not recorded" instead of being blank --
            # errors="coerce" turns that into NaN rather than leaving the
            # column as object dtype, which would break the later to_parquet
            # write (Arrow can't infer one type for a mixed int/'-' column).
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Candidate party/coalition columns: anything not in NON_PARTY_COLS.
    # These sometimes load as object dtype (stray quotes/whitespace/thousands
    # separators in the raw pipe-delimited file), so coerce to numeric BEFORE
    # filtering on dtype -- filtering first would silently drop every party
    # column and leave party_keys empty.
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
        print(f"    ⚠️  Columns dropped from party_keys (all-NaN after coercion): {sorted(dropped)}")

    return df, party_keys


def make_geo_id(df):
    return (
        df["ID_ESTADO"].astype(int).astype(str).str.zfill(2)
        + "_"
        + df["SECCION"].astype(int).astype(str).str.zfill(4)
    )


def make_casilla_id(df):
    # CLAVE_CASILLA is NOT a reliable unique key: special/itinerant casillas
    # (TIPO_CASILLA == 'S') can have multiple actas recorded against the same
    # CLAVE_CASILLA (e.g. the booth filled a second ballot box during the day).
    # In that case CLAVE_CASILLA collapses two genuinely different vote
    # records into one ID, which causes duplicate-key collisions downstream
    # in fact_casilla_vote (confirmed: ~48k collisions in DIP_MR_2018, ~46k in
    # SEN_MR_2018, 0 in PRE_2018 -- the closer/longer-ballot races apparently
    # needed supplemental actas more often).
    #
    # CLAVE_ACTA is the correct granularity: it's CLAVE_CASILLA plus a 2-digit
    # acta sequence suffix (e.g. '010009S0100' -> '010009S010007' /
    # '010009S010008' for the same physical casilla's two actas).
    return df["CLAVE_ACTA"].astype(str).str.strip()


def build_dim_election(meta: dict) -> pd.DataFrame:
    return pd.DataFrame([meta])


def build_dim_geography(df: pd.DataFrame) -> pd.DataFrame:
    # 2018 has no ID_MUNICIPIO / MUNICIPIO / CIRCUNSCRIPCION columns at all,
    # unlike 2024. Only state + district + section are available here.
    GEO_COLS = [
        "ID_ESTADO", "NOMBRE_ESTADO", "SECCION",
        "ID_DISTRITO", "NOMBRE_DISTRITO",
    ]
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
        "ID_ESTADO", "SECCION", "CLAVE_CASILLA", "CLAVE_ACTA",
        "TIPO_CASILLA", "ID_CASILLA", "EXT_CONTIGUA", "CASILLA",
        "NUM_ACTA_IMPRESO", "LISTA_NOMINAL_CASILLA", "OBSERVACIONES",
        "MECANISMOS_TRASLADO", "FECHA_HORA",
    ]
    cols = [c for c in CASILLA_COLS if c in df.columns]
    out = (
        df[cols]
        # Dedup on CLAVE_ACTA, not CLAVE_CASILLA -- see make_casilla_id() for
        # why CLAVE_CASILLA alone can refer to more than one real acta.
        .drop_duplicates(subset=["CLAVE_ACTA"])
        .copy()
        .reset_index(drop=True)
    )
    out["election_id"] = election_id
    out["casilla_id"]  = make_casilla_id(out)
    out["geo_id"]      = make_geo_id(out)
    out = out[["casilla_id", "election_id", "geo_id"] + [c for c in out.columns if c not in ("casilla_id", "election_id", "geo_id")]]

    # Defensive check: object-dtype columns that are NOT expected text fields
    # (CLAVE_CASILLA, CLAVE_ACTA, TIPO_CASILLA, OBSERVACIONES, MECANISMOS_TRASLADO,
    # FECHA_HORA, NOMBRE_DISTRITO etc. are fine as text) will break to_parquet
    # if they contain a mix of numbers and placeholder strings like '-'.
    EXPECTED_TEXT_COLS = {
        "casilla_id", "election_id", "geo_id", "CLAVE_CASILLA", "CLAVE_ACTA",
        "TIPO_CASILLA", "OBSERVACIONES", "MECANISMOS_TRASLADO", "FECHA_HORA",
    }
    suspect = [
        c for c in out.columns
        if out[c].dtype == object and c not in EXPECTED_TEXT_COLS
    ]
    if suspect:
        print(f"    ⚠️  Unexpected object-dtype columns in dim_casilla (may break to_parquet): {suspect}")

    return out


def build_dim_party(party_keys: list) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "party_key":    key,
            "is_coalition": PARTY_META_2018.get(key, {"is_coalition": False})["is_coalition"],
            "members":      ",".join(PARTY_META_2018.get(key, {"members": []})["members"]),
        }
        for key in party_keys
    ])


def build_dim_candidatos(election_type: str) -> pd.DataFrame:
    """
    Placeholder for 2018: no INTEGRACION_CARGOS-equivalent candidates CSV has
    been identified yet (see CANDIDATES_CSV_2018). Returns an empty frame so
    downstream concat/write steps don't break; revisit once a source is found.
    """
    csv_rel = CANDIDATES_CSV_2018.get(election_type)
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
    df = df.copy()
    df["casilla_id"] = make_casilla_id(df)

    # Defensive check: casilla_id must be unique per row of df_raw, or the
    # melt below will produce duplicate (casilla_id, party_key) pairs that
    # later violate fact_casilla_vote's UNIQUE constraint in SQLite. Confirmed
    # this can happen when CLAVE_ACTA itself isn't unique (shouldn't occur,
    # but checking explicitly is cheap and fails loudly here instead of in
    # pipeline.py's executemany).
    dupe_count = df["casilla_id"].duplicated().sum()
    if dupe_count > 0:
        raise ValueError(
            f"[{election_id}] casilla_id is not unique: {dupe_count:,} duplicate "
            f"rows found in df_raw after make_casilla_id(). Fix the key before "
            f"building fact_casilla_vote, or duplicate-key inserts will fail "
            f"downstream in pipeline.py."
        )

    VOTE_META = ["CNR", "VN", "TOTAL_VOTOS_CALCULADOS"]
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

    if dim_party.empty or "party_key" not in dim_party.columns:
        raise ValueError(
            f"[{election_id}] dim_party is empty — party_keys detected during "
            f"load_raw() was empty, so no party columns were found. Check "
            f"column dtypes in df_raw (party columns may have loaded as "
            f"object/string instead of numeric)."
        )

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

    # 2018 has no NUM_VOTOS_VALIDOS-equivalent split out; TOTAL_VOTOS_CALCULADOS
    # is the pre-calculated total, so we just confirm it's present and non-negative
    # rather than reconciling components the way 2024's sanity_check does.
    missing_totals = df_raw["TOTAL_VOTOS_CALCULADOS"].isna().sum()
    negative_totals = (df_raw["TOTAL_VOTOS_CALCULADOS"] < 0).sum()

    print(f"\nOrphan casilla_ids      : {len(orphan_casillas)}")
    print(f"Orphan party_keys       : {len(orphan_parties)}")
    print(f"Orphan geo_ids          : {len(orphan_geos)}")
    print(f"Missing TOTAL_VOTOS_CALCULADOS : {missing_totals:,}")
    print(f"Negative TOTAL_VOTOS_CALCULADOS: {negative_totals:,}")
    print(f"\ndim_geography      : {len(dim_geography):>10,} rows")
    print(f"dim_casilla        : {len(dim_casilla):>10,} rows")
    print(f"dim_party          : {len(dim_party):>10,} rows")
    print(f"fact_casilla_vote  : {len(fact):>10,} rows")

## Main loop — one pass per election type
all_elections  = []
all_geography  = []
all_casillas   = []
all_parties    = []
all_candidatos = []
all_facts      = []

for election_type, meta in ELECTION_META_2018.items():
    election_id = meta["election_id"]
    print(f"\nProcessing {election_type}...")

    csv_path = RAW_CSV_PATHS[election_type]
    print(f"  Reading: {csv_path}")

    df_raw, party_keys = load_raw(csv_path)

    dim_election   = build_dim_election(meta)
    dim_geography  = build_dim_geography(df_raw)
    dim_casilla    = build_dim_casilla(df_raw, election_id)
    dim_party      = build_dim_party(party_keys)
    dim_candidatos = build_dim_candidatos(election_type)
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

# Geography: sections appear in all 3 election types — deduplicate.
# NOTE: 2018 districts (ID_DISTRITO) are the same for federal deputies and
# president, but Senate is elected by state, not district -- if NOMBRE_DISTRITO
# or ID_DISTRITO differ across election types for the same geo_id, this dedup
# silently keeps whichever row appears first. Worth double-checking once we
# review this section.
dim_geography_final = (
    pd.concat(all_geography, ignore_index=True)
    .drop_duplicates(subset=["geo_id"])
    .sort_values(["ID_ESTADO", "SECCION"])
    .reset_index(drop=True)
)

# Casillas: scoped by election_id so no cross-election-type collision
dim_casilla_final = pd.concat(all_casillas, ignore_index=True)

# Parties: cycle-scoped (PARTY_META_2018) -- deduplicate across the 3 election
# types within 2018, but NOT reconciled against 2024's dim_party. That
# equivalence/merge step happens later, outside this notebook.
dim_party_final = (
    pd.concat(all_parties, ignore_index=True)
    .drop_duplicates(subset=["party_key"])
    .reset_index(drop=True)
)

# Candidatos: empty until a 2018 candidates source is identified
all_candidatos_nonempty = [df for df in all_candidatos if not df.empty]
dim_candidatos_final = (
    pd.concat(all_candidatos_nonempty, ignore_index=True)
    if all_candidatos_nonempty
    else pd.DataFrame()
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

if not dim_candidatos_final.empty:
    dim_candidatos_final.to_parquet(OUT / "dim_candidatos.parquet", index=False)
else:
    print("  ⚠️  dim_candidatos is empty — skipping parquet write")

fact_final.to_parquet(
    OUT / "fact_casilla_vote.parquet",
    index=False,
    partition_cols=["election_id"],
)

print("Written to", OUT.resolve())
for f in sorted(OUT.rglob("*.parquet")):
    print(f"  {f.relative_to(OUT)}")
