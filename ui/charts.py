"""
All Plotly chart rendering: bar charts, ternary bubble plots, and timeseries.
"""

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from ui.common import (
    CANDIDATES, PARTY_GROUPS,
    agg_blocs, plotly_base, pivot_candidates,
    ts_agg_for_plot, ts_base_layout, ts_party_color,
)


# ── Bar charts ─────────────────────────────────────────────────────────────────

def render_candidate_results(df: pd.DataFrame):
    """Horizontal bar chart aggregated by candidate (PRE_2024)."""
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
        title=f"Resultados por Candidato - {df_plot['Votos'].sum():,} votos validos",
    )
    fig.update_traces(textposition="outside", showlegend=False)
    fig.update_layout(**plotly_base(),
                      xaxis_range=[0, df_plot["Votos"].max() * 1.15], height=300)
    st.plotly_chart(fig, use_container_width=True)


def render_party_results(df: pd.DataFrame, title_suffix: str = ""):
    """Horizontal bar chart by party/coalition (PRE_2024)."""
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


def render_both_charts(df: pd.DataFrame):
    """Two-column layout: candidate bar + party bar (PRE_2024)."""
    col_cand, col_party = st.columns(2)
    with col_cand:
        st.markdown('<div class="section-label">Por Candidato</div>', unsafe_allow_html=True)
        render_candidate_results(df)
    with col_party:
        st.markdown('<div class="section-label">Por Partido / Coalicion</div>',
                    unsafe_allow_html=True)
        render_party_results(df)


def render_hist_both_charts(df: pd.DataFrame, blocs: dict):
    """Two-column bar chart for historical PRE elections: by bloc + by party."""
    by_party  = df.groupby("party_key")["votes"].sum()
    sample_pk = df["party_key"].iloc[0] if len(df) else None
    if sample_pk and "num_votos_validos" in df.columns:
        total_validos = int(df[df["party_key"] == sample_pk]["num_votos_validos"].sum())
    else:
        total_validos = int(by_party.sum())
    total_validos = max(total_validos, int(by_party.sum()), 1)

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


# ── Ternary bubble plots ───────────────────────────────────────────────────────

def _ternary_grid_traces(sqrt3_2: float) -> list:
    """Shared dotted grid lines for all ternary plots."""
    traces = []
    for frac in [1/3, 2/3]:
        ax, ay = 1 - frac, 0
        bx, by = (1 - frac) * 0.5, (1 - frac) * sqrt3_2
        cx, cy = frac, 0
        dx, dy = frac + (1 - frac) * 0.5, (1 - frac) * sqrt3_2
        ex, ey = (1 - frac) + frac * 0.5, frac * sqrt3_2
        fx, fy = frac * 0.5, frac * sqrt3_2
        for (x0, y0, x1, y1) in [(ax,ay,bx,by),(cx,cy,dx,dy),(ex,ey,fx,fy)]:
            traces.append(go.Scatter(
                x=[x0, x1], y=[y0, y1], mode="lines",
                line=dict(color="rgba(180,180,180,0.4)", width=0.8, dash="dot"),
                hoverinfo="skip", showlegend=False,
            ))
    return traces


def render_ternary_bubble(df: pd.DataFrame, group_cols, label_cols,
                           title_suffix: str, n_bubbles: int = None, height: int = 580):
    """Ternary bubble plot for PRE_2024 (uses CANDIDATES / PARTY_GROUPS)."""
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

    cand_total     = agg["CAND_SHH"] + agg["CAND_FCM"] + agg["CAND_MC"]
    agg["pct_SHH"] = agg["CAND_SHH"] / cand_total
    agg["pct_FCM"] = agg["CAND_FCM"] / cand_total
    agg["pct_MC"]  = agg["CAND_MC"]  / cand_total

    sqrt3_2    = np.sqrt(3) / 2
    agg["tx"]  = agg["pct_FCM"] + agg["pct_MC"] * 0.5
    agg["ty"]  = agg["pct_MC"]  * sqrt3_2

    has_estado = "nombre_estado" in lbl
    mun_cols   = [c for c in lbl if c != "nombre_estado"]
    agg["_label"] = (
        agg[mun_cols].astype(str).agg(" · ".join, axis=1)
        if mun_cols else agg[lbl].astype(str).agg(" · ".join, axis=1)
    )

    def _winner_color(row):
        w = max({"SHH": row["CAND_SHH"], "FCM": row["CAND_FCM"], "MC": row["CAND_MC"]},
                key=lambda k: {"SHH": row["CAND_SHH"], "FCM": row["CAND_FCM"],
                               "MC": row["CAND_MC"]}[k])
        return {"SHH": "#8B0000", "FCM": "#1E90FF", "MC": "#FF8C00"}[w]

    agg["_color"] = agg.apply(_winner_color, axis=1)

    def _hover(r):
        est = f"Estado: {r['nombre_estado']}<br>" if has_estado else ""
        return (
            f"<b>{r['_label']}</b><br>{est}"
            f"SHH: {r['pct_SHH']*100:.1f}%<br>"
            f"FCM: {r['pct_FCM']*100:.1f}%<br>"
            f"MC:  {r['pct_MC']*100:.1f}%<br>"
            f"Votos validos: {int(r['NUM_VOTOS_VALIDOS']):,}"
        )
    agg["_text"] = agg.apply(_hover, axis=1)
    agg["_size"] = (agg["NUM_VOTOS_VALIDOS"] / agg["NUM_VOTOS_VALIDOS"].max() * 55).clip(lower=6)

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
                   marker=dict(size=10, color=props["color"]),
                   name=props["label"], showlegend=True)
        for props in CANDIDATES.values()
    ]

    fig = go.Figure(data=_ternary_grid_traces(sqrt3_2)
                    + [triangle, centroid, vertex_labels, bubbles] + legend_traces)
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


