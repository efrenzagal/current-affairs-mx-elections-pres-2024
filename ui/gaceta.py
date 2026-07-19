"""
Gaceta Parlamentaria section — deputy alignment, party cohesion, vote browser.
Self-contained: owns its own loaders, controls, and render functions.
Call render_gaceta() from the main app.
"""

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st


# ── Cached loaders ─────────────────────────────────────────────────────────────

@st.cache_data
def load_gaceta_alignment() -> pd.DataFrame:
    return pd.read_parquet("data/materialized/gaceta_deputy_alignment.parquet")


@st.cache_data
def load_gaceta_cohesion() -> pd.DataFrame:
    return pd.read_parquet("data/materialized/gaceta_party_cohesion.parquet")


@st.cache_data
def load_gaceta_votes() -> pd.DataFrame:
    return pd.read_parquet("data/materialized/gaceta_vote_index.parquet")


# ── Sub-page renderers ─────────────────────────────────────────────────────────

def render_deputy_alignment(alignment_df: pd.DataFrame, votes_df: pd.DataFrame,
                             leg_sel: int, party_sel: str):
    df = alignment_df[alignment_df["legislature"] == leg_sel].copy()
    if party_sel != "Todos":
        df = df[df["party_key"] == party_sel]
    if df.empty:
        st.info("Sin datos para esta selección.")
        return

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Diputados", f"{len(df):,}")
    m2.metric("Alineación promedio", f"{df['alignment_rate'].mean():.1%}")
    m3.metric("Ausencia promedio",   f"{df['absence_rate'].mean():.1%}")
    m4.metric("Votaciones", f"{votes_df[votes_df['legislature'] == leg_sel].shape[0]:,}")

    st.markdown('<div class="section-label">Alineación con mayoría del partido</div>',
                unsafe_allow_html=True)

    fig = px.scatter(
        df, x="absence_rate", y="alignment_rate",
        color="party_key",
        hover_name="deputy_name",
        hover_data={"party_key": True, "votes_active": True, "votes_absent": True},
        labels={
            "absence_rate":  "Tasa de ausencia",
            "alignment_rate": "Alineación con partido",
            "party_key":     "Partido",
            "votes_active":  "Votos activos",
            "votes_absent":  "Ausencias",
        },
        title=f"Alineación vs Ausencia · Legislatura {leg_sel}",
    )
    fig.update_traces(marker=dict(size=7, opacity=0.75))
    fig.update_layout(
        height=480,
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        font=dict(family="IBM Plex Sans"),
        xaxis=dict(tickformat=".0%", gridcolor="#eee"),
        yaxis=dict(tickformat=".0%", gridcolor="#eee"),
    )
    st.plotly_chart(fig, use_container_width=True)

    st.markdown('<div class="section-label">Ranking de diputados</div>', unsafe_allow_html=True)
    sort_col = st.radio("Ordenar por",
                        ["Menor alineación", "Mayor alineación", "Mayor ausencia"],
                        horizontal=True)
    sort_map = {
        "Menor alineación": ("alignment_rate", True),
        "Mayor alineación": ("alignment_rate", False),
        "Mayor ausencia":   ("absence_rate",   False),
    }
    scol, sasc = sort_map[sort_col]
    display = df.sort_values(scol, ascending=sasc)[[
        "deputy_name", "party_key", "alignment_rate", "absence_rate",
        "votes_active", "votes_aligned", "votes_absent",
    ]].rename(columns={
        "deputy_name":    "Diputado",
        "party_key":      "Partido",
        "alignment_rate": "Alineación",
        "absence_rate":   "Ausencia",
        "votes_active":   "Votos activos",
        "votes_aligned":  "Alineados",
        "votes_absent":   "Ausencias",
    })
    display["Alineación"] = display["Alineación"].map("{:.1%}".format)
    display["Ausencia"]   = display["Ausencia"].map("{:.1%}".format)
    st.dataframe(display, use_container_width=True, hide_index=True)


def render_party_cohesion(cohesion_df: pd.DataFrame, leg_sel: int, party_sel: str):
    df = cohesion_df.copy()
    if party_sel != "Todos":
        df = df[df["party_key"] == party_sel]

    st.markdown('<div class="section-label">Cohesión por partido y legislatura</div>',
                unsafe_allow_html=True)

    pivot = cohesion_df.pivot_table(
        index="party_key", columns="legislature",
        values="cohesion_mean", aggfunc="mean",
    ).round(3)
    fig_heat = px.imshow(
        pivot,
        text_auto=".2f",
        color_continuous_scale="RdYlGn",
        zmin=0.5, zmax=1.0,
        labels={"x": "Legislatura", "y": "Partido", "color": "Cohesión"},
        title="Cohesión media por partido × legislatura",
    )
    fig_heat.update_layout(
        height=420,
        font=dict(family="IBM Plex Mono", size=11),
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        coloraxis_colorbar=dict(tickformat=".0%"),
    )
    st.plotly_chart(fig_heat, use_container_width=True)

    leg_cohesion = cohesion_df[cohesion_df["legislature"] == leg_sel].sort_values(
        "cohesion_mean", ascending=True
    )
    fig_bar = px.bar(
        leg_cohesion, x="cohesion_mean", y="party_key", orientation="h",
        text=leg_cohesion["cohesion_mean"].map("{:.1%}".format),
        labels={"cohesion_mean": "Cohesión media", "party_key": "Partido"},
        title=f"Cohesión media · Legislatura {leg_sel}",
    )
    fig_bar.update_traces(textposition="outside")
    fig_bar.update_layout(
        height=350, xaxis=dict(tickformat=".0%", range=[0, 1.05]),
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        font=dict(family="IBM Plex Sans"),
    )
    st.plotly_chart(fig_bar, use_container_width=True)

    st.dataframe(
        cohesion_df[cohesion_df["legislature"] == leg_sel]
        .sort_values("cohesion_mean", ascending=False)
        .rename(columns={
            "party_key":          "Partido",
            "cohesion_mean":      "Cohesión media",
            "pct_unanimous":      "% unánimes",
            "cohesion_unanimous": "Votos unánimes",
            "votes_counted":      "Total votaciones",
            "legislature":        "Legislatura",
        })
        .style.format({"Cohesión media": "{:.1%}", "% unánimes": "{:.1%}"}),
        use_container_width=True, hide_index=True,
    )


