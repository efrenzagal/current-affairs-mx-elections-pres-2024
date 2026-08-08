"""
INE · Explorador Electoral v2
5-section layout: Trayectoria | Aprobación | Congreso · Composición |
Congreso · Votos por diputado | Congreso · Clasificación de votos

Run:
    streamlit run ine_explorer_v2.py
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from ui.approval import render_approval
from ui.gaceta import render_gaceta
from ui.hemicycle import render_hemicycle_composition
from ui.trajectory import render_trajectory

# ── Page config ────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="",
    page_icon=Path("assets/favicon.svg"),
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

# ── Lazy section navigation ────────────────────────────────────────────────────

SECTION_LABELS = [
    "Trayectoria",
    "Aprobación",
    "Congreso · Composición",
    "Congreso · Votos por diputado",
    "Congreso · Clasificación de votos",
]
active_section = st.segmented_control(
    "Sección",
    SECTION_LABELS,
    default=SECTION_LABELS[0],
    key="main_section",
    label_visibility="collapsed",
    width="stretch",
)
previous_section = st.session_state.get("_last_main_section")
section_just_opened = previous_section != active_section
st.session_state["_last_main_section"] = active_section

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1 · TRAYECTORIA
# ══════════════════════════════════════════════════════════════════════════════

if active_section == "Trayectoria":
    render_trajectory()


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2 · APROBACIÓN
# ══════════════════════════════════════════════════════════════════════════════

elif active_section == "Aprobación":
    render_approval()


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3 · CONGRESO · COMPOSICIÓN
# ══════════════════════════════════════════════════════════════════════════════

elif active_section == "Congreso · Composición":
    render_hemicycle_composition()


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4 · CONGRESO · VOTOS POR DIPUTADO
# ══════════════════════════════════════════════════════════════════════════════

elif active_section == "Congreso · Votos por diputado":
    render_gaceta("Diputado", randomize_deputy=section_just_opened)


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 5 · CONGRESO · CLASIFICACIÓN DE VOTOS
# ══════════════════════════════════════════════════════════════════════════════

elif active_section == "Congreso · Clasificación de votos":
    render_gaceta("Clasificación")
