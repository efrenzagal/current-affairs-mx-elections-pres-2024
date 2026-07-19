"""
INE · Explorador Electoral v2
6-tab layout: Elecciones | Tendencias | Trayectoria | Aprobación | Congreso · Composición | Congreso · Votos

Run:
    streamlit run ine_explorer_v2.py
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from ui.common import (
    CYCLE_BLOCS, MATERIALIZED_DIR, TIMESERIES_PATH,
    election_label, safe_int,
)
from ui.charts import (
    render_both_charts, render_hist_both_charts,
    render_ternary_bubble, render_hist_ternary,
    render_timeseries_for_estado,
)
from ui.approval import render_approval
from ui.gaceta import render_gaceta
from ui.trajectory import render_trajectory
from ui.maps import (
    load_municipios_geojson, render_mexico_map,
    render_hist_winner_map, render_national_generic_map,
)
from ui.tables import header_badge, render_results_table, render_scorecards

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

@st.cache_data(show_spinner=False)
def get_available_elections() -> list[str]:
    if not MATERIALIZED_DIR.exists():
        return []
    return sorted(
        f.stem.replace("view_estado_", "")
        for f in MATERIALIZED_DIR.glob("view_estado_*.parquet")
    )

@st.cache_data(show_spinner="Cargando datos...")
def load_view(granularity: str, election_id: str) -> pd.DataFrame:
    path = MATERIALIZED_DIR / f"view_{granularity}_{election_id}.parquet"
    if not path.exists():
        return pd.DataFrame()
    return pd.read_parquet(path)

@st.cache_data(show_spinner=False)
def load_candidates() -> pd.DataFrame:
    path = MATERIALIZED_DIR / "dim_candidatos.parquet"
    if not path.exists():
        return pd.DataFrame()
    return pd.read_parquet(path)

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


# ── Shared state controls helper ───────────────────────────────────────────────

def _election_controls(elections: list[str]) -> tuple[str, str, str, int]:
    """Renders Año / Tipo / Estado controls; returns (election_sel, estado_sel, page, id_estado)."""
    TYPE_LABELS = {"PRE": "Presidencial", "DIP": "Diputados", "SEN": "Senadores"}

    all_years    = sorted({e.split("_")[-1] for e in elections}, reverse=True)
    default_year = "2024" if "2024" in all_years else all_years[0]

    cols = st.columns([0.7, 1.1, 0.9, 1.3])
    with cols[0]:
        year_sel = st.selectbox("Año", all_years, index=all_years.index(default_year),
                                key="el_year")
    year_elections = sorted(
        [e for e in elections if e.endswith(f"_{year_sel}")],
        key=lambda e: list(TYPE_LABELS.keys()).index("_".join(e.split("_")[:-1]))
        if "_".join(e.split("_")[:-1]) in TYPE_LABELS else 99,
    )
    default_et = next((e for e in year_elections if e.startswith("PRE")), year_elections[0])
    with cols[1]:
        election_sel = st.selectbox(
            "Tipo", year_elections,
            index=year_elections.index(default_et),
            format_func=lambda e: TYPE_LABELS.get("_".join(e.split("_")[:-1]), election_label(e)),
            key="el_type",
        )
    with cols[2]:
        page = st.selectbox("Vista", ["Estado", "Municipio"], key="el_page")

    df_est_full = load_view("estado", election_sel)
    estado_options = (
        df_est_full[["id_estado", "nombre_estado"]]
        .dropna().drop_duplicates().sort_values("nombre_estado")
    )
    estado_names = estado_options["nombre_estado"].tolist()
    default_e    = next((e for e in estado_names if "CIUDAD" in e.upper()), estado_names[0])
    with cols[3]:
        estado_sel = st.selectbox("Estado", estado_names,
                                  index=estado_names.index(default_e), key="el_estado")

    id_estado = int(
        estado_options.loc[estado_options["nombre_estado"] == estado_sel, "id_estado"].iloc[0]
    )
    return election_sel, estado_sel, page, id_estado, df_est_full


def render_results_tab(
    df_raw: pd.DataFrame,
    page_level: str,
    election_id: str,
    candidates_df: pd.DataFrame = None,
    id_distrito: Optional[int] = None,
    scorecards: Optional[tuple] = None,
):
    if candidates_df is None:
        candidates_df = pd.DataFrame()

    blocs   = CYCLE_BLOCS.get(election_id)
    is_2024 = election_id == "PRE_2024"

    if page_level == "Estado":
        st.markdown("---")
        map_col, divider_col, ternary_col = st.columns([1.15, 0.04, 0.85], gap="medium")

        if is_2024:
            with map_col:
                render_mexico_map(df_raw, map_key_suffix="estado", height=560)
            with divider_col:
                st.markdown('<div class="panel-divider"></div>', unsafe_allow_html=True)
            with ternary_col:
                st.markdown('<div class="section-label">Distribucion ternaria por Municipio</div>',
                            unsafe_allow_html=True)
                n_muns = df_raw["municipio"].nunique()
                st.caption(f"Mostrando todos los municipios disponibles: {n_muns:,}")
                render_ternary_bubble(df_raw, "municipio", "municipio",
                                      "por Municipio", n_bubbles=None, height=560)
        elif blocs is not None:
            geo = load_municipios_geojson()
            with map_col:
                render_hist_winner_map(df_raw, blocs, geo,
                                       f"Ganador por Municipio · {election_label(election_id)}",
                                       height=560)
            with divider_col:
                st.markdown('<div class="panel-divider"></div>', unsafe_allow_html=True)
            with ternary_col:
                st.markdown('<div class="section-label">Distribución ternaria por Municipio</div>',
                            unsafe_allow_html=True)
                render_hist_ternary(df_raw, blocs, election_label(election_id), height=560)
        else:
            with map_col:
                st.info("Visualización de mapa no disponible para este tipo de elección.")

        st.markdown("---")
        if scorecards is not None:
            render_scorecards(*scorecards)

        st.markdown("---")
        if is_2024:
            render_both_charts(df_raw)
        elif blocs is not None:
            render_hist_both_charts(df_raw, blocs)

        if is_2024:
            st.markdown("---")
            render_results_table(df_raw, "municipio", "Municipio",
                                 election_id, candidates_df, id_distrito)
            render_results_table(df_raw, "seccion", "Sección",
                                 election_id, candidates_df, id_distrito)

    elif page_level == "Municipio":
        st.markdown("---")
        if is_2024:
            render_mexico_map(df_raw, map_key_suffix="municipio")
        if scorecards is not None:
            render_scorecards(*scorecards)

        st.markdown("---")
        if is_2024:
            render_both_charts(df_raw)
        elif blocs is not None:
            render_hist_both_charts(df_raw, blocs)

        st.markdown("---")
        col1, _ = st.columns(2)
        with col1:
            render_results_table(df_raw, "seccion", "Sección",
                                 election_id, candidates_df, id_distrito)


# ── Tabs ───────────────────────────────────────────────────────────────────────

tab_elec, tab_tend, tab_traj, tab_aprob, tab_comp, tab_votos = st.tabs([
    "Elecciones",
    "Tendencias",
    "Trayectoria",
    "Aprobación",
    "Congreso · Composición",
    "Congreso · Votos",
])

elections    = get_available_elections()
candidates_df = load_candidates()

# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 · ELECCIONES
# ══════════════════════════════════════════════════════════════════════════════

with tab_elec:
    if not elections:
        st.error("No se encontraron archivos Parquet. Ejecuta el pipeline de ingesta primero.")
        st.stop()

    election_sel, estado_sel, page, id_estado_sel, df_est_full = _election_controls(elections)

    if page == "Estado":
        df_view = df_est_full[df_est_full["id_estado"] == id_estado_sel]
        if df_view.empty:
            st.info("Sin datos para este estado.")
        else:
            meta_row  = df_view.drop_duplicates("id_estado").iloc[0]
            num_dist  = safe_int(meta_row.get("num_distritos"))
            num_mun   = safe_int(meta_row.get("num_municipios"))
            num_sec   = safe_int(meta_row.get("num_secciones"))
            num_cas   = safe_int(meta_row.get("num_casillas"))
            total_v   = safe_int(df_view.drop_duplicates("id_estado")["total_votos"].sum())
            lista_nom = safe_int(meta_row.get("lista_nominal_part"))
            part_pct  = total_v / lista_nom * 100 if lista_nom > 0 else 0
            nulos_raw = safe_int(df_view.drop_duplicates("id_estado")["num_votos_nulos"].sum())
            nulos_pct = nulos_raw / total_v * 100 if total_v > 0 else 0

            header_badge([estado_sel, f"{num_dist} distritos",
                          f"{num_mun} municipios", f"{num_sec} secciones", f"{num_cas} actas"])

            df_mun_view = load_view("municipio", election_sel)
            df_mun_view = df_mun_view[df_mun_view["id_estado"] == id_estado_sel]
            render_results_tab(df_mun_view, "Estado", election_sel, candidates_df,
                               scorecards=(total_v, lista_nom, part_pct, nulos_pct))

    else:  # Municipio
        df_mun_full = load_view("municipio", election_sel)
        df_e        = df_mun_full[df_mun_full["id_estado"] == id_estado_sel]
        municipios  = sorted(df_e["municipio"].dropna().unique())
        mun_sel     = st.selectbox("Municipio", municipios, key="el_mun")
        df_view     = df_e[df_e["municipio"] == mun_sel]

        if df_view.empty:
            st.info("Sin datos para este municipio.")
        else:
            meta_row  = df_view.drop_duplicates("municipio").iloc[0]
            num_cas   = safe_int(meta_row.get("num_casillas"))
            num_sec   = safe_int(meta_row.get("num_secciones"))
            total_v   = safe_int(df_view.drop_duplicates("municipio")["total_votos"].sum())
            lista_nom = safe_int(meta_row.get("lista_nominal_part"))
            part_pct  = total_v / lista_nom * 100 if lista_nom > 0 else 0
            nulos_raw = safe_int(df_view.drop_duplicates("municipio")["num_votos_nulos"].sum())
            nulos_pct = nulos_raw / total_v * 100 if total_v > 0 else 0

            header_badge([estado_sel, f"Municipio: {mun_sel}",
                          f"{num_sec} secciones", f"{num_cas} actas"])
            render_results_tab(df_view, "Municipio", election_sel, candidates_df,
                               scorecards=(total_v, lista_nom, part_pct, nulos_pct))

            df_sec_view = load_view("seccion", election_sel)
            df_sec_view = df_sec_view[
                (df_sec_view["id_estado"] == id_estado_sel) &
                (df_sec_view["municipio"] == mun_sel)
            ]
            if not df_sec_view.empty:
                st.markdown("---")
                render_results_table(df_sec_view, "seccion", "Sección",
                                     election_sel, candidates_df)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 · TENDENCIAS
# ══════════════════════════════════════════════════════════════════════════════

with tab_tend:
    df_ts = load_timeseries(
        TIMESERIES_PATH.stat().st_mtime if TIMESERIES_PATH.exists() else 0.0
    )

    if df_ts.empty:
        st.info("No se encontró el archivo de series de tiempo. "
                "Ejecuta `python ingestion/electoral_materialize.py timeseries` primero.")
    else:
        # Estado picker (timeseries is per-state; national aggregation is complex)
        state_opts = (
            df_ts[["id_estado", "nombre_estado"]]
            .dropna().drop_duplicates()
            .sort_values("nombre_estado")
        )
        state_names  = state_opts["nombre_estado"].tolist()
        default_s    = next((s for s in state_names if "Ciudad" in s), state_names[0])
        ts_estado    = st.selectbox("Estado", state_names,
                                    index=state_names.index(default_s), key="ts_estado")
        ts_id_estado = int(
            state_opts.loc[state_opts["nombre_estado"] == ts_estado, "id_estado"].iloc[0]
        )

        st.markdown("---")
        render_timeseries_for_estado(df_ts, ts_id_estado, ts_estado)

        # Historical map context below the timeseries
        if elections:
            st.markdown("---")
            st.markdown('<div class="section-label">Mapa histórico nacional por elección</div>',
                        unsafe_allow_html=True)
            geo = load_municipios_geojson()
            pre_elections = [e for e in elections if e.startswith("PRE") and CYCLE_BLOCS.get(e)]
            if pre_elections and geo:
                map_sel = st.selectbox("Elección", pre_elections,
                                       format_func=election_label, key="ts_map_sel")
                df_nat  = load_view("municipio", map_sel)
                blocs   = CYCLE_BLOCS.get(map_sel)
                if not df_nat.empty and blocs:
                    render_hist_winner_map(df_nat, blocs, geo,
                                           f"Ganador por Municipio · {election_label(map_sel)}")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 · TRAYECTORIA
# ══════════════════════════════════════════════════════════════════════════════

with tab_traj:
    render_trajectory()


# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 · APROBACIÓN
# ══════════════════════════════════════════════════════════════════════════════

with tab_aprob:
    render_approval()


# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 · CONGRESO · COMPOSICIÓN
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
