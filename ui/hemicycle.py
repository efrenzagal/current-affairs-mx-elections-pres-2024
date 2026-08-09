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


@st.cache_data(show_spinner=False)
def load_temporal_history(cache_version: tuple[int, int]) -> dict:
    path = HEMICYCLE_CACHE_DIR / "temporal_history.json"
    if not path.exists():
        return {
            "occupancy_by_seat": {},
            "party_by_person": {},
            "electoral_person_by_seat": {},
        }
    return json.loads(path.read_text(encoding="utf-8"))


def electoral_affiliation_note(
    parties: list[dict], election_party: str, person_name: str
) -> str | None:
    """Explain a general election-party/observed-group mismatch conservatively."""
    if not parties or not election_party:
        return None
    election_party = str(election_party).strip().upper()
    observed_parties = [
        str(episode.get("party_key") or "").strip().upper()
        for episode in parties
        if episode.get("party_key")
    ]
    if not observed_parties:
        return None
    first = parties[0]
    first_party = observed_parties[0]
    if not first_party or first_party == election_party:
        return None
    first_date = str(first.get("valid_from") or "")[:10] or "fecha no disponible"
    unique_observed = list(dict.fromkeys(observed_parties))
    observation_count = sum(int(episode.get("observations") or 0) for episode in parties)
    person = display_person_name(person_name)
    heading = "**Partido electoral distinto del grupo parlamentario observado.** "

    if election_party not in unique_observed and len(unique_observed) == 1:
        votes = (
            f"En las {observation_count} votaciones nominales descargadas"
            if observation_count
            else "En todas las votaciones nominales descargadas"
        )
        evidence = (
            f"{votes}, {person} figura siempre en el grupo parlamentario "
            f"{first_party}, desde la primera observación ({first_date}); no aparece "
            f"registrada bajo {election_party}."
        )
    elif election_party not in unique_observed:
        evidence = (
            f"{person} figura primero en {first_party} ({first_date}) y después en "
            f"{', '.join(unique_observed[1:])}; ninguna observación aparece registrada "
            f"bajo {election_party}."
        )
    else:
        evidence = (
            f"{person} figura primero en {first_party} ({first_date}). La cronología "
            "inferior muestra cuándo aparece después el grupo electoral."
        )

    return (
        f"{heading}El INE atribuyó electoralmente el escaño a {election_party}. "
        f"{evidence} Esto compara la etiqueta del grupo parlamentario impresa en las "
        "votaciones, no la coincidencia del sentido de sus votos con otro partido, y no "
        "demuestra por sí solo una fecha de cambio de militancia."
    )


