"""
Presidential approval ratings page.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "election_data.db"

PRESIDENT_COLORS = {
    "EZPL": "#2E7D32",
    "VFQ": "#1565C0",
    "FCH": "#1E90FF",
    "EPN": "#C62828",
    "AMLO": "#8B0000",
    "Sheinbaum": "#C84B31",
}

PRESIDENT_LABELS = {
    "EZPL": "Ernesto Zedillo",
    "VFQ": "Vicente Fox",
    "FCH": "Felipe Calderón",
    "EPN": "Enrique Peña Nieto",
    "AMLO": "Andrés Manuel López Obrador",
    "Sheinbaum": "Claudia Sheinbaum",
}

PRESIDENT_ORDER = list(PRESIDENT_LABELS)


def _file_version(path: Path) -> tuple[int, int]:
    """Cheap, hashable cache key that changes whenever the loader rebuilds the DB."""
    try:
        stat = path.stat()
    except FileNotFoundError:
        return (0, 0)
    return (stat.st_mtime_ns, stat.st_size)


@st.cache_data(show_spinner="Cargando aprobación presidencial...")
def load_approval_data(db_version: tuple[int, int]) -> pd.DataFrame:
    """Read fact_approval_poll straight from the warehouse.

    encuestadora_clean = pollster_name as stored, which is already the house
    name with its /tel, /viv, /online suffix stripped at ingest time (see
    clean_pollster() in approval/ingest.py) — distinct houses that
    happen to share a base name (BGC / BGC Telefonica / BGC Vivienda) stay
    separate rows, matching how the old xlsx-derived column behaved.
    """
    del db_version
    if not DB_PATH.exists():
        return pd.DataFrame()

    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql_query(
            """
            SELECT
                f.poll_month, f.president AS "Presidente",
                d.pollster_name AS "Encuestadora",
                f.metodo, f.aprueba AS "Aprueba", f.desaprueba AS "Desaprueba",
                f.extraction
            FROM fact_approval_poll f
            JOIN dim_approval_pollster d USING (pollster_id)
            WHERE f.aprueba IS NOT NULL AND f.desaprueba IS NOT NULL
            """,
            conn,
        )
    finally:
        conn.close()

    df["fecha"] = pd.to_datetime(df["poll_month"], format="%Y-%m", errors="coerce")
    df["ratio_ad"] = df["Aprueba"] / df["Desaprueba"]
    df["metodo"] = df["metodo"].fillna("No especificado")
    df["encuestadora_clean"] = df["Encuestadora"]
    df["Presidente"] = pd.Categorical(df["Presidente"], categories=PRESIDENT_ORDER, ordered=True)
    df["source"] = df["extraction"].map(
        lambda x: "sin verificar" if x == "grafica" else "verificado"
    )
    return df.dropna(subset=["fecha", "Aprueba"]).sort_values("fecha")


def _monthly_trend(df: pd.DataFrame) -> pd.DataFrame:
    return (
        df.groupby(["Presidente", "fecha"], observed=True)
        .agg(
            mediana=("Aprueba", "median"),
            q25=("Aprueba", lambda x: x.quantile(0.25)),
            q75=("Aprueba", lambda x: x.quantile(0.75)),
            n=("Aprueba", "size"),
        )
        .reset_index()
        .query("n >= 2")
    )


def _house_effects(df: pd.DataFrame, min_n: int = 5) -> pd.DataFrame:
    out = df.copy()
    out["mediana_mes"] = out.groupby("fecha")["Aprueba"].transform("median")
    out["desviacion"] = out["Aprueba"] - out["mediana_mes"]
    return (
        out.groupby("encuestadora_clean")
        .agg(
            efecto_casa=("desviacion", "mean"),
            sd_efecto=("desviacion", "std"),
            n=("desviacion", "size"),
        )
        .reset_index()
        .query("n >= @min_n")
        .sort_values("efecto_casa")
    )


def _house_effects_by_president(df: pd.DataFrame, min_n: int = 3) -> pd.DataFrame:
    out = df.copy()
    out["mediana_mes"] = out.groupby(["Presidente", "fecha"], observed=True)["Aprueba"].transform("median")
    out["desviacion"] = out["Aprueba"] - out["mediana_mes"]
    return (
        out.groupby(["encuestadora_clean", "Presidente"], observed=True)
        .agg(efecto_casa=("desviacion", "mean"), n=("desviacion", "size"))
        .reset_index()
        .query("n >= @min_n")
    )


def render_approval():
    df = load_approval_data(_file_version(DB_PATH))
    if df.empty:
        st.error("No se encontró election_data.db, o fact_approval_poll está vacía.")
        return

    c1, c2, c3 = st.columns(3)
    c1.metric("Encuestas", f"{len(df):,}")
    c2.metric("Encuestadoras", f"{df['encuestadora_clean'].nunique():,}")
    c3.metric("Periodo", f"{df['fecha'].min():%Y}–{df['fecha'].max():%Y}")

    presidents = st.multiselect(
        "Presidentes",
        PRESIDENT_ORDER,
        default=PRESIDENT_ORDER,
        format_func=lambda x: PRESIDENT_LABELS.get(x, x),
    )
    methods = st.multiselect(
        "Método",
        sorted(df["metodo"].dropna().unique()),
        default=sorted(df["metodo"].dropna().unique()),
    )
    view = df[df["Presidente"].astype(str).isin(presidents) & df["metodo"].isin(methods)].copy()
    if view.empty:
        st.info("Sin datos para esta selección.")
        return

    st.markdown('<div class="section-label">Serie mensual</div>', unsafe_allow_html=True)
    trend = _monthly_trend(view)
    fig = px.scatter(
        view,
        x="fecha",
        y="Aprueba",
        color="Presidente",
        hover_name="encuestadora_clean",
        hover_data={"Encuestadora": True, "metodo": True, "Desaprueba": True},
        color_discrete_map=PRESIDENT_COLORS,
        labels={"fecha": "", "Aprueba": "Aprobación (%)", "Presidente": "Presidente"},
    )
    for president in PRESIDENT_ORDER:
        sub = trend[trend["Presidente"].astype(str) == president]
        if sub.empty:
            continue
        fig.add_scatter(
            x=sub["fecha"],
            y=sub["mediana"],
            mode="lines",
            line=dict(width=3, color=PRESIDENT_COLORS.get(president, "#444")),
            name=f"{PRESIDENT_LABELS.get(president, president)} · mediana",
            legendgroup=president,
            showlegend=False,
        )
    fig.update_layout(
        height=520,
        yaxis=dict(range=[0, 100], ticksuffix="%"),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(family="IBM Plex Sans"),
    )
    st.plotly_chart(fig, use_container_width=True)

    left, right = st.columns([1, 1], gap="large")
    with left:
        st.markdown('<div class="section-label">Efecto de casa</div>', unsafe_allow_html=True)
        house = _house_effects(view)
        fig_house = px.bar(
            house,
            x="efecto_casa",
            y="encuestadora_clean",
            orientation="h",
            color="efecto_casa",
            color_continuous_scale=["#1565C0", "#F7F7F5", "#C84B31"],
            color_continuous_midpoint=0,
            hover_data={"n": True, "sd_efecto": ":.1f"},
            labels={"efecto_casa": "pp vs mediana mensual", "encuestadora_clean": ""},
        )
        fig_house.update_layout(height=520, showlegend=False, coloraxis_showscale=False)
        st.plotly_chart(fig_house, use_container_width=True)

    with right:
        st.markdown('<div class="section-label">Efecto por sexenio</div>', unsafe_allow_html=True)
        heat = _house_effects_by_president(view)
        pivot = heat.pivot(index="encuestadora_clean", columns="Presidente", values="efecto_casa")
        fig_heat = px.imshow(
            pivot,
            color_continuous_scale=["#1565C0", "white", "#C84B31"],
            zmin=-10,
            zmax=10,
            labels={"x": "Presidente", "y": "Encuestadora", "color": "pp"},
            aspect="auto",
        )
        fig_heat.update_layout(height=520, font=dict(family="IBM Plex Sans"))
        st.plotly_chart(fig_heat, use_container_width=True)

    st.markdown('<div class="section-label">Datos</div>', unsafe_allow_html=True)
    display = view[[
        "fecha", "Presidente", "Encuestadora", "metodo", "Aprueba",
        "Desaprueba", "ratio_ad", "source",
    ]].sort_values("fecha", ascending=False)
    st.dataframe(
        display.rename(columns={
            "fecha": "Fecha",
            "metodo": "Método",
            "ratio_ad": "A/D",
            "source": "Verificación",
        }),
        use_container_width=True,
        hide_index=True,
    )
