## Initialization
import re
import pandas as pd
from pathlib import Path

RAW_DIR = Path("data/electoral_data_raw/raw_eleccion_judicial_2025")
OUT = Path("data/electoral_data_clean/clean_eleccion_judicial_2025")
OUT.mkdir(parents=True, exist_ok=True)

# Every race's CSV has the same 7-line preamble before the per-casilla table:
#   1 title, 2 date, 3 blank, 4 summary header, 5 summary values, 6 blank,
#   7 per-casilla header. Confirmed identical across all six files.
CASILLA_HEADER_SKIPROWS = 6

CAND_COL_RE = re.compile(r"^CAND\d+$")

# Each race ships as two CSVs inside its own timestamped folder:
#   <PREFIX>_2025.csv              -- casilla-level vote counts
#   <PREFIX>_CANDIDATURAS_2025.csv -- candidate roster
#
# scope_cols pins down what makes a CANDxx column's identity unique.
# NO_CANDIDATO ("CAND01", "CAND02", ...) is NOT a globally stable label: for
# TCCA/JD it resets to CAND01 at the start of every one of the ~60+ separate
# judicial-district races sharing this file. Confirmed directly against
# JUZ_CANDIDATURAS_2025.csv -- CAND01 in circuito 1/distrito 1 is a different
# named person than CAND01 in circuito 1/distrito 2. scope_cols is exactly
# the set of columns (present on both the votes rows and the candidaturas
# rows) needed to disambiguate NO_CANDIDATO into one real identity.
#
# term_years reflects the one-off transition rule for this specific 2025
# cohort, not the reform's steady-state term: circuit magistrates/district
# judges normally serve 9-year terms going forward, but the 2025-elected
# cohort serves only 8 (through 2033). TDJ and TEPJF terms were both cut
# from 9 to 6 years by the same reform, with no transition exception noted
# for either. SCJN ministers serve 12 years (down from 15).
RACE_META = {
    "SCJN": {
        "folder_suffix": "SCJN", "prefix": "MIN",
        "election_id": "MIN_2025", "seat_method": "national_at_large",
        "total_seats": 9, "term_years": 12, "scope_cols": [],
    },
    "TDJ": {
        "folder_suffix": "TDJ", "prefix": "MAG_TDJ",
        "election_id": "TDJ_2025", "seat_method": "national_at_large",
        "total_seats": 5, "term_years": 6, "scope_cols": [],
    },
    "TEPJF_SS": {
        "folder_suffix": "TEPJF_SS", "prefix": "MAG_SS",
        "election_id": "TEPJF_SS_2025", "seat_method": "national_at_large",
        "total_seats": 2, "term_years": 6, "scope_cols": [],
    },
    "TEPJF_SR": {
        "folder_suffix": "TEPJF_SR", "prefix": "MAG_SR",
        "election_id": "TEPJF_SR_2025", "seat_method": "regional_at_large",
        "total_seats": 15, "term_years": 6, "scope_cols": ["CIRCUNSCRIPCION"],
    },
    # ID_ENTIDAD is deliberately NOT part of scope_cols here. A judicial
    # district can straddle a state line: circuito 10/distrito 1's candidate
    # roster is registered under entidad 27, but its casillas in the votes
    # file come from both entidad 27 and entidad 30. (circuito, distrito)
    # alone is confirmed unique in both candidaturas files (no district
    # number is reused with a different registered entidad), so it's the
    # correct join key; ID_ENTIDAD is kept as informational metadata only.
    "TCCA": {
        "folder_suffix": "TCCA", "prefix": "MAG_TC",
        "election_id": "TCCA_2025", "seat_method": "multi_winner_district",
        "total_seats": 464, "term_years": 8,
        "scope_cols": ["CIRCUITO_JUDICIAL", "DISTRITO_JUDICIAL_ELECTORAL"],
    },
    "JD": {
        "folder_suffix": "JD", "prefix": "JUZ",
        "election_id": "JD_2025", "seat_method": "multi_winner_district",
        "total_seats": 386, "term_years": 8,
        "scope_cols": ["CIRCUITO_JUDICIAL", "DISTRITO_JUDICIAL_ELECTORAL"],
    },
}

VOTE_META = [
    "VOTOS_NULOS", "RECUADROS_NO_UTILIZADOS", "TOTAL_VOTOS_CASILLA",
    "NUMERO_PERSONAS_VOTARON", "LISTA_NOMINAL_CASILLA",
]
## Helper functions
def find_race_folder(suffix: str) -> Path:
    matches = list(RAW_DIR.glob(f"*_{suffix}"))
    if not matches:
        raise FileNotFoundError(f"No folder ending in _{suffix} under {RAW_DIR}")
    return matches[0]


