## Initialization
from __future__ import annotations

import re
import time
from io import StringIO
from pathlib import Path

import pandas as pd
import requests

BASE_URL = "https://portalanterior.ine.mx/documentos/RESELEC/esta2000/comp_test/reportes/distritales"
CACHE_DIR = Path("data/electoral_data_raw/raw_2000")
MAX_DISTRICT_PROBE = 40  # safety cap; CDMX/Distrito Federal has the most districts (~30)

# 2000 only carries casilla-level MR votes for these three races. RP for diputados/
# senadores is reported off the same underlying .dat votes (just rendered as a
# different .html report), so there is no separate RP dataset to fetch — same
# convention as DIP_MR_2012/SEN_MR_2012 (no DIP_RP_2012/SEN_RP_2012 either).
OFFICE_META = {
    "PRESIDENCIA_2000":  {"election_id": "PRE_2000",    "year": 2000, "election_type": "PRE", "chamber": None,       "seat_method": "direct", "total_seats": 1,   "term_years": 6, "folder": "presidente"},
    "DIPUTACIONES_2000": {"election_id": "DIP_MR_2000", "year": 2000, "election_type": "DIP", "chamber": "deputies", "seat_method": "fptp",   "total_seats": 300, "term_years": 3, "folder": "diputado"},
    "SENADURIAS_2000":   {"election_id": "SEN_MR_2000", "year": 2000, "election_type": "SEN", "chamber": "senate",   "seat_method": "fptp",   "total_seats": 96,  "term_years": 6, "folder": "senador"},
}

CANDIDATES_CSV_2000 = {
    "PRESIDENCIA_2000":  None,
    "DIPUTACIONES_2000": None,
    "SENADURIAS_2000":   None,
}

# Historical coalition composition for 2000 — "A. CAM." (Alianza por el
# Cambio, Fox) and "A. MEX." (Alianza por México, Cárdenas) were real
# multi-party coalitions; PCD/PARM/DSPPN ran solo with their own candidates
# (matching their separate columns in the source data, no change needed
# there). Everything else defaults to is_coalition=False/no members.
PARTY_META_2000 = {
    "A. CAM.": {"is_coalition": True, "members": ["PAN", "PVEM"]},
    "A. MEX.": {"is_coalition": True, "members": ["PRD", "PT", "CONVERGENCIA", "PSN", "PAS"]},
}

# casilla column in the raw .dat is free text ("BASICA 1", "CONTIGUA 1", ...);
# map its leading word to the same TIPO_CASILLA codes used in 2012/2024.
TIPO_CASILLA_CODES = {
    "BASICA":        "B",
    "CONTIGUA":      "C",
    "EXTRAORDINARIA": "E",
    "EXT.":          "E",
    "ESPECIAL":      "S",
}

# Columns that are identity/metadata, not party vote counts. Detected dynamically
# rather than hardcoded per-party because coalition names ("A. CAM.", "A. MEX.", ...)
# differ across presidente/diputado/senador files.
NON_PARTY_COLS = {
    "id_estado", "distrito", "seccion", "casilla",
    "tipo_casilla", "id_casilla", "tipo",  # "tipo" = MR/RP marker on diputado/senador files
    "cand_no_regis", "nulos", "total", "status",
}

# INEGI state ID → state name (same mapping used in csv_to_arrow_2012.py).
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

OUT = Path("data/electoral_data_clean/clean_2000")
OUT.mkdir(parents=True, exist_ok=True)


## Helper functions — fetch + cache

REQUEST_DELAY_SECONDS = 1.5   # politeness delay between live requests (cache hits skip this)
MAX_RETRIES_429 = 4
RETRY_BACKOFF_BASE_SECONDS = 20  # 20, 40, 80, 160s — this legacy server's rate limit window is long
MISSING_SUFFIX = ".missing"      # marks a confirmed 404 so reruns don't re-probe it live


