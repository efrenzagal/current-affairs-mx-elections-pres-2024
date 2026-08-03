"""
Trayectoria ideológica por municipio — ternary across all PRE elections.
Vertices: Left (PRD/Morena tradition), Right (PAN), Center (everyone else —
PRI, MC, and the smaller parties that aren't ideologically anchored to
either pole; a residual "non-Morena, non-PAN" bucket rather than a claim
that PRI and MC share one coherent ideology).
Call render_trajectory() from the main app.
"""

from __future__ import annotations

from html import escape
import random
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from ui.charts import render_hist_both_charts, render_timeseries_for_estado
from ui.common import (
    CATEGORY_COLORS, COALITION_MEMBERS, CYCLE_BLOCS, IDEOLOGY_MAP, MATERIALIZED_DIR,
    TIMESERIES_PATH, _norm, classify_ternary, fmt_num, fmt_pct, safe_int,
    split_coalition_votes_by_geo, title_case_es,
)
from ui.maps import load_estados_geojson, load_municipios_geojson, render_winner_map, render_winner_map_estado
from ui.tables import header_badge, metric_card

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

        # Dissolve mixed-ideology coalitions (e.g. PAN_PRD, PRI_PRD) into
        # their member parties before classifying L/R/C — a coalition vote
        # is 100% a vote for its candidate, but attributing 100% of it to
        # one ideological bloc would misclassify the other member's share.
        df_ideo = split_coalition_votes_by_geo(df[geo_cols + ["party_key", "votes"]])
        df_ideo["bloc"] = df_ideo["party_key"].map(IDEOLOGY_MAP)
        df_ideo = df_ideo.dropna(subset=["bloc"])

        piv = (
            df_ideo.pivot_table(index=geo_cols, columns="bloc", values="votes",
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


@st.cache_data(show_spinner=False)
def load_vertex_members(year: int) -> dict[str, tuple[str, ...]]:
    """Return the observed party/candidacy keys assigned to each ternary vertex."""
    path = MATERIALIZED_DIR / f"view_municipio_PRE_{year}.parquet"
    if not path.exists():
        return {bloc: () for bloc in ("L", "R", "C")}

    parties = pd.read_parquet(path, columns=["party_key"])["party_key"].dropna().unique()
    members = {bloc: [] for bloc in ("L", "R", "C")}
    for party_key in sorted(parties):
        # Coalitions we dissolve into members (see COALITION_MEMBERS) are
        # excluded here — their votes end up folded into each member's own
        # row, so listing the composite key too would be double-counting
        # what the ternary/trend charts actually plot.
        if party_key in COALITION_MEMBERS:
            continue
        bloc = IDEOLOGY_MAP.get(party_key)
        if bloc:
            members[bloc].append(party_key)
    return {bloc: tuple(keys) for bloc, keys in members.items()}


def _display_party_key(party_key: str) -> str:
    """Make warehouse party keys legible without hiding the source grouping."""
    if party_key.startswith("CAND_IND_"):
        return f"Independiente {party_key.rsplit('_', 1)[-1]}"
    if party_key.startswith("C_"):
        return "Coalición " + party_key[2:].replace("_", " + ")
    return party_key.replace("_", " + ")


_PARTY_ABBREV = {
    "MOVIMIENTO CIUDADANO": "MC",
    "NUEVA ALIANZA": "PANAL",
}


def _display_party_key_short(party_key: str) -> str:
    """Abbreviated form of _display_party_key for hover text, where space is tight."""
    if party_key.startswith("CAND_IND_"):
        return f"Ind. {party_key.rsplit('_', 1)[-1]}"
    if party_key in _PARTY_ABBREV:
        return _PARTY_ABBREV[party_key]
    if party_key.startswith("C_"):
        return "Coalición " + party_key[2:].replace("_", " + ")
    return party_key.replace("_", " + ")


def _display_party_list_short(keys: tuple[str, ...], limit: int = 3) -> str:
    """Compact party listing for hover text — full list lives in the table below
    the chart (render_vertex_members); the hover just needs a quick summary."""
    names = [_display_party_key_short(key) for key in keys]
    if not names:
        return "—"
    if len(names) <= limit:
        return ", ".join(names)
    return f"{', '.join(names[:limit])} +{len(names) - limit} más"


def render_vertex_members(years: list[int]) -> None:
    """Show the exact L/R/C assignment used for every plotted election cycle."""
    st.markdown("<div class='section-label'>Vértices por ciclo</div>", unsafe_allow_html=True)
    st.caption("Partidos y candidaturas independientes incluidos en cada vértice de los puntos mostrados.")
    rows = []
    for year in years:
        members = load_vertex_members(year)
        cells = ["<br>".join(escape(_display_party_key(key)) for key in members[bloc]) or "—"
                 for bloc in ("L", "C", "R")]
        rows.append(
            f"<tr><td>{year}</td><td>{cells[0]}</td><td>{cells[1]}</td><td>{cells[2]}</td></tr>"
        )
    st.markdown(
        """<table style='width:100%; table-layout:fixed; border-collapse:collapse; font-size:0.82rem'>
        <thead><tr><th style='width:8%'>Ciclo</th><th>Izquierda</th><th>Centro</th><th>Derecha</th></tr></thead>
        <tbody>""" + "".join(rows) + "</tbody></table>",
        unsafe_allow_html=True,
    )


@st.cache_data(show_spinner="Cargando series de tiempo...")
def load_timeseries(_mtime: float) -> pd.DataFrame:
    if not TIMESERIES_PATH.exists():
        return pd.DataFrame()
    df = pd.read_parquet(TIMESERIES_PATH)
    df["nombre_estado"] = df["nombre_estado"].str.strip().str.title()
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


def aggregate_geo(df_all: pd.DataFrame, id_estado: int | None = None) -> pd.DataFrame:
    """Roll up the per-municipio L/R/C votes to a state or national level.

    Percentages are recomputed from the summed bloc votes (not averaged from
    per-municipio percentages) so a state/national trajectory reflects the
    actual vote mass rather than an unweighted mean of municipios.
    """
    d = df_all if id_estado is None else df_all[df_all["id_estado"] == id_estado]
    g = d.groupby("year", as_index=False)[["L", "R", "C"]].sum()
    total = (g["L"] + g["R"] + g["C"]).replace(0, float("nan"))
    g["pct_L"] = (g["L"] / total * 100).round(1)
    g["pct_R"] = (g["R"] / total * 100).round(1)
    g["pct_C"] = (g["C"] / total * 100).round(1)
    g["total_votos"] = total
    return g.dropna(subset=["pct_L"]).sort_values("year").reset_index(drop=True)


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
        members = load_vertex_members(yr)
        assignments = "<br>".join(
            f"{label}: {_display_party_list_short(members[bloc])}"
            for bloc, label in (("L", "Izquierda"), ("C", "Centro"), ("R", "Derecha"))
        )
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
                f"Votos: {total_votos:,}<br><br>"
                f"<b>Asignación del ciclo</b><br>{assignments}<extra></extra>"
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
        text=["<b>Izquierda</b>", "<b>Derecha</b>", "<b>Centro</b>"],
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


def _rank_estados(df_all: pd.DataFrame, estado_size: pd.Series) -> pd.DataFrame:
    estados = df_all[["id_estado", "nombre_estado"]].dropna().drop_duplicates()
    estados["_size"] = estados["id_estado"].map(estado_size).fillna(0)
    estados = estados.sort_values("_size", ascending=False)
    estados["_display"] = estados["nombre_estado"].map(title_case_es)
    return estados


def _prep_ternary_df(df: pd.DataFrame, tie_radius: float = 8.0) -> pd.DataFrame:
    df = df.sort_values("year").reset_index(drop=True)
    df["tx"], df["ty"] = zip(*df.apply(
        lambda r: _ternary_xy(r["pct_L"], r["pct_R"], r["pct_C"]), axis=1
    ))
    df["category"] = df.apply(
        lambda r: classify_ternary(r["pct_L"], r["pct_R"], r["pct_C"], tie_radius=tie_radius),
        axis=1,
    )
    return df


# ── Main render ────────────────────────────────────────────────────────────────

def render_trajectory():
    df_all = load_trajectory_data()
    if df_all.empty:
        st.error("No se encontraron datos. Ejecuta el pipeline de ingesta primero.")
        return

    # "Size" = biggest recorded turnout (total_votos) each unit has ever had,
    # used to rank the selectors so the largest municipios/states surface first.
    mun_size = df_all.groupby(["id_estado", "municipio_key"])["total_votos"].max()
    estado_size = mun_size.groupby("id_estado").sum()
    nacional_2024 = df_all.loc[df_all["year"] == 2024, "total_votos"].sum()

    # ══════════════════════════════════════════════════════════════════════
    # TRAYECTORIA POR GEOGRAFÍA (Nacional / Estado / Municipio)
    # ══════════════════════════════════════════════════════════════════════
    st.markdown('<div class="section-label">Trayectoria ideológica</div>',
                unsafe_allow_html=True)

    level = st.radio(
        "Nivel geográfico", ["Nacional", "Estado", "Municipio"],
        horizontal=True, key="traj_geo_level",
    )

    id_estado_geo = None
    mun_sel = None
    geo_label = "Nacional"

    if level in ("Estado", "Municipio"):
        estados = _rank_estados(df_all, estado_size)
        state_names = estados["nombre_estado"].tolist()
        display_by_state = dict(zip(estados["nombre_estado"], estados["_display"]))
        if "traj_geo_estado" not in st.session_state:
            st.session_state.traj_geo_estado = random.choice(state_names)

        if level == "Municipio":
            col_e, col_m = st.columns([1, 1])
            with col_e:
                estado_geo_sel = st.selectbox(
                    "Estado", state_names, key="traj_geo_estado",
                    format_func=lambda v: display_by_state.get(v, v),
                )
        else:
            estado_geo_sel = st.selectbox(
                "Estado", state_names, key="traj_geo_estado",
                format_func=lambda v: display_by_state.get(v, v),
            )
        id_estado_geo = int(estados.loc[
            estados["nombre_estado"] == estado_geo_sel, "id_estado"
        ].iloc[0])
        geo_label = title_case_es(estado_geo_sel)

        if level == "Municipio":
            with col_m:
                df_estado = df_all[df_all["id_estado"] == id_estado_geo]
                mun_rank = df_estado[["municipio", "municipio_key"]].drop_duplicates()
                mun_rank["_size"] = mun_rank["municipio_key"].map(
                    mun_size.xs(id_estado_geo, level="id_estado")
                ).fillna(0)
                mun_rank = mun_rank.dropna(subset=["municipio"]).sort_values("_size", ascending=False)
                municipios = mun_rank["municipio"].tolist()
                display_by_mun = dict(zip(mun_rank["municipio"], mun_rank["municipio"].map(title_case_es)))
                if st.session_state.get("traj_mun") not in municipios:
                    st.session_state.traj_mun = random.choice(municipios)
                mun_sel = st.selectbox(
                    "Municipio", municipios, key="traj_mun",
                    format_func=lambda v: display_by_mun.get(v, v),
                )
            geo_label = title_case_es(mun_sel)

    if level == "Municipio":
        df_geo = df_all[
            (df_all["id_estado"] == id_estado_geo) & (df_all["municipio"] == mun_sel)
        ]
    else:
        df_geo = aggregate_geo(df_all, id_estado_geo)

    if df_geo.empty:
        st.info("Sin datos para este nivel geográfico.")
        return
    df_geo = _prep_ternary_df(df_geo)

    col_l, col_r = st.columns([1, 1], gap="large")
    with col_l:
        st.plotly_chart(
            _build_ternary(df_geo, geo_label, show_bubbles=False, show_labels=True, tie_radius=8.0),
            use_container_width=True,
        )
    with col_r:
        st.markdown('<div class="section-label">Tendencias</div>', unsafe_allow_html=True)
        st.plotly_chart(_build_trend(df_geo), use_container_width=True)

    # ── Tamaño relativo del nivel (votos 2024 vs. estado y país) ─────────────────
    votos_2024     = df_geo.loc[df_geo["year"] == 2024, "total_votos"]
    votos_2024     = float(votos_2024.iloc[0]) if not votos_2024.empty else None
    votos_promedio = float(df_geo["total_votos"].mean())

    if level == "Nacional":
        sc1, sc2 = st.columns(2)
        with sc1:
            metric_card("Votos 2024", fmt_num(votos_2024) if votos_2024 is not None else "—")
        with sc2:
            metric_card(
                "Promedio histórico", fmt_num(votos_promedio),
                sub=f"entre {len(df_geo)} elecciones",
            )
    elif level == "Estado":
        sc1, sc2, sc3 = st.columns(3)
        with sc1:
            metric_card("Votos 2024", fmt_num(votos_2024) if votos_2024 is not None else "—")
        with sc2:
            pct_nacional = votos_2024 / nacional_2024 * 100 if votos_2024 and nacional_2024 > 0 else None
            metric_card(
                "% nacional (2024)",
                fmt_pct(pct_nacional) if pct_nacional is not None else "—",
            )
        with sc3:
            metric_card(
                "Promedio histórico", fmt_num(votos_promedio),
                sub=f"entre {len(df_geo)} elecciones",
            )
    else:
        estado_2024 = df_all.loc[
            (df_all["id_estado"] == id_estado_geo) & (df_all["year"] == 2024), "total_votos"
        ].sum()
        sc1, sc2, sc3, sc4 = st.columns(4)
        with sc1:
            metric_card("Votos 2024", fmt_num(votos_2024) if votos_2024 is not None else "—")
        with sc2:
            pct_estado = votos_2024 / estado_2024 * 100 if votos_2024 and estado_2024 > 0 else None
            metric_card(
                "% del estado (2024)",
                fmt_pct(pct_estado) if pct_estado is not None else "—",
            )
        with sc3:
            pct_nacional = votos_2024 / nacional_2024 * 100 if votos_2024 and nacional_2024 > 0 else None
            metric_card(
                "% nacional (2024)",
                fmt_pct(pct_nacional) if pct_nacional is not None else "—",
            )
        with sc4:
            metric_card(
                "Promedio histórico", fmt_num(votos_promedio),
                sub=f"entre {len(df_geo)} elecciones",
            )

    # ── Tendencias por partido — siempre a nivel estado/nacional ─────────────────
    # (un municipio no tiene suficiente volumen de partidos menores para un
    # desglose útil, así que la serie de partidos usa el estado contenedor)
    ts_label = "Nacional" if id_estado_geo is None else title_case_es(estado_geo_sel)
    st.markdown("---")
    st.markdown(f'<div class="section-label">Tendencias por partido — {ts_label}</div>',
                unsafe_allow_html=True)
    df_ts = load_timeseries(
        TIMESERIES_PATH.stat().st_mtime if TIMESERIES_PATH.exists() else 0.0
    )
    if df_ts.empty:
        st.info("No se encontró el archivo de series de tiempo. "
                "Ejecuta `python ingestion/electoral_materialize.py timeseries` primero.")
    else:
        render_timeseries_for_estado(df_ts, id_estado_geo, ts_label)

    # ── Análisis por elección (deep dive) — misma escala que la serie de partido ─
    st.markdown("---")
    st.markdown(f'<div class="section-label">Análisis por elección — {ts_label}</div>',
                unsafe_allow_html=True)

    years_available = df_geo["year"].tolist()
    default_year = max(years_available)
    hist_year = st.selectbox(
        "Año de la elección", years_available,
        index=years_available.index(default_year), key="traj_hist_year",
    )

    df_raw_year = load_raw_year(hist_year)
    df_raw_geo = (
        df_raw_year if id_estado_geo is None
        else df_raw_year[df_raw_year["id_estado"] == id_estado_geo]
    )

    if df_raw_geo.empty:
        st.info("Sin datos de partido para esta elección.")
    else:
        total_v   = safe_int(df_raw_geo.drop_duplicates("municipio_key")["total_votos"].sum())
        lista_nom = safe_int(df_raw_geo.drop_duplicates("municipio_key")["lista_nominal_part"].sum())
        part_pct  = total_v / lista_nom * 100 if lista_nom > 0 else 0
        num_mun   = df_raw_geo["municipio_key"].nunique()
        nulos_raw = safe_int(df_raw_geo.drop_duplicates("municipio_key")["num_votos_nulos"].sum())
        nulos_pct = nulos_raw / total_v * 100 if total_v > 0 else 0

        header_badge([
            f"{ts_label} · {hist_year}",
            f"{fmt_num(total_v)} votos emitidos",
            f"{fmt_num(lista_nom)} en lista nominal",
            f"Participación: {fmt_pct(part_pct)}",
            f"Votos nulos: {fmt_pct(nulos_pct)}",
            f"{num_mun} municipios",
        ])

        blocs = CYCLE_BLOCS.get(f"PRE_{hist_year}")
        if blocs is not None:
            render_hist_both_charts(df_raw_geo, blocs)
        else:
            st.info("Sin agrupación ideológica definida para esta elección.")

        st.markdown("---")
        map_unit = "Estado" if level == "Nacional" else "Municipio"
        st.markdown(
            f'<div class="section-label">Mapa de ganador por {map_unit.lower()} (beta)</div>',
            unsafe_allow_html=True,
        )
        st.caption(
            "⚠️ Vista beta: para algunas elecciones históricas el mapeo de "
            "partidos/coaliciones a un bloque puede ser impreciso o incompleto "
            f"entre ciclos. Aun así, el patrón agregado por {map_unit.lower()} es informativo."
        )
        if blocs is not None:
            if level == "Nacional":
                geo = load_estados_geojson()
                render_winner_map_estado(
                    df_raw_geo, blocs, geo,
                    f"Ganador por Estado · {ts_label} · PRE {hist_year}",
                    height=520,
                )
            else:
                geo = load_municipios_geojson()
                render_winner_map(
                    df_raw_geo, blocs, geo,
                    f"Ganador por Municipio · {ts_label} · PRE {hist_year}",
                    height=520,
                )

    st.markdown("---")
    render_vertex_members(df_geo["year"].astype(int).tolist())

    missing = [y for y in PRE_YEARS if y not in df_geo["year"].tolist()]
    if missing:
        st.caption(
            f"Años sin datos para {geo_label}: {', '.join(map(str, missing))}. "
            "Los porcentajes excluyen partidos menores no mapeados y votos nulos."
        )