def read_summary(votes_path: Path) -> dict:
    """Parse the official header block (lines 4-5): expected/counted actas,
    turnout, total votes -- as published, not reconstructed from casillas."""
    with open(votes_path, encoding="utf-8-sig") as f:
        lines = [f.readline() for _ in range(5)]
    header = [h.strip() for h in lines[3].strip().split(",")]
    values = [v.strip() for v in lines[4].strip().split(",")]
    raw = dict(zip(header, values))
    return {
        "actas_esperadas": int(raw["ACTAS_ESPERADAS"]),
        "actas_computadas": int(raw["ACTAS_COMPUTADAS"]),
        "pct_actas_computadas": float(raw["PORCENTAJE_ACTAS_COMPUTADAS"]),
        "lista_nominal_actas_computadas": int(raw["LISTA_NOMINAL_ACTAS_COMPUTADAS"]),
        "total_personas_votaron": int(raw["TOTAL_PERSONAS_VOTARON"]),
        "pct_participacion_ciudadana": float(raw["PORCENTAJE_PARTICIPACION_CIUDADANA"]),
        "total_votos": int(raw["TOTAL_VOTOS"]),
    }


def load_votes(votes_path: Path) -> pd.DataFrame:
    df = pd.read_csv(votes_path, skiprows=CASILLA_HEADER_SKIPROWS, encoding="utf-8-sig", low_memory=False)
    df.columns = df.columns.str.strip()
    return df


def load_candidaturas(cand_path: Path) -> pd.DataFrame:
    df = pd.read_csv(cand_path, encoding="utf-8-sig", low_memory=False)
    df.columns = df.columns.str.strip()
    for col in ("NOMBRE_CANDIDATO", "GENERO", "PODER_POSTULANTE", "ESTATUS_CANCELADO", "MATERIA"):
        if col in df.columns:
            df[col] = df[col].str.strip()
    return df


def find_cand_cols(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if CAND_COL_RE.match(c)]


def make_geo_id(df: pd.DataFrame) -> pd.Series:
    return (
        df["ID_ENTIDAD"].astype(int).astype(str).str.zfill(2)
        + "_"
        + df["SECCION"].astype(int).astype(str).str.zfill(4)
    )


def make_casilla_id(df: pd.DataFrame) -> pd.Series:
    """CLAVE_CASILLA is NOT a reliable unique key on its own: "voto
    anticipado" rows (TIPO_CASILLA_SECCIONAL == 'A', one per federal
    electoral district, SECCION=0) all share the generic label "A1" across
    every entidad/distrito -- confirmed 292 such collisions in the SCJN
    file alone, one row per (entidad, distrito federal) pair. This synthetic
    key is confirmed unique across all six race files (0 collisions)."""
    return (
        df["ID_ENTIDAD"].astype(int).astype(str).str.zfill(2)
        + "_"
        + df["ID_DISTRITO_FEDERAL"].astype(int).astype(str).str.zfill(2)
        + "_"
        + df["SECCION"].astype(int).astype(str).str.zfill(4)
        + "_"
        + df["ID_CASILLA"].astype(int).astype(str)
        + "_"
        + df["TIPO_CASILLA_SECCIONAL"].astype(str).str.strip()
    )


def make_candidato_id(df: pd.DataFrame, election_id: str, scope_cols: list[str], no_candidato: pd.Series) -> pd.Series:
    """Reconstructs the same key on both the dim and fact side straight from
    each row's own columns -- no merge needed, mirrors how fact_casilla_vote
    uses party_key as a ready-made FK rather than joining dim_party in."""
    key = pd.Series(election_id, index=df.index)
    for col in scope_cols:
        key = key.str.cat(df[col].astype("Int64").astype(str), sep="|")
    return key.str.cat(no_candidato, sep="|")


def build_dim_election(meta: dict, summary: dict) -> pd.DataFrame:
    row = {
        "election_id": meta["election_id"],
        "year": 2025,
        "election_type": "JUD",
        "chamber": None,
        "seat_method": meta["seat_method"],
        "total_seats": meta["total_seats"],
        "term_years": meta["term_years"],
    }
    row.update(summary)
    return pd.DataFrame([row])


