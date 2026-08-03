"""
INE · Explorador Electoral v2
5-section layout: Trayectoria | Aprobación | Congreso · Composición |
Congreso · Votos por diputado | Congreso · Clasificación de votos

Run:
    streamlit run ine_explorer_v2.py
"""

from __future__ import annotations

import json
from pathlib import Path

import plotly.io as pio
import streamlit as st
import streamlit.components.v1 as components

from ui.approval import render_approval
from ui.gaceta import render_gaceta
from ui.person_names import display_person_name
from ui.trajectory import render_trajectory

# ── Page config ────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="INE · Explorador Electoral",
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

st.markdown("**INE · Explorador Electoral de México**")

# ── Pre-built hemicycle assets ─────────────────────────────────────────────────

HEMICYCLE_CACHE_DIR = Path("data/cache/hemicycles")
HEMICYCLE_MANIFEST = HEMICYCLE_CACHE_DIR / "manifest.json"


def get_hemicycle_cache_version() -> tuple[int, int]:
    """Version the read cache by the manifest file; never query SQLite here."""
    if not HEMICYCLE_MANIFEST.exists():
        return (0, 0)
    stat = HEMICYCLE_MANIFEST.stat()
    return (stat.st_mtime_ns, stat.st_size)


@st.cache_data(show_spinner=False)
def load_hemicycle_manifest(cache_version: tuple[int, int]) -> dict:
    with HEMICYCLE_MANIFEST.open(encoding="utf-8") as cache_file:
        return json.load(cache_file)


@st.cache_data(show_spinner=False)
def load_prebuilt_hemicycle(election_id: str, cache_version: tuple[int, int]):
    """Deserialize the pre-built figure and table without running seat logic."""
    figure_path = HEMICYCLE_CACHE_DIR / f"{election_id}.figure.json"
    table_path = HEMICYCLE_CACHE_DIR / f"{election_id}.summary.html"
    return pio.from_json(figure_path.read_text(encoding="utf-8")), table_path.read_text(encoding="utf-8")


def render_hemicycle_summary(table_html: str) -> None:
    components.html(
        f"""<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&display=swap" rel="stylesheet">
        <div style="background:#1a1a1a;border-radius:8px;padding:16px;font-family:'IBM Plex Mono',monospace">
        {table_html}</div>""",
        height=360,
        scrolling=True,
    )


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
    if not HEMICYCLE_MANIFEST.exists():
        st.info(
            "Los hemiciclos aún no están preparados. Ejecuta "
            "`python3 aux_scripts/build_hemicycle_cache.py`."
        )
    else:
        cache_version = get_hemicycle_cache_version()
        manifest = load_hemicycle_manifest(cache_version)
        elections = manifest.get("elections", [])
        dip_years = {e.rsplit("_", 1)[-1] for e in elections if e.startswith("DIP_MR_")}
        sen_years = {e.rsplit("_", 1)[-1] for e in elections if e.startswith("SEN_MR_")}
        shared_years = sorted(dip_years & sen_years, key=int)

        if not shared_years:
            st.info("La caché no contiene años con datos de ambas cámaras.")
        else:
            source = manifest.get("source", {})
            st.caption(
                "Composición electoral final, no la afiliación parlamentaria actual. "
                "Fuente: "
                f"[{source.get('name', 'INE')}]"
                f"({source.get('url', 'https://ine.mx/integracion-de-diputaciones-y-senadurias-pef-2023-2024/')}). "
                "Los escaños se leen directamente de la integración oficial; no se estiman."
            )
            year_tab_sel = st.radio(
                "Año electoral",
                shared_years,
                horizontal=True,
                index=len(shared_years) - 1,
                key="comp_year",
            )

            dip_id = f"DIP_MR_{year_tab_sel}"
            sen_id = f"SEN_MR_{year_tab_sel}"
            dip_fig, dip_table = load_prebuilt_hemicycle(dip_id, cache_version)
            sen_fig, sen_table = load_prebuilt_hemicycle(sen_id, cache_version)

            dip_event = None
            dip_col, sen_col = st.columns(2, gap="large")
            with dip_col:
                st.markdown("#### Cámara de Diputados · 500 escaños")
                if dip_fig is None:
                    st.info(f"Sin datos de escaños para {dip_id}.")
                else:
                    dip_event = st.plotly_chart(
                        dip_fig,
                        use_container_width=True,
                        on_select="rerun",
                        selection_mode="points",
                        key=f"dip_{year_tab_sel}",
                    )
                    render_hemicycle_summary(dip_table)
            with sen_col:
                st.markdown("#### Senado de la República · 128 escaños")
                if sen_fig is None:
                    st.info(f"Sin datos de escaños para {sen_id}.")
                else:
                    st.plotly_chart(sen_fig, use_container_width=True, key=f"sen_{year_tab_sel}")
                    render_hemicycle_summary(sen_table)

            dip_points = (
                (dip_event.get("selection") or {}).get("points", [])
                if dip_event
                else []
            )
            st.markdown("---")
            if not dip_points:
                st.caption(
                    "Selecciona un escaño de la Cámara de Diputados para consultar "
                    "el historial de votaciones de esa persona."
                )
            else:
                selected_data = dip_points[0].get("customdata") or []
                candidate_name = selected_data[0] if selected_data else ""
                party = selected_data[1] if len(selected_data) > 1 else ""
                seat_type = selected_data[2] if len(selected_data) > 2 else ""
                diputado_id = selected_data[5] if len(selected_data) > 5 else ""
                if candidate_name:
                    st.markdown(
                        f"### Historial de votaciones · "
                        f"{display_person_name(candidate_name)}"
                    )
                    st.caption(
                        f"Escaño {seat_type} · {party} · Legislatura 66"
                    )
                    render_gaceta(
                        "Diputado",
                        diputado_id=diputado_id,
                        candidate_name=candidate_name,
                    )
                else:
                    st.info("Este escaño no tiene un nombre de candidatura asociado.")


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
