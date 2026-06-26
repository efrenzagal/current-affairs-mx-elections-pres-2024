"""
timeseries_explorer.py
======================
Streamlit app — Electoral results across time (2012 · 2018 · 2024)

Two modes:
  A) Por Estado  — select one or more states, see all parties over time
  B) Por Partido — select one or more parties, compare states over time

Filters live at the top of the page in a compact horizontal bar.
No sidebar.

Run:
    python build_timeseries.py          # generate the parquet first
    streamlit run timeseries_explorer.py
"""

from pathlib import Path
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# ── Page config ────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Resultados Electorales · México",
    page_icon="🗳️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans:ital,wght@0,300;0,400;0,600;1,300&display=swap');

html, body, [class*="css"] { font-family: 'IBM Plex Sans', sans-serif; }
h1, h2, h3                 { font-family: 'IBM Plex Mono', monospace; letter-spacing: -0.02em; }

/* hide the sidebar toggle arrow entirely */
[data-testid="collapsedControl"] { display: none; }

/* filter bar label overrides */
.filter-label {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.65rem;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: #C84B31;
    margin-bottom: 2px;
}

/* tighten selectbox / radio padding in filter row */
div[data-testid="stHorizontalBlock"] .stSelectbox,
div[data-testid="stHorizontalBlock"] .stRadio { margin-bottom: 0; }

