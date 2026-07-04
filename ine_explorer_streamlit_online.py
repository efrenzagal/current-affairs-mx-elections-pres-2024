"""
INE PREP · Explorador de Resultados Electorales
Streamlit dashboard backed by materialized Parquet files.

Run:
    python ingestion/electoral_ingest.py       # raw sources -> SQLite
    python ingestion/electoral_materialize.py    # SQLite -> all parquets
    streamlit run ine_explorer_streamlit_online.py
"""

from pathlib import Path
from typing import Optional

import pandas as pd
import streamlit as st

from ui.common import (
    CYCLE_BLOCS, MATERIALIZED_DIR, TIMESERIES_PATH,
    election_label, safe_int,
)
from ui.charts  import render_both_charts, render_hist_both_charts, render_ternary_bubble, render_hist_ternary, render_timeseries_for_estado
from ui.gaceta  import render_gaceta
from ui.maps    import load_municipios_geojson, render_mexico_map, render_hist_winner_map, render_national_generic_map
from ui.tables  import header_badge, render_results_table, render_scorecards

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


# ── Data loaders ───────────────────────────────────────────────────────────────

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
    # _mtime forces cache invalidation when the parquet is regenerated.
    if not TIMESERIES_PATH.exists():
        return pd.DataFrame()
    df = pd.read_parquet(TIMESERIES_PATH)
    df["nombre_estado"] = df["nombre_estado"].str.strip().str.title()

    # MORENA was founded in 2014 — inject explicit 0s for 2012 so the line
    # starts at the origin rather than beginning abruptly at 2018.
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


@st.cache_data(show_spinner="Cargando datos nacionales...")
def load_mun_national(election_id: str) -> pd.DataFrame:
    return load_view("municipio", election_id)


# ── Results orchestrator ───────────────────────────────────────────────────────

def render_results_tab(
    df_raw: pd.DataFrame,
    page_level: str,
    election_id: str,
    candidates_df: pd.DataFrame = None,
    id_distrito: Optional[int] = None,
    scorecards: Optional[tuple] = None,
):
    """
    Sequences the four result sub-views: map/ternary → scorecards → bars → tables.
    Works for all elections: detects PRE-with-blocs vs DIP/SEN automatically.
    """
    if candidates_df is None:
        candidates_df = pd.DataFrame()

    blocs   = CYCLE_BLOCS.get(election_id)
    is_2024 = election_id == "PRE_2024"

    if page_level == "Estado":
        # 1. MAP + TERNARY
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

        # 2. SCORECARDS
        st.markdown("---")
        if scorecards is not None:
            render_scorecards(*scorecards)

        # 3. BAR CHARTS
        st.markdown("---")
        if is_2024:
            render_both_charts(df_raw)
        elif blocs is not None:
            render_hist_both_charts(df_raw, blocs)
        else:
            render_hist_both_charts(df_raw, {
                "map": {},
                "A": {"label": "A", "color": "#888"},
                "B": {"label": "B", "color": "#555"},
                "C": {"label": "C", "color": "#333"},
            })

        # 4. TABLES (2024 only — historical elections lack dim_candidatos entries)
        if is_2024:
            st.markdown("---")
            render_results_table(df_raw, "municipio", "Municipio",
                                 election_id, candidates_df, id_distrito)
            render_results_table(df_raw, "seccion", "Sección",
                                 election_id, candidates_df, id_distrito)

    elif page_level == "Municipio":
        # 1. MAP (single polygon) — 2024 only
        st.markdown("---")
        if is_2024:
            render_mexico_map(df_raw, map_key_suffix="municipio")
        if scorecards is not None:
            render_scorecards(*scorecards)

        # 2. BAR CHARTS
        st.markdown("---")
        if is_2024:
            render_both_charts(df_raw)
        elif blocs is not None:
            render_hist_both_charts(df_raw, blocs)
        else:
            render_hist_both_charts(df_raw, {
                "map": {},
                "A": {"label": "A", "color": "#888"},
                "B": {"label": "B", "color": "#555"},
                "C": {"label": "C", "color": "#333"},
            })

        # 3. TABLES
        st.markdown("---")
        col1, _ = st.columns(2)
        with col1:
            render_results_table(df_raw, "seccion", "Sección",
                                 election_id, candidates_df, id_distrito)