def fetch_district_dat(office_folder: str, id_estado: int, district: int) -> str | None:
    """
    Fetch one state/district .dat file, caching to disk so reruns don't re-hit
    portalanterior.ine.mx. Returns None on a confirmed 404 ("no more districts").
    404s are cached too (as an empty <district>.dat.missing marker) — otherwise
    every rerun has to re-probe the state's district boundary live, which is
    exactly what triggers 429s on a quiet, already-cached rerun.
    A 429 (rate limited) is NOT a "no more districts" signal — back off and retry.
    """
    cache_path   = CACHE_DIR / office_folder / f"{id_estado:02d}" / f"{district:02d}.dat"
    missing_path = cache_path.with_suffix(cache_path.suffix + MISSING_SUFFIX)

    if cache_path.exists():
        return cache_path.read_text(encoding="latin-1")
    if missing_path.exists():
        return None

    url = f"{BASE_URL}/{id_estado:02d}/{office_folder}/{district:02d}.dat"

    for attempt in range(MAX_RETRIES_429 + 1):
        resp = requests.get(url, timeout=30)
        if resp.status_code == 404:
            missing_path.parent.mkdir(parents=True, exist_ok=True)
            missing_path.touch()
            return None
        if resp.status_code == 429:
            wait = RETRY_BACKOFF_BASE_SECONDS * (2 ** attempt)
            print(f"    ⏳ 429 rate limited on {url}, backing off {wait}s (attempt {attempt + 1}/{MAX_RETRIES_429})")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        resp.encoding = "latin-1"
        text = resp.text

        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(text, encoding="latin-1")
        time.sleep(REQUEST_DELAY_SECONDS)
        return text

    raise RuntimeError(f"Gave up on {url} after {MAX_RETRIES_429} retries (still 429)")


def discover_state_districts(office_folder: str, id_estado: int) -> list[tuple[int, str]]:
    """Probe district 01, 02, ... until a 404, capped at MAX_DISTRICT_PROBE."""
    found = []
    for district in range(1, MAX_DISTRICT_PROBE + 1):
        text = fetch_district_dat(office_folder, id_estado, district)
        if text is None:
            break
        found.append((district, text))
    return found


## Helper functions — parsing

