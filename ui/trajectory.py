"""
Trayectoria ideológica por municipio — ternary across all PRE elections.
Vertices: Left (PRD/Morena), Right (PAN), Center (PRI/MC).
Call render_trajectory() from the main app.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from ui.common import IDEOLOGY_MAP, MATERIALIZED_DIR

# ── Constants ──────────────────────────────────────────────────────────────────

VERTEX_COLORS = {
    "L": "#8B0000",
    "R": "#1E90FF",
    "C": "#006847",
}
PRE_YEARS = [1994, 2000, 2006, 2012, 2018, 2024]
YEAR_COLORS = {
    1994: "#B0C4DE",
    2000: "#88AACC",
    2006: "#5590C0",
    2012: "#2B7BB0",
    2018: "#0D5FA0",
    2024: "#C84B31",
}

# Equilateral triangle: L=bottom-left(0,0), R=bottom-right(1,0), C=top(0.5, √3/2)
_S32 = np.sqrt(3) / 2


# ── Data loading ───────────────────────────────────────────────────────────────

@st.cache_data(show_spinner=False)
def load_trajectory_data() -> pd.DataFrame:
    frames = []
    for year in PRE_YEARS:
        path = MATERIALIZED_DIR / f"view_municipio_PRE_{year}.parquet"
        if not path.exists():
            continue
        df = pd.read_parquet(path).dropna(subset=["municipio"])
        if df.empty:
            continue
        df["bloc"] = df["party_key"].map(IDEOLOGY_MAP)
        df = df.dropna(subset=["bloc"])

        geo_cols = ["id_estado", "nombre_estado", "municipio"]
        tv = (
            df.drop_duplicates(geo_cols)[geo_cols + ["total_votos"]]
        )
        piv = (
            df.pivot_table(index=geo_cols, columns="bloc", values="votes",
                           aggfunc="sum", fill_value=0)
            .reset_index()
        )
        for col in ("L", "R", "C"):
            if col not in piv.columns:
                piv[col] = 0
        agg = tv.merge(piv, on=geo_cols)
        agg["year"] = year
        total = (agg["L"] + agg["R"] + agg["C"]).replace(0, float("nan"))
        agg["pct_L"] = (agg["L"] / total * 100).round(1)
        agg["pct_R"] = (agg["R"] / total * 100).round(1)
        agg["pct_C"] = (agg["C"] / total * 100).round(1)
        frames.append(agg.dropna(subset=["pct_L"]))

    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _ternary_xy(pct_l, pct_r, pct_c):
    """Map L/R/C pct → (x, y) in equilateral triangle."""
    total = pct_l + pct_r + pct_c
    l, r, c = pct_l / total, pct_r / total, pct_c / total
    return r + c * 0.5, c * _S32


# ── Ternary figure ─────────────────────────────────────────────────────────────

def _build_ternary(df: pd.DataFrame, mun_sel: str) -> go.Figure:
    fig = go.Figure()

    # Triangle
    fig.add_trace(go.Scatter(
        x=[0, 1, 0.5, 0], y=[0, 0, _S32, 0],
        mode="lines", line=dict(color="rgba(255,255,255,0.5)", width=1.5),
        hoverinfo="skip", showlegend=False,
    ))

    # Grid lines parallel to each side at 1/3 and 2/3
    for f in (1/3, 2/3):
        # parallel to base (horizontal)
        fig.add_trace(go.Scatter(
            x=[f * 0.5, f * 0.5 + (1 - f)], y=[f * _S32, f * _S32],
            mode="lines", line=dict(color="rgba(180,180,180,0.18)", width=0.8, dash="dot"),
            hoverinfo="skip", showlegend=False,
        ))
        # parallel to left edge
        fig.add_trace(go.Scatter(
            x=[f, f + (1-f)*0.5], y=[0, (1-f)*_S32],
            mode="lines", line=dict(color="rgba(180,180,180,0.18)", width=0.8, dash="dot"),
            hoverinfo="skip", showlegend=False,
        ))
        # parallel to right edge
        fig.add_trace(go.Scatter(
            x=[1-f, (1-f)*0.5], y=[0, (1-f)*_S32],
            mode="lines", line=dict(color="rgba(180,180,180,0.18)", width=0.8, dash="dot"),
            hoverinfo="skip", showlegend=False,
        ))

    # Centroid marker
    cx, cy = _ternary_xy(1, 1, 1)
    fig.add_trace(go.Scatter(
        x=[cx], y=[cy], mode="markers",
        marker=dict(symbol="cross", size=9, color="rgba(160,160,160,0.5)",
                    line=dict(width=1.5, color="rgba(160,160,160,0.7)")),
        hoverinfo="skip", showlegend=False,
    ))

    # Trajectory line
    if len(df) > 1:
        fig.add_trace(go.Scatter(
            x=df["tx"], y=df["ty"], mode="lines",
            line=dict(color="rgba(220,220,220,0.35)", width=1.8, dash="dot"),
            hoverinfo="skip", showlegend=False,
        ))

    # Year bubbles
    max_v = df["total_votos"].replace(0, float("nan")).max() or 1
    for _, row in df.iterrows():
        yr = int(row["year"])
        size = float(max(12, row["total_votos"] / max_v * 52))
        fig.add_trace(go.Scatter(
            x=[row["tx"]], y=[row["ty"]],
            mode="markers+text",
            marker=dict(size=size, color=YEAR_COLORS.get(yr, "#888"),
                        opacity=0.90, line=dict(width=1.8, color="white")),
            text=[str(yr)],
            textposition="top center",
            textfont=dict(size=11, family="IBM Plex Mono",
                          color=YEAR_COLORS.get(yr, "#888")),
            name=str(yr),
            hovertemplate=(
                f"<b>{yr}</b><br>"
                f"Izquierda: {row['pct_L']:.1f}%<br>"
                f"Derecha:   {row['pct_R']:.1f}%<br>"
                f"Centro:    {row['pct_C']:.1f}%<br>"
                f"Votos: {int(row['total_votos']):,}<extra></extra>"
            ),
            showlegend=True,
        ))

    # Vertex labels (outside triangle corners)
    fig.add_trace(go.Scatter(
        x=[-0.07, 1.07, 0.5],
        y=[-0.10, -0.10, _S32 + 0.10],
        mode="text",
        text=[
            "<b>Izquierda</b><br><span style='font-size:9px'>PRD · Morena · PT</span>",
            "<b>Derecha</b><br><span style='font-size:9px'>PAN</span>",
            "<b>Centro</b><br><span style='font-size:9px'>PRI · MC</span>",
        ],
        textfont=dict(size=12, family="IBM Plex Mono",
                      color=[VERTEX_COLORS["L"], VERTEX_COLORS["R"], VERTEX_COLORS["C"]]),
        hoverinfo="skip", showlegend=False,
    ))

    fig.update_layout(
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        font_family="IBM Plex Sans", font_color="#888",
        title=dict(
            text=f"<b>{mun_sel}</b>",
            font=dict(family="IBM Plex Mono", size=13),
        ),
        xaxis=dict(visible=False, range=[-0.22, 1.22]),
        yaxis=dict(visible=False, range=[-0.20, _S32 + 0.22],
                   scaleanchor="x", scaleratio=1),
        height=520,
        legend=dict(orientation="h", yanchor="top", y=-0.02,
                    xanchor="center", x=0.5,
                    font=dict(size=11, family="IBM Plex Mono")),
        margin=dict(l=10, r=10, t=50, b=10),
    )
    return fig


# ── Trend line figure ──────────────────────────────────────────────────────────

def _build_trend(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    for col, color, label in [
        ("pct_L", VERTEX_COLORS["L"], "Izquierda"),
        ("pct_R", VERTEX_COLORS["R"], "Derecha"),
        ("pct_C", VERTEX_COLORS["C"], "Centro"),
    ]:
        fig.add_trace(go.Scatter(
            x=df["year"], y=df[col],
            mode="lines+markers", name=label,
            line=dict(color=color, width=2.5),
            marker=dict(size=8, color=color, line=dict(width=1.5, color="white")),
            hovertemplate=f"<b>{label}</b><br>%{{x}}: %{{y:.1f}}%<extra></extra>",
        ))

    fig.update_layout(
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        font_family="IBM Plex Sans", font_color="#888",
        title=dict(text="Tendencia por bloque",
                   font=dict(family="IBM Plex Mono", size=13)),
        xaxis=dict(
            title="Año", tickvals=df["year"].tolist(),
            tickfont=dict(family="IBM Plex Mono"),
            gridcolor="rgba(150,150,150,0.15)",
            linecolor="rgba(150,150,150,0.3)",
        ),
        yaxis=dict(
            title="% votos (entre L+D+C)",
            range=[0, 100],
            gridcolor="rgba(150,150,150,0.15)",
        ),
        legend=dict(orientation="h", yanchor="bottom", y=-0.25,
                    xanchor="center", x=0.5),
        height=520,
        margin=dict(l=50, r=20, t=50, b=60),
    )
    return fig


# ── Main render ────────────────────────────────────────────────────────────────

def render_trajectory():
    st.markdown('<div class="section-label">Trayectoria ideológica por municipio</div>',
                unsafe_allow_html=True)

    df_all = load_trajectory_data()
    if df_all.empty:
        st.error("No se encontraron datos. Ejecuta el pipeline de ingesta primero.")
        return

    col_e, col_m = st.columns([1, 1])
    with col_e:
        estados = sorted(df_all["nombre_estado"].dropna().unique())
        default_e = next((e for e in estados if "CIUDAD" in e.upper()), estados[0])
        estado_sel = st.selectbox("Estado", estados,
                                  index=estados.index(default_e), key="traj_estado")
    with col_m:
        municipios = sorted(
            df_all[df_all["nombre_estado"] == estado_sel]["municipio"].dropna().unique()
        )
        mun_sel = st.selectbox("Municipio", municipios, key="traj_mun")

    df = df_all[
        (df_all["nombre_estado"] == estado_sel) &
        (df_all["municipio"] == mun_sel)
    ].sort_values("year").reset_index(drop=True)

    if df.empty:
        st.info("Sin datos para este municipio.")
        return

    df["tx"], df["ty"] = zip(*df.apply(
        lambda r: _ternary_xy(r["pct_L"], r["pct_R"], r["pct_C"]), axis=1
    ))

    col_l, col_r = st.columns([1, 1], gap="large")
    with col_l:
        st.plotly_chart(_build_ternary(df, mun_sel), use_container_width=True)
    with col_r:
        st.plotly_chart(_build_trend(df), use_container_width=True)

    missing = [y for y in PRE_YEARS if y not in df["year"].tolist()]
    if missing:
        st.caption(
            f"Años sin datos para este municipio: {', '.join(map(str, missing))}. "
            "Los porcentajes excluyen partidos menores no mapeados y votos nulos."
        )