# ── App shell ──────────────────────────────────────────────────────────────────

st.markdown("**INE · Explorador Electoral de México**")

_seccion = st.radio(
    "Sección", ["Electoral", "Gaceta Parlamentaria"],
    horizontal=True, label_visibility="collapsed",
)

if _seccion == "Gaceta Parlamentaria":
    render_gaceta()
    st.stop()

# ── ELECTORAL SECTION ──────────────────────────────────────────────────────────

elections = get_available_elections()
if not elections:
    st.error(
        "No se encontraron archivos Parquet materializados. "
        "Ejecuta `python ingestion/electoral_ingest.py` y luego `python ingestion/electoral_materialize.py` primero."
    )
    st.stop()

candidates_df = load_candidates()

TYPE_LABELS = {"PRE": "Presidencial", "DIP": "Diputados", "SEN": "Senadores"}

# ── Controls row ──────────────────────────────────────────────────────────────

all_years    = sorted({e.split("_")[-1] for e in elections}, reverse=True)
default_year = "2024" if "2024" in all_years else all_years[0]

_cols = st.columns([0.8, 1.2, 1.0, 1.4])
with _cols[0]:
    year_sel = st.selectbox("Año", all_years, index=all_years.index(default_year))

year_elections = [e for e in elections if e.endswith(f"_{year_sel}")]
year_elections_sorted = sorted(
    year_elections,
    key=lambda e: list(TYPE_LABELS.keys()).index("_".join(e.split("_")[:-1]))
    if "_".join(e.split("_")[:-1]) in TYPE_LABELS else 99,
)
default_et = next((e for e in year_elections_sorted if e.startswith("PRE")),
                  year_elections_sorted[0])

with _cols[1]:
    election_sel = st.selectbox(
        "Tipo de elección", year_elections_sorted,
        index=year_elections_sorted.index(default_et),
        format_func=lambda e: TYPE_LABELS.get("_".join(e.split("_")[:-1]), election_label(e)),
    )
with _cols[2]:
    page_options = ["Estado", "Municipio", "Nacional · Histórico"]
    page = st.selectbox("Unidad de análisis", page_options)

# Estado view is always loaded first — used by all three pages for state picker.
df_est_full = load_view("estado", election_sel)
if df_est_full.empty:
    st.error("Sin datos. Ejecuta `python ingestion/electoral_materialize.py views` primero.")
    st.stop()

estado_options = (
    df_est_full[["id_estado", "nombre_estado"]]
    .dropna().drop_duplicates().sort_values("nombre_estado")
)
estado_names = estado_options["nombre_estado"].tolist()
default_e    = next((e for e in estado_names if "CIUDAD" in e.upper()), estado_names[0])

with _cols[3]:
    if page != "Nacional · Histórico":
        estado_sel = st.selectbox("Estado", estado_names, index=estado_names.index(default_e))
    else:
        estado_sel = estado_names[0]
        st.empty()

id_estado_sel = int(
    estado_options.loc[estado_options["nombre_estado"] == estado_sel, "id_estado"].iloc[0]
)


# ── PAGE: ESTADO ───────────────────────────────────────────────────────────────

if page == "Estado":
    df_view = df_est_full[df_est_full["id_estado"] == id_estado_sel]
    if df_view.empty:
        st.info("Sin datos para este estado.")
        st.stop()

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

    header_badge([
        estado_sel,
        f"{num_dist} distritos federales",
        f"{num_mun} municipios",
        f"{num_sec} secciones",
        f"{num_cas} actas",
    ])

    df_mun_view = load_view("municipio", election_sel)
    df_mun_view = df_mun_view[df_mun_view["id_estado"] == id_estado_sel]

    render_results_tab(
        df_mun_view, "Estado", election_sel, candidates_df,
        scorecards=(total_v, lista_nom, part_pct, nulos_pct),
    )

    st.markdown("---")
    with st.expander("Serie de tiempo · votos históricos por partido", expanded=False):
        df_ts = load_timeseries(
            TIMESERIES_PATH.stat().st_mtime if TIMESERIES_PATH.exists() else 0.0
        )
        if df_ts.empty:
            st.info(
                "No se encontró el archivo de series de tiempo. "
                "Ejecuta `python ingestion/electoral_materialize.py timeseries` primero."
            )
        else:
            render_timeseries_for_estado(df_ts, id_estado_sel, estado_sel)