def parse_dat(text: str, id_estado: int, district: int) -> pd.DataFrame:
    df = pd.read_csv(StringIO(text), sep=";")
    df.columns = df.columns.str.strip()
    df.columns = [re.sub(r"\s+", " ", c).strip() for c in df.columns]

    df = df.rename(columns={
        "id_estado": "ID_ESTADO_RAW",
        "distrito":  "DISTRITO_RAW",
        "seccion":   "SECCION",
        "casilla":   "CASILLA_RAW",
        "CAND. NO REGIS.": "CAND_NO_REGIS",
        "NULOS": "NULOS",
        "TOTAL": "TOTAL",
    })

    df["ID_ESTADO"] = id_estado
    # distrito is known from the URL; cross-check against the file's own column when present
    df["ID_DISTRITO"] = district

    tipo_id = df["CASILLA_RAW"].astype(str).str.strip().str.split(r"\s+", n=1, expand=True)
    df["TIPO_CASILLA"] = tipo_id[0].str.upper().map(TIPO_CASILLA_CODES).fillna("X")

    # A handful of states (e.g. 24, 29) have CONTIGUA labels missing their number
    # entirely ("CONTIGUA" instead of "CONTIGUA 2") — these are genuinely distinct
    # casillas (different vote totals), not duplicates. Without a real number they'd
    # all collapse to ID_CASILLA=0 and collide on casilla_id; assign each one a
    # disambiguating fallback number (90+) scoped to its own seccion+tipo group
    # instead of silently colliding.
    raw_num = pd.to_numeric(tipo_id[1] if 1 in tipo_id.columns else pd.Series(index=df.index, dtype=object), errors="coerce")
    df["ID_CASILLA"] = raw_num.fillna(0).astype(int)
    missing = raw_num.isna()
    if missing.any():
        fallback_group = df.loc[missing, "SECCION"].astype(str) + "_" + df.loc[missing, "TIPO_CASILLA"]
        df.loc[missing, "ID_CASILLA"] = 90 + fallback_group.groupby(fallback_group).cumcount()

    for col in ["SECCION", "CAND_NO_REGIS", "NULOS", "TOTAL"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    party_keys = [
        c for c in df.columns
        if c.lower() not in NON_PARTY_COLS
        and c not in {"ID_ESTADO_RAW", "DISTRITO_RAW", "CASILLA_RAW", "STATUS", "ID_ESTADO", "ID_DISTRITO", "TIPO_CASILLA", "ID_CASILLA"}
        and pd.to_numeric(df[c], errors="coerce").notna().any()
    ]
    for col in party_keys:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["NOMBRE_ESTADO"] = ESTADO_NOMBRES.get(id_estado)

    return df, party_keys


def load_office(office_folder: str) -> tuple[pd.DataFrame, list[str]]:
    frames = []
    party_keys = set()

    for id_estado in range(1, 33):
        districts = discover_state_districts(office_folder, id_estado)
        print(f"    Estado {id_estado:02d} ({ESTADO_NOMBRES[id_estado]}): {len(districts)} districts")
        for district, text in districts:
            df, keys = parse_dat(text, id_estado, district)
            frames.append(df)
            party_keys.update(keys)

    df_all = pd.concat(frames, ignore_index=True)
    party_keys = sorted(party_keys)

    # Some party columns only exist in a subset of states' files; fill the rest with 0
    for col in party_keys:
        if col not in df_all.columns:
            df_all[col] = 0
        df_all[col] = df_all[col].fillna(0)

    # diputado/senador files carry a "tipo" column (MR/RP) on casillas especiales:
    # transit voters away from their home district get an RP-only ballot (no MR
    # candidate to vote for) recorded as a second row for the same physical casilla.
    # It's one election with one combined vote total per casilla, not two — Mexico
    # uses a single ballot for both MR and RP seats (confirmed: voters cast one vote
    # that counts for both their district race and their party's national PR tally;
    # only casillas especiales transit voters get the restricted RP-only ballot).
    # Collapse those paired rows by summing votes instead of treating them as
    # separate elections or distinct casillas.
    if "tipo" in df_all.columns:
        df_all["__casilla_id"] = make_casilla_id(df_all)
        dupe_count = df_all["__casilla_id"].duplicated().sum()
        if dupe_count:
            print(f"    Combining {dupe_count} MR+RP casilla-especial vote pairs into single totals")
            identity_cols = ["ID_ESTADO", "SECCION", "ID_DISTRITO", "TIPO_CASILLA", "ID_CASILLA", "NOMBRE_ESTADO"]
            sum_cols = party_keys + ["CAND_NO_REGIS", "NULOS", "TOTAL"]
            agg = {c: "sum" for c in sum_cols}
            agg.update({c: "first" for c in identity_cols})
            df_all = df_all.groupby("__casilla_id", as_index=False).agg(agg)
        df_all = df_all.drop(columns=["__casilla_id"])

    return df_all, party_keys


## Helper functions — star schema (mirrors csv_to_arrow_2012.py)

def make_geo_id(df: pd.DataFrame) -> pd.Series:
    return (
        df["ID_ESTADO"].astype(int).astype(str).str.zfill(2)
        + "_"
        + df["SECCION"].astype(int).astype(str).str.zfill(4)
    )


def make_casilla_id(df: pd.DataFrame) -> pd.Series:
    """Synthesised key: {ID_ESTADO 2d}_{SECCION 4d}_{TIPO_CASILLA}{ID_CASILLA 2d}"""
    return (
        df["ID_ESTADO"].astype(int).astype(str).str.zfill(2)
        + "_"
        + df["SECCION"].astype(int).astype(str).str.zfill(4)
        + "_"
        + df["TIPO_CASILLA"].astype(str)
        + df["ID_CASILLA"].astype(int).astype(str).str.zfill(2)
    )


def build_dim_election(meta: dict) -> pd.DataFrame:
    keep = {k: v for k, v in meta.items() if k != "folder"}
    return pd.DataFrame([keep])


def build_dim_geography(df: pd.DataFrame) -> pd.DataFrame:
    GEO_COLS = ["ID_ESTADO", "NOMBRE_ESTADO", "SECCION", "ID_DISTRITO"]
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
    CASILLA_COLS = ["ID_ESTADO", "SECCION", "TIPO_CASILLA", "ID_CASILLA"]
    out = df[CASILLA_COLS].copy().reset_index(drop=True)
    out["casilla_id"]  = make_casilla_id(df)
    out["election_id"] = election_id
    out["geo_id"]       = make_geo_id(df)
    out = out.drop_duplicates(subset=["election_id", "casilla_id"]).reset_index(drop=True)
    front = ["casilla_id", "election_id", "geo_id"]
    return out[front + [c for c in out.columns if c not in front]]


def build_dim_party(party_keys: list) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "party_key":    key,
            "is_coalition": PARTY_META_2000.get(key, {"is_coalition": False})["is_coalition"],
            "members":      ",".join(PARTY_META_2000.get(key, {"members": []})["members"]),
        }
        for key in party_keys
    ])


