"""
Shared constants, pure helpers, and data-shaping utilities.
No streamlit imports — safe to import from any module.
"""

import unicodedata
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

# ── Paths ──────────────────────────────────────────────────────────────────────

MATERIALIZED_DIR = Path("data/materialized")
TIMESERIES_PATH  = MATERIALIZED_DIR / "timeseries_estados.parquet"

# ── Election metadata ──────────────────────────────────────────────────────────

NON_PARTY_COLS = {
    "election_id", "casilla_id", "geo_id", "id_estado", "nombre_estado",
    "id_distrito_federal", "cabecera_distrital_federal", "id_municipio",
    "municipio", "seccion", "tipo_casilla", "id_casilla", "ext_contigua",
    "lista_nominal", "lista_nominal_part", "urna_electronica", "estatus_acta",
    "ruta_acta", "acta_casilla_mec", "num_votos_validos", "num_votos_can_nreg",
    "num_votos_nulos", "total_votos", "num_casillas", "num_secciones",
    "num_municipios", "num_estados", "num_distritos", "party_key", "votes",
}

TIPO_LABELS = {
    "B": "Basica", "C": "Contigua", "E": "Extraordinaria",
    "S": "Especial", "MEC": "Mesa de Escrutinio",
}

# 2024 presidential candidates (used for bar charts and ternary in PRE_2024)
CANDIDATES = {
    "CAND_SHH": {"label": "C. Sheinbaum (SHH)", "color": "#8B0000"},
    "CAND_FCM": {"label": "X. Galvez (FCM)",     "color": "#1E90FF"},
    "CAND_MC":  {"label": "J. Alvarez Maynez (MC)", "color": "#FF8C00"},
}

# party_key → candidate code mapping for dim_candidatos lookups
CANDIDATE_PARTY_KEY = {
    "SHH": "PVEM_PT_MORENA",
    "FCM": "PAN_PRI_PRD",
    "MC":  "MC",
}

PARTY_GROUPS = {
    "MORENA":         {"label": "Morena",         "color": "#8B0000", "cand": "CAND_SHH"},
    "PT":             {"label": "PT",             "color": "#8B0000", "cand": "CAND_SHH"},
    "PVEM":           {"label": "PVEM",           "color": "#8B0000", "cand": "CAND_SHH"},
    "PVEM_PT_MORENA": {"label": "PVEM+PT+Morena", "color": "#8B0000", "cand": "CAND_SHH"},
    "PVEM_PT":        {"label": "PVEM+PT",        "color": "#8B0000", "cand": "CAND_SHH"},
    "PVEM_MORENA":    {"label": "PVEM+Morena",    "color": "#8B0000", "cand": "CAND_SHH"},
    "PT_MORENA":      {"label": "PT+Morena",      "color": "#8B0000", "cand": "CAND_SHH"},
    "PAN":            {"label": "PAN",            "color": "#1E90FF", "cand": "CAND_FCM"},
    "PRI":            {"label": "PRI",            "color": "#1E90FF", "cand": "CAND_FCM"},
    "PRD":            {"label": "PRD",            "color": "#1E90FF", "cand": "CAND_FCM"},
    "PAN_PRI_PRD":    {"label": "PAN+PRI+PRD",   "color": "#1E90FF", "cand": "CAND_FCM"},
    "PAN_PRI":        {"label": "PAN+PRI",        "color": "#1E90FF", "cand": "CAND_FCM"},
    "PAN_PRD":        {"label": "PAN+PRD",        "color": "#1E90FF", "cand": "CAND_FCM"},
    "PRI_PRD":        {"label": "PRI+PRD",        "color": "#1E90FF", "cand": "CAND_FCM"},
    "MC":             {"label": "MC",             "color": "#FF8C00", "cand": "CAND_MC"},
}

