"""
INE PREP 2024 - Explorador de Resultados Electorales (online version)
Streamlit dashboard backed by materialized Parquet files.

Online version scope: Estado, Municipio. The Nacional page was removed --
rendering 2,500+ municipio polygons on a single map was too heavy for the
hosted deployment. The Estado page now opens with a multi-year timeseries
(by party) for the selected state, followed by the last-election results
(map / ternary / bar charts / tables) for that same state.

Run:
    python ingestion/pipeline.py              # clean parquets -> SQLite
    python ingestion/materialize.py           # SQLite -> all parquets (views + timeseries)
    streamlit run ine_explorer_streamlit_online.py

Dependencies:
    pip install streamlit pandas plotly pyarrow
"""

import json
import unicodedata
from typing import Optional
import numpy as np
import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from pathlib import Path

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="INE · Explorador Electoral",
    page_icon="🗳️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans:wght@300;400;600&display=swap');
html, body, [class*="css"] { font-family: 'IBM Plex Sans', sans-serif; }
h1, h2, h3 { font-family: 'IBM Plex Mono', monospace; letter-spacing: -0.02em; }
.metric-card {
    background: #F7F7F5;
    border-left: 4px solid #C84B31;
    padding: 1.4rem 1.6rem;
    border-radius: 2px;
    margin-bottom: 0.5rem;
}
.metric-card .label {
    font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.1em;
    color: #888; font-family: 'IBM Plex Mono', monospace; margin-bottom: 0.3rem;
}
.metric-card .value {
    font-size: 2.4rem; font-weight: 600; color: #1A1A1A;
    font-family: 'IBM Plex Mono', monospace; line-height: 1.1;
}
.metric-card .sub {
    font-size: 0.75rem; color: #666; font-family: 'IBM Plex Sans', sans-serif;
    margin-top: 0.2rem;
}
.tag {
    display: inline-block; background: #EAEAEA; color: #444;
    font-family: 'IBM Plex Mono', monospace; font-size: 0.72rem;
    padding: 2px 8px; border-radius: 2px; margin-right: 4px;
}
.section-label {
    font-family: 'IBM Plex Mono', monospace; font-size: 0.7rem;
    text-transform: uppercase; letter-spacing: 0.1em;
    color: #C84B31; margin-bottom: 0.3rem;
    margin-top: 1rem;
}
.panel-divider {
    border-left: 1px solid rgba(136, 136, 136, 0.28);
    height: 640px;
    margin: 2.3rem auto 0 auto;
    width: 1px;
}
</style>
""", unsafe_allow_html=True)

# ── Constants ──────────────────────────────────────────────────────────────────

MATERIALIZED_DIR = Path("data/materialized")

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

CANDIDATES = {
    "CAND_SHH": {"label": "C. Sheinbaum (SHH)", "color": "#8B0000"},
    "CAND_FCM": {"label": "X. Galvez (FCM)",     "color": "#1E90FF"},
    "CAND_MC":  {"label": "J. Alvarez Maynez (MC)", "color": "#FF8C00"},
}

# party_key used to look up the winner's name in dim_candidatos, per candidate code
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

# Consistent ideological corners across all presidential elections:
#   A (bottom-left) = Left  (PRD lineage → MORENA)
#   B (bottom-right) = Right (PAN lineage)
#   C (top)          = Center/Establishment (PRI historically;
#                      MC in 2024 after PRI absorbed into the B coalition)
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

MAP_METRICS = {
    "Ganador":       {"label": "Ganador (por municipio)",        "kind": "winner"},
    "% SHH":         {"label": "% Sheinbaum (SHH)",             "kind": "continuous", "col": "pct_shh",       "scale": [[0,"#fff0f0"],[0.5,"#8B0000"],[1,"#4a0000"]], "range": [20,90],  "cb_title": "SHH %",          "fmt": ":.1f"},
    "% FCM":         {"label": "% Galvez (FCM)",                "kind": "continuous", "col": "pct_fcm",       "scale": [[0,"#f0f6ff"],[0.5,"#1E90FF"],[1,"#0a3d7a"]], "range": [5,60],   "cb_title": "FCM %",          "fmt": ":.1f"},
    "% MC":          {"label": "% Alvarez Maynez (MC)",         "kind": "continuous", "col": "pct_mc",        "scale": [[0,"#fff8f0"],[0.5,"#FF8C00"],[1,"#7a3d00"]], "range": [0,30],   "cb_title": "MC %",           "fmt": ":.1f"},
    "Participación": {"label": "Participación electoral (%)",   "kind": "continuous", "col": "participacion", "scale": [[0,"#f5f5f0"],[0.5,"#4CAF50"],[1,"#1B5E20"]], "range": [30,90],  "cb_title": "Participación %","fmt": ":.1f"},
    "Votos totales": {"label": "Votos totales emitidos",        "kind": "continuous", "col": "total_votos",   "scale": [[0,"#fafaf5"],[0.5,"#9C27B0"],[1,"#4A148C"]], "range": None,     "cb_title": "Votos",          "fmt": ":,"},
    "Lista nominal": {"label": "Lista nominal (electores reg.)","kind": "continuous", "col": "lista_nominal", "scale": [[0,"#fafaf5"],[0.5,"#607D8B"],[1,"#1C313A"]], "range": None,     "cb_title": "Electores",      "fmt": ":,"},
}

# ── Data loading (cached) ──────────────────────────────────────────────────────

@st.cache_data(show_spinner=False)
def get_available_elections() -> list[str]:
    if not MATERIALIZED_DIR.exists():
        return []
    return sorted(
        f.stem.replace("view_estado_", "")
        for f in MATERIALIZED_DIR.glob("view_estado_*.parquet")
    )

@st.cache_data(show_spinner="Cargando datos...")
def load_view(granularity: str, election_id: str) -> pd.DataFrame:
    path = MATERIALIZED_DIR / f"view_{granularity}_{election_id}.parquet"
    if not path.exists():
        return pd.DataFrame()
    return pd.read_parquet(path)

@st.cache_data(show_spinner="Cargando GeoJSON...")
def load_municipios_geojson(geojson_path: str = None) -> dict:
    """Load the pre-processed GeoJSON (feat IDs already set by pipeline)."""
    # Prefer the pre-processed version; fall back to raw with a warning
    processed = MATERIALIZED_DIR / "municipios_processed.geojson"
    raw       = Path(geojson_path) if geojson_path else Path("municipios.geojson")
    if processed.exists():
        path = processed
    elif raw.exists():
        st.warning(
            "Usando GeoJSON sin procesar. Ejecuta `python ingestion/materialize.py views` "
            "para generar la version optimizada."
        )
        path = raw
    else:
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@st.cache_data(show_spinner=False)
def load_candidates() -> pd.DataFrame:
    """Load dim_candidatos.parquet; return empty DataFrame if not yet built."""
    path = MATERIALIZED_DIR / "dim_candidatos.parquet"
    if not path.exists():
        return pd.DataFrame()
    return pd.read_parquet(path)


def resolve_candidate_name(
    candidates_df: pd.DataFrame,
    party_key: str,
    election_id: str,
    id_distrito: Optional[int] = None,
    id_estado: Optional[int] = None,
) -> Optional[str]:
    """
    Return the candidate name for a given party_key + election, or None if not
    found. dim_candidatos is a WINNERS-ONLY table (one row per seat actually
    won) — it does not contain losing candidates, so callers must expect None
    for any party_key that didn't win the unit being queried.

    dim_candidatos has election_type (PRE/DIP_MR/DIP_RP/SEN_MR/SEN_RP), not
    election_id. Derive election_type by dropping only the trailing _YEAR
    segment (e.g. 'DIP_MR_2024' -> 'DIP_MR', 'PRE_2024' -> 'PRE') — NOT by
    taking the first token, which would incorrectly collapse 'DIP_MR' and
    'DIP_RP' both to 'DIP' and never match.

    CRITICAL: id_distrito_federal is NOT globally unique — federal districts
    are numbered 1..N *within each state* (e.g. district 1 exists in all 32
    states), so filtering by id_distrito alone silently collapses dozens of
    unrelated district races into one ambiguous match. id_estado and
    id_distrito must be supplied TOGETHER for a district-level race (DIP/SEN);
    supplying district without state will not narrow the match. Presidential
    races have no state/district at all (single national winner), so neither
    is needed there.
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
        scoped_sub = sub[
            (sub["id_estado"] == id_estado) &
            (sub["id_distrito_federal"] == id_distrito)
        ]
        if not scoped_sub.empty:
            sub = scoped_sub
        else:
            # No exact (estado, distrito) match — do NOT fall back to a
            # district-only match, since that reintroduces the cross-state
            # ambiguity bug. Better to return nothing than a wrong name.
            return None
    name = sub["candidate_name"].iloc[0]
    return name if pd.notna(name) and str(name).strip() else None

# ── Helpers ────────────────────────────────────────────────────────────────────

def _norm(s: str) -> str:
    s = str(s).upper().strip()
    s = unicodedata.normalize("NFD", s)
    return "".join(c for c in s if unicodedata.category(c) != "Mn")

def fmt_pct(v): return f"{v:.1f}%"
def fmt_num(v): return f"{int(v):,}"

def safe_int(v, default: int = 0) -> int:
    """int() that falls back to `default` instead of crashing on NaN/None --
    older cycles (e.g. 2000) are missing lista_nominal in the source data, so
    lista_nominal_part comes back NaN for that whole election."""
    return default if pd.isna(v) else int(v)