def build_dim_candidatos(election_type: str) -> pd.DataFrame:
    csv_rel = CANDIDATES_CSV_2000.get(election_type)
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

    VOTE_META = ["CAND_NO_REGIS", "NULOS", "TOTAL"]
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


def sanity_check(fact: pd.DataFrame, dim_casilla: pd.DataFrame, dim_party: pd.DataFrame,
                  dim_geography: pd.DataFrame, election_id: str) -> None:
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

    print(f"\nOrphan casilla_ids : {len(orphan_casillas)}")
    print(f"Orphan party_keys  : {len(orphan_parties)}")
    print(f"Orphan geo_ids     : {len(orphan_geos)}")
    print(f"\ndim_geography      : {len(dim_geography):>10,} rows")
    print(f"dim_casilla        : {len(dim_casilla):>10,} rows")
    print(f"dim_party          : {len(dim_party):>10,} rows")
    print(f"fact_casilla_vote  : {len(fact):>10,} rows")


## Main loop — one pass per office
all_elections  = []
all_geography  = []
all_casillas   = []
all_parties    = []
all_candidatos = []
all_facts      = []

for election_type, meta in OFFICE_META.items():
    election_id = meta["election_id"]
    print(f"\nProcessing {election_type}...")

    df_raw, party_keys = load_office(meta["folder"])

    dim_election   = build_dim_election(meta)
    dim_geography  = build_dim_geography(df_raw)
    dim_casilla    = build_dim_casilla(df_raw, election_id)
    dim_party      = build_dim_party(party_keys)
    dim_candidatos = build_dim_candidatos(election_type)
    fact           = build_fact(df_raw, party_keys, election_id)

    sanity_check(fact, dim_casilla, dim_party, dim_geography, election_id)

    all_elections.append(dim_election)
    all_geography.append(dim_geography)
    all_casillas.append(dim_casilla)
    all_parties.append(dim_party)
    all_candidatos.append(dim_candidatos)
    all_facts.append(fact)

print("\n✓ All 2000 elections processed")


## Concat + dedup across elections
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

# delete_matching clears each partition before writing. Without it pyarrow
# defaults to overwrite_or_ignore, which drops a second randomly-named copy
# of every row into the existing partition dir on each re-run.
fact_final.to_parquet(
    OUT / "fact_casilla_vote.parquet",
    index=False,
    partition_cols=["election_id"],
    existing_data_behavior="delete_matching",
)

print("\nWritten to", OUT.resolve())
for f in sorted(OUT.rglob("*.parquet")):
    print(f"  {f.relative_to(OUT)}")