# Ideological corners: A = Left, B = Right, C = Center/Establishment
# Consistent across all presidential cycles so ternary plots are comparable.
CYCLE_BLOCS: dict[str, dict] = {
    "PRE_1994": {
        "A": {"label": "Cárdenas — PRD",                   "color": "#FFCC00"},
        "B": {"label": "Fernández de Cevallos — PAN",      "color": "#003893"},
        "C": {"label": "Zedillo — PRI",                    "color": "#006847"},
        "map": {"PRD": "A", "PAN": "B", "PRI": "C"},
    },
    "PRE_2000": {
        "A": {"label": "Cárdenas — Alianza por México",    "color": "#FFCC00"},
        "B": {"label": "Fox — Alianza por el Cambio",      "color": "#003893"},
        "C": {"label": "Labastida — PRI",                  "color": "#006847"},
        "map": {"A. MEX.": "A", "A. CAM.": "B", "PRI": "C"},
    },
    "PRE_2006": {
        "A": {"label": "AMLO — Por el Bien de Todos",      "color": "#FFCC00"},
        "B": {"label": "Calderón — PAN",                   "color": "#003893"},
        "C": {"label": "Madrazo — Alianza por México",     "color": "#006847"},
        "map": {"PBT": "A", "PAN": "B", "APM": "C"},
    },
    "PRE_2012": {
        "A": {"label": "AMLO — PRD+PT+MC",                 "color": "#FFCC00"},
        "B": {"label": "Vázquez Mota — PAN",               "color": "#003893"},
        "C": {"label": "Peña Nieto — PRI+PVEM",            "color": "#006847"},
        "map": {
            "PRD": "A", "C_PRD_PT_MC": "A", "C_PRD_PT": "A",
            "C_PRD_MC": "A", "PT": "A", "MC": "A", "C_PT_MC": "A",
            "PAN": "B",
            "PRI": "C", "C_PRI_PVEM": "C", "PVEM": "C",
        },
    },
    "PRE_2018": {
        "A": {"label": "AMLO — Juntos Haremos Historia",   "color": "#8B0000"},
        "B": {"label": "Anaya — Por México al Frente",     "color": "#003893"},
        "C": {"label": "Meade — Todos por México",         "color": "#006847"},
        "map": {
            "MORENA": "A", "PT": "A", "ENCUENTRO SOCIAL": "A",
            "PT_MORENA": "A", "PT_MORENA_PES": "A", "MORENA_PES": "A",
            "PAN": "B", "PRD": "B", "MOVIMIENTO CIUDADANO": "B",
            "PAN_PRD_MC": "B", "PAN_PRD": "B", "PAN_MC": "B", "PRD_MC": "B",
            "PRI": "C", "PVEM": "C", "NUEVA ALIANZA": "C",
            "PRI_PVEM": "C", "PRI_NA": "C", "PRI_PVEM_NA": "C", "PVEM_NA": "C",
        },
    },
    "PRE_2024": {
        # PRI merged into FCM (B/right), so MC takes the "new center" vertex (C/top)
        "A": {"label": "Sheinbaum — Sigamos Haciendo Historia", "color": "#8B0000"},
        "B": {"label": "Gálvez — Fuerza y Corazón por México",  "color": "#1E90FF"},
        "C": {"label": "Máynez — Movimiento Ciudadano",          "color": "#FF8C00"},
        "map": {k: ("A" if v["cand"] == "CAND_SHH" else "B" if v["cand"] == "CAND_FCM" else "C")
                for k, v in PARTY_GROUPS.items()},
    },
}