def metric_card(label, value, sub=None):
    sub_html = f'<div class="sub">{sub}</div>' if sub else ""
    st.markdown(
        f'<div class="metric-card">'
        f'<div class="label">{label}</div>'
        f'<div class="value">{value}</div>'
        f'{sub_html}</div>',
        unsafe_allow_html=True,
    )


def render_scorecards(total_v: int, lista_nom: int, part_pct: float, nulos_pct: float):
    c1, c2, c3 = st.columns(3)
    with c1:
        metric_card("Votos emitidos", fmt_num(total_v),
                    sub=f"de {fmt_num(lista_nom)} en lista nominal" if lista_nom else None)
    with c2:
        metric_card("Participación", fmt_pct(part_pct))
    with c3:
        metric_card("Votos nulos", fmt_pct(nulos_pct),
                    sub=f"{fmt_num(round(total_v * nulos_pct / 100))} votos" if total_v else None)


def election_label(election_id: str) -> str:
    parts = election_id.split("_")
    year = parts[-1] if parts and parts[-1].isdigit() else ""
    kind = "_".join(parts[:-1]) if year else election_id
    labels = {
        "PRE": "Presidencial",
        "DIP_MR": "Diputaciones federales · mayoría relativa",
        "DIP_RP": "Diputaciones federales · representación proporcional",
        "SEN_MR": "Senadurías · mayoría relativa",
        "SEN_RP": "Senadurías · representación proporcional",
    }
    base = labels.get(kind, kind.replace("_", " ").title())
    return f"{base} {year}".strip()

def header_badge(tags: list):
    spans = "".join(f'<span class="tag">{t}</span>' for t in tags)
    st.markdown(
        f'<div style="background:#1A1A1A;color:#F7F7F5;padding:0.8rem 1.2rem;'
        f'border-radius:3px;margin-bottom:1rem;">{spans}</div>',
        unsafe_allow_html=True,
    )

def plotly_base():
    return dict(
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        font_family="IBM Plex Sans", title_font_family="IBM Plex Mono",
        font_color="#888888",
        xaxis=dict(gridcolor="rgba(128,128,128,0.2)", zerolinecolor="rgba(128,128,128,0.3)"),
        yaxis=dict(gridcolor="rgba(128,128,128,0.2)", zerolinecolor="rgba(128,128,128,0.3)"),
    )

# ── Aggregate long-form parquet rows into candidate totals ─────────────────────

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

def get_scalar(df: pd.DataFrame, col: str, agg="sum"):
    if col not in df.columns:
        return 0
    return df[col].sum() if agg == "sum" else df[col].iloc[0]

# ── Charts ─────────────────────────────────────────────────────────────────────

def render_party_results(df: pd.DataFrame, title_suffix: str = ""):
    present = [k for k in PARTY_GROUPS if k in df["party_key"].values]
    if not present:
        st.info("No se encontraron columnas de partido en estos datos.")
        return

    by_party      = df.groupby("party_key")["votes"].sum()
    total_validos = int(df[df["party_key"] == present[0]]["num_votos_validos"].sum()) \
        if present else 1

    rows = []
    for col in present:
        votos = int(by_party.get(col, 0))
        if votos == 0:
            continue
        info = PARTY_GROUPS[col]
        pct  = votos / total_validos * 100 if total_validos > 0 else 0
        rows.append({"Partido / Coalicion": info["label"], "Votos": votos,
                     "pct": pct, "color": info["color"]})

    if not rows:
        st.info("Sin votos de partido para esta seleccion.")
        return

    df_plot = pd.DataFrame(rows).sort_values("Votos", ascending=False)
    fig = px.bar(
        df_plot, x="Votos", y="Partido / Coalicion", orientation="h",
        color="Partido / Coalicion",
        color_discrete_map={r["Partido / Coalicion"]: r["color"] for r in rows},
        text=df_plot["pct"].map(lambda x: f"{x:.1f}%"),
        title=f"Votos por Partido / Coalicion{(' - ' + title_suffix) if title_suffix else ''}",
    )
    fig.update_traces(textposition="outside", showlegend=False)
    fig.update_layout(
        **plotly_base(),
        xaxis_range=[0, df_plot["Votos"].max() * 1.18],
        height=max(300, len(df_plot) * 38 + 80),
    )
    st.plotly_chart(fig, use_container_width=True)


def render_candidate_results(df: pd.DataFrame):
    cand_totals   = pivot_candidates(df)
    sample_party  = df["party_key"].iloc[0] if len(df) else None
    total_validos = int(df[df["party_key"] == sample_party]["num_votos_validos"].sum()) \
        if sample_party else 0

    data = []
    for key, props in CANDIDATES.items():
        votos = cand_totals.get(key, 0)
        pct   = (votos / total_validos * 100) if total_validos > 0 else 0
        data.append({"Candidato": props["label"], "Votos": votos,
                     "pct": pct, "color": props["color"]})

    df_plot = pd.DataFrame(data).sort_values("Votos", ascending=False)
    fig = px.bar(
        df_plot, x="Votos", y="Candidato", orientation="h",
        color="Candidato",
        color_discrete_map={d["Candidato"]: d["color"] for d in data},
        text=df_plot["pct"].map(lambda x: f"{x:.1f}%"),
        title=f"Resultados por Candidato - {fmt_num(total_validos)} votos validos",
    )
    fig.update_traces(textposition="outside", showlegend=False)
    fig.update_layout(**plotly_base(),
                      xaxis_range=[0, df_plot["Votos"].max() * 1.15], height=300)
    st.plotly_chart(fig, use_container_width=True)


def render_both_charts(df: pd.DataFrame):
    col_cand, col_party = st.columns(2)
    with col_cand:
        st.markdown('<div class="section-label">Por Candidato</div>', unsafe_allow_html=True)
        render_candidate_results(df)
    with col_party:
        st.markdown('<div class="section-label">Por Partido / Coalicion</div>',
                    unsafe_allow_html=True)
        render_party_results(df)


# ── Ternary bubble ─────────────────────────────────────────────────────────────