# ── PAGE: MUNICIPIO ────────────────────────────────────────────────────────────

elif page == "Municipio":
    df_mun_full = load_view("municipio", election_sel)
    if df_mun_full.empty:
        st.error("Sin datos. Ejecuta `python ingestion/electoral_materialize.py views` primero.")
        st.stop()

    df_e       = df_mun_full[df_mun_full["id_estado"] == id_estado_sel]
    municipios = sorted(df_e["municipio"].dropna().unique())
    mun_sel    = st.selectbox("Municipio", municipios)
    df_view    = df_e[df_e["municipio"] == mun_sel]

    if df_view.empty:
        st.info("Sin datos para este municipio.")
        st.stop()

    meta_row  = df_view.drop_duplicates("municipio").iloc[0]
    num_cas   = safe_int(meta_row.get("num_casillas"))
    num_sec   = safe_int(meta_row.get("num_secciones"))
    total_v   = safe_int(df_view.drop_duplicates("municipio")["total_votos"].sum())
    lista_nom = safe_int(meta_row.get("lista_nominal_part"))
    part_pct  = total_v / lista_nom * 100 if lista_nom > 0 else 0
    nulos_raw = safe_int(df_view.drop_duplicates("municipio")["num_votos_nulos"].sum())
    nulos_pct = nulos_raw / total_v * 100 if total_v > 0 else 0

    header_badge([estado_sel, f"Municipio: {mun_sel}",
                  f"{num_sec} seccion(es)", f"{num_cas} acta(s)"])

    render_results_tab(
        df_view, "Municipio", election_sel, candidates_df,
        scorecards=(total_v, lista_nom, part_pct, nulos_pct),
    )

    df_sec_view = load_view("seccion", election_sel)
    df_sec_view = df_sec_view[
        (df_sec_view["id_estado"] == id_estado_sel) &
        (df_sec_view["municipio"] == mun_sel)
    ]
    if not df_sec_view.empty:
        st.markdown("---")
        render_results_table(df_sec_view, "seccion", "Sección",
                             election_sel, candidates_df)


# ── PAGE: NACIONAL · HISTÓRICO ─────────────────────────────────────────────────

elif page == "Nacional · Histórico":
    geo = load_municipios_geojson()
    if not geo:
        st.error(
            "No se encontró el GeoJSON de municipios. "
            "Ejecuta `python ingestion/electoral_materialize.py views` primero."
        )
        st.stop()

    df_nacional = load_mun_national(election_sel)
    if df_nacional.empty:
        st.error("Sin datos para esta elección.")
        st.stop()

    blocs              = CYCLE_BLOCS.get(election_sel)
    is_pre_with_blocs  = blocs is not None

    header_badge([
        election_label(election_sel),
        f"{df_nacional['_join_key'].nunique():,} municipios",
    ])

    if is_pre_with_blocs:
        map_col, div_col, tern_col = st.columns([1.15, 0.04, 0.85], gap="medium")
        with map_col:
            render_hist_winner_map(df_nacional, blocs, geo,
                                   f"Ganador por Municipio · {election_label(election_sel)}")
        with div_col:
            st.markdown('<div class="panel-divider"></div>', unsafe_allow_html=True)
        with tern_col:
            st.markdown('<div class="section-label">Distribución ternaria por Municipio</div>',
                        unsafe_allow_html=True)
            render_hist_ternary(df_nacional, blocs, election_label(election_sel))
    else:
        render_national_generic_map(df_nacional, geo, election_label(election_sel))