# ── Ideology blocs (fixed across all PRE elections) ───────────────────────────
# L = Left (PRD/Morena tradition), R = Right (PAN tradition), C = Center (PRI/MC)
# Used by the municipio trajectory view to make ternaries comparable across years.
IDEOLOGY_MAP: dict[str, str] = {
    # 1994
    "PRD": "L", "PT": "L", "PFCRN": "L", "PPS": "L", "PARM": "L",
    "PAN": "R", "UNO_PDM": "R",
    "PRI": "C", "PVEM": "C",
    # 2000
    "A. MEX.": "L",    # Alianza por México = PRD+PT+Convergencia+PSN+PAS
    "A. CAM.": "R",    # Alianza por el Cambio = PAN+PVEM
    "PCD": "C", "DSPPN": "C",
    # 2006
    "PBT": "L",        # Por el Bien de Todos = PRD+PT+Convergencia
    "APM": "C",        # Alianza por México = PRI+PVEM+PANAL
    "ASDC": "L",
    "NVA_A": "C",
    # 2012
    "C_PRD_PT_MC": "L", "C_PRD_PT": "L", "C_PRD_MC": "L", "C_PT_MC": "L",
    "C_PRI_PVEM": "C", "MC": "C", "PANAL": "C",
    # 2018
    "MORENA": "L", "PT_MORENA": "L", "PT_MORENA_PES": "L", "MORENA_PES": "L",
    "PT_PES": "L",
    "ENCUENTRO SOCIAL": "R",
    "PAN_PRD_MC": "R", "PAN_PRD": "R", "PAN_MC": "R",
    "MOVIMIENTO CIUDADANO": "C", "PRD_MC": "C",
    "PRI_PVEM": "C", "PRI_NA": "C", "PRI_PVEM_NA": "C", "PVEM_NA": "C",
    "NUEVA ALIANZA": "C",
    "CAND_IND_01": "C", "CAND_IND_02": "C",
    # 2024
    "PVEM_MORENA": "L", "PVEM_PT": "L", "PVEM_PT_MORENA": "L",
    "PAN_PRI_PRD": "R", "PAN_PRI": "R", "PRI_PRD": "R",
}

MAP_METRICS = {
    "Ganador":       {"label": "Ganador (por municipio)",        "kind": "winner"},
    "% SHH":         {"label": "% Sheinbaum (SHH)",             "kind": "continuous", "col": "pct_shh",       "scale": [[0,"#fff0f0"],[0.5,"#8B0000"],[1,"#4a0000"]], "range": [20,90],  "cb_title": "SHH %",          "fmt": ":.1f"},
    "% FCM":         {"label": "% Galvez (FCM)",                "kind": "continuous", "col": "pct_fcm",       "scale": [[0,"#f0f6ff"],[0.5,"#1E90FF"],[1,"#0a3d7a"]], "range": [5,60],   "cb_title": "FCM %",          "fmt": ":.1f"},
    "% MC":          {"label": "% Alvarez Maynez (MC)",         "kind": "continuous", "col": "pct_mc",        "scale": [[0,"#fff8f0"],[0.5,"#FF8C00"],[1,"#7a3d00"]], "range": [0,30],   "cb_title": "MC %",           "fmt": ":.1f"},
    "Participación": {"label": "Participación electoral (%)",   "kind": "continuous", "col": "participacion", "scale": [[0,"#f5f5f0"],[0.5,"#4CAF50"],[1,"#1B5E20"]], "range": [30,90],  "cb_title": "Participación %","fmt": ":.1f"},
    "Votos totales": {"label": "Votos totales emitidos",        "kind": "continuous", "col": "total_votos",   "scale": [[0,"#fafaf5"],[0.5,"#9C27B0"],[1,"#4A148C"]], "range": None,     "cb_title": "Votos",          "fmt": ":,"},
    "Lista nominal": {"label": "Lista nominal (electores reg.)","kind": "continuous", "col": "lista_nominal", "scale": [[0,"#fafaf5"],[0.5,"#607D8B"],[1,"#1C313A"]], "range": None,     "cb_title": "Electores",      "fmt": ":,"},
}

# ── Ternary zone classification ─────────────────────────────────────────────────
# Classifies an L/R/C percentage triple into a "base" (one bloc holds an
# outright majority, so no two-way coalition of the other blocs can unseat
# it), "contenciosa" (no majority; the top two blocs are the live contest),
# or "empate" (no majority AND no clear pair leading — all three near
# 33/33/33) category.
#
# The base/majority cutoff is fixed at 50%, not an arbitrary margin: if a
# bloc doesn't clear half the vote, the other two could in principle
# cooperate and outvote it, so the result isn't "theirs." This avoids
# mislabeling near-even splits (e.g. 31/38/30) as a clean "base derecha" win.