def render_ternary_bubble(df: pd.DataFrame, group_cols, label_cols, title_suffix,
                          n_bubbles: int = None, height: int = 580):
    grp = group_cols if isinstance(group_cols, list) else [group_cols]
    lbl = label_cols if isinstance(label_cols, list) else [label_cols]

    shh_keys = [k for k in PARTY_GROUPS if PARTY_GROUPS[k]["cand"] == "CAND_SHH"]
    fcm_keys = [k for k in PARTY_GROUPS if PARTY_GROUPS[k]["cand"] == "CAND_FCM"]
    mc_keys  = [k for k in PARTY_GROUPS if PARTY_GROUPS[k]["cand"] == "CAND_MC"]

    agg = df.groupby(grp).apply(lambda g: pd.Series({
        "CAND_SHH":          g[g["party_key"].isin(shh_keys)]["votes"].sum(),
        "CAND_FCM":          g[g["party_key"].isin(fcm_keys)]["votes"].sum(),
        "CAND_MC":           g[g["party_key"].isin(mc_keys)]["votes"].sum(),
        "NUM_VOTOS_VALIDOS": g[g["party_key"] == g["party_key"].iloc[0]]["num_votos_validos"].sum(),
    })).reset_index()

    agg = agg[agg["NUM_VOTOS_VALIDOS"] > 0].copy()
    if n_bubbles is not None:
        agg = agg.nlargest(n_bubbles, "NUM_VOTOS_VALIDOS").copy()
    if agg.empty:
        st.info("Sin datos suficientes para el grafico ternario.")
        return

    cand_total      = agg["CAND_SHH"] + agg["CAND_FCM"] + agg["CAND_MC"]
    agg["pct_SHH"]  = agg["CAND_SHH"] / cand_total
    agg["pct_FCM"]  = agg["CAND_FCM"] / cand_total
    agg["pct_MC"]   = agg["CAND_MC"]  / cand_total

    sqrt3_2   = np.sqrt(3) / 2
    agg["tx"] = agg["pct_FCM"] + agg["pct_MC"] * 0.5
    agg["ty"] = agg["pct_MC"]  * sqrt3_2

    has_estado = "nombre_estado" in lbl
    mun_cols   = [c for c in lbl if c != "nombre_estado"]
    if has_estado and mun_cols:
        agg["_label"] = agg[mun_cols].astype(str).agg(" · ".join, axis=1)
    else:
        agg["_label"] = agg[lbl].astype(str).agg(" · ".join, axis=1)

    def winner_color(row):
        w = max({"SHH": row["CAND_SHH"], "FCM": row["CAND_FCM"], "MC": row["CAND_MC"]},
                key=lambda k: {"SHH": row["CAND_SHH"], "FCM": row["CAND_FCM"],
                               "MC": row["CAND_MC"]}[k])
        return {"SHH": "#8B0000", "FCM": "#1E90FF", "MC": "#FF8C00"}[w]

    agg["_color"] = agg.apply(winner_color, axis=1)

    def make_hover(r):
        estado_line = f"Estado: {r['nombre_estado']}<br>" if has_estado else ""
        return (
            f"<b>{r['_label']}</b><br>{estado_line}"
            f"SHH: {r['pct_SHH']*100:.1f}%<br>"
            f"FCM: {r['pct_FCM']*100:.1f}%<br>"
            f"MC:  {r['pct_MC']*100:.1f}%<br>"
            f"Votos validos: {int(r['NUM_VOTOS_VALIDOS']):,}"
        )

    agg["_text"] = agg.apply(make_hover, axis=1)

    grid_traces = []
    for frac in [1/3, 2/3]:
        ax, ay = 1 - frac, 0
        bx, by = (1 - frac) * 0.5, (1 - frac) * sqrt3_2
        cx, cy = frac, 0
        dx, dy = frac + (1 - frac) * 0.5, (1 - frac) * sqrt3_2
        ex, ey = (1 - frac) + frac * 0.5, frac * sqrt3_2
        fx, fy = frac * 0.5, frac * sqrt3_2
        for (x0, y0, x1, y1) in [(ax,ay,bx,by),(cx,cy,dx,dy),(ex,ey,fx,fy)]:
            grid_traces.append(go.Scatter(
                x=[x0,x1], y=[y0,y1], mode="lines",
                line=dict(color="rgba(180,180,180,0.4)", width=0.8, dash="dot"),
                hoverinfo="skip", showlegend=False,
            ))

    vertex_labels = go.Scatter(
        x=[0, 1, 0.5], y=[-0.06, -0.06, sqrt3_2 + 0.04],
        mode="text",
        text=[
            "<b>SHH</b><br><span style='font-size:10px'>Sheinbaum</span>",
            "<b>FCM</b><br><span style='font-size:10px'>Galvez</span>",
            "<b>MC</b><br><span style='font-size:10px'>Alvarez Maynez</span>",
        ],
        textfont=dict(size=13, family="IBM Plex Mono",
                      color=["#8B0000", "#1E90FF", "#FF8C00"]),
        hoverinfo="skip", showlegend=False,
    )
    centroid = go.Scatter(
        x=[0.5], y=[sqrt3_2 / 3], mode="markers+text",
        marker=dict(symbol="cross", size=10, color="rgba(100,100,100,0.6)",
                    line=dict(width=1.5, color="gray")),
        text=["33/33/33"], textposition="middle right",
        textfont=dict(size=9, color="#999"),
        hoverinfo="skip", showlegend=False,
    )
    triangle = go.Scatter(
        x=[0, 1, 0.5, 0], y=[0, 0, sqrt3_2, 0], mode="lines",
        line=dict(color="white", width=1.5),
        hoverinfo="skip", showlegend=False,
    )

    max_votes     = agg["NUM_VOTOS_VALIDOS"].max()
    agg["_size"]  = (agg["NUM_VOTOS_VALIDOS"] / max_votes * 55).clip(lower=6)

    bubbles = go.Scatter(
        x=agg["tx"], y=agg["ty"],
        mode="markers",
        marker=dict(size=agg["_size"], color=agg["_color"], opacity=0.72,
                    line=dict(width=0.8, color="white")),
        hovertemplate=agg["_text"] + "<extra></extra>",
        showlegend=False,
    )
    legend_traces = [
        go.Scatter(x=[None], y=[None], mode="markers",
                   marker=dict(size=10, color=props["color"]),
                   name=props["label"], showlegend=True)
        for props in CANDIDATES.values()
    ]

    fig = go.Figure(data=grid_traces + [triangle, centroid, vertex_labels, bubbles]
                    + legend_traces)
    fig.update_layout(
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        font_family="IBM Plex Sans", title_font_family="IBM Plex Mono",
        font_color="#888888",
        title=f"Distribución ternaria de votos - {title_suffix} ({len(agg)} unidades)",
        xaxis=dict(visible=False, range=[-0.12, 1.12]),
        yaxis=dict(visible=False, scaleanchor="x", scaleratio=1,
                   range=[-0.15, sqrt3_2 + 0.12]),
        height=height,
        legend=dict(orientation="h", yanchor="bottom", y=-0.15, xanchor="center", x=0.5),
        margin=dict(l=20, r=20, t=60, b=80),
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption(
        "Cada burbuja representa una unidad geografica. "
        "Su posicion refleja el pct de votos de cada candidato (entre los tres principales). "
        "El tamano es proporcional al volumen de votos validos. "
        "El color indica al ganador. "
        "El centro marca el empate perfecto 33/33/33."
    )


# ── Historical visualizations (any election) ───────────────────────────────────

def _agg_blocs(df: pd.DataFrame, group_cols, blocs: dict) -> pd.DataFrame:
    """Aggregate votes into 3 blocs (A/B/C) per geographic unit."""
    grp = group_cols if isinstance(group_cols, list) else [group_cols]
    a_keys = [k for k, v in blocs["map"].items() if v == "A"]
    b_keys = [k for k, v in blocs["map"].items() if v == "B"]
    c_keys = [k for k, v in blocs["map"].items() if v == "C"]

    extra = {c: "first" for c in ["_join_key", "total_votos", "nombre_estado"]
             if c in df.columns and c not in grp}

    def _row(g):
        a  = g[g["party_key"].isin(a_keys)]["votes"].sum()
        b  = g[g["party_key"].isin(b_keys)]["votes"].sum()
        c  = g[g["party_key"].isin(c_keys)]["votes"].sum()
        tv = g["total_votos"].iloc[0] if "total_votos" in g.columns else 0
        jk = g["_join_key"].iloc[0]  if "_join_key"   in g.columns else ""
        return pd.Series({"bloc_A": a, "bloc_B": b, "bloc_C": c,
                          "total_votos": tv, "_join_key": jk})

    agg = df.groupby(grp).apply(_row).reset_index()
    total = (agg["bloc_A"] + agg["bloc_B"] + agg["bloc_C"]).replace(0, float("nan"))
    agg["pct_A"] = (agg["bloc_A"] / total * 100).round(1)
    agg["pct_B"] = (agg["bloc_B"] / total * 100).round(1)
    agg["pct_C"] = (agg["bloc_C"] / total * 100).round(1)
    agg["winner"] = agg[["bloc_A", "bloc_B", "bloc_C"]].idxmax(axis=1).str.replace("bloc_", "")
    return agg.dropna(subset=["pct_A"])


def render_hist_ternary(df: pd.DataFrame, blocs: dict, title: str,
                        n_bubbles: int = None, height: int = 580):
    grp = ["nombre_estado", "municipio"] if "nombre_estado" in df.columns else ["municipio"]
    agg = _agg_blocs(df, grp, blocs)
    if n_bubbles:
        agg = agg.nlargest(n_bubbles, "total_votos")
    if agg.empty:
        st.info("Sin datos suficientes para el gráfico ternario.")
        return

    sqrt3_2 = np.sqrt(3) / 2
    total   = agg["bloc_A"] + agg["bloc_B"] + agg["bloc_C"]
    pct_a   = agg["bloc_A"] / total
    pct_b   = agg["bloc_B"] / total
    pct_c   = agg["bloc_C"] / total
    agg["tx"] = pct_b + pct_c * 0.5
    agg["ty"] = pct_c * sqrt3_2

    color_map  = {"A": blocs["A"]["color"], "B": blocs["B"]["color"], "C": blocs["C"]["color"]}
    agg["_color"] = agg["winner"].map(color_map)

    mun_col = "municipio" if "municipio" in agg.columns else agg.columns[0]
    def make_hover(r):
        est = f"{r['nombre_estado']} · " if "nombre_estado" in agg.columns else ""
        return (
            f"<b>{est}{r[mun_col]}</b><br>"
            f"{blocs['A']['label']}: {r['pct_A']:.1f}%<br>"
            f"{blocs['B']['label']}: {r['pct_B']:.1f}%<br>"
            f"{blocs['C']['label']}: {r['pct_C']:.1f}%<br>"
            f"Votos: {int(r['bloc_A']+r['bloc_B']+r['bloc_C']):,}"
        )
    agg["_text"] = agg.apply(make_hover, axis=1)
    agg["_size"] = (total / total.max() * 55).clip(lower=5)

    grid_traces = []
    for frac in [1/3, 2/3]:
        ax, ay = 1 - frac, 0;               bx, by = (1-frac)*0.5, (1-frac)*sqrt3_2
        cx, cy = frac,     0;               dx, dy = frac + (1-frac)*0.5, (1-frac)*sqrt3_2
        ex, ey = (1-frac) + frac*0.5, frac*sqrt3_2;  fx, fy = frac*0.5, frac*sqrt3_2
        for (x0,y0,x1,y1) in [(ax,ay,bx,by),(cx,cy,dx,dy),(ex,ey,fx,fy)]:
            grid_traces.append(go.Scatter(
                x=[x0,x1], y=[y0,y1], mode="lines",
                line=dict(color="rgba(180,180,180,0.4)", width=0.8, dash="dot"),
                hoverinfo="skip", showlegend=False,
            ))

    lbl_a = blocs["A"]["label"].split("—")[0].strip() if "—" in blocs["A"]["label"] else blocs["A"]["label"]
    lbl_b = blocs["B"]["label"].split("—")[0].strip() if "—" in blocs["B"]["label"] else blocs["B"]["label"]
    lbl_c = blocs["C"]["label"].split("—")[0].strip() if "—" in blocs["C"]["label"] else blocs["C"]["label"]

    vertex_labels = go.Scatter(
        x=[0, 1, 0.5], y=[-0.06, -0.06, sqrt3_2 + 0.04],
        mode="text",
        text=[f"<b>{lbl_a}</b>", f"<b>{lbl_b}</b>", f"<b>{lbl_c}</b>"],
        textfont=dict(size=12, family="IBM Plex Mono",
                      color=[blocs["A"]["color"], blocs["B"]["color"], blocs["C"]["color"]]),
        hoverinfo="skip", showlegend=False,
    )
    centroid = go.Scatter(
        x=[0.5], y=[sqrt3_2/3], mode="markers+text",
        marker=dict(symbol="cross", size=10, color="rgba(100,100,100,0.6)",
                    line=dict(width=1.5, color="gray")),
        text=["33/33/33"], textposition="middle right",
        textfont=dict(size=9, color="#999"),
        hoverinfo="skip", showlegend=False,
    )
    triangle = go.Scatter(
        x=[0, 1, 0.5, 0], y=[0, 0, sqrt3_2, 0], mode="lines",
        line=dict(color="white", width=1.5), hoverinfo="skip", showlegend=False,
    )
    bubbles = go.Scatter(
        x=agg["tx"], y=agg["ty"], mode="markers",
        marker=dict(size=agg["_size"], color=agg["_color"], opacity=0.72,
                    line=dict(width=0.8, color="white")),
        hovertemplate=agg["_text"] + "<extra></extra>",
        showlegend=False,
    )
    legend_traces = [
        go.Scatter(x=[None], y=[None], mode="markers",
                   marker=dict(size=10, color=blocs[k]["color"]),
                   name=blocs[k]["label"], showlegend=True)
        for k in ("A", "B", "C")
    ]
    fig = go.Figure(data=grid_traces + [triangle, centroid, vertex_labels, bubbles] + legend_traces)
    fig.update_layout(
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        font_family="IBM Plex Sans", title_font_family="IBM Plex Mono",
        font_color="#888888",
        title=f"<b>Distribución ternaria · {title}</b> ({len(agg):,} municipios)",
        xaxis=dict(visible=False, range=[-0.12, 1.12]),
        yaxis=dict(visible=False, scaleanchor="x", scaleratio=1,
                   range=[-0.18, sqrt3_2 + 0.15]),
        height=height,
        legend=dict(orientation="h", yanchor="bottom", y=-0.18, xanchor="center", x=0.5),
        margin=dict(l=20, r=20, t=60, b=90),
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption("Cada burbuja = un municipio. Posición = distribución de votos entre los 3 candidatos principales. Tamaño ∝ votos totales.")


@st.cache_data(show_spinner="Cargando datos nacionales...")
def load_mun_national(election_id: str) -> pd.DataFrame:
    return load_view("municipio", election_id)


def render_hist_winner_map(df: pd.DataFrame, blocs: dict, geojson: dict, title: str,
                           height: int = 560):
    agg = _agg_blocs(df, ["nombre_estado", "municipio"], blocs)
    if agg.empty:
        st.info("Sin datos para el mapa.")
        return

    all_ids = {f["id"] for f in geojson["features"]}
    agg = agg[agg["_join_key"].isin(all_ids)].copy()
    agg["_label"] = agg["nombre_estado"] + " — " + agg["municipio"]

    matched_ids = set(agg["_join_key"])
    matched_features = [f for f in geojson["features"] if f["id"] in matched_ids]
    center, zoom = _bbox_to_zoom_center(matched_features)

    fig = go.Figure()
    for bloc_key in ("A", "B", "C"):
        cfg    = blocs[bloc_key]
        subset = agg[agg["winner"] == bloc_key]
        if subset.empty:
            continue
        ids_set = set(subset["_join_key"])
        geo_sub = {"type": "FeatureCollection",
                   "features": [f for f in geojson["features"] if f["id"] in ids_set]}
        fig.add_trace(go.Choroplethmapbox(
            geojson=geo_sub, locations=subset["_join_key"],
            z=[1] * len(subset),
            colorscale=[[0, cfg["color"]], [1, cfg["color"]]],
            showscale=False, marker_opacity=0.82,
            marker_line_width=0.25, marker_line_color="rgba(255,255,255,0.1)",
            hovertext=subset["_label"],
            customdata=subset[["pct_A", "pct_B", "pct_C", "total_votos"]].values,
            hovertemplate=(
                "<b>%{hovertext}</b><br>"
                f"<span style='color:{blocs['A']['color']}'>{blocs['A']['label'].split('—')[0].strip()}</span>: %{{customdata[0]:.1f}}%<br>"
                f"<span style='color:{blocs['B']['color']}'>{blocs['B']['label'].split('—')[0].strip()}</span>: %{{customdata[1]:.1f}}%<br>"
                f"<span style='color:{blocs['C']['color']}'>{blocs['C']['label'].split('—')[0].strip()}</span>: %{{customdata[2]:.1f}}%<br>"
                "Votos: %{customdata[3]:,}<extra></extra>"
            ),
            name=cfg["label"], showlegend=True,
        ))

    fig.update_layout(
        mapbox=dict(style="carto-darkmatter", zoom=zoom, center=center),
        legend=dict(orientation="h", yanchor="top", y=-0.04,
                    xanchor="center", x=0.5, font=dict(size=11), itemsizing="constant"),
        title=dict(text=f"<b>{title}</b>", font=dict(family="IBM Plex Mono", size=14)),
        margin=dict(l=0, r=0, t=50, b=70),
        height=height,
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig, use_container_width=True)


# ── Top-N table ────────────────────────────────────────────────────────────────

def render_results_table(
    df: pd.DataFrame,
    group_cols,
    group_name: str,
    election_id: str,
    candidates_df: pd.DataFrame,
    id_distrito: Optional[int] = None,
):
    """
    Winners-only results table — one row per geographic unit, showing the
    winning candidate's name and their share of votos validos as a single
    progress bar. No party-by-party breakdown here (see render_both_charts
    for that); this table is deliberately "just the winner".

    dim_candidatos is winners-only, so a name can only be resolved for the
    candidate who actually won that unit, and ONLY when both id_estado and
    id_distrito_federal are known (district numbers repeat across states, so
    district alone is ambiguous — see resolve_candidate_name docstring).

    IMPORTANT: the estado/municipio/seccion materialized views do not
    currently carry id_estado/id_distrito_federal as grouped columns (only
    the casilla-level view does), so name resolution below only actually
    fires for the presidential race (single national winner, no district
    needed). Every other table falls back to showing the candidate code
    (SHH/FCM/MC) until id_estado + id_distrito_federal are threaded through
    query_estado/query_municipio/query_seccion in ingestion/materialize.py.
    """
    grp = group_cols if isinstance(group_cols, list) else [group_cols]
    grp = [c for c in grp if c in df.columns]
    if not grp:
        return

    shh_keys = [k for k in PARTY_GROUPS if PARTY_GROUPS[k]["cand"] == "CAND_SHH"]
    fcm_keys = [k for k in PARTY_GROUPS if PARTY_GROUPS[k]["cand"] == "CAND_FCM"]
    mc_keys  = [k for k in PARTY_GROUPS if PARTY_GROUPS[k]["cand"] == "CAND_MC"]

    agg = df.groupby(grp).apply(lambda g: pd.Series({
        "CAND_SHH":          g[g["party_key"].isin(shh_keys)]["votes"].sum(),
        "CAND_FCM":          g[g["party_key"].isin(fcm_keys)]["votes"].sum(),
        "CAND_MC":           g[g["party_key"].isin(mc_keys)]["votes"].sum(),
        "NUM_VOTOS_VALIDOS": g[g["party_key"] == g["party_key"].iloc[0]]["num_votos_validos"].sum(),
    })).reset_index()

    agg = agg[agg["NUM_VOTOS_VALIDOS"] > 0].sort_values("NUM_VOTOS_VALIDOS", ascending=False)
    if agg.empty:
        return

    election_type     = "_".join(election_id.split("_")[:-1])
    is_presidential    = election_type == "PRE"
    has_district_grp   = "id_distrito_federal" in grp
    has_estado_grp     = "id_estado" in grp
    can_resolve_name   = is_presidential or (has_district_grp and has_estado_grp)

    records = []
    for _, row in agg.iterrows():
        validos = row["NUM_VOTOS_VALIDOS"]
        votes   = {"SHH": row["CAND_SHH"], "FCM": row["CAND_FCM"], "MC": row["CAND_MC"]}
        winner  = max(votes, key=votes.get)
        pct_win = votes[winner] / validos * 100 if validos > 0 else 0
        label   = " · ".join(str(row[c]) for c in grp)

        ganador_display = winner
        if can_resolve_name:
            row_distrito = int(row["id_distrito_federal"]) if has_district_grp and pd.notna(row.get("id_distrito_federal")) else None
            row_estado   = int(row["id_estado"]) if has_estado_grp and pd.notna(row.get("id_estado")) else None
            resolved = resolve_candidate_name(
                candidates_df, CANDIDATE_PARTY_KEY[winner], election_id,
                id_distrito=row_distrito, id_estado=row_estado,
            )
            if resolved:
                ganador_display = resolved

        records.append({
            group_name:      label,
            "Votos validos": int(validos),
            "Ganador":       ganador_display,
            "% Ganador":     round(pct_win, 1),
        })

    out_df = pd.DataFrame(records)

    st.markdown(
        f'<div class="section-label">{group_name} — ganadores</div>',
        unsafe_allow_html=True,
    )
    st.caption("Haz clic en cualquier columna para ordenar · puedes filtrar con ⌘F")
    st.dataframe(
        out_df,
        use_container_width=True,
        height=min(600, max(200, len(out_df) * 35 + 40)),
        column_config={
            "Votos validos": st.column_config.NumberColumn(format="%d"),
            "% Ganador": st.column_config.ProgressColumn(
                "% Ganador", min_value=0, max_value=100, format="%.1f%%"
            ),
        },
    )


# ── Choropleth map ─────────────────────────────────────────────────────────────

def _bbox_to_zoom_center(features):
    lats, lons = [], []
    for feat in features:
        geom = feat.get("geometry") or {}
        if geom.get("type") == "Polygon":
            rings = geom["coordinates"]
        elif geom.get("type") == "MultiPolygon":
            rings = [r for poly in geom["coordinates"] for r in poly]
        else:
            continue
        for ring in rings:
            for lon, lat in ring:
                lats.append(lat); lons.append(lon)
    if not lats:
        return {"lat": 23.5, "lon": -102}, 4.0
    clat = (min(lats) + max(lats)) / 2
    clon = (min(lons) + max(lons)) / 2
    span = max(max(lats) - min(lats), max(lons) - min(lons))
    zoom = max(3.0, min(9.0, np.log2(360 / span) - 0.4)) if span > 0 else 4.0
    return {"lat": clat, "lon": clon}, round(zoom, 1)


def _build_map_agg(df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate long municipio df into one row per municipio with all map columns."""
    df_map = df[~df["municipio"].str.contains("EXTRANJERO", case=False, na=False)].copy()

    shh_keys = [k for k in PARTY_GROUPS if PARTY_GROUPS[k]["cand"] == "CAND_SHH"]
    fcm_keys = [k for k in PARTY_GROUPS if PARTY_GROUPS[k]["cand"] == "CAND_FCM"]
    mc_keys  = [k for k in PARTY_GROUPS if PARTY_GROUPS[k]["cand"] == "CAND_MC"]

    agg = df_map.groupby(["nombre_estado", "municipio"]).apply(lambda g: pd.Series({
        "CAND_SHH":          g[g["party_key"].isin(shh_keys)]["votes"].sum(),
        "CAND_FCM":          g[g["party_key"].isin(fcm_keys)]["votes"].sum(),
        "CAND_MC":           g[g["party_key"].isin(mc_keys)]["votes"].sum(),
        "NUM_VOTOS_VALIDOS": g[g["party_key"] == g["party_key"].iloc[0]]["num_votos_validos"].sum(),
        "total_votos":       g[g["party_key"] == g["party_key"].iloc[0]]["total_votos"].sum(),
        "lista_nominal":     g["lista_nominal_part"].iloc[0] if "lista_nominal_part" in g.columns else 0,
        # Use pre-computed join key from the parquet (set by pipeline._norm)
        "_join_key":         g["_join_key"].iloc[0] if "_join_key" in g.columns else "",
    })).reset_index()

    agg = agg[agg["NUM_VOTOS_VALIDOS"] > 0].copy()
    cand_total           = agg["CAND_SHH"] + agg["CAND_FCM"] + agg["CAND_MC"]
    agg["pct_shh"]       = (agg["CAND_SHH"] / cand_total * 100).round(1)
    agg["pct_fcm"]       = (agg["CAND_FCM"] / cand_total * 100).round(1)
    agg["pct_mc"]        = (agg["CAND_MC"]  / cand_total * 100).round(1)
    agg["participacion"] = (
        agg["total_votos"] / agg["lista_nominal"].replace(0, float("nan")) * 100
    ).round(1)

    def _winner(row):
        v = {"SHH": row["CAND_SHH"], "FCM": row["CAND_FCM"], "MC": row["CAND_MC"]}
        return max(v, key=v.get)

    agg["winner"]       = agg.apply(_winner, axis=1)
    agg["winner_label"] = agg["winner"].map(
        {"SHH": "Sheinbaum (SHH)", "FCM": "Galvez (FCM)", "MC": "Alvarez Maynez (MC)"}
    )
    # Fallback: compute join key if column was missing (e.g. older parquets)
    if "_join_key" not in agg.columns or agg["_join_key"].eq("").all():
        agg["_join_key"] = (
            agg["nombre_estado"].map(_norm) + "||" + agg["municipio"].map(_norm)
        )
    agg["_label"] = agg["nombre_estado"] + " - " + agg["municipio"]
    return agg


def _build_winner_fig(agg: pd.DataFrame, geo: dict, center: dict, zoom: float,
                      opacity: float) -> go.Figure:
    """Discrete winner choropleth — one color per candidate."""
    agg_matched = agg[agg["_join_key"].isin({f["id"] for f in geo["features"]})].copy()
    fig = go.Figure()
    winner_cfg = {
        "SHH": {"color": "#8B0000", "name": "Sheinbaum (SHH)"},
        "FCM": {"color": "#1E90FF", "name": "Galvez (FCM)"},
        "MC":  {"color": "#FF8C00", "name": "Alvarez Maynez (MC)"},
    }
    for cand_key, cfg in winner_cfg.items():
        subset = agg_matched[agg_matched["winner"] == cand_key]
        if subset.empty:
            continue
        ids_set = set(subset["_join_key"])
        geo_sub = {"type": "FeatureCollection",
                   "features": [f for f in geo["features"] if f["id"] in ids_set]}
        fig.add_trace(go.Choroplethmapbox(
            geojson=geo_sub, locations=subset["_join_key"],
            z=[1] * len(subset),
            colorscale=[[0, cfg["color"]], [1, cfg["color"]]],
            showscale=False, marker_opacity=opacity,
            marker_line_width=0.3, marker_line_color="rgba(255,255,255,0.15)",
            hovertext=subset["_label"],
            customdata=subset[["pct_shh","pct_fcm","pct_mc","participacion","total_votos"]].values,
            hovertemplate=(
                "<b>%{hovertext}</b><br>"
                f"Ganador: {cfg['name']}<br>"
                "SHH: %{customdata[0]:.1f}%<br>"
                "FCM: %{customdata[1]:.1f}%<br>"
                "MC:  %{customdata[2]:.1f}%<br>"
                "Participacion: %{customdata[3]:.1f}%<br>"
                "Votos: %{customdata[4]:,}<extra></extra>"
            ),
            name=cfg["name"], showlegend=True,
        ))
    fig.update_layout(
        mapbox=dict(style="carto-darkmatter", zoom=zoom, center=center),
        legend=dict(
            orientation="h", yanchor="top", y=-0.05,
            xanchor="center", x=0.5, font=dict(size=12), itemsizing="constant",
        ),
        title="Ganador por Municipio",
        margin=dict(l=0, r=0, t=50, b=60),
    )
    return fig


def _build_continuous_fig(agg: pd.DataFrame, geo: dict, center: dict, zoom: float,
                           metric: dict, opacity: float) -> go.Figure:
    """Continuous choropleth for a single numeric metric."""
    agg_matched = agg[agg["_join_key"].isin({f["id"] for f in geo["features"]})].copy()
    # Exclude internal columns from hover — only show human-readable fields
    # (note: "_join_key" is intentionally absent from this dict, so it has
    # never been shown in the hover tooltip; hover_data is built from this
    # dict's keys only)
    hover_cols = {
        "winner_label": "Ganador",
        "pct_shh": "SHH %",
        "pct_fcm": "FCM %",
        "pct_mc":  "MC %",
        "participacion": "Participación %",
        "total_votos":   "Votos totales",
        "lista_nominal": "Lista nominal",
    }
    col     = metric["col"]
    r_color = metric["range"] if metric["range"] else [
        float(agg_matched[col].quantile(0.05)),
        float(agg_matched[col].quantile(0.95)),
    ]
    fig = px.choropleth_mapbox(
        agg_matched, geojson=geo, locations="_join_key", color=col,
        color_continuous_scale=metric["scale"], range_color=r_color,
        mapbox_style="carto-darkmatter", zoom=zoom, center=center,
        opacity=opacity, hover_name="_label",
        hover_data={k: True for k in hover_cols},
        labels=hover_cols,
        title=metric["label"],
    )
    # Plotly auto-prepends the `locations` column (_join_key) as the first
    # hover line regardless of hover_data — strip it by rebuilding the
    # hovertemplate from hover_name + the custom fields only.
    field_lines = "".join(
        f"{label}=%{{customdata[{i}]}}<br>" for i, label in enumerate(hover_cols.values())
    )
    fig.update_traces(
        marker_line_width=0,
        hovertemplate=f"<b>%{{hovertext}}</b><br>{field_lines}<extra></extra>",
    )
    fig.update_layout(coloraxis_colorbar=dict(
        title=metric["cb_title"], thickness=10, len=0.55))
    return fig


_MAP_BASE_LAYOUT = dict(
    plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
    font_family="IBM Plex Sans", title_font_family="IBM Plex Mono",
    font_color="#888888", margin=dict(l=0, r=0, t=50, b=10),
)

# Candidate heatmap options (left panel of the second row)
_CAND_METRICS = {
    "SHH": MAP_METRICS["% SHH"],
    "FCM": MAP_METRICS["% FCM"],
    "MC":  MAP_METRICS["% MC"],
}
_CAND_LABELS = {
    "SHH": "Sheinbaum (SHH)",
    "FCM": "Galvez (FCM)",
    "MC":  "Alvarez Maynez (MC)",
}

# Electoral info options (right panel of the second row)
_INFO_METRICS = {k: MAP_METRICS[k] for k in ["Participación", "Votos totales", "Lista nominal"]}


# ── Timeseries (multi-year, by party, state granularity) ───────────────────────
# Built by ingestion/materialize.py from SQLite -- a separate, lighter parquet than
# the per-election views above (those are single-election, casilla-level
# aggregates; this one spans 2012/2018/2024 at state granularity only).

TIMESERIES_PATH = Path("data/materialized/timeseries_estados.parquet")

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


def _ts_fallback_color(key: str) -> str:
    h = hash(key) % 360
    return f"hsl({h},55%,42%)"


def ts_party_color(key: str) -> str:
    return TS_PARTY_COLORS.get(key, _ts_fallback_color(key))


@st.cache_data(show_spinner="Cargando series de tiempo...")
def load_timeseries(_mtime: float) -> pd.DataFrame:
    # _mtime forces st.cache_data to invalidate whenever ingestion/materialize.py
    # regenerates the underlying parquet, even though this function body
    # didn't change between app reruns.
    if not TIMESERIES_PATH.exists():
        return pd.DataFrame()
    df = pd.read_parquet(TIMESERIES_PATH)
    df["nombre_estado"] = df["nombre_estado"].str.strip().str.title()

    # Synthetic MORENA 2012 zeros: MORENA was founded in 2014, so there are no
    # rows for it in the 2012 cycle. Inject explicit 0s so the line starts at
    # the origin rather than beginning abruptly at 2018.
    pre_2012 = df[(df["election_type"] == "PRE") & (df["year"] == 2012)]
    if not pre_2012.empty and not ((df["party_key"] == "MORENA") & (df["year"] == 2012)).any():
        state_info = (
            pre_2012[["nombre_estado", "id_estado", "election_type"]]
            .drop_duplicates("nombre_estado")
        )
        synthetic = state_info.copy()
        synthetic["year"]               = 2012
        synthetic["election_id"]        = "PRE_2012"
        synthetic["party_key"]          = "MORENA"
        synthetic["is_coalition"]       = False
        synthetic["votes_raw"]          = 0.0
        synthetic["votes_split"]        = 0.0
        synthetic["pct_raw"]            = 0.0
        synthetic["pct_split"]          = 0.0
        synthetic["lista_nominal"]      = None
        synthetic["total_votos_estado"] = None
        for col in df.columns:
            if col not in synthetic.columns:
                synthetic[col] = None
        df = pd.concat([df, synthetic[df.columns]], ignore_index=True)

    return df


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
    denom = agg["total_votos_estado"].replace(0, float("nan"))
    agg["pct_raw"]   = agg["votes_raw"]   / denom * 100
    agg["pct_split"] = agg["votes_split"] / denom * 100
    return agg


def ts_base_layout(title: str, y_label: str, years: list, height: int = TS_CHART_HEIGHT) -> dict:
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


def render_timeseries_for_estado(df_ts: pd.DataFrame, id_estado_sel: int, estado_label: str):
    """
    Multi-year (2012/2018/2024) votes-by-party chart for a single state,
    ported from timeseries_explorer_streamlit.py's "Por Estado" mode but
    fixed to the state already chosen above -- no separate state picker
    here, since this section lives inside the Estado page.

    Matched by id_estado (1-32, consistent across every cycle's source data)
    rather than nombre_estado -- state name spelling/accents vary enough
    across cycles (and even within results views before canonicalization)
    that a string match silently drops every state.
    """
    df_state = df_ts[df_ts["id_estado"] == id_estado_sel]
    if df_state.empty:
        st.info("Sin datos históricos para este estado.")
        return

    election_types = sorted(df_state["election_type"].unique())
    et_options      = {TS_ELECTION_TYPE_LABELS.get(e, e): e for e in election_types}

    label_of = (
        df_state.dropna(subset=["party_label"])
        .drop_duplicates("party_key")
        .set_index("party_key")["party_label"]
        .to_dict()
    )
    coalition_keys = set(df_state.loc[df_state["is_coalition"] == True, "party_key"].unique())
    all_parties     = sorted(df_state["party_key"].unique())

    # Row 1: type picker + partido multiselect
    row1 = st.columns([1.2, 2.8])
    with row1[0]:
        et_keys     = list(et_options.keys())
        pre_default = et_keys.index("Presidencia") if "Presidencia" in et_keys else 0
        et_label    = st.selectbox("Tipo", et_keys, index=pre_default, key="ts_et")
        et          = et_options[et_label]
    with row1[1]:
        direct_parties = [p for p in all_parties if p not in coalition_keys]
        default_p      = [p for p in ["MORENA", "PAN", "PRI", "MC", "PRD"] if p in direct_parties]
        parties_to_show = st.multiselect(
            "Partidos", direct_parties, default=default_p,
            format_func=lambda p: label_of.get(p, p), key="ts_parties",
        )

    # Row 2: coalition mode + metric + area
    row2 = st.columns([1.6, 1.6, 1])
    with row2[0]:
        coalition_mode = st.radio(
            "Votos de coalición", ["Divididos", "Como coalición"],
            horizontal=True, key="ts_coalition",
        )
        split = coalition_mode == "Divididos"
    with row2[1]:
        metric  = st.radio("Métrica", ["% del total", "Votos abs."], index=0, horizontal=True, key="ts_metric")
        use_pct = metric == "% del total"
    with row2[2]:
        show_area = st.checkbox("Área bajo curva", value=False, key="ts_area")

    # When "Como coalición" is active, show a coalition multiselect and rebuild selectable
    if not split:
        selectable = all_parties
        extra = st.multiselect(
            "Coaliciones", [p for p in all_parties if p in coalition_keys], default=[],
            format_func=lambda p: label_of.get(p, p), key="ts_coalitions",
        )
        parties_to_show = parties_to_show + extra

    y_col   = ("pct_split"   if split else "pct_raw")   if use_pct else ("votes_split" if split else "votes_raw")
    y_label = "% de votos" if use_pct else "Votos"
    y_fmt   = ":.1f" if use_pct else ":,.0f"

    if use_pct:
        st.caption(
            "% = votos del partido / **total de votos emitidos** en el estado "
            "(incluye nulos, no registrados y partidos no seleccionados)."
        )

    if not parties_to_show:
        st.info("Selecciona al menos un partido.")
        return

    df_f = df_state[
        (df_state["election_type"] == et)
        & (df_state["party_key"].isin(parties_to_show))
    ].copy()
    if split:
        df_f = df_f[df_f["is_coalition"] == False]
    if df_f.empty:
        st.warning("Sin datos para estos filtros.")
        return

    df_agg = ts_agg_for_plot(df_f, ["year", "election_type", "nombre_estado", "party_key"])

    party_order = (
        df_agg.groupby("party_key")[y_col].sum()
        .sort_values(ascending=False)
        .index.tolist()
    )

    fig = go.Figure()
    for party in party_order:
        grp = df_agg[df_agg["party_key"] == party]
        grp   = grp.sort_values("year")
        color = ts_party_color(party)
        label = label_of.get(party, party)
        fig.add_trace(go.Scatter(
            x=grp["year"], y=grp[y_col],
            mode="lines+markers", name=label,
            line=dict(color=color, width=2.5),
            marker=dict(color=color, size=8),
            fill="tozeroy" if show_area else "none",
            hovertemplate=f"<b>{label}</b>: %{{y{y_fmt}}}<extra></extra>",
        ))
    fig.update_layout(**ts_base_layout(
        f"{estado_label} · {et_label}", y_label, sorted(df_agg["year"].unique())
    ))
    st.plotly_chart(fig, use_container_width=True)


def render_mexico_map(
    df: pd.DataFrame,
    map_key_suffix: str = "nacional",
    height: int = 650,
):
    """
    Lightweight online map layout: render only one map at a time.
    The user chooses between winner, candidate vote share, or electoral metric.
    This avoids rendering three Plotly Mapbox figures simultaneously.
    """
    geo = load_municipios_geojson()
    if not geo:
        st.warning(
            "No se encontro el GeoJSON de municipios. Descargalo con:\n\n"
            "`curl -L -o municipios.geojson "
            "https://raw.githubusercontent.com/angelnmara/geojson/master/MunicipiosMexico.json`\n\n"
            "Luego ejecuta `python ingestion/materialize.py views` para pre-procesarlo."
        )
        return

    agg = _build_map_agg(df)

    geo_keys    = {f["id"] for f in geo["features"]}
    unmatched   = len(agg) - len(agg[agg["_join_key"].isin(geo_keys)])
    matched_ids = set(agg[agg["_join_key"].isin(geo_keys)]["_join_key"])
    center, zoom = _bbox_to_zoom_center(
        [f for f in geo["features"] if f["id"] in matched_ids]
    )

    st.markdown('<div class="section-label">Mapa municipal</div>', unsafe_allow_html=True)

    map_type = st.radio(
        "Tipo de mapa",
        options=["Ganador", "% votos por candidato", "Información electoral"],
        horizontal=True,
        key=f"map_type_{map_key_suffix}",
    )

    map_cols = st.columns([1, 1])
    with map_cols[0]:
        zoom = st.slider(
            "Zoom",
            min_value=3.0, max_value=9.0, value=float(zoom), step=0.1,
            key=f"map_zoom_{map_key_suffix}",
        )
    with map_cols[1]:
        # Keep opacity low by default because municipio polygons are dense.
        opacity = st.slider(
            "Transparencia",
            min_value=0.05, max_value=1.0, value=0.25, step=0.05,
            key=f"map_opacity_{map_key_suffix}",
        )

    if map_type == "Ganador":
        fig = _build_winner_fig(agg, geo, center, zoom, opacity)

    elif map_type == "% votos por candidato":
        cand_sel = st.selectbox(
            "Candidato",
            options=list(_CAND_LABELS.keys()),
            format_func=lambda k: _CAND_LABELS[k],
            key=f"map_cand_{map_key_suffix}",
        )
        fig = _build_continuous_fig(
            agg, geo, center, zoom, _CAND_METRICS[cand_sel], opacity
        )

    else:
        info_sel = st.selectbox(
            "Métrica",
            options=list(_INFO_METRICS.keys()),
            format_func=lambda k: _INFO_METRICS[k]["label"],
            key=f"map_info_{map_key_suffix}",
        )
        fig = _build_continuous_fig(
            agg, geo, center, zoom, _INFO_METRICS[info_sel], opacity
        )

    fig.update_layout(**_MAP_BASE_LAYOUT, height=height)
    st.plotly_chart(fig, use_container_width=True)

    if unmatched > 0:
        st.caption(f"{unmatched} municipios sin geometria en el GeoJSON.")


# ── Results tab (shared across Estado / Municipio) ──────────────────────────────
# Order: Scorecards → Map/Ternary panel → Bar charts → Tables
# (Scorecard metrics are always rendered by the caller before this function)

def render_hist_both_charts(df: pd.DataFrame, blocs: dict):
    """Two-panel bar chart for historical PRE elections: by candidate bloc + by party."""
    by_party  = df.groupby("party_key")["votes"].sum()
    # Use num_votos_validos as denominator like 2024 does; fall back to sum of party votes
    sample_pk = df["party_key"].iloc[0] if len(df) else None
    if sample_pk and "num_votos_validos" in df.columns:
        total_validos = int(df[df["party_key"] == sample_pk]["num_votos_validos"].sum())
    else:
        total_validos = int(by_party.sum())
    total_validos = max(total_validos, int(by_party.sum()), 1)

    # ── Candidate panel ──────────────────────────────────────────────────────────
    a_keys = [k for k, v in blocs["map"].items() if v == "A"]
    b_keys = [k for k, v in blocs["map"].items() if v == "B"]
    c_keys = [k for k, v in blocs["map"].items() if v == "C"]
    cand_rows = []
    for bloc_key, keys in [("A", a_keys), ("B", b_keys), ("C", c_keys)]:
        votes = int(by_party.reindex(keys, fill_value=0).sum())
        if votes == 0:
            continue
        cand_rows.append({
            "Candidato": blocs[bloc_key]["label"],
            "Votos":     votes,
            "pct":       votes / total_validos * 100,
            "color":     blocs[bloc_key]["color"],
        })
    cand_rows.sort(key=lambda r: r["Votos"], reverse=True)

    # ── Party panel ──────────────────────────────────────────────────────────────
    party_rows = []
    for pk, v in by_party.sort_values(ascending=False).items():
        if v == 0:
            continue
        bloc  = blocs["map"].get(pk)
        color = blocs[bloc]["color"] if bloc else "#666666"
        party_rows.append({"Partido": pk, "Votos": int(v),
                           "pct": v / total_validos * 100, "color": color})

    def _hbar(rows, x_col, y_col):
        df_p = pd.DataFrame(rows)
        fig  = px.bar(
            df_p, x=x_col, y=y_col, orientation="h",
            color=y_col,
            color_discrete_map={r[y_col]: r["color"] for r in rows},
            text=df_p["pct"].map(lambda x: f"{x:.1f}%"),
        )
        fig.update_traces(textposition="outside", showlegend=False)
        fig.update_layout(
            **plotly_base(),
            title=dict(text=""),
            xaxis_range=[0, df_p[x_col].max() * 1.18],
            height=max(300, len(df_p) * 42 + 80),
        )
        return fig

    col_cand, col_party = st.columns(2)
    with col_cand:
        st.markdown('<div class="section-label">Por Candidato</div>', unsafe_allow_html=True)
        if cand_rows:
            st.plotly_chart(_hbar(cand_rows, "Votos", "Candidato"), use_container_width=True)
    with col_party:
        st.markdown('<div class="section-label">Por Partido / Coalición</div>', unsafe_allow_html=True)
        if party_rows:
            st.plotly_chart(_hbar(party_rows, "Votos", "Partido"), use_container_width=True)


def render_results_tab(df_raw: pd.DataFrame, page_level: str, election_id: str,
                       candidates_df: pd.DataFrame = None, id_distrito: Optional[int] = None,
                       scorecards: Optional[tuple] = None):
    if candidates_df is None:
        candidates_df = pd.DataFrame()

    blocs    = CYCLE_BLOCS.get(election_id)
    is_2024  = election_id == "PRE_2024"

    if page_level == "Estado":
        # 1. MAP + TERNARY
        st.markdown("---")
        map_col, divider_col, ternary_col = st.columns([1.15, 0.04, 0.85], gap="medium")
        if is_2024:
            with map_col:
                render_mexico_map(df_raw, map_key_suffix="estado", height=560)
            with divider_col:
                st.markdown('<div class="panel-divider"></div>', unsafe_allow_html=True)
            with ternary_col:
                st.markdown('<div class="section-label">Distribucion ternaria por Municipio</div>',
                            unsafe_allow_html=True)
                max_muns = df_raw["municipio"].nunique()
                st.caption(f"Mostrando todos los municipios disponibles: {max_muns:,}")
                render_ternary_bubble(
                    df_raw, "municipio", "municipio", "por Municipio",
                    n_bubbles=None, height=560,
                )
        elif blocs is not None:
            geo = load_municipios_geojson()
            with map_col:
                render_hist_winner_map(df_raw, blocs, geo,
                                       f"Ganador por Municipio · {election_label(election_id)}",
                                       height=560)
            with divider_col:
                st.markdown('<div class="panel-divider"></div>', unsafe_allow_html=True)
            with ternary_col:
                st.markdown('<div class="section-label">Distribución ternaria por Municipio</div>',
                            unsafe_allow_html=True)
                render_hist_ternary(df_raw, blocs, election_label(election_id), height=560)
        else:
            with map_col:
                st.info("Visualización de mapa no disponible para este tipo de elección.")

        # 2. SCORECARDS (below map so visuals are always first)
        st.markdown("---")
        if scorecards is not None:
            render_scorecards(*scorecards)

        # 3. BAR CHARTS
        st.markdown("---")
        if is_2024:
            render_both_charts(df_raw)
        elif blocs is not None:
            render_hist_both_charts(df_raw, blocs)
        else:
            render_hist_both_charts(df_raw, {"map": {}, "A": {"label": "A", "color": "#888"},
                                             "B": {"label": "B", "color": "#555"},
                                             "C": {"label": "C", "color": "#333"}})

        # 4. TABLES (2024 only — historical elections don't have dim_candidatos entries)
        if is_2024:
            st.markdown("---")
            render_results_table(df_raw, "municipio", "Municipio",
                                 election_id, candidates_df, id_distrito)
            render_results_table(df_raw, "seccion", "Sección",
                                 election_id, candidates_df, id_distrito)

    elif page_level == "Municipio":
        # 1. MAP (single polygon) — 2024 only
        st.markdown("---")
        if is_2024:
            render_mexico_map(df_raw, map_key_suffix="municipio")
        if scorecards is not None:
            render_scorecards(*scorecards)

        # 2. BAR CHARTS
        st.markdown("---")
        if is_2024:
            render_both_charts(df_raw)
        elif blocs is not None:
            render_hist_both_charts(df_raw, blocs)
        else:
            render_hist_both_charts(df_raw, {"map": {}, "A": {"label": "A", "color": "#888"},
                                             "B": {"label": "B", "color": "#555"},
                                             "C": {"label": "C", "color": "#333"}})

        # 3. TABLES
        st.markdown("---")
        col1, col2 = st.columns(2)
        with col1:
            render_results_table(df_raw, "seccion", "Sección",
                                 election_id, candidates_df, id_distrito)


# ── App shell (single panel -- no sidebar) ──────────────────────────────────────

st.markdown("**INE · Explorador Electoral de México**")

elections = get_available_elections()
if not elections:
    st.error(
        "No se encontraron archivos Parquet materializados. "
        "Ejecuta `python ingestion/pipeline.py` y luego `python ingestion/materialize.py` primero."
    )
    st.stop()

# Load candidate names once (used by tables across all pages)
candidates_df = load_candidates()

TYPE_LABELS = {"PRE": "Presidencial", "DIP": "Diputados", "SEN": "Senadores"}

# ── Step 1: year ─────────────────────────────────────────────────────────────
all_years = sorted({e.split("_")[-1] for e in elections}, reverse=True)
default_year = "2024" if "2024" in all_years else all_years[0]

# ── Step 2: election type for that year ──────────────────────────────────────
# (computed before column layout so estado list can load)
_col_tmp = st.columns([0.8, 1.2, 1.0, 1.4])
with _col_tmp[0]:
    year_sel = st.selectbox("Año", all_years, index=all_years.index(default_year))

year_elections = [e for e in elections if e.endswith(f"_{year_sel}")]
year_elections_sorted = sorted(year_elections,
    key=lambda e: list(TYPE_LABELS.keys()).index("_".join(e.split("_")[:-1]))
    if "_".join(e.split("_")[:-1]) in TYPE_LABELS else 99)
default_et = next((e for e in year_elections_sorted if e.startswith("PRE")), year_elections_sorted[0])
with _col_tmp[1]:
    election_sel = st.selectbox(
        "Tipo de elección", year_elections_sorted,
        index=year_elections_sorted.index(default_et),
        format_func=lambda e: TYPE_LABELS.get("_".join(e.split("_")[:-1]), election_label(e)),
    )
with _col_tmp[2]:
    page_options = ["Estado", "Municipio", "Nacional · Histórico"]
    page = st.selectbox("Unidad de análisis", page_options, index=page_options.index("Estado"))

# Estado view always loaded first -- state picker and scorecard/metadata source.
df_est_full = load_view("estado", election_sel)
if df_est_full.empty:
    st.error("Sin datos. Ejecuta `python ingestion/materialize.py views` primero.")
    st.stop()

estado_options = (
    df_est_full[["id_estado", "nombre_estado"]]
    .dropna().drop_duplicates().sort_values("nombre_estado")
)
estado_names = estado_options["nombre_estado"].tolist()
default_e    = next((e for e in estado_names if "CIUDAD" in e.upper()), estado_names[0])

with _col_tmp[3]:
    if page != "Nacional · Histórico":
        estado_sel = st.selectbox("Estado", estado_names, index=estado_names.index(default_e))
    else:
        estado_sel = estado_names[0]
        st.empty()
id_estado_sel = int(estado_options.loc[estado_options["nombre_estado"] == estado_sel, "id_estado"].iloc[0])


# ── PAGE: ESTADO ───────────────────────────────────────────────────────────────
if page == "Estado":
    df_view = df_est_full[df_est_full["id_estado"] == id_estado_sel]
    if df_view.empty:
        st.info("Sin datos para este estado.")
        st.stop()

    meta_row  = df_view.drop_duplicates("id_estado").iloc[0]
    num_dist  = safe_int(meta_row.get("num_distritos"))
    num_mun   = safe_int(meta_row.get("num_municipios"))
    num_sec   = safe_int(meta_row.get("num_secciones"))
    num_cas   = safe_int(meta_row.get("num_casillas"))
    total_v   = safe_int(df_view.drop_duplicates("id_estado")["total_votos"].sum())
    lista_nom = safe_int(meta_row.get("lista_nominal_part"))
    part_pct  = total_v / lista_nom * 100 if lista_nom > 0 else 0
    nulos_raw = safe_int(df_view.drop_duplicates("id_estado")["num_votos_nulos"].sum())
    nulos_pct = nulos_raw / total_v * 100 if total_v > 0 else 0

    header_badge([
        estado_sel,
        f"{num_dist} distritos federales",
        f"{num_mun} municipios",
        f"{num_sec} secciones",
        f"{num_cas} actas",
    ])

    # ── Map + Ternary + Scorecards + Bars (always first) ──────────────────────
    df_mun_view = load_view("municipio", election_sel)
    df_mun_view = df_mun_view[df_mun_view["id_estado"] == id_estado_sel]

    render_results_tab(
        df_mun_view, "Estado", election_sel, candidates_df,
        scorecards=(total_v, lista_nom, part_pct, nulos_pct),
    )

    # ── Serie de tiempo (collapsed by default) ─────────────────────────────────
    st.markdown("---")
    with st.expander("Serie de tiempo · votos históricos por partido", expanded=False):
        df_ts = load_timeseries(TIMESERIES_PATH.stat().st_mtime if TIMESERIES_PATH.exists() else 0.0)
        if df_ts.empty:
            st.info(
                "No se encontró el archivo de series de tiempo. "
                "Ejecuta `python ingestion/materialize.py timeseries` primero."
            )
        else:
            render_timeseries_for_estado(df_ts, id_estado_sel, estado_sel)


# ── PAGE: MUNICIPIO ────────────────────────────────────────────────────────────
elif page == "Municipio":
    df_mun_full = load_view("municipio", election_sel)
    if df_mun_full.empty:
        st.error("Sin datos. Ejecuta `python ingestion/materialize.py views` primero.")
        st.stop()

    df_e = df_mun_full[df_mun_full["id_estado"] == id_estado_sel]
    municipios = sorted(df_e["municipio"].dropna().unique())
    mun_sel    = st.selectbox("Municipio", municipios)
    df_view    = df_e[df_e["municipio"] == mun_sel]

    if df_view.empty:
        st.info("Sin datos para este municipio.")
        st.stop()

    meta_row  = df_view.drop_duplicates("municipio").iloc[0]
    num_cas   = safe_int(meta_row.get("num_casillas"))
    num_sec   = safe_int(meta_row.get("num_secciones"))
    total_v   = safe_int(df_view.drop_duplicates("municipio")["total_votos"].sum())
    lista_nom = safe_int(meta_row.get("lista_nominal_part"))
    part_pct  = total_v / lista_nom * 100 if lista_nom > 0 else 0
    nulos_raw = safe_int(df_view.drop_duplicates("municipio")["num_votos_nulos"].sum())
    nulos_pct = nulos_raw / total_v * 100 if total_v > 0 else 0

    header_badge([
        estado_sel, f"Municipio: {mun_sel}",
        f"{num_sec} seccion(es)", f"{num_cas} acta(s)",
    ])

    render_results_tab(
        df_view, "Municipio", election_sel, candidates_df,
        scorecards=(total_v, lista_nom, part_pct, nulos_pct),
    )

    # Secciones table (loaded separately, appended after the main results block)
    df_sec_view = load_view("seccion", election_sel)
    df_sec_view = df_sec_view[
        (df_sec_view["id_estado"] == id_estado_sel) &
        (df_sec_view["municipio"] == mun_sel)
    ]
    if not df_sec_view.empty:
        st.markdown("---")
        render_results_table(df_sec_view, "seccion", "Sección",
                             election_sel, candidates_df)


# ── PAGE: NACIONAL · HISTÓRICO ─────────────────────────────────────────────────
elif page == "Nacional · Histórico":
    geo = load_municipios_geojson()
    if not geo:
        st.error("No se encontró el GeoJSON de municipios. Ejecuta `python ingestion/materialize.py views` primero.")
        st.stop()

    df_nacional = load_mun_national(election_sel)
    if df_nacional.empty:
        st.error("Sin datos para esta elección.")
        st.stop()

    election_type = "_".join(election_sel.split("_")[:-1])
    blocs = CYCLE_BLOCS.get(election_sel)
    is_pre_with_blocs = blocs is not None

    header_badge([election_label(election_sel), f"{df_nacional['_join_key'].nunique():,} municipios"])

    if is_pre_with_blocs:
        # Map + ternary side by side
        map_col, div_col, tern_col = st.columns([1.15, 0.04, 0.85], gap="medium")
        with map_col:
            render_hist_winner_map(df_nacional, blocs, geo,
                                   f"Ganador por Municipio · {election_label(election_sel)}")
        with div_col:
            st.markdown('<div class="panel-divider"></div>', unsafe_allow_html=True)
        with tern_col:
            st.markdown('<div class="section-label">Distribución ternaria por Municipio</div>',
                        unsafe_allow_html=True)
            render_hist_ternary(df_nacional, blocs, election_label(election_sel))
    else:
        # Non-presidential or election without bloc mapping: winner map only
        # Build a generic party-color winner map using TS_PARTY_COLORS
        agg_gen = df_nacional.groupby(["nombre_estado", "municipio", "_join_key", "party_key"],
                                       as_index=False)["votes"].sum()
        idx_win = agg_gen.groupby("_join_key")["votes"].idxmax()
        winners = agg_gen.loc[idx_win].copy()
        winners["_color"] = winners["party_key"].map(
            lambda k: TS_PARTY_COLORS.get(k, _ts_fallback_color(k))
        )
        winners["_label"] = winners["nombre_estado"] + " — " + winners["municipio"]
        all_ids = {f["id"] for f in geo["features"]}
        winners = winners[winners["_join_key"].isin(all_ids)]

        party_keys_present = sorted(winners["party_key"].unique())
        fig = go.Figure()
        for pk in party_keys_present:
            subset = winners[winners["party_key"] == pk]
            color  = TS_PARTY_COLORS.get(pk, _ts_fallback_color(pk))
            ids_set = set(subset["_join_key"])
            geo_sub = {"type": "FeatureCollection",
                       "features": [f for f in geo["features"] if f["id"] in ids_set]}
            fig.add_trace(go.Choroplethmapbox(
                geojson=geo_sub, locations=subset["_join_key"],
                z=[1] * len(subset),
                colorscale=[[0, color], [1, color]],
                showscale=False, marker_opacity=0.82,
                marker_line_width=0.25, marker_line_color="rgba(255,255,255,0.1)",
                hovertext=subset["_label"],
                customdata=subset["votes"].values,
                hovertemplate="<b>%{hovertext}</b><br>Ganador: " + pk + "<br>Votos: %{customdata:,}<extra></extra>",
                name=pk, showlegend=True,
            ))
        fig.update_layout(
            mapbox=dict(style="carto-darkmatter", zoom=4.1, center={"lat": 23.6, "lon": -102.5}),
            legend=dict(orientation="h", yanchor="top", y=-0.04, xanchor="center", x=0.5,
                        font=dict(size=10), itemsizing="constant"),
            title=dict(text=f"<b>Ganador por Municipio · {election_label(election_sel)}</b>",
                       font=dict(family="IBM Plex Mono", size=14)),
            margin=dict(l=0, r=0, t=50, b=80), height=600,
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig, use_container_width=True)
        st.caption(f"Mapa de ganador por municipio. Elecciones sin estructura ternaria definida (DIP/SEN) muestran el partido con más votos en cada municipio.")