def render_vote_browser(votes_df: pd.DataFrame, leg_sel: int):
    df = votes_df[votes_df["legislature"] == leg_sel].copy()
    df["vote_date"] = pd.to_datetime(df["vote_date"], errors="coerce")

    st.markdown('<div class="section-label">Votaciones registradas</div>', unsafe_allow_html=True)

    v1, v2, v3 = st.columns(3)
    v1.metric("Total votaciones", f"{len(df):,}")
    v2.metric("Con fecha", f"{df['vote_date'].notna().sum():,}")
    v3.metric("Con quórum", f"{df['quorum_ok'].sum():,}" if "quorum_ok" in df.columns else "n/d")

    df_dated = df[df["vote_date"].notna()].copy()
    if not df_dated.empty:
        df_dated["month"] = df_dated["vote_date"].dt.to_period("M").astype(str)
        monthly = (
            df_dated.groupby("month")
            .agg(
                mayoria_simple=("mayoria_simple_ok", "sum"),
                mayoria_absoluta=("mayoria_absoluta_ok", "sum"),
                mayoria_calificada=("mayoria_calificada_ok", "sum"),
            )
            .reset_index()
            .melt("month", var_name="umbral", value_name="n")
        )
        fig_time = px.bar(
            monthly, x="month", y="n", color="umbral",
            labels={"month": "Mes", "n": "Votaciones", "umbral": "Umbral"},
            title=f"Umbrales alcanzados por mes · Legislatura {leg_sel}",
        )
        fig_time.update_layout(
            height=320, xaxis_tickangle=-45,
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
            font=dict(family="IBM Plex Sans"),
        )
        st.plotly_chart(fig_time, use_container_width=True)

    search   = st.text_input("Buscar en título",
                             placeholder="ej. reforma, presupuesto, Poder Judicial…")
    df_show  = df_dated if not df_dated.empty else df
    if search:
        df_show = df_show[df_show["title"].str.contains(search, case=False, na=False)]

    display_cols = [
        "vote_date", "title", "favor", "contra", "abstencion", "quorum",
        "ausente", "presentes", "total", "quorum_ok", "mayoria_simple_ok",
        "mayoria_absoluta_ok", "mayoria_calificada_ok",
        "mayoria_absoluta_requerida", "mayoria_calificada_requerida",
    ]
    display_cols = [c for c in display_cols if c in df_show.columns]

    st.dataframe(
        df_show[display_cols]
        .sort_values("vote_date", ascending=False)
        .rename(columns={
            "vote_date": "Fecha",
            "title": "Título",
            "favor": "Favor",
            "contra": "Contra",
            "abstencion": "Abstención",
            "quorum": "Quórum *",
            "ausente": "Ausente",
            "presentes": "Presentes",
            "total": "Total",
            "quorum_ok": "Quórum",
            "mayoria_simple_ok": "Mayoría simple",
            "mayoria_absoluta_ok": "Mayoría absoluta",
            "mayoria_calificada_ok": "Mayoría calificada",
            "mayoria_absoluta_requerida": "Req. absoluta",
            "mayoria_calificada_requerida": "Req. calificada",
        }),
        use_container_width=True, hide_index=True,
    )


# ── Top-level entry point ──────────────────────────────────────────────────────

def render_gaceta():
    """Load data, render controls, dispatch to sub-page renderers."""
    try:
        alignment_df = load_gaceta_alignment()
        cohesion_df  = load_gaceta_cohesion()
        votes_df     = load_gaceta_votes()
    except FileNotFoundError:
        st.error(
            "Archivos de Gaceta no encontrados. "
            "Ejecuta `python ingestion/gaceta_materialize.py` primero."
        )
        return

    all_legs    = sorted(alignment_df["legislature"].unique(), reverse=True)
    all_parties = sorted(alignment_df["party_key"].unique())

    gc1, gc2, gc3 = st.columns([1, 1, 2])
    with gc1:
        leg_sel = st.selectbox("Legislatura", all_legs,
                               format_func=lambda x: f"Legislatura {x}")
    with gc2:
        party_sel = st.selectbox("Partido", ["Todos"] + all_parties)
    with gc3:
        page = st.radio(
            "Vista",
            ["Alineación de diputados", "Cohesión de partidos", "Votaciones"],
            horizontal=True,
        )

    st.markdown("---")

    if page == "Alineación de diputados":
        render_deputy_alignment(alignment_df, votes_df, leg_sel, party_sel)
    elif page == "Cohesión de partidos":
        render_party_cohesion(cohesion_df, leg_sel, party_sel)
    else:
        render_vote_browser(votes_df, leg_sel)