CATEGORY_COLORS = {
    "Base Izquierda":                "#8B0000",
    "Base Derecha":                  "#1E90FF",
    "Base Centro":                   "#006847",
    "Contenciosa Izquierda-Centro":  "#CC7A00",
    "Contenciosa Izquierda-Derecha": "#7B2D8B",
    "Contenciosa Centro-Derecha":    "#1F8A8A",
    "Empate":                        "#AAAAAA",
}

_CONTENTIOUS_LABELS = {
    frozenset(("L", "C")): "Contenciosa Izquierda-Centro",
    frozenset(("L", "R")): "Contenciosa Izquierda-Derecha",
    frozenset(("C", "R")): "Contenciosa Centro-Derecha",
}
_BASE_LABELS = {"L": "Base Izquierda", "R": "Base Derecha", "C": "Base Centro"}


def classify_ternary(pct_l: float, pct_r: float, pct_c: float,
                      tie_radius: float = 8.0) -> str:
    """
    Classify a normalized L/R/C percentage triple (summing to ~100).
    A bloc only earns "Base" status with an outright majority (>50) — below
    that, the other two blocs combined can always match or beat it, so the
    result is contested. tie_radius: max deviation from 33.33 (on every
    axis) within the non-majority zone to call it a full three-way tie
    rather than a two-way contentious race.
    """
    vals = {"L": pct_l, "R": pct_r, "C": pct_c}
    ranked = sorted(vals.items(), key=lambda kv: kv[1], reverse=True)
    (top_k, top_v), (second_k, _), _ = ranked
    if top_v > 50:
        return _BASE_LABELS[top_k]
    if max(abs(v - 100 / 3) for v in vals.values()) <= tie_radius:
        return "Empate"
    return _CONTENTIOUS_LABELS[frozenset((top_k, second_k))]


# ── Timeseries constants ───────────────────────────────────────────────────────

TS_PARTY_COLORS: dict[str, str] = {
    "MORENA":           "#8B0000",
    "PAN":              "#1E90FF",
    "PRI":              "#006847",
    "PRD":              "#FFD700",
    "MC":               "#FF8C00",
    "PT":               "#CC0000",
    "PVEM":             "#4CAF50",
    "NUEVA ALIANZA":    "#00BCD4",
    "ENCUENTRO SOCIAL": "#E91E8C",
    "PVEM_PT_MORENA":   "#8B0000",
    "PT_MORENA":        "#A02020",
    "PT_MORENA_PES":    "#A83030",
    "PVEM_MORENA":      "#7A3030",
    "PVEM_PT":          "#6B8C50",
    "PT_PES":           "#B84040",
    "MORENA_PES":       "#922020",
    "PAN_PRI_PRD":      "#1E90FF",
    "PAN_PRI":          "#3A80C8",
    "PAN_PRD":          "#2878B8",
    "PAN_PRD_MC":       "#3070B0",
    "PAN_MC":           "#4488CC",
    "PRI_PRD":          "#2E7A60",
    "PRI_PVEM_NA":      "#1A6640",
    "PRI_PVEM":         "#1E7048",
    "PRI_NA":           "#226050",
    "PRD_MC":           "#C89000",
    "PVEM_NA":          "#5EA050",
}

TS_ELECTION_TYPE_LABELS = {"PRE": "Presidencia", "DIP": "Diputaciones", "SEN": "Senadurias"}
TS_CHART_HEIGHT = 340

# ── Pure helpers ───────────────────────────────────────────────────────────────

def _norm(s: str) -> str:
    s = str(s).upper().strip()
    s = unicodedata.normalize("NFD", s)
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def fmt_pct(v) -> str:
    return f"{v:.1f}%"