def build_dim_geography(df: pd.DataFrame, election_id: str) -> pd.DataFrame:
    cols = [
        "ID_ENTIDAD", "ENTIDAD", "SECCION",
        "ID_DISTRITO_FEDERAL", "DISTRITO_FEDERAL",
        "CIRCUITO_JUDICIAL", "DISTRITO_JUDICIAL_ELECTORAL",
    ]
    if "CIRCUNSCRIPCION" in df.columns:
        cols.append("CIRCUNSCRIPCION")

    out = (
        df[cols]
        .drop_duplicates()
        .dropna(subset=["ID_ENTIDAD", "SECCION"])
        .sort_values(["ID_ENTIDAD", "SECCION"])
        .reset_index(drop=True)
    )
    out["geo_id"] = make_geo_id(out)
    out["election_id"] = election_id
    return out[["geo_id", "election_id"] + cols]


def build_dim_casilla(df: pd.DataFrame, election_id: str) -> pd.DataFrame:
    cols = [
        "CLAVE_CASILLA", "ID_ENTIDAD", "ID_DISTRITO_FEDERAL", "SECCION", "ID_CASILLA",
        "TIPO_CASILLA_SECCIONAL", "LISTA_NOMINAL_CASILLA",
        "ESTATUS_CASILLA", "OBSERVACIONES", "SHA", "FECHA_HORA",
    ]
    out = df[cols].copy().reset_index(drop=True)
    out["election_id"] = election_id
    out["casilla_id"] = make_casilla_id(out)
    out = out.drop_duplicates(subset=["casilla_id"])
    out["geo_id"] = make_geo_id(out)
    ordered = ["casilla_id", "election_id", "geo_id"] + [c for c in out.columns if c not in ("casilla_id", "election_id", "geo_id")]
    return out[ordered]


def build_dim_candidato(df_cand: pd.DataFrame, election_id: str, scope_cols: list[str]) -> pd.DataFrame:
    out = df_cand.copy()
    out["election_id"] = election_id
    out["candidato_id"] = make_candidato_id(out, election_id, scope_cols, out["NO_CANDIDATO"])

    # ID_ENTIDAD is kept as informational metadata even when it isn't part of
    # scope_cols (TCCA/JD) -- see the RACE_META note on why it's excluded
    # from the join key there.
    info_cols = [c for c in ("ID_ENTIDAD",) if c in out.columns and c not in scope_cols]
    keep = ["candidato_id", "election_id"] + scope_cols + info_cols + [
        "ID_CANDIDATO", "NO_CANDIDATO", "NOMBRE_CANDIDATO", "GENERO",
    ]
    if "MATERIA" in out.columns:
        keep.append("MATERIA")
    keep += ["PODER_POSTULANTE", "ESTATUS_CANCELADO"]
    return out[keep]


def build_fact(df: pd.DataFrame, cand_cols: list[str], election_id: str, scope_cols: list[str]) -> pd.DataFrame:
    df = df.copy()
    for col in cand_cols + VOTE_META:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["casilla_id"] = make_casilla_id(df)

    id_vars = ["casilla_id"] + scope_cols + VOTE_META
    melted = df[id_vars + cand_cols].melt(
        id_vars=id_vars, value_vars=cand_cols,
        var_name="no_candidato", value_name="votes",
    )
    # "-" (coerced to NaN above) means this candidate slot doesn't exist for
    # this casilla's local race -- drop it rather than counting it as 0
    # votes for a person who wasn't on that ballot.
    melted = melted.dropna(subset=["votes"]).copy()
    melted["votes"] = melted["votes"].astype(int)
    melted["election_id"] = election_id
    melted["candidato_id"] = make_candidato_id(melted, election_id, scope_cols, melted["no_candidato"])

    cols = ["election_id", "casilla_id", "candidato_id", "no_candidato", "votes"] + VOTE_META
    return melted[cols]