/* section divider label */
.section-label {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.65rem;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: #C84B31;
    margin-top: 1.4rem;
    margin-bottom: 0.2rem;
}
</style>
""", unsafe_allow_html=True)

# ── Theme detection ────────────────────────────────────────────────────────────
# st.get_option("theme.base") only returns "light"/"dark" when explicitly set
# in config.toml — otherwise None. We default DARK=True to match the dark OS
# theme visible in the screenshot. Flip FORCE_LIGHT=True for light mode.

FORCE_LIGHT = False

def _is_dark() -> bool:
    if FORCE_LIGHT:
        return False
    try:
        theme = st.get_option("theme.base")
        if theme == "light":
            return False
    except Exception:
        pass
    return True   # "dark" or None → dark

DARK = _is_dark()

if DARK:
    PLOT_BG    = "#0E1117"   # matches Streamlit dark default
    GRID_COLOR = "#2A2A35"
    TEXT_COLOR = "#FAFAFA"
    SUB_COLOR  = "#AAAAAA"
    LEGEND_BG  = "#181820"
    PLOTLY_TPL = "plotly_dark"
else:
    PLOT_BG    = "#FFFFFF"
    GRID_COLOR = "#E2E2DE"
    TEXT_COLOR = "#1A1A1A"
    SUB_COLOR  = "#888888"
    LEGEND_BG  = "#F7F7F5"
    PLOTLY_TPL = "plotly_white"

# ── Constants ──────────────────────────────────────────────────────────────────

DATA_PATH = Path("data/materialized/timeseries_estados.parquet")

PARTY_COLORS: dict[str, str] = {
    # ── Core parties — exact match with R scripts and existing Streamlit app ──
    "MORENA":           "#8B0000",  # dark red
    "PAN":              "#1E90FF",  # dodger blue
    "PRI":              "#006847",  # PRI green
    "PRD":              "#FFD700",  # gold
    "MC":               "#FF8C00",  # orange
    "PT":               "#CC0000",  # bright red (slightly lighter than MORENA)
    "PVEM":             "#4CAF50",  # mid green (distinct from PRI dark green)
    "NUEVA ALIANZA":    "#9B59B6",  # purple
    "ENCUENTRO SOCIAL": "#E91E8C",  # pink

    # ── Coalitions — derived from dominant/first member, desaturated ──
    # Morena bloc (red family)
    "PVEM_PT_MORENA":   "#8B0000",  # same as MORENA — this IS the SHH coalition
    "PT_MORENA":        "#A02020",
    "PT_MORENA_PES":    "#A83030",
    "PVEM_MORENA":      "#7A3030",
    "PVEM_PT":          "#6B8C50",
    "PT_PES":           "#B84040",
    "MORENA_PES":       "#922020",

    # PAN/opposition bloc (blue family)
    "PAN_PRI_PRD":      "#1E90FF",  # same as PAN — this IS the FCM coalition
    "PAN_PRI":          "#3A80C8",
    "PAN_PRD":          "#2878B8",
    "PAN_PRD_MC":       "#3070B0",
    "PAN_MC":           "#4488CC",
    "PRI_PRD":          "#2E7A60",  # PRI-ish green
    "PRI_PVEM_NA":      "#1A6640",
    "PRI_PVEM":         "#1E7048",
    "PRI_NA":           "#226050",
    "PRD_MC":           "#C89000",  # gold-orange bridge
    "PVEM_NA":          "#5EA050",
}

ELECTION_TYPE_LABELS = {"PRE": "Presidencia", "DIP": "Diputaciones", "SEN": "Senadurias"}
YEARS = [2012, 2018, 2024]
CHART_HEIGHT = 340   # px — tall enough to read lines, short enough to scan facets


def _fallback_color(key: str) -> str:
    h = hash(key) % 360
    return f"hsl({h},55%,{'60' if DARK else '42'}%)"

def party_color(key: str) -> str:
    return PARTY_COLORS.get(key, _fallback_color(key))

def state_color(j: int) -> str:
    return f"hsl({(j * 137) % 360}, 55%, {'62' if DARK else '42'}%)"


# ── Data loading ───────────────────────────────────────────────────────────────

@st.cache_data(show_spinner="Cargando datos...")
def load_data() -> pd.DataFrame:
    if not DATA_PATH.exists():
        return pd.DataFrame()
    df = pd.read_parquet(DATA_PATH)
    df["nombre_estado"] = df["nombre_estado"].str.strip().str.title()

    # ── Synthetic MORENA 2012 zeros ───────────────────────────────────────────
    # MORENA was founded in 2014 and didn't exist in 2012, so there are no rows
    # for it in that cycle. For visualisation continuity we inject explicit 0s
    # so the line starts at the origin rather than beginning abruptly at 2018.
    pre_2012 = df[(df["election_type"] == "PRE") & (df["year"] == 2012)]
    if not pre_2012.empty and not ((df["party_key"] == "MORENA") & (df["year"] == 2012)).any():
        # One synthetic row per state present in 2012 presidential data
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
        # Fill any remaining columns the parquet has with None
        for col in df.columns:
            if col not in synthetic.columns:
                synthetic[col] = None
        df = pd.concat([df, synthetic[df.columns]], ignore_index=True)

    return df


# ── Aggregation — collapses DIP_MR + DIP_RP → one point per year ─────────────

def agg_for_plot(df: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    agg = (
        df
        .groupby(group_cols, as_index=False)
        .agg(
            votes_raw=("votes_raw", "sum"),
            votes_split=("votes_split", "sum"),
            total_votos_estado=("total_votos_estado", "sum"),
            lista_nominal=("lista_nominal", "sum"),
        )
    )
    # Guard against zero/null denominator (e.g. synthetic MORENA 2012 rows
    # have total_votos_estado = None which sums to 0.0 after groupby)
    denom = agg["total_votos_estado"].replace(0, float("nan"))
    agg["pct_raw"]   = agg["votes_raw"]   / denom * 100
    agg["pct_split"] = agg["votes_split"] / denom * 100
    return agg


# ── Shared Plotly layout ───────────────────────────────────────────────────────

def base_layout(title: str, y_label: str, height: int = CHART_HEIGHT) -> dict:
    return dict(
        template=PLOTLY_TPL,
        title=dict(
            text=f"<b>{title}</b>",
            font=dict(family="IBM Plex Mono", size=14, color=TEXT_COLOR),
            x=0, xanchor="left",
        ),
        paper_bgcolor=PLOT_BG,
        plot_bgcolor=PLOT_BG,
        height=height,
        font=dict(family="IBM Plex Sans", color=TEXT_COLOR),
        xaxis=dict(
            tickvals=YEARS,
            ticktext=[str(y) for y in YEARS],
            tickfont=dict(family="IBM Plex Mono", size=12, color=TEXT_COLOR),
            showgrid=False,
            zeroline=False,
            linecolor=GRID_COLOR,
            linewidth=1,
            showline=True,
        ),
        yaxis=dict(
            title=y_label,
            tickfont=dict(family="IBM Plex Mono", size=11, color=TEXT_COLOR),
            title_font=dict(color=SUB_COLOR, size=11),
            showgrid=False,
            zeroline=False,
            showline=False,
        ),
        legend=dict(
            bgcolor=LEGEND_BG,
            bordercolor=GRID_COLOR,
            borderwidth=1,
            font=dict(family="IBM Plex Mono", size=10, color=TEXT_COLOR),
            orientation="h",
            yanchor="bottom", y=1.02,
            xanchor="left",   x=0,
        ),
        hovermode="x unified",
        hoverlabel=dict(
            bgcolor=LEGEND_BG,
            bordercolor=GRID_COLOR,
            font=dict(family="IBM Plex Mono", size=11, color=TEXT_COLOR),
        ),
        margin=dict(l=55, r=20, t=70, b=40),
    )


# ── Filter bar helpers ─────────────────────────────────────────────────────────

def filter_bar_common(df: pd.DataFrame, n_cols: int = 6):
    """
    Renders a horizontal filter bar and returns the selected values.
    Returns: et, et_label, split, use_pct, y_col, y_label, show_area
    """
    election_types = sorted(df["election_type"].unique())
    et_options     = {ELECTION_TYPE_LABELS.get(e, e): e for e in election_types}

    cols = st.columns([1.4, 1.6, 1.6, 1.4, 1, 1])

    with cols[0]:
        et_keys = list(et_options.keys())
        pre_default = et_keys.index("Presidencia") if "Presidencia" in et_keys else 0
        et_label = st.selectbox("Tipo de elección", et_keys, index=pre_default, label_visibility="visible")
        et = et_options[et_label]

    with cols[1]:
        coalition_mode = st.radio(
            "Votos de coalición",
            ["Divididos", "Como coalición"],
            horizontal=True,
        )
        split = coalition_mode == "Divididos"

    with cols[2]:
        metric  = st.radio("Métrica", ["% del total", "Votos abs."], index=1, horizontal=True)
        use_pct = metric == "% del total"

    y_col   = ("pct_split"   if split else "pct_raw")   if use_pct else ("votes_split" if split else "votes_raw")
    y_label = "% de votos" if use_pct else "Votos"
    y_fmt   = ".1f" if use_pct else ":,.0f"   # 42.3  vs  3,456,789

    with cols[5]:
        show_area = st.checkbox("Área bajo curva", value=False)

    return et, et_label, split, use_pct, y_col, y_label, y_fmt, show_area


# ── Mode A: Por Estado ────────────────────────────────────────────────────────
# Fixed logic: you pick states, you always get one chart per state.
# Lines within each chart = parties, coloured by party identity.

def render_por_estado(df: pd.DataFrame):
    et, et_label, split, use_pct, y_col, y_label, y_fmt, show_area = filter_bar_common(df)

    coalition_keys = set(df.loc[df["is_coalition"] == True, "party_key"].unique())
    all_parties    = sorted(df["party_key"].unique())
    direct_parties = [p for p in all_parties if p not in coalition_keys]
    selectable     = direct_parties if split else all_parties
    default_p      = [p for p in ["MORENA", "PAN", "PRI", "MC", "PRD"] if p in selectable]

    r2 = st.columns([2, 2])
    with r2[0]:
        states = sorted(df["nombre_estado"].unique())
        default_states = [s for s in ["Ciudad De Mexico", "Jalisco", "Nuevo Leon"] if s in states]
        selected_states = st.multiselect("Estado(s)", states, default=default_states or states[:1])
    with r2[1]:
        parties_to_show = st.multiselect("Partidos a mostrar", selectable, default=default_p)
        if not split:
            extra = st.multiselect("Coaliciones", [p for p in all_parties if p in coalition_keys], default=[])
            parties_to_show = parties_to_show + extra

    st.markdown("---")

    if not selected_states:
        st.info("Selecciona al menos un estado.")
        return
    if not parties_to_show:
        st.info("Selecciona al menos un partido.")
        return

    df_f = df[
        (df["election_type"] == et)
        & (df["nombre_estado"].isin(selected_states))
        & (df["party_key"].isin(parties_to_show))
    ].copy()
    if split:
        df_f = df_f[df_f["is_coalition"] == False]
    if df_f.empty:
        st.warning("Sin datos para estos filtros.")
        return

    df_agg = agg_for_plot(df_f, ["year", "election_type", "nombre_estado", "party_key"])

    st.markdown(f"### {et_label} · Por Estado")
    st.caption(
        f"{'Coaliciones divididas proporcionalmente · ' if split else ''}"
        f"Un gráfico por estado · Líneas = partidos"
    )

    # One chart per state — lines = parties coloured by party
    for estado in selected_states:
        df_estado = df_agg[df_agg["nombre_estado"] == estado]
        if df_estado.empty:
            continue
        fig = go.Figure()
        for party, grp in df_estado.groupby("party_key"):
            grp   = grp.sort_values("year")
            color = party_color(party)
            fig.add_trace(go.Scatter(
                x=grp["year"], y=grp[y_col],
                mode="lines+markers", name=party,
                line=dict(color=color, width=2.5),
                marker=dict(color=color, size=8),
                fill="tozeroy" if show_area else "none",
                hovertemplate=f"<b>{party}</b>: %{{y{y_fmt}}}<extra></extra>",
            ))
        fig.update_layout(**base_layout(estado, y_label))
        st.plotly_chart(fig, use_container_width=True)


# ── Mode B: Por Partido ───────────────────────────────────────────────────────
# Fixed logic: you pick parties, you always get one chart per party.
# Lines within each chart = states, coloured by state.

def render_por_partido(df: pd.DataFrame):
    et, et_label, split, use_pct, y_col, y_label, y_fmt, show_area = filter_bar_common(df)

    coalition_keys = set(df.loc[df["is_coalition"] == True, "party_key"].unique())
    all_parties    = sorted(df["party_key"].unique())
    direct_parties = [p for p in all_parties if p not in coalition_keys]
    selectable     = direct_parties if split else all_parties
    default_p      = [p for p in ["MORENA", "PAN", "PRI", "MC"] if p in selectable]

    r2 = st.columns([2, 2, 1])
    with r2[0]:
        selected_parties = st.multiselect("Partido(s)", selectable, default=default_p)
        if not split:
            extra = st.multiselect("Coaliciones", [p for p in all_parties if p in coalition_keys], default=[])
            selected_parties = selected_parties + extra
    with r2[1]:
        states = sorted(df["nombre_estado"].unique())
        selected_states = st.multiselect("Filtrar estados (vacío = top N)", states, default=[])
    with r2[2]:
        top_n = st.slider("Top N por tamaño", 5, 32, 10)

    st.markdown("---")

    if not selected_parties:
        st.info("Selecciona al menos un partido.")
        return

    df_f = df[df["election_type"] == et].copy()
    if split:
        df_f = df_f[df_f["is_coalition"] == False]
    df_f = df_f[df_f["party_key"].isin(selected_parties)]

    if selected_states:
        df_f = df_f[df_f["nombre_estado"].isin(selected_states)]
    else:
        top_states = (
            df[df["election_type"] == et]
            .groupby("nombre_estado")["votes_raw"].sum()
            .nlargest(top_n).index.tolist()
        )
        df_f = df_f[df_f["nombre_estado"].isin(top_states)]

    if df_f.empty:
        st.warning("Sin datos para estos filtros.")
        return

    df_agg        = agg_for_plot(df_f, ["year", "election_type", "nombre_estado", "party_key"])
    estados_shown = sorted(df_agg["nombre_estado"].unique())
    sc_map        = {s: state_color(j) for j, s in enumerate(estados_shown)}

    st.markdown(f"### {et_label} · Por Partido")
    st.caption(
        f"{'Coaliciones divididas proporcionalmente · ' if split else ''}"
        f"Un gráfico por partido · Líneas = estados"
    )

    # One chart per party — lines = states
    for party in selected_parties:
        df_party = df_agg[df_agg["party_key"] == party]
        if df_party.empty:
            continue
        fig = go.Figure()
        for estado, grp in df_party.groupby("nombre_estado"):
            grp = grp.sort_values("year")
            fig.add_trace(go.Scatter(
                x=grp["year"], y=grp[y_col],
                mode="lines+markers", name=estado,
                line=dict(color=sc_map[estado], width=2),
                marker=dict(size=6),
                fill="tozeroy" if show_area else "none",
                hovertemplate=f"<b>{estado}</b>: %{{y{y_fmt}}}<extra></extra>",
            ))
        fig.update_layout(**base_layout(party, y_label))
        st.plotly_chart(fig, use_container_width=True)

    # Summary table
    st.markdown("---")
    st.markdown('<div class="section-label">Tabla resumen</div>', unsafe_allow_html=True)

    pivot = (
        df_agg
        .groupby(["party_key", "nombre_estado", "year"], as_index=False)[y_col]
        .sum()
        .pivot_table(index=["party_key", "nombre_estado"], columns="year", values=y_col)
        .reset_index()
    )
    pivot.columns = [str(c) for c in pivot.columns]
    year_cols = [str(y) for y in YEARS if str(y) in pivot.columns]

    if len(year_cols) >= 2:
        first, last = year_cols[0], year_cols[-1]
        pivot["_d"] = (
            pd.to_numeric(pivot[last], errors="coerce")
            - pd.to_numeric(pivot[first], errors="coerce")
        )
        pivot["Δ"] = pivot["_d"].map(lambda x: f"{x:+.1f}" if pd.notna(x) else "—")
        pivot = pivot.drop(columns=["_d"])

    for col in year_cols:
        pivot[col] = pd.to_numeric(pivot[col], errors="coerce").map(
            lambda x: f"{x:.1f}" if pd.notna(x) else "—"
        )

    pivot = pivot.rename(columns={"party_key": "Partido", "nombre_estado": "Estado"})
    st.dataframe(pivot, use_container_width=True, hide_index=True)


# ── App shell ──────────────────────────────────────────────────────────────────

def main():
    df = load_data()

    if df.empty:
        st.error(
            "No se encontró el archivo de datos. "
            "Ejecuta `python build_timeseries.py` primero."
        )
        st.stop()

    st.markdown("## 🗳️ Resultados Electorales · México")
    st.markdown("##### Evolución 2012 · 2018 · 2024")
    st.markdown("---")

    mode = st.radio(
        "Modo",
        ["Por Estado", "Por Partido"],
        horizontal=True,
        label_visibility="collapsed",
    )
    st.markdown("---")

    if mode == "Por Estado":
        render_por_estado(df)
    else:
        render_por_partido(df)

    st.markdown("---")
    st.caption(
        "Fuente: INE PREP 2012, 2018, 2024 · "
        "Votos de coalición atribuidos proporcionalmente a cada partido miembro"
    )


if __name__ == "__main__":
    main()