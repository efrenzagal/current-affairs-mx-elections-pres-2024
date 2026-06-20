"""
INE PREP 2024 - Explorador de Resultados Electorales  (Parquet-backed v2)
Identical feature set to v1 — backend only change (CSV → materialized Parquets).

Run:
    python pipeline.py all          # ingest + materialize first
    streamlit run ine_explorer_streamlit.py

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
    page_title="INE PREP 2024 · Explorador",
    page_icon="🗳️",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans:wght@300;400;600&display=swap');
html, body, [class*="css"] { font-family: 'IBM Plex Sans', sans-serif; }
h1, h2, h3 { font-family: 'IBM Plex Mono', monospace; letter-spacing: -0.02em; }
.metric-card {
    background: #F7F7F5;
    border-left: 3px solid #C84B31;
    padding: 1rem 1.2rem;
    border-radius: 2px;
    margin-bottom: 0.5rem;
}
.metric-card .label {
    font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.08em;
    color: #888; font-family: 'IBM Plex Mono', monospace;
}
.metric-card .value {
    font-size: 1.6rem; font-weight: 600; color: #1A1A1A;
    font-family: 'IBM Plex Mono', monospace;
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

MAP_METRICS = {
    "Ganador":       {"label": "Ganador (por municipio)",        "kind": "winner"},
    "% SHH":         {"label": "% Sheinbaum (SHH)",             "kind": "continuous", "col": "pct_shh",       "scale": [[0,"#fff0f0"],[0.5,"#8B0000"],[1,"#4a0000"]], "range": [20,90],  "cb_title": "SHH %",          "fmt": ":.1f"},
    "% FCM":         {"label": "% Galvez (FCM)",                "kind": "continuous", "col": "pct_fcm",       "scale": [[0,"#f0f6ff"],[0.5,"#1E90FF"],[1,"#0a3d7a"]], "range": [5,60],   "cb_title": "FCM %",          "fmt": ":.1f"},
    "% MC":          {"label": "% Alvarez Maynez (MC)",         "kind": "continuous", "col": "pct_mc",        "scale": [[0,"#fff8f0"],[0.5,"#FF8C00"],[1,"#7a3d00"]], "range": [0,30],   "cb_title": "MC %",           "fmt": ":.1f"},
    "Participacion": {"label": "Participacion electoral (%)",   "kind": "continuous", "col": "participacion", "scale": [[0,"#f5f5f0"],[0.5,"#4CAF50"],[1,"#1B5E20"]], "range": [30,90],  "cb_title": "Participacion %","fmt": ":.1f"},
    "Votos totales": {"label": "Votos totales emitidos",        "kind": "continuous", "col": "total_votos",   "scale": [[0,"#fafaf5"],[0.5,"#9C27B0"],[1,"#4A148C"]], "range": None,     "cb_title": "Votos",          "fmt": ":,"},
    "Lista nominal": {"label": "Lista nominal (electores reg.)","kind": "continuous", "col": "lista_nominal", "scale": [[0,"#fafaf5"],[0.5,"#607D8B"],[1,"#1C313A"]], "range": None,     "cb_title": "Electores",      "fmt": ":,"},
}

# ── Data loading (cached) ──────────────────────────────────────────────────────

@st.cache_data(show_spinner=False)
def get_available_elections() -> list[str]:
    if not MATERIALIZED_DIR.exists():
        return []
    return sorted(
        f.stem.replace("view_nacional_", "")
        for f in MATERIALIZED_DIR.glob("view_nacional_*.parquet")
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
            "Usando GeoJSON sin procesar. Ejecuta `python pipeline.py materialize` "
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

def metric_card(label, value):
    st.markdown(f"""<div class="metric-card">
        <div class="label">{label}</div>
        <div class="value">{value}</div>
    </div>""", unsafe_allow_html=True)

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
                          n_bubbles: int = None):
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
        mode="markers+text",
        marker=dict(size=agg["_size"], color=agg["_color"], opacity=0.72,
                    line=dict(width=0.8, color="white")),
        text=agg["_label"],
        textposition="top center",
        textfont=dict(size=9, family="IBM Plex Sans", color="white"),
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
        title=f"Distribucion ternaria de votos - {title_suffix} (top {len(agg)})",
        xaxis=dict(visible=False, range=[-0.12, 1.12]),
        yaxis=dict(visible=False, scaleanchor="x", scaleratio=1,
                   range=[-0.15, sqrt3_2 + 0.12]),
        height=580,
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
    query_estado/query_municipio/query_seccion in pipeline.py.
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
    import math
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
    zoom = max(3.5, min(10.0, np.log2(360 / span) + 0.5)) if span > 0 else 5.0
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
        "participacion": "Participacion %",
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
_INFO_METRICS = {k: MAP_METRICS[k] for k in ["Participacion", "Votos totales", "Lista nominal"]}


def render_mexico_map(df: pd.DataFrame, map_key_suffix: str = "nacional"):
    """
    Three-panel map layout:
      Row 1 (full width) — discrete winner map, always shown
      Row 2 (two columns):
        Left  — candidate heatmap (user picks candidate)
        Right — electoral info heatmap (user picks metric)
    """
    geo = load_municipios_geojson()
    if not geo:
        st.warning(
            "No se encontro el GeoJSON de municipios. Descargalo con:\n\n"
            "`curl -L -o municipios.geojson "
            "https://raw.githubusercontent.com/angelnmara/geojson/master/MunicipiosMexico.json`\n\n"
            "Luego ejecuta `python pipeline.py materialize` para pre-procesarlo."
        )
        return
    agg = _build_map_agg(df)

    geo_keys    = {f["id"] for f in geo["features"]}
    unmatched   = len(agg) - len(agg[agg["_join_key"].isin(geo_keys)])
    matched_ids = set(agg[agg["_join_key"].isin(geo_keys)]["_join_key"])
    center, zoom = _bbox_to_zoom_center(
        [f for f in geo["features"] if f["id"] in matched_ids]
    )

    # Shared opacity slider — one per map instance
    opacity = st.slider(
        "Transparencia",
        min_value=0.05, max_value=1.0, value=0.25, step=0.05,
        key=f"map_opacity_{map_key_suffix}",
    )

    # ── Row 1: full-width winner map ───────────────────────────────────────────
    st.markdown('<div class="section-label">Ganador por Municipio</div>',
                unsafe_allow_html=True)
    fig_winner = _build_winner_fig(agg, geo, center, zoom, opacity)
    fig_winner.update_layout(**_MAP_BASE_LAYOUT, height=600)
    st.plotly_chart(fig_winner, use_container_width=True)

    # ── Row 2: two-column heatmaps ─────────────────────────────────────────────
    col_left, col_right = st.columns(2)

    with col_left:
        st.markdown('<div class="section-label">% Votos por Candidato</div>',
                    unsafe_allow_html=True)
        cand_sel = st.selectbox(
            "Candidato",
            options=list(_CAND_LABELS.keys()),
            format_func=lambda k: _CAND_LABELS[k],
            key=f"map_cand_{map_key_suffix}",
        )
        fig_cand = _build_continuous_fig(
            agg, geo, center, zoom, _CAND_METRICS[cand_sel], opacity
        )
        fig_cand.update_layout(**_MAP_BASE_LAYOUT, height=480)
        st.plotly_chart(fig_cand, use_container_width=True)

    with col_right:
        st.markdown('<div class="section-label">Informacion Electoral</div>',
                    unsafe_allow_html=True)
        info_sel = st.selectbox(
            "Metrica",
            options=list(_INFO_METRICS.keys()),
            format_func=lambda k: _INFO_METRICS[k]["label"],
            key=f"map_info_{map_key_suffix}",
        )
        fig_info = _build_continuous_fig(
            agg, geo, center, zoom, _INFO_METRICS[info_sel], opacity
        )
        fig_info.update_layout(**_MAP_BASE_LAYOUT, height=480)
        st.plotly_chart(fig_info, use_container_width=True)

    if unmatched > 0:
        st.caption(f"{unmatched} municipios sin geometria en el GeoJSON.")


# ── Results tab (shared across Estado / Municipio / Nacional) ──────────────────
# Order: Map → Ternary → Bar charts → Tables
# (Scorecard metrics are always rendered by the caller before this function)

def render_results_tab(df_raw: pd.DataFrame, page_level: str, election_id: str,
                       candidates_df: pd.DataFrame = None, id_distrito: Optional[int] = None):
    if candidates_df is None:
        candidates_df = pd.DataFrame()

    if page_level == "Nacional":
        df_estado_view    = load_view("estado",    election_id)
        df_municipio_view = load_view("municipio", election_id)

        # 1. MAP
        st.markdown("---")
        render_mexico_map(df_municipio_view, map_key_suffix="nacional")

        # 2. TERNARY
        st.markdown("---")
        col_a, col_b = st.columns(2)

        with col_a:
            st.markdown('<div class="section-label">Distribucion ternaria - por Estado</div>',
                        unsafe_allow_html=True)
            todos_estados = sorted(df_estado_view["nombre_estado"].dropna().unique())
            n_estados = st.slider(
                "Estados a mostrar (por volumen de votos)",
                min_value=2, max_value=max(len(todos_estados), 2),
                value=min(5, len(todos_estados)),
                key="slider_estados_ternary",
            )
            render_ternary_bubble(
                df_estado_view, "nombre_estado", "nombre_estado",
                "por Estado", n_bubbles=n_estados,
            )

        with col_b:
            st.markdown('<div class="section-label">Distribucion ternaria - Top municipios</div>',
                        unsafe_allow_html=True)
            todos_estados_mun  = sorted(df_municipio_view["nombre_estado"].dropna().unique())
            estados_mun_filter = st.multiselect(
                "Filtrar por estado (vacio = todos)",
                options=todos_estados_mun, default=[],
                key="muns_ternary_estados",
                help="Restringe los municipios a los estados seleccionados antes de elegir el top-N.",
            )
            df_mun = (
                df_municipio_view[df_municipio_view["nombre_estado"].isin(estados_mun_filter)]
                if estados_mun_filter else df_municipio_view
            )
            max_muns = df_mun.groupby(["nombre_estado", "municipio"]).ngroups
            n_muns = st.slider(
                "Municipios a mostrar (por volumen de votos)",
                min_value=2, max_value=min(max(max_muns, 2), 200),
                value=min(5, max_muns),
                key="slider_muns_ternary",
            )
            render_ternary_bubble(
                df_mun, ["nombre_estado", "municipio"], ["municipio", "nombre_estado"],
                "Top municipios (Nacional)", n_bubbles=n_muns,
            )

        # 3. BAR CHARTS
        st.markdown("---")
        render_both_charts(df_raw)

        # 4. TABLES
        st.markdown("---")
        col1, col2 = st.columns(2)
        with col1:
            render_results_table(df_estado_view, "nombre_estado", "Estado",
                                 election_id, candidates_df, id_distrito)
        with col2:
            render_results_table(df_municipio_view, ["nombre_estado", "municipio"], "Municipio",
                                 election_id, candidates_df, id_distrito)

    elif page_level == "Estado":
        # 1. MAP
        st.markdown("---")
        render_mexico_map(df_raw, map_key_suffix="estado")

        # 2. TERNARY
        st.markdown("---")
        st.markdown('<div class="section-label">Distribucion ternaria por Municipio</div>',
                    unsafe_allow_html=True)
        max_muns = df_raw["municipio"].nunique()
        n_muns = st.slider(
            "Municipios a mostrar (por volumen de votos)",
            min_value=2, max_value=max(max_muns, 2),
            value=min(5, max_muns),
            key="slider_estado_muns_ternary",
        )
        render_ternary_bubble(
            df_raw, "municipio", "municipio", "por Municipio", n_bubbles=n_muns,
        )

        # 3. BAR CHARTS
        st.markdown("---")
        render_both_charts(df_raw)

        # 4. TABLES
        st.markdown("---")
        col1, col2 = st.columns(2)
        with col1:
            render_results_table(df_raw, "municipio", "Municipio",
                                 election_id, candidates_df, id_distrito)
        with col2:
            render_results_table(df_raw, "seccion", "Seccion",
                                 election_id, candidates_df, id_distrito)

    elif page_level == "Municipio":
        # 1. MAP (single polygon — useful for geo-join validation)
        st.markdown("---")
        render_mexico_map(df_raw, map_key_suffix="municipio")

        # 2. No ternary at municipio level (single unit)

        # 3. BAR CHARTS
        st.markdown("---")
        render_both_charts(df_raw)

        # 4. TABLES
        st.markdown("---")
        col1, col2 = st.columns(2)
        with col1:
            render_results_table(df_raw, "seccion", "Seccion",
                                 election_id, candidates_df, id_distrito)


# ── App shell ──────────────────────────────────────────────────────────────────

st.markdown("## INE PREP 2024")
st.markdown("##### Explorador de resultados electorales")

elections = get_available_elections()
if not elections:
    st.error(
        "No se encontraron archivos Parquet materializados. "
        "Ejecuta `python pipeline.py all` primero."
    )
    st.stop()

default_election = "PRE_2024" if "PRE_2024" in elections else elections[0]
election_sel = st.sidebar.selectbox(
    "Eleccion", elections, index=elections.index(default_election)
)

# Load candidate names once (used by tables across all pages)
candidates_df = load_candidates()

st.sidebar.markdown("---")
page = st.sidebar.selectbox(
    "Granularidad", ["Nacional", "Estado", "Municipio", "Casilla"]
)


# ── PAGE: NACIONAL ─────────────────────────────────────────────────────────────
if page == "Nacional":
    df_nac = load_view("nacional", election_sel)
    if df_nac.empty:
        st.error("Sin datos. Ejecuta pipeline.py primero.")
        st.stop()

    meta_row  = df_nac.iloc[0]
    num_est   = int(meta_row.get("num_estados",   32))
    num_dist  = int(meta_row.get("num_distritos", 300))
    num_mun   = int(meta_row.get("num_municipios", 0))
    num_cas   = int(meta_row.get("num_casillas",   0))
    total_v   = int(
        df_nac.drop_duplicates(["election_id"])["total_votos"].sum()
        if "election_id" in df_nac.columns
        else df_nac["total_votos"].iloc[0]
    )
    lista_nom = int(meta_row.get("lista_nominal_part", 0))
    part_pct  = total_v / lista_nom * 100 if lista_nom > 0 else 0
    # FIX: sum num_votos_nulos across all party rows deduplicated by election_id,
    # not just iloc[0] which was only the first party's row.
    nulos_raw = int(df_nac.drop_duplicates("election_id")["num_votos_nulos"].sum())
    nulos_pct = nulos_raw / total_v * 100 if total_v > 0 else 0

    header_badge([
        "Mexico",
        f"{num_est} estados",
        f"{num_dist} distritos",
        f"{num_mun:,} municipios",
        f"{num_cas:,} actas",
    ])

    c1, c2, c3, c4 = st.columns(4)
    with c1: metric_card("Total votos",   fmt_num(total_v))
    with c2: metric_card("Lista nominal", fmt_num(lista_nom))
    with c3: metric_card("Participacion", fmt_pct(part_pct))
    with c4: metric_card("Votos nulos",   fmt_pct(nulos_pct))

    render_results_tab(df_nac, "Nacional", election_sel, candidates_df)


# ── PAGE: ESTADO ───────────────────────────────────────────────────────────────
elif page == "Estado":
    df_est_full = load_view("estado", election_sel)
    if df_est_full.empty:
        st.error("Sin datos. Ejecuta pipeline.py primero.")
        st.stop()

    st.sidebar.markdown("---")
    st.sidebar.markdown('<div class="section-label">Unidad de analisis</div>',
                        unsafe_allow_html=True)

    estados    = sorted(df_est_full["nombre_estado"].dropna().unique())
    default_e  = next((e for e in estados if "CIUDAD" in e.upper()), estados[0])
    estado_sel = st.sidebar.selectbox(
        "Estado", estados, index=estados.index(default_e))
    df_view = df_est_full[df_est_full["nombre_estado"] == estado_sel]

    if df_view.empty:
        st.info("Sin datos para este estado.")
        st.stop()

    meta_row  = df_view.drop_duplicates("nombre_estado").iloc[0]
    num_dist  = int(meta_row.get("num_distritos",  0))
    num_mun   = int(meta_row.get("num_municipios", 0))
    num_sec   = int(meta_row.get("num_secciones",  0))
    num_cas   = int(meta_row.get("num_casillas",   0))
    total_v   = int(df_view.drop_duplicates("nombre_estado")["total_votos"].sum())
    lista_nom = int(meta_row.get("lista_nominal_part", 0))
    part_pct  = total_v / lista_nom * 100 if lista_nom > 0 else 0
    nulos_raw = int(df_view.drop_duplicates("nombre_estado")["num_votos_nulos"].sum())
    nulos_pct = nulos_raw / total_v * 100 if total_v > 0 else 0

    header_badge([
        estado_sel,
        f"{num_dist} distritos federales",
        f"{num_mun} municipios",
        f"{num_sec} secciones",
        f"{num_cas} actas",
    ])

    c1, c2, c3, c4 = st.columns(4)
    with c1: metric_card("Total votos",   fmt_num(total_v))
    with c2: metric_card("Lista nominal", fmt_num(lista_nom))
    with c3: metric_card("Participacion", fmt_pct(part_pct))
    with c4: metric_card("Votos nulos",   fmt_pct(nulos_pct))

    # For estado-level map and ternary we use the municipio view filtered to this state
    df_mun_view = load_view("municipio", election_sel)
    df_mun_view = df_mun_view[df_mun_view["nombre_estado"] == estado_sel]

    render_results_tab(df_mun_view, "Estado", election_sel, candidates_df)


# ── PAGE: MUNICIPIO ────────────────────────────────────────────────────────────
elif page == "Municipio":
    df_mun_full = load_view("municipio", election_sel)
    if df_mun_full.empty:
        st.error("Sin datos. Ejecuta pipeline.py primero.")
        st.stop()

    st.sidebar.markdown("---")
    st.sidebar.markdown('<div class="section-label">Unidad de analisis</div>',
                        unsafe_allow_html=True)

    estados    = sorted(df_mun_full["nombre_estado"].dropna().unique())
    default_e  = next((e for e in estados if "CIUDAD" in e.upper()), estados[0])
    estado_sel = st.sidebar.selectbox(
        "Estado", estados, index=estados.index(default_e))

    df_e       = df_mun_full[df_mun_full["nombre_estado"] == estado_sel]
    municipios = sorted(df_e["municipio"].dropna().unique())
    mun_sel    = st.sidebar.selectbox("Municipio", municipios)
    df_view    = df_e[df_e["municipio"] == mun_sel]

    if df_view.empty:
        st.info("Sin datos para este municipio.")
        st.stop()

    meta_row  = df_view.drop_duplicates("municipio").iloc[0]
    num_cas   = int(meta_row.get("num_casillas",  0))
    num_sec   = int(meta_row.get("num_secciones", 0))
    total_v   = int(df_view.drop_duplicates("municipio")["total_votos"].sum())
    lista_nom = int(meta_row.get("lista_nominal_part", 0))
    part_pct  = total_v / lista_nom * 100 if lista_nom > 0 else 0
    nulos_raw = int(df_view.drop_duplicates("municipio")["num_votos_nulos"].sum())
    nulos_pct = nulos_raw / total_v * 100 if total_v > 0 else 0

    header_badge([
        estado_sel, f"Municipio: {mun_sel}",
        f"{num_sec} seccion(es)", f"{num_cas} acta(s)",
    ])

    c1, c2, c3, c4 = st.columns(4)
    with c1: metric_card("Total votos",   fmt_num(total_v))
    with c2: metric_card("Lista nominal", fmt_num(lista_nom))
    with c3: metric_card("Participacion", fmt_pct(part_pct))
    with c4: metric_card("Votos nulos",   fmt_pct(nulos_pct))

    render_results_tab(df_view, "Municipio", election_sel, candidates_df)

    # Secciones table (loaded separately, appended after the main results block)
    df_sec_view = load_view("seccion", election_sel)
    df_sec_view = df_sec_view[
        (df_sec_view["nombre_estado"] == estado_sel) &
        (df_sec_view["municipio"] == mun_sel)
    ]
    if not df_sec_view.empty:
        st.markdown("---")
        render_results_table(df_sec_view, "seccion", "Seccion",
                             election_sel, candidates_df)


# ── PAGE: CASILLA ──────────────────────────────────────────────────────────────
elif page == "Casilla":
    df_cas_full = load_view("casilla", election_sel)
    if df_cas_full.empty:
        st.error("Sin datos. Ejecuta pipeline.py primero.")
        st.stop()

    st.sidebar.markdown("---")
    st.sidebar.markdown('<div class="section-label">Unidad de analisis</div>',
                        unsafe_allow_html=True)

    estados        = sorted(df_cas_full["nombre_estado"].dropna().unique())
    default_estado = next((e for e in estados if "CIUDAD" in e.upper()), estados[0])
    estado_sel     = st.sidebar.selectbox("Estado", estados,
                                          index=estados.index(default_estado))
    df_e = df_cas_full[df_cas_full["nombre_estado"] == estado_sel]

    municipios = sorted(df_e["municipio"].dropna().unique())
    mun_sel    = st.sidebar.selectbox("Municipio", municipios)
    df_em      = df_e[df_e["municipio"] == mun_sel]

    secciones   = sorted(df_em["seccion"].dropna().unique().astype(int))
    seccion_sel = st.sidebar.selectbox("Seccion", secciones)
    df_s        = df_em[df_em["seccion"] == seccion_sel]

    casillas    = sorted(df_s["casilla_id"].dropna().unique())
    casilla_sel = st.sidebar.selectbox("ID Casilla", casillas)
    df_cas      = df_s[df_s["casilla_id"] == casilla_sel]

    st.sidebar.markdown("---")
    st.sidebar.markdown('<div class="section-label">Filtro tipo / extension</div>',
                        unsafe_allow_html=True)
    tipos_av  = sorted(df_cas["tipo_casilla"].dropna().unique())
    tipos_sel = st.sidebar.multiselect(
        "Tipo de casilla", tipos_av, default=tipos_av,
        format_func=lambda x: f"{x} - {TIPO_LABELS.get(x, x)}")
    df_tf = df_cas[df_cas["tipo_casilla"].isin(tipos_sel)] if tipos_sel else df_cas.copy()

    exts_av    = sorted(df_tf["ext_contigua"].dropna().unique().astype(int))
    ext_labels = {0: "0 - Principal"} | {i: f"{i} - Extension {i}" for i in exts_av if i > 0}
    exts_sel   = st.sidebar.multiselect(
        "Extension contigua", exts_av, default=exts_av,
        format_func=lambda x: ext_labels.get(x, str(x)))
    df_view = df_tf[df_tf["ext_contigua"].isin(exts_sel)].copy() if exts_sel else df_tf.copy()

    if df_view.empty:
        st.info("Sin datos para esta seleccion.")
        st.stop()

    row = df_view.iloc[0]
    header_badge([
        f"Estado {int(row['id_estado'])}", estado_sel,
        f"Municipio: {mun_sel}",
        f"Seccion {seccion_sel}",
        f"{df_view['casilla_id'].nunique()} casilla(s)",
    ])

    # Scorecards
    st.markdown('<div class="section-label">Metricas principales</div>', unsafe_allow_html=True)
    df_part   = df_view[(df_view["tipo_casilla"] != "S") & (df_view["seccion"] > 0)]
    lista_nom = int(df_part.drop_duplicates("casilla_id")["lista_nominal"].sum())
    total_votos = int(df_view.drop_duplicates("casilla_id")["total_votos"].sum())
    part_pct    = (
        df_part.drop_duplicates("casilla_id")["total_votos"].sum()
        / lista_nom * 100 if lista_nom > 0 else 0
    )
    nulos_raw = int(df_view.drop_duplicates("casilla_id")["num_votos_nulos"].sum())
    nulos_pct = nulos_raw / total_votos * 100 if total_votos > 0 else 0

    c1, c2, c3, c4 = st.columns(4)
    with c1: metric_card("Total votos",   fmt_num(total_votos))
    with c2: metric_card("Lista nominal", fmt_num(lista_nom))
    with c3: metric_card("Participacion", fmt_pct(part_pct))
    with c4: metric_card("Votos nulos",   fmt_pct(nulos_pct))

    st.markdown("---")

    # Location metadata grid
    g1, g2, g3 = st.columns(3)
    with g1:
        st.markdown('<div class="section-label">Ubicacion</div>', unsafe_allow_html=True)
        st.markdown(f"| | |\n|---|---|\n| Estado | {estado_sel} |\n"
                    f"| Municipio | {mun_sel} |\n| Seccion | {seccion_sel} |")
    with g2:
        st.markdown('<div class="section-label">Distrito federal</div>', unsafe_allow_html=True)
        st.markdown(f"| | |\n|---|---|\n| Distrito | {int(row['id_distrito_federal'])} |\n"
                    f"| Cabecera | {row['cabecera_distrital_federal']} |")
    with g3:
        st.markdown('<div class="section-label">Casilla</div>', unsafe_allow_html=True)
        tipos_str = ", ".join(sorted(df_view["tipo_casilla"].unique()))
        urna      = "Si" if int(row["urna_electronica"] or 0) == 1 else "No"
        st.markdown(f"| | |\n|---|---|\n| ID Casilla | {casilla_sel} |\n"
                    f"| Tipos | {tipos_str} |\n| Urna electronica | {urna} |")
    st.markdown("")

    # Bar charts
    st.markdown("---")
    render_both_charts(df_view)

    # Raw actas expander
    st.markdown("---")
    with st.expander("Ver actas (raw)"):
        pivot = df_view.pivot_table(
            index=["casilla_id", "tipo_casilla", "id_casilla", "ext_contigua",
                   "seccion", "lista_nominal", "num_votos_validos",
                   "num_votos_nulos", "total_votos", "estatus_acta"],
            columns="party_key",
            values="votes",
            aggfunc="sum",
        ).reset_index()
        pivot.columns.name = None
        st.dataframe(pivot, use_container_width=True, height=240)