def sanity_check(tag: str, df_votes: pd.DataFrame, fact: pd.DataFrame, dim_casilla: pd.DataFrame, dim_cand: pd.DataFrame):
    print(f"\n{'─'*55}")
    print(f"  {tag}")
    print(f"{'─'*55}")

    orphan_casillas   = set(fact["casilla_id"])   - set(dim_casilla["casilla_id"])
    orphan_candidatos = set(fact["candidato_id"]) - set(dim_cand["candidato_id"])

    per_casilla = fact.groupby("casilla_id")["votes"].sum().reset_index(name="cand_votes_sum")
    check = df_votes[["ID_ENTIDAD", "ID_DISTRITO_FEDERAL", "SECCION", "ID_CASILLA", "TIPO_CASILLA_SECCIONAL",
                       "VOTOS_NULOS", "RECUADROS_NO_UTILIZADOS", "TOTAL_VOTOS_CASILLA"]].copy()
    check["casilla_id"] = make_casilla_id(check)
    for c in ("VOTOS_NULOS", "RECUADROS_NO_UTILIZADOS", "TOTAL_VOTOS_CASILLA"):
        check[c] = pd.to_numeric(check[c], errors="coerce")
    check = check.merge(per_casilla, on="casilla_id", how="left")
    check["cand_votes_sum"] = check["cand_votes_sum"].fillna(0)
    check["_expected"] = check["cand_votes_sum"] + check["VOTOS_NULOS"].fillna(0) + check["RECUADROS_NO_UTILIZADOS"].fillna(0)
    mismatches = (check["_expected"] != check["TOTAL_VOTOS_CASILLA"]).sum()
    uncomputed = check["TOTAL_VOTOS_CASILLA"].isna().sum()

    print(f"Orphan casilla_ids   : {len(orphan_casillas)}")
    print(f"Orphan candidato_ids : {len(orphan_candidatos)}")
    print(f"TOTAL_VOTOS_CASILLA mismatches (excl. uncomputed): {mismatches - uncomputed}")
    print(f"Uncomputed casillas (no TOTAL_VOTOS_CASILLA)      : {uncomputed}")
    print(f"dim_casilla  : {len(dim_casilla):>10,} rows")
    print(f"dim_cand     : {len(dim_cand):>10,} rows")
    print(f"fact         : {len(fact):>10,} rows")
## Main loop — one pass per race
all_elections  = []
all_geography  = []
all_casillas   = []
all_candidatos = []
all_facts      = []

for tag, meta in RACE_META.items():
    election_id = meta["election_id"]
    scope_cols = meta["scope_cols"]
    print(f"\nProcessing {tag} ({election_id})...")

    folder = find_race_folder(meta["folder_suffix"])
    votes_path = folder / f"{meta['prefix']}_2025.csv"
    cand_path = folder / f"{meta['prefix']}_CANDIDATURAS_2025.csv"
    print(f"  {votes_path}")
    print(f"  {cand_path}")

    summary = read_summary(votes_path)
    df_votes = load_votes(votes_path)
    df_cand = load_candidaturas(cand_path)
    cand_cols = find_cand_cols(df_votes)
    print(f"  {len(cand_cols)} candidate columns, {len(df_votes):,} casilla rows, {len(df_cand):,} candidatos")

    dim_election = build_dim_election(meta, summary)
    dim_geo      = build_dim_geography(df_votes, election_id)
    dim_casilla  = build_dim_casilla(df_votes, election_id)
    dim_cand     = build_dim_candidato(df_cand, election_id, scope_cols)
    fact         = build_fact(df_votes, cand_cols, election_id, scope_cols)

    sanity_check(tag, df_votes, fact, dim_casilla, dim_cand)

    all_elections.append(dim_election)
    all_geography.append(dim_geo)
    all_casillas.append(dim_casilla)
    all_candidatos.append(dim_cand)
    all_facts.append(fact)

print("\n✓ All races processed")
## Concat across races — each race's election_id keeps rows disjoint, so
## only within-race duplicates (e.g. a repeated candidatura row) get merged.
dim_election_final = pd.concat(all_elections, ignore_index=True)

dim_geography_final = (
    pd.concat(all_geography, ignore_index=True)
    .drop_duplicates(subset=["geo_id", "election_id"])
    .reset_index(drop=True)
)

dim_casilla_final = pd.concat(all_casillas, ignore_index=True)

dim_candidato_final = (
    pd.concat(all_candidatos, ignore_index=True)
    .drop_duplicates(subset=["candidato_id"])
    .reset_index(drop=True)
)

fact_final = pd.concat(all_facts, ignore_index=True)

print(f"dim_election          : {len(dim_election_final):>10,} rows")
print(f"dim_geography          : {len(dim_geography_final):>10,} rows")
print(f"dim_casilla             : {len(dim_casilla_final):>10,} rows")
print(f"dim_candidato_judicial : {len(dim_candidato_final):>10,} rows")
print(f"fact_judicial_casilla_vote : {len(fact_final):>10,} rows")
## Write out
dim_election_final.to_parquet(OUT / "dim_election.parquet", index=False)
dim_geography_final.to_parquet(OUT / "dim_geography.parquet", index=False)
dim_casilla_final.to_parquet(OUT / "dim_casilla.parquet", index=False)
dim_candidato_final.to_parquet(OUT / "dim_candidato_judicial.parquet", index=False)

fact_final.to_parquet(
    OUT / "fact_judicial_casilla_vote.parquet",
    index=False,
    partition_cols=["election_id"],
    existing_data_behavior="delete_matching",
)

print("\nWritten to", OUT.resolve())
for f in sorted(OUT.rglob("*.parquet")):
    print(f"  {f.relative_to(OUT)}")
