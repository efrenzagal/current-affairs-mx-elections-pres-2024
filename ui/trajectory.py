"""
Trayectoria ideológica por municipio — ternary across all PRE elections.
Vertices: Left (PRD/Morena), Right (PAN), Center (PRI/MC).
Call render_trajectory() from the main app.
"""

from __future__ import annotations

import random
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from ui.charts import render_hist_both_charts
from ui.common import (
    CATEGORY_COLORS, CYCLE_BLOCS, IDEOLOGY_MAP, MATERIALIZED_DIR,
    _norm, classify_ternary, fmt_num, fmt_pct, safe_int,
)
from ui.tables import header_badge

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
    label_frames = []
    for year in PRE_YEARS:
        path = MATERIALIZED_DIR / f"view_municipio_PRE_{year}.parquet"
        if not path.exists():
            continue
        df = pd.read_parquet(path).dropna(subset=["municipio"])
        if df.empty:
            continue
        df["bloc"] = df["party_key"].map(IDEOLOGY_MAP)
        df = df.dropna(subset=["bloc"])
        df["municipio_key"] = df["municipio"].map(_norm)

        # Historical files vary in capitalization and accents (for example,
        # COQUIMATLAN in 1994 vs. Coquimatlán in 2006).  Use a normalized
        # municipality key so every cycle contributes to one trajectory.
        geo_cols = ["id_estado", "municipio_key"]
        labels = df[["id_estado", "municipio_key", "nombre_estado", "municipio"]].drop_duplicates()
        labels["year"] = year
        label_frames.append(labels)
        tv = (
            df.groupby(geo_cols, as_index=False)["total_votos"].max()
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
        # Some historical files do not report total_votos consistently. The
        # mapped bloc total is a safe display fallback and prevents a missing
        # source value from breaking the chart tooltip.
        agg["total_votos"] = agg["total_votos"].fillna(total)
        frames.append(agg.dropna(subset=["pct_L"]))

    if not frames:
        return pd.DataFrame()

    data = pd.concat(frames, ignore_index=True)
    # Prefer the most recent spelling for the user-facing state/municipality
    # labels while preserving the normalized key solely for matching.
    labels = (
        pd.concat(label_frames, ignore_index=True)
        .sort_values("year", ascending=False)
        .drop_duplicates(["id_estado", "municipio_key"])
        .drop(columns="year")
    )
    return data.merge(labels, on=["id_estado", "municipio_key"], how="left")


@st.cache_data(show_spinner=False)
def load_raw_year(year: int) -> pd.DataFrame:
    path = MATERIALIZED_DIR / f"view_municipio_PRE_{year}.parquet"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_parquet(path).dropna(subset=["municipio"])
    df["municipio_key"] = df["municipio"].map(_norm)
    return df


def _ternary_xy(pct_l, pct_r, pct_c):
    """Map L/R/C pct → (x, y) in equilateral triangle."""
    total = pct_l + pct_r + pct_c
    l, r, c = pct_l / total, pct_r / total, pct_c / total
    return r + c * 0.5, c * _S32


# ── Ternary figure ─────────────────────────────────────────────────────────────

def _build_ternary(df: pd.DataFrame, mun_sel: str, show_bubbles: bool = True,
                    show_labels: bool = True, tie_radius: float = 8.0) -> go.Figure:
    fig = go.Figure()

    # Triangle
    fig.add_trace(go.Scatter(
        x=[0, 1, 0.5, 0], y=[0, 0, _S32, 0],
        mode="lines", line=dict(color="rgba(255,255,255,0.5)", width=1.5),
        hoverinfo="skip", showlegend=False,
    ))

    # "No-majority" zone: the medial triangle (edge midpoints). Outside of it,
    # in each corner, a single bloc holds >50% and can't be outvoted by a
    # coalition of the other two — that's the majority/"base" cutoff made
    # visible. Inside it, no bloc clears 50%.
    mid_lr = (0.5, 0.0)
    mid_rc = (0.75, _S32 / 2)
    mid_lc = (0.25, _S32 / 2)
    fig.add_trace(go.Scatter(
        x=[mid_lr[0], mid_rc[0], mid_lc[0], mid_lr[0]],
        y=[mid_lr[1], mid_rc[1], mid_lc[1], mid_lr[1]],
        mode="lines", fill="toself",
        fillcolor="rgba(150,150,150,0.07)",
        line=dict(color="rgba(200,200,200,0.35)", width=1, dash="dash"),
        hoverinfo="skip", showlegend=False,
    ))

    # "Empate" zone within the no-majority region: a circle around 33/33/33.
    cx0, cy0 = _ternary_xy(1, 1, 1)
    r = tie_radius / 100
    theta = np.linspace(0, 2 * np.pi, 60)
    fig.add_trace(go.Scatter(
        x=cx0 + r * np.cos(theta), y=cy0 + r * np.sin(theta),
        mode="lines", fill="toself",
        fillcolor="rgba(170,170,170,0.14)",
        line=dict(color="rgba(170,170,170,0.5)", width=1, dash="dot"),
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
        # Arrowheads showing chronological direction between consecutive years
        for (_, a), (_, b) in zip(df.iloc[:-1].iterrows(), df.iloc[1:].iterrows()):
            fig.add_annotation(
                x=b["tx"], y=b["ty"], ax=a["tx"], ay=a["ty"],
                xref="x", yref="y", axref="x", ayref="y",
                showarrow=True, arrowhead=3, arrowsize=1,
                arrowwidth=1.6, arrowcolor="rgba(220,220,220,0.55)",
                standoff=10 if show_bubbles else 4,
            )

    # Year bubbles / points, colored by ternary-zone category
    max_v = df["total_votos"].replace(0, float("nan")).max() or 1
    seen_categories = set()
    for _, row in df.iterrows():
        yr = int(row["year"])
        total_votos = 0 if pd.isna(row["total_votos"]) else int(row["total_votos"])
        category = row["category"]
        cat_color = CATEGORY_COLORS[category]
        if show_bubbles:
            size = float(max(12, total_votos / max_v * 52))
            marker = dict(size=size, color=YEAR_COLORS.get(yr, "#888"),
                          opacity=0.90, line=dict(width=2.5, color=cat_color))
        else:
            size = 10
            marker = dict(size=size, color=cat_color, opacity=0.95,
                          line=dict(width=1.5, color="white"))
        fig.add_trace(go.Scatter(
            x=[row["tx"]], y=[row["ty"]],
            mode="markers+text" if show_labels else "markers",
            marker=marker,
            text=[str(yr)] if show_labels else None,
            textposition="top center",
            textfont=dict(size=11, family="IBM Plex Mono",
                          color=YEAR_COLORS.get(yr, "#888") if show_bubbles else cat_color),
            hovertemplate=(
                f"<b>{yr}</b> · {category}<br>"
                f"Izquierda: {row['pct_L']:.1f}%<br>"
                f"Derecha:   {row['pct_R']:.1f}%<br>"
                f"Centro:    {row['pct_C']:.1f}%<br>"
                f"Votos: {total_votos:,}<extra></extra>"
            ),
            showlegend=False,
        ))
        seen_categories.add(category)

    # Category legend (only entries actually present for this municipio)
    for category in sorted(seen_categories):
        fig.add_trace(go.Scatter(
            x=[None], y=[None], mode="markers",
            marker=dict(size=10, color=CATEGORY_COLORS[category]),
            name=category, showlegend=True,
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

    # "Size" = biggest recorded turnout (total_votos) each unit has ever had,
    # used to rank the selectors so the largest municipios/states surface first.
    mun_size = df_all.groupby(["id_estado", "municipio_key"])["total_votos"].max()
    estado_size = mun_size.groupby("id_estado").sum()

    col_e, col_m = st.columns([1, 1])
    with col_e:
        estados = (
            df_all[["id_estado", "nombre_estado"]]
            .dropna().drop_duplicates()
        )
        estados["_size"] = estados["id_estado"].map(estado_size).fillna(0)
        estados = estados.sort_values("_size", ascending=False)
        state_names = estados["nombre_estado"].tolist()
        if "traj_estado" not in st.session_state:
            st.session_state.traj_estado = random.choice(state_names)
        estado_sel = st.selectbox("Estado", state_names, key="traj_estado")
        id_estado_sel = int(estados.loc[
            estados["nombre_estado"] == estado_sel, "id_estado"
        ].iloc[0])
    with col_m:
        df_estado = df_all[df_all["id_estado"] == id_estado_sel]
        mun_rank = (
            df_estado[["municipio", "municipio_key"]].drop_duplicates()
        )
        mun_rank["_size"] = mun_rank["municipio_key"].map(
            mun_size.xs(id_estado_sel, level="id_estado")
        ).fillna(0)
        mun_rank = mun_rank.dropna(subset=["municipio"]).sort_values("_size", ascending=False)
        municipios = mun_rank["municipio"].tolist()
        if st.session_state.get("traj_mun") not in municipios:
            st.session_state.traj_mun = random.choice(municipios)
        mun_sel = st.selectbox("Municipio", municipios, key="traj_mun")

    df = df_all[
        (df_all["id_estado"] == id_estado_sel) &
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
        opt_l, opt_r = st.columns([1, 1])
        with opt_l:
            show_bubbles = st.checkbox("Mostrar burbujas", value=False, key="traj_show_bubbles")
        with opt_r:
            show_labels = st.checkbox("Mostrar etiquetas de año", value=True, key="traj_show_labels")
        tie_radius = st.slider(
            "Radio de \"Empate\" (± pts sobre 33.3%)", min_value=0, max_value=15,
            value=8, key="traj_tie_radius",
            help="Una coalición del 2do + 3er bloque siempre puede superar al líder "
                 "si este no pasa de 50% — por eso \"Base\" exige mayoría absoluta. "
                 "Este control solo ajusta qué tan cerca de 33/33/33 se considera empate "
                 "en vez de una contienda de dos bloques.",
        )
        df["category"] = df.apply(
            lambda r: classify_ternary(r["pct_L"], r["pct_R"], r["pct_C"], tie_radius=tie_radius),
            axis=1,
        )
        st.plotly_chart(
            _build_ternary(df, mun_sel, show_bubbles, show_labels, tie_radius),
            use_container_width=True,
        )
    with col_r:
        st.plotly_chart(_build_trend(df), use_container_width=True)

    missing = [y for y in PRE_YEARS if y not in df["year"].tolist()]
    if missing:
        st.caption(
            f"Años sin datos para este municipio: {', '.join(map(str, missing))}. "
            "Los porcentajes excluyen partidos menores no mapeados y votos nulos."
        )

    # ── Resultados por partido / coalición (elección seleccionada) ──────────────
    st.markdown("---")
    st.markdown('<div class="section-label">Resultados por partido y coalición</div>',
                unsafe_allow_html=True)

    years_available = df["year"].tolist()
    default_year = max(years_available)
    hist_year = st.selectbox(
        "Año de la elección", years_available,
        index=years_available.index(default_year), key="traj_hist_year",
    )

    mun_key = _norm(mun_sel)
    df_raw_year = load_raw_year(hist_year)
    df_raw_mun = df_raw_year[
        (df_raw_year["id_estado"] == id_estado_sel) &
        (df_raw_year["municipio_key"] == mun_key)
    ]

    if df_raw_mun.empty:
        st.info("Sin datos de partido para esta elección.")
    else:
        meta_row  = df_raw_mun.drop_duplicates("municipio_key").iloc[0]
        total_v   = safe_int(df_raw_mun.drop_duplicates("municipio_key")["total_votos"].sum())
        lista_nom = safe_int(meta_row.get("lista_nominal_part"))
        part_pct  = total_v / lista_nom * 100 if lista_nom > 0 else 0
        num_sec   = safe_int(meta_row.get("num_secciones"))
        num_cas   = safe_int(meta_row.get("num_casillas"))
        nulos_raw = safe_int(df_raw_mun.drop_duplicates("municipio_key")["num_votos_nulos"].sum())
        nulos_pct = nulos_raw / total_v * 100 if total_v > 0 else 0

        header_badge([
            f"{mun_sel} · {hist_year}",
            f"{fmt_num(total_v)} votos emitidos",
            f"{fmt_num(lista_nom)} en lista nominal",
            f"Participación: {fmt_pct(part_pct)}",
            f"Votos nulos: {fmt_pct(nulos_pct)}",
            f"{num_sec} secciones", f"{num_cas} actas",
        ])

        blocs = CYCLE_BLOCS.get(f"PRE_{hist_year}")
        if blocs is not None:
            render_hist_both_charts(df_raw_mun, blocs)
        else:
            st.info("Sin agrupación ideológica definida para esta elección.")