def fmt_num(v) -> str:
    return f"{int(v):,}"


def safe_int(v, default: int = 0) -> int:
    """int() that falls back to default instead of crashing on NaN/None."""
    return default if pd.isna(v) else int(v)


def election_label(election_id: str) -> str:
    parts = election_id.split("_")
    year  = parts[-1] if parts and parts[-1].isdigit() else ""
    kind  = "_".join(parts[:-1]) if year else election_id
    labels = {
        "PRE":    "Presidencial",
        "DIP_MR": "Diputaciones federales · mayoría relativa",
        "DIP_RP": "Diputaciones federales · representación proporcional",
        "SEN_MR": "Senadurías · mayoría relativa",
        "SEN_RP": "Senadurías · representación proporcional",
    }
    base = labels.get(kind, kind.replace("_", " ").title())
    return f"{base} {year}".strip()


def plotly_base() -> dict:
    return dict(
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        font_family="IBM Plex Sans", title_font_family="IBM Plex Mono",
        font_color="#888888",
        xaxis=dict(gridcolor="rgba(128,128,128,0.2)", zerolinecolor="rgba(128,128,128,0.3)"),
        yaxis=dict(gridcolor="rgba(128,128,128,0.2)", zerolinecolor="rgba(128,128,128,0.3)"),
    )


def pivot_candidates(df: pd.DataFrame) -> pd.Series:
    by_party = df.groupby("party_key")["votes"].sum()
    shh_keys = [k for k in PARTY_GROUPS if PARTY_GROUPS[k]["cand"] == "CAND_SHH"]
    fcm_keys = [k for k in PARTY_GROUPS if PARTY_GROUPS[k]["cand"] == "CAND_FCM"]
    mc_keys  = [k for k in PARTY_GROUPS if PARTY_GROUPS[k]["cand"] == "CAND_MC"]
    return pd.Series({
        "CAND_SHH": by_party.reindex(shh_keys, fill_value=0).sum(),
        "CAND_FCM": by_party.reindex(fcm_keys, fill_value=0).sum(),
        "CAND_MC":  by_party.reindex(mc_keys,  fill_value=0).sum(),
    })


def get_scalar(df: pd.DataFrame, col: str, agg: str = "sum"):
    if col not in df.columns:
        return 0
    return df[col].sum() if agg == "sum" else df[col].iloc[0]


def resolve_candidate_name(
    candidates_df: pd.DataFrame,
    party_key: str,
    election_id: str,
    id_distrito: Optional[int] = None,
    id_estado: Optional[int] = None,
) -> Optional[str]:
    """
    Return the candidate name for a given party_key + election, or None if not
    found. dim_candidatos is a WINNERS-ONLY table — it does not contain losing
    candidates, so callers must expect None for any party_key that didn't win.

    CRITICAL: id_distrito_federal is NOT globally unique — districts are
    numbered 1..N within each state, so id_estado and id_distrito must be
    supplied TOGETHER for district-level races. Presidential races need neither.
    """
    if candidates_df.empty:
        return None
    election_type = "_".join(election_id.split("_")[:-1])
    mask = (
        (candidates_df["election_type"] == election_type) &
        (candidates_df["party_key"]     == party_key)
    )
    sub = candidates_df[mask]
    if sub.empty:
        return None
    if (
        id_estado is not None and id_distrito is not None
        and "id_estado" in sub.columns and "id_distrito_federal" in sub.columns
    ):
        scoped = sub[
            (sub["id_estado"] == id_estado) &
            (sub["id_distrito_federal"] == id_distrito)
        ]
        if not scoped.empty:
            sub = scoped
        else:
            return None
    name = sub["candidate_name"].iloc[0]
    return name if pd.notna(name) and str(name).strip() else None


