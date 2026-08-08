"""
Congreso · Composición — pre-built hemicycle diagrams for the Cámara de
Diputados and Senado, with seat-click drill-down into a deputy's voting
history via ui.gaceta.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import plotly.io as pio
import streamlit as st
import streamlit.components.v1 as components

from ui.gaceta import render_gaceta
from ui.person_names import display_person_name
from ui.senado import render_senado

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
def load_prebuilt_hemicycle(election_id: str, view: str, cache_version: tuple[int, int]):
    """Deserialize the pre-built figure and table without running seat logic."""
    figure_path = HEMICYCLE_CACHE_DIR / f"{election_id}.{view}.figure.json"
    table_path = HEMICYCLE_CACHE_DIR / f"{election_id}.{view}.summary.html"
    if not figure_path.exists() or not table_path.exists():
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


def render_hemicycle_composition() -> None:
    if not HEMICYCLE_MANIFEST.exists():
        st.info(
            "Los hemiciclos aún no están preparados. Ejecuta "
            "`python3 aux_scripts/build_hemicycle_cache.py`."
        )
        return

    cache_version = get_hemicycle_cache_version()
    manifest = load_hemicycle_manifest(cache_version)
    elections = manifest.get("elections", [])
    dip_years = {e.rsplit("_", 1)[-1] for e in elections if e.startswith("DIP_MR_")}
    sen_years = {e.rsplit("_", 1)[-1] for e in elections if e.startswith("SEN_MR_")}
    shared_years = sorted(dip_years & sen_years, key=int)

    if not shared_years:
        st.info("La caché no contiene años con datos de ambas cámaras.")
        return

    source = manifest.get("source", {})
    available_views = manifest.get("views", ["electoral"])
    view_labels = {
        "current": "Composición actual",
        "electoral": "Resultado electoral 2024",
    }
    view = st.radio(
        "Vista",
        available_views,
        format_func=lambda value: view_labels.get(value, value),
        horizontal=True,
        key="comp_view",
    )
    if view == "current":
        rosters = manifest.get("rosters", {})
        cutoffs = []
        for meta in rosters.values():
            observed_at = meta.get("observed_at")
            if not observed_at:
                continue
            cutoff = datetime.fromisoformat(str(observed_at)).astimezone(
                ZoneInfo("America/Mexico_City")
            ).date().isoformat()
            if cutoff not in cutoffs:
                cutoffs.append(cutoff)
        cutoffs.sort()
        cutoff = ", ".join(cutoffs) if cutoffs else "sin fecha"
        st.caption(
            f"Personas en funciones y grupos parlamentarios según los directorios oficiales · "
            f"corte {cutoff}. El partido electoral se conserva en cada escaño como referencia."
        )
    else:
        st.caption(
            "Integración electoral final de 2024, no la afiliación parlamentaria actual. Fuente: "
            f"[{source.get('name', 'INE')}]"
            f"({source.get('url', 'https://ine.mx/integracion-de-diputaciones-y-senadurias-pef-2023-2024/')})."
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
    dip_fig, dip_table = load_prebuilt_hemicycle(dip_id, view, cache_version)
    sen_fig, sen_table = load_prebuilt_hemicycle(sen_id, view, cache_version)

    dip_event = None
    sen_event = None
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
            sen_event = st.plotly_chart(
                sen_fig,
                use_container_width=True,
                on_select="rerun",
                selection_mode="points",
                key=f"sen_{year_tab_sel}",
            )
            render_hemicycle_summary(sen_table)

    dip_points = (
        (dip_event.get("selection") or {}).get("points", [])
        if dip_event
        else []
    )
    sen_points = (
        (sen_event.get("selection") or {}).get("points", [])
        if sen_event
        else []
    )
    st.markdown("---")
    if not dip_points and not sen_points:
        st.caption(
            "Selecciona un escaño de la Cámara de Diputados o del Senado para "
            "consultar el historial de votaciones de esa persona."
        )
        return

    if dip_points:
        selected_data = dip_points[0].get("customdata") or []
        candidate_name = selected_data[0] if selected_data else ""
        party = selected_data[1] if len(selected_data) > 1 else ""
        seat_type = selected_data[2] if len(selected_data) > 2 else ""
        diputado_id = selected_data[5] if len(selected_data) > 5 else ""
        vote_person_id = selected_data[7] if len(selected_data) > 7 else ""
        member_status = selected_data[8] if len(selected_data) > 8 else "electoral"
        if not candidate_name:
            st.info("Este escaño no tiene un nombre de candidatura asociado.")
            return

        st.markdown(f"### Historial de votaciones · {display_person_name(candidate_name)}")
        st.caption(f"Escaño {seat_type} · {party} · {member_status.replace('_', ' ').title()} · Legislatura 66")
        if member_status == "vacante":
            st.info("Este escaño figura vacante en el directorio oficial consultado.")
            return
        if view == "current" and not vote_person_id:
            st.info("Esta persona aún no tiene votaciones nominales enlazadas en la base local.")
            return
        render_gaceta(
            "Diputado",
            diputado_id=diputado_id,
            gaceta_deputy_id=vote_person_id or None,
            candidate_name=candidate_name,
        )
        return

    selected_data = sen_points[0].get("customdata") or []
    candidate_name = selected_data[0] if selected_data else ""
    party = selected_data[1] if len(selected_data) > 1 else ""
    seat_type = selected_data[2] if len(selected_data) > 2 else ""
    senador_seat_id = selected_data[6] if len(selected_data) > 6 else ""
    vote_person_id = selected_data[7] if len(selected_data) > 7 else ""
    member_status = selected_data[8] if len(selected_data) > 8 else "electoral"
    if not candidate_name:
        st.info("Este escaño no tiene un nombre de candidatura asociado.")
        return

    st.markdown(f"### Historial de votaciones · {display_person_name(candidate_name)}")
    st.caption(f"Escaño {seat_type} · {party} · {member_status.replace('_', ' ').title()} · Legislatura 66")
    if member_status == "vacante":
        st.info("Este escaño figura vacante en el directorio oficial consultado.")
        return
    if view == "current" and not vote_person_id:
        st.info("Esta persona aún no tiene votaciones nominales enlazadas en la base local.")
        return
    render_senado(
        "Senador",
        senador_seat_id=senador_seat_id,
        senador_id=int(vote_person_id) if vote_person_id else None,
        candidate_name=candidate_name,
    )