def render_seat_timeline(
    history: dict,
    chamber: str,
    seat_id: str,
    vote_person_id: str,
    *,
    use_electoral_bridge: bool = False,
    election_party: str = "",
    person_name: str = "",
) -> None:
    occupancy = history.get("occupancy_by_seat", {}).get(chamber, {}).get(seat_id, [])
    timeline_person_id = vote_person_id
    if use_electoral_bridge and not timeline_person_id:
        timeline_person_id = (
            history.get("electoral_person_by_seat", {}).get(chamber, {}).get(seat_id, "")
        )
    parties = history.get("party_by_person", {}).get(chamber, {}).get(timeline_person_id, [])
    if not occupancy and not parties:
        return
    note = electoral_affiliation_note(parties, election_party, person_name)
    if note:
        st.info(note)
    with st.expander("Cronología observada del escaño y la afiliación"):
        if occupancy:
            st.caption(
                "Ocupación según cortes guardados del directorio; las fechas son límites de "
                "observación, no fechas legales inferidas retroactivamente."
            )
            for episode in occupancy:
                start = str(episode.get("valid_from", ""))[:10]
                end = episode.get("valid_to")
                interval = (
                    f"desde {start} hasta antes de {str(end)[:10]}"
                    if end
                    else f"desde {start} · último estado observado"
                )
                name = display_person_name(episode.get("occupant_name") or "Vacante")
                st.markdown(
                    f"- `{interval}` · "
                    f"{name} · {episode.get('status', '')} · {episode.get('party_key', '')}"
                )
        if parties:
            st.caption("Afiliación impresa en las votaciones nominales de esta persona.")
            for episode in parties:
                start = str(episode.get("valid_from", ""))[:10]
                end = episode.get("valid_to")
                interval = (
                    f"desde {start} hasta antes de {str(end)[:10]}"
                    if end
                    else f"desde {start} · último grupo observado"
                )
                conflicts = int(episode.get("conflicting_observations") or 0)
                conflict_note = f" · {conflicts} conflictos del mismo día" if conflicts else ""
                st.markdown(
                    f"- `{interval}` · "
                    f"{episode.get('party_key', '')} · {episode.get('observations', 0)} observaciones"
                    f"{conflict_note}"
                )


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
    temporal_history = load_temporal_history(cache_version)
    elections = manifest.get("elections", [])
    dip_years = {e.rsplit("_", 1)[-1] for e in elections if e.startswith("DIP_MR_")}
    sen_years = {e.rsplit("_", 1)[-1] for e in elections if e.startswith("SEN_MR_")}
    shared_years = sorted(dip_years & sen_years, key=int)

    if not shared_years:
        st.info("La caché no contiene años con datos de ambas cámaras.")
        return

    source = manifest.get("source", {})
    available_views = manifest.get("views", ["electoral"])
    composition_dates = manifest.get("composition_dates", [])
    if composition_dates:
        available_views = [*available_views, "historical"]
    view_labels = {
        "current": "Composición actual",
        "electoral": "Resultado electoral 2024",
        "historical": "Composición en fecha",
    }
    view = st.radio(
        "Vista",
        available_views,
        format_func=lambda value: view_labels.get(value, value),
        horizontal=True,
        key="comp_view",
    )
    selected_composition_date = None
    asset_view = view
    if view == "historical":
        selected_composition_date = st.selectbox(
            "Fecha del directorio oficial",
            composition_dates,
            index=len(composition_dates) - 1,
            key="comp_snapshot_date",
        )
        asset_view = f"asof-{selected_composition_date}"

    if view in {"current", "historical"}:
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
        cutoff = selected_composition_date or (", ".join(cutoffs) if cutoffs else "sin fecha")
        st.caption(
            f"Directorio oficial observado al corte {cutoff}. Licencias y vacantes se muestran "
            "como estados separados y no se suman al grupo parlamentario. El partido electoral "
            "se conserva como referencia."
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
    dip_fig, dip_table = load_prebuilt_hemicycle(dip_id, asset_view, cache_version)
    sen_fig, sen_table = load_prebuilt_hemicycle(sen_id, asset_view, cache_version)

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
        election_name = selected_data[9] if len(selected_data) > 9 else ""
        election_party = selected_data[10] if len(selected_data) > 10 else ""
        reported_current_party = selected_data[11] if len(selected_data) > 11 else ""
        if not candidate_name:
            st.info("Este escaño no tiene un nombre de candidatura asociado.")
            return

        st.markdown(f"### Historial de votaciones · {display_person_name(candidate_name)}")
        st.caption(f"Escaño {seat_type} · {party} · {member_status.replace('_', ' ').title()} · Legislatura 66")
        if view != "electoral" and election_name:
            st.caption(
                f"Origen electoral: {display_person_name(election_name)} · {election_party}. "
                + (f"Grupo registrado: {reported_current_party}." if reported_current_party else "")
            )
        render_seat_timeline(
            temporal_history,
            "DIP",
            diputado_id,
            vote_person_id,
            use_electoral_bridge=view == "electoral",
            election_party=election_party,
            person_name=candidate_name,
        )
        if member_status == "vacante":
            st.info("Este escaño figura vacante en el directorio oficial consultado.")
            return
        if member_status == "licencia":
            st.info(
                "El directorio marca a esta persona con licencia. No se atribuye el escaño "
                "a un grupo activo hasta identificar una suplencia en una fuente oficial."
            )
            return
        if view in {"current", "historical"} and not vote_person_id:
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
    election_name = selected_data[9] if len(selected_data) > 9 else ""
    election_party = selected_data[10] if len(selected_data) > 10 else ""
    reported_current_party = selected_data[11] if len(selected_data) > 11 else ""
    if not candidate_name:
        st.info("Este escaño no tiene un nombre de candidatura asociado.")
        return

    st.markdown(f"### Historial de votaciones · {display_person_name(candidate_name)}")
    st.caption(f"Escaño {seat_type} · {party} · {member_status.replace('_', ' ').title()} · Legislatura 66")
    if view != "electoral" and election_name:
        st.caption(
            f"Origen electoral: {display_person_name(election_name)} · {election_party}. "
            + (f"Grupo registrado: {reported_current_party}." if reported_current_party else "")
        )
    render_seat_timeline(
        temporal_history,
        "SEN",
        senador_seat_id,
        vote_person_id,
        use_electoral_bridge=view == "electoral",
        election_party=election_party,
        person_name=candidate_name,
    )
    if member_status == "vacante":
        st.info("Este escaño figura vacante en el directorio oficial consultado.")
        return
    if view in {"current", "historical"} and not vote_person_id:
        st.info("Esta persona aún no tiene votaciones nominales enlazadas en la base local.")
        return
    render_senado(
        "Senador",
        senador_seat_id=senador_seat_id,
        senador_id=int(vote_person_id) if vote_person_id else None,
        candidate_name=candidate_name,
    )