def agg_blocs(df: pd.DataFrame, group_cols, blocs: dict) -> pd.DataFrame:
    """Aggregate votes into 3 blocs (A/B/C) per geographic unit."""
    grp    = group_cols if isinstance(group_cols, list) else [group_cols]
    a_keys = [k for k, v in blocs["map"].items() if v == "A"]
    b_keys = [k for k, v in blocs["map"].items() if v == "B"]
    c_keys = [k for k, v in blocs["map"].items() if v == "C"]

    def _row(g):
        a  = g[g["party_key"].isin(a_keys)]["votes"].sum()
        b  = g[g["party_key"].isin(b_keys)]["votes"].sum()
        c  = g[g["party_key"].isin(c_keys)]["votes"].sum()
        tv = g["total_votos"].iloc[0] if "total_votos" in g.columns else 0
        jk = g["_join_key"].iloc[0]   if "_join_key"   in g.columns else ""
        mc = g["_mun_code"].iloc[0]   if "_mun_code"   in g.columns else ""
        return pd.Series({"bloc_A": a, "bloc_B": b, "bloc_C": c,
                          "total_votos": tv, "_join_key": jk, "_mun_code": mc})

    rows = []
    for keys, g in df.dropna(subset=grp).groupby(grp):
        row = _row(g).to_dict()
        key_vals = keys if isinstance(keys, tuple) else (keys,)
        row.update(dict(zip(grp, key_vals)))
        rows.append(row)
    if not rows:
        return pd.DataFrame(columns=grp + ["bloc_A", "bloc_B", "bloc_C",
                                            "total_votos", "_join_key", "_mun_code",
                                            "pct_A", "pct_B", "pct_C", "winner"])
    agg = pd.DataFrame(rows)
    total = (agg["bloc_A"] + agg["bloc_B"] + agg["bloc_C"]).replace(0, float("nan"))
    agg["pct_A"]  = (agg["bloc_A"] / total * 100).round(1)
    agg["pct_B"]  = (agg["bloc_B"] / total * 100).round(1)
    agg["pct_C"]  = (agg["bloc_C"] / total * 100).round(1)
    agg["winner"] = agg[["bloc_A","bloc_B","bloc_C"]].idxmax(axis=1).str.replace("bloc_", "")
    return agg.dropna(subset=["pct_A"])


# ── Timeseries helpers ─────────────────────────────────────────────────────────

def _ts_fallback_color(key: str) -> str:
    h = hash(key) % 360
    return f"hsl({h},55%,42%)"


def ts_party_color(key: str) -> str:
    return TS_PARTY_COLORS.get(key, _ts_fallback_color(key))


def ts_agg_for_plot(df: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    agg = (
        df
        .groupby(group_cols, as_index=False)
        .agg(
            votes_raw=("votes_raw", "sum"),
            votes_split=("votes_split", "sum"),
            total_votos_estado=("total_votos_estado", "sum"),
            lista_nominal=("lista_nominal", "sum"),
        )
    )
    denom         = agg["total_votos_estado"].replace(0, float("nan"))
    agg["pct_raw"]   = agg["votes_raw"]   / denom * 100
    agg["pct_split"] = agg["votes_split"] / denom * 100
    return agg


def ts_base_layout(title: str, y_label: str, years: list,
                   height: int = TS_CHART_HEIGHT) -> dict:
    return dict(
        title=dict(
            text=f"<b>{title}</b>",
            font=dict(family="IBM Plex Mono", size=14),
            x=0, xanchor="left",
        ),
        height=height,
        font=dict(family="IBM Plex Sans"),
        xaxis=dict(
            tickvals=years,
            ticktext=[str(y) for y in years],
            tickfont=dict(family="IBM Plex Mono", size=12),
        ),
        yaxis=dict(
            title=y_label,
            tickfont=dict(family="IBM Plex Mono", size=11),
        ),
        legend=dict(
            font=dict(family="IBM Plex Mono", size=10),
            orientation="h",
            yanchor="bottom", y=1.02,
            xanchor="left",   x=0,
        ),
        hovermode="x unified",
        margin=dict(l=55, r=20, t=70, b=40),
    )
