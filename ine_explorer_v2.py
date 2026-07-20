"""
INE · Explorador Electoral v2
4-tab layout: Trayectoria | Aprobación | Congreso · Composición | Congreso · Votos

Run:
    streamlit run ine_explorer_v2.py
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from ui.approval import render_approval
from ui.gaceta import render_gaceta
from ui.trajectory import render_trajectory

from aux_scripts.seat_allocations.hemicycle_explorer import (
    build_figure,
    build_summary_html,
    get_elections,
    load_composicion_dip,
    load_composicion_sen,
    dip_winners_from_votes,
    sen_winners_from_votes,
)

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

st.markdown("**INE · Explorador Electoral de México**")

# ── Data loaders ───────────────────────────────────────────────────────────────

DB_PATH = Path("election_data.db")

@st.cache_resource(show_spinner=False)
def get_db_conn():
    if not DB_PATH.exists():
        return None
    return sqlite3.connect(str(DB_PATH), check_same_thread=False)

@st.cache_data(show_spinner="Cargando hemiciclo...")
def load_hemicycle_winners(election_id: str) -> pd.DataFrame:
    conn = get_db_conn()
    if conn is None:
        return pd.DataFrame()
    if election_id.startswith("DIP"):
        winners = load_composicion_dip(election_id, conn)
        if winners is None or winners.empty:
            winners = dip_winners_from_votes(conn, election_id)
    else:
        winners = load_composicion_sen(election_id, conn)
        if winners is None or winners.empty:
            winners = sen_winners_from_votes(conn, election_id)
    return winners if winners is not None else pd.DataFrame()

@st.cache_data(show_spinner=False)
def get_hemicycle_elections(prefix: str) -> list[str]:
    conn = get_db_conn()
    if conn is None:
        return []
    return get_elections(conn, prefix)


# ── Tabs ───────────────────────────────────────────────────────────────────────

tab_traj, tab_aprob, tab_comp, tab_votos = st.tabs([
    "Trayectoria",
    "Aprobación",
    "Congreso · Composición",
    "Congreso · Votos",
])

# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 · TRAYECTORIA
# ══════════════════════════════════════════════════════════════════════════════

with tab_traj:
    render_trajectory()


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 · APROBACIÓN
# ══════════════════════════════════════════════════════════════════════════════

with tab_aprob:
    render_approval()


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 · CONGRESO · COMPOSICIÓN
# ══════════════════════════════════════════════════════════════════════════════

with tab_comp:
    conn = get_db_conn()
    if conn is None:
        st.error("No se encontró la base de datos. "
                 "Ejecuta `python ingestion/electoral_ingest.py` primero.")
    else:
        chamber_sel = st.radio(
            "Cámara",
            ["Cámara de Diputados · 500 escaños", "Senado de la República · 128 escaños"],
            horizontal=True,
            key="comp_chamber",
        )
        is_dip   = chamber_sel.startswith("Cámara")
        prefix   = "DIP_MR" if is_dip else "SEN_MR"
        hemi_elections = get_hemicycle_elections(prefix)

        if not hemi_elections:
            st.info("Sin datos de escaños en la base de datos.")
        else:
            year_labels = [e.split("_")[-1] for e in hemi_elections]
            year_tab_sel = st.radio("Año", year_labels, horizontal=True,
                                    index=len(year_labels) - 1, key="comp_year")
            election_id  = f"{prefix}_{year_tab_sel}"

            winners = load_hemicycle_winners(election_id)
            if winners.empty:
                st.info(f"Sin datos de escaños para {election_id}.")
            else:
                fig        = build_figure(winners, election_id)
                table_html = build_summary_html(winners, election_id)

                hem_col, tbl_col = st.columns([2.2, 1], gap="large")
                with hem_col:
                    st.plotly_chart(fig, use_container_width=True)
                with tbl_col:
                    components.html(
                        f"""<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&display=swap" rel="stylesheet">
                        <div style="background:#1a1a1a;border-radius:8px;padding:16px;font-family:'IBM Plex Mono',monospace">
                        {table_html}</div>""",
                        height=600,
                        scrolling=True,
                    )


# ══════════════════════════════════════════════════════════════════════════════
# TAB 5 · CONGRESO · VOTOS (GACETA)
# ══════════════════════════════════════════════════════════════════════════════

with tab_votos:
    render_gaceta()