def render_hist_ternary(df: pd.DataFrame, blocs: dict, title: str,
                        n_bubbles: int = None, height: int = 580):
    """Ternary bubble plot for historical PRE elections (uses CYCLE_BLOCS)."""
    grp = ["nombre_estado", "municipio"] if "nombre_estado" in df.columns else ["municipio"]
    agg = agg_blocs(df, grp, blocs)
    if n_bubbles:
        agg = agg.nlargest(n_bubbles, "total_votos")
    if agg.empty:
        st.info("Sin datos suficientes para el gráfico ternario.")
        return

    sqrt3_2 = np.sqrt(3) / 2
    total   = agg["bloc_A"] + agg["bloc_B"] + agg["bloc_C"]
    agg["tx"] = (agg["bloc_B"] / total) + (agg["bloc_C"] / total) * 0.5
    agg["ty"] = (agg["bloc_C"] / total) * sqrt3_2

    color_map     = {"A": blocs["A"]["color"], "B": blocs["B"]["color"], "C": blocs["C"]["color"]}
    agg["_color"] = agg["winner"].map(color_map)

    mun_col = "municipio" if "municipio" in agg.columns else agg.columns[0]

    def _hover(r):
        est = f"{r['nombre_estado']} · " if "nombre_estado" in agg.columns else ""
        return (
            f"<b>{est}{r[mun_col]}</b><br>"
            f"{blocs['A']['label']}: {r['pct_A']:.1f}%<br>"
            f"{blocs['B']['label']}: {r['pct_B']:.1f}%<br>"
            f"{blocs['C']['label']}: {r['pct_C']:.1f}%<br>"
            f"Votos: {int(r['bloc_A']+r['bloc_B']+r['bloc_C']):,}"
        )
    agg["_text"] = agg.apply(_hover, axis=1)
    agg["_size"] = (total / total.max() * 55).clip(lower=5)

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
        x=[0.5], y=[sqrt3_2 / 3], mode="markers+text",
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

    fig = go.Figure(data=_ternary_grid_traces(sqrt3_2)
                    + [triangle, centroid, vertex_labels, bubbles] + legend_traces)
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


# ── Timeseries ─────────────────────────────────────────────────────────────────

def render_timeseries_for_estado(df_ts: pd.DataFrame, id_estado_sel: int,
                                  estado_label: str):
    """
    Multi-year presidential votes-by-party chart for a single state (votes
    are always shown split by party, never grouped as coalitions). Matched
    by id_estado (1-32, consistent across all cycles) rather than
    nombre_estado — state name spelling varies enough to silently drop rows.
    """
    df_state = df_ts[
        (df_ts["id_estado"] == id_estado_sel) & (df_ts["election_type"] == "PRE")
    ]
    if df_state.empty:
        st.info("Sin datos históricos para este estado.")
        return

    label_of = (
        df_state.dropna(subset=["party_label"])
        .drop_duplicates("party_key")
        .set_index("party_key")["party_label"]
        .to_dict()
    )
    coalition_keys  = set(df_state.loc[df_state["is_coalition"] == True, "party_key"].unique())
    direct_parties  = sorted(p for p in df_state["party_key"].unique() if p not in coalition_keys)

    y_col   = "votes_split"
    y_label = "Votos"
    y_fmt   = ":,.0f"

    df_f = df_state[
        (df_state["party_key"].isin(direct_parties)) & (df_state["is_coalition"] == False)
    ].copy()
    if df_f.empty:
        st.warning("Sin datos para estos filtros.")
        return

    df_agg = ts_agg_for_plot(df_f, ["year", "election_type", "nombre_estado", "party_key"])
    party_totals = df_agg.groupby("party_key")["votes_split"].sum()
    party_order = (
        party_totals[party_totals >= 10_000]
        .sort_values(ascending=False).index.tolist()
    )
    if not party_order:
        st.info("Ningún partido supera los 10,000 votos en este estado.")
        return

    fig = go.Figure()
    for party in party_order:
        grp   = df_agg[df_agg["party_key"] == party].sort_values("year")
        color = ts_party_color(party)
        label = label_of.get(party, party)
        fig.add_trace(go.Scatter(
            x=grp["year"], y=grp[y_col],
            mode="lines+markers", name=label,
            line=dict(color=color, width=2.5),
            marker=dict(color=color, size=8),
            hovertemplate=f"<b>{label}</b>: %{{y{y_fmt}}}<extra></extra>",
        ))
    fig.update_layout(**ts_base_layout(
        f"{estado_label} · Presidencial", y_label, sorted(df_agg["year"].unique())
    ))
    st.plotly_chart(fig, use_container_width=True)
