"""
Gaceta Parlamentaria section — vote detail (party grid) and deputy vote calendar.
Self-contained: owns its own loaders, controls, and render functions.
Call render_gaceta() from the main app.
"""

from __future__ import annotations

import sqlite3
import math
from difflib import SequenceMatcher
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import requests
import streamlit as st
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "election_data.db"
VOTE_INDEX_PATH = ROOT / "data" / "materialized" / "gaceta_vote_index.parquet"

GACETA_HOST = "https://gaceta.diputados.gob.mx"
MESES_ES = {
    1: "ene", 2: "feb", 3: "mar", 4: "abr", 5: "may", 6: "jun",
    7: "jul", 8: "ago", 9: "sep", 10: "oct", 11: "nov", 12: "dic",
}
ANEXO_MATCH_THRESHOLD = 0.35

VOTE_LEVELS = ["Sí", "No", "Abstención", "Ausente", "Presente, sin voto"]
VOTE_COLORS = {
    "Sí": "#2E7D32",
    "No": "#C62828",
    "Abstención": "#9E9E9E",
    "Ausente": "#111111",
    "Presente, sin voto": "#F9A825",
}
VOTE_RECODE = {
    "Favor": "Sí",
    "Contra": "No",
    "Abstención": "Abstención",
    "Abstencion": "Abstención",
    "Ausente": "Ausente",
    "Quórum *": "Presente, sin voto",
}


# ── Cached loaders ─────────────────────────────────────────────────────────────

def _file_version(path: Path) -> tuple[int, int]:
    """Return a cheap, hashable cache key that changes when a file is rebuilt."""
    try:
        stat = path.stat()
    except FileNotFoundError:
        return (0, 0)
    return (stat.st_mtime_ns, stat.st_size)


def get_gaceta_cache_versions() -> tuple[tuple[int, int], tuple[tuple[int, int], ...]]:
    """Version the Parquet and SQLite reads, including SQLite's WAL sidecar."""
    vote_index_version = _file_version(VOTE_INDEX_PATH)
    database_version = tuple(
        _file_version(path)
        for path in (DB_PATH, DB_PATH.with_name(f"{DB_PATH.name}-wal"), DB_PATH.with_name(f"{DB_PATH.name}-shm"))
    )
    return vote_index_version, database_version


@st.cache_resource(show_spinner=False)
def get_connection(database_version: tuple[tuple[int, int], ...]) -> sqlite3.Connection:
    """Open a new connection whenever an ingest changes the SQLite files."""
    del database_version
    return sqlite3.connect(DB_PATH, check_same_thread=False)


@st.cache_data
def load_gaceta_votes(vote_index_version: tuple[int, int]) -> pd.DataFrame:
    del vote_index_version
    return pd.read_parquet(VOTE_INDEX_PATH)


@st.cache_data
def load_vote_deputies(
    gaceta_vote_id: str, database_version: tuple[tuple[int, int], ...]
) -> pd.DataFrame:
    conn = get_connection(database_version)
    return pd.read_sql_query(
        """
        SELECT f.deputy_id, f.party_key, f.vote_choice, f.ordinal, d.deputy_name
        FROM fact_gaceta_deputy_vote AS f
        JOIN dim_gaceta_deputy AS d ON d.deputy_id = f.deputy_id
        WHERE f.gaceta_vote_id = ?
        ORDER BY f.party_key, f.ordinal, d.deputy_name
        """,
        conn, params=(gaceta_vote_id,),
    )


@st.cache_data
def load_legislature_deputies(
    leg_sel: int, database_version: tuple[tuple[int, int], ...]
) -> pd.DataFrame:
    conn = get_connection(database_version)
    return pd.read_sql_query(
        """
        SELECT DISTINCT f.deputy_id, d.deputy_name
        FROM fact_gaceta_deputy_vote AS f
        JOIN dim_gaceta_vote AS v ON v.gaceta_vote_id = f.gaceta_vote_id
        JOIN dim_gaceta_deputy AS d ON d.deputy_id = f.deputy_id
        WHERE v.legislature = ?
        ORDER BY d.deputy_name
        """,
        conn, params=(int(leg_sel),),
    )


@st.cache_data
def load_classification(database_version: tuple[tuple[int, int], ...]) -> pd.DataFrame:
    conn = get_connection(database_version)
    df = pd.read_sql_query(
        """
        SELECT
            v.gaceta_vote_id, v.legislature, v.vote_date, v.title, v.source_url, v.gaceta_date,
            r.favor, r.contra, r.abstencion, r.ausente, r.total,
            c.origen, c.etapa_votacion, c.tipo_instrumento, c.tema_politica,
            c.confianza, c.requiere_revision, c.evidencia
        FROM dim_gaceta_vote AS v
        LEFT JOIN (
            SELECT
                gaceta_vote_id,
                SUM(CASE WHEN vote_choice = 'Favor' THEN count ELSE 0 END) AS favor,
                SUM(CASE WHEN vote_choice = 'Contra' THEN count ELSE 0 END) AS contra,
                SUM(CASE WHEN vote_choice = 'Abstención' THEN count ELSE 0 END) AS abstencion,
                SUM(CASE WHEN vote_choice = 'Ausente' THEN count ELSE 0 END) AS ausente,
                SUM(CASE WHEN vote_choice = 'Total' THEN count ELSE 0 END) AS total
            FROM fact_gaceta_vote_summary
            WHERE party_key = 'Total'
            GROUP BY gaceta_vote_id
        ) AS r ON r.gaceta_vote_id = v.gaceta_vote_id
        LEFT JOIN fact_gaceta_vote_classification AS c ON c.gaceta_vote_id = v.gaceta_vote_id
        ORDER BY v.legislature, v.vote_date, v.gaceta_vote_id
        """,
        conn,
    )
    df["vote_date"] = pd.to_datetime(df["vote_date"], errors="coerce")
    df["year"] = df["vote_date"].dt.year
    df["requiere_revision"] = df["requiere_revision"].map({1: "Sí", 0: "No"})
    df["votos_efectivos"] = df["favor"] + df["contra"]
    # margen: 1.0 = unanimous between favor/contra, 0.5 = evenly split. More
    # intuitive for a human reader than its complement (contenciosidad).
    df["margen"] = (
        df[["favor", "contra"]].max(axis=1) / df["votos_efectivos"]
    ).where(df["votos_efectivos"] > 0)
    df["contencioso"] = 1 - df["margen"]
    df["favorable"] = df["favor"] > df["contra"]
    return df


@st.cache_data
def load_deputy_calendar(
    deputy_id: str, leg_sel: int, database_version: tuple[tuple[int, int], ...]
) -> pd.DataFrame:
    conn = get_connection(database_version)
    return pd.read_sql_query(
        """
        SELECT v.gaceta_vote_id, v.vote_date, v.title, f.party_key, f.vote_choice
        FROM fact_gaceta_deputy_vote AS f
        JOIN dim_gaceta_vote AS v ON v.gaceta_vote_id = f.gaceta_vote_id
        WHERE f.deputy_id = ? AND v.legislature = ?
        ORDER BY v.vote_date, v.gaceta_vote_id
        """,
        conn, params=(deputy_id, int(leg_sel)),
    )


def gaceta_issue_url(legislature: int, gaceta_date: str | None) -> str | None:
    """Daily Gaceta Parlamentaria issue for a vote's dictamen — the source_url
    on dim_gaceta_vote only points to the vote tally table, not the bill text."""
    if not gaceta_date or pd.isna(gaceta_date):
        return None
    try:
        year, month, day = str(gaceta_date)[:10].split("-")
    except ValueError:
        return None
    return f"{GACETA_HOST}/Gaceta/{int(legislature)}/{year}/{MESES_ES[int(month)]}/{year}{month}{day}.html"


@st.cache_data(ttl=6 * 60 * 60, show_spinner=False)
def find_gaceta_document(legislature: int, gaceta_date: str, title: str) -> tuple[str, str] | None:
    """Best-effort match of a vote's dictamen to its PDF annex within that
    day's Gaceta issue. Only issues from ~2016 onward publish annex PDFs;
    older issues embed the dictamen text directly in the HTML, so callers
    should fall back to gaceta_issue_url() when this returns None."""
    issue_url = gaceta_issue_url(legislature, gaceta_date)
    if issue_url is None:
        return None
    try:
        response = requests.get(issue_url, timeout=15)
        response.raise_for_status()
    except requests.RequestException:
        return None
    response.encoding = "iso-8859-1"
    soup = BeautifulSoup(response.text, "html.parser")
    anexos = soup.find("div", id="Anexos")
    if anexos is None:
        return None

    candidates = []
    for p in anexos.find_all("p"):
        link = p.find("a", href=True)
        if link is None:
            continue
        candidates.append((link["href"], p.get_text(" ", strip=True)))
    if not candidates:
        return None

    def score(label: str) -> float:
        return SequenceMatcher(None, title.lower(), label.lower()).ratio()

    href, label = max(candidates, key=lambda c: score(c[1]))
    if score(label) < ANEXO_MATCH_THRESHOLD:
        return None
    return f"{GACETA_HOST}{href}", label


# ── Helpers ──────────────────────────────────────────────────────────────────

def _format_label(value: str) -> str:
    """snake_case classification code -> readable Spanish label, e.g.
    'derechos_humanos_e_igualdad' -> 'Derechos humanos e igualdad'."""
    if not isinstance(value, str):
        return value
    return value.replace("_", " ").capitalize()


def _clean_title(title: str) -> str:
    return (
        pd.Series([title or ""])
        .str.replace(r"<[^>]+>", " ", regex=True)
        .str.replace(r"\s+", " ", regex=True)
        .str.strip()
        .iloc[0]
    )


def _party_display(party_key: pd.Series) -> pd.Series:
    return party_key.map({"MRN": "MORENA", "SP": "Sin partido"}).fillna(party_key)


def _status_icon(ok) -> str:
    return "✅" if bool(ok) else "❌"


def _add_tile_coords(df: pd.DataFrame, group_col: str, columns: int) -> pd.DataFrame:
    df = df.copy()
    df["tile_number"] = df.groupby(group_col, observed=True).cumcount() + 1
    df["x"] = (df["tile_number"] - 1) % columns + 1
    df["y"] = -((df["tile_number"] - 1) // columns + 1)
    return df


def _tile_grid_figure(df: pd.DataFrame, facet_col: str, facet_col_wrap: int | None = None,
                       facet_row: str | None = None, height: int = 500, marker_size: int = 9):
    kwargs = dict(
        x="x", y="y", color="vote_display",
        category_orders={"vote_display": VOTE_LEVELS},
        color_discrete_map=VOTE_COLORS,
        custom_data=["tooltip"],
    )
    if facet_row:
        kwargs["facet_row"] = facet_row
        kwargs["facet_row_spacing"] = 0.015
    else:
        kwargs["facet_col"] = facet_col
        kwargs["facet_col_wrap"] = facet_col_wrap

    fig = px.scatter(df, **kwargs)
    fig.update_traces(
        marker=dict(size=marker_size, symbol="square", line=dict(width=0.4, color="white")),
        hovertemplate="%{customdata[0]}<extra></extra>",
    )
    fig.update_xaxes(visible=False, showticklabels=False)
    fig.update_yaxes(visible=False, showticklabels=False)

    n_axes = sum(1 for k in fig.layout if k.startswith("yaxis"))
    for i in range(1, n_axes + 1):
        suffix = "" if i == 1 else str(i)
        fig.layout[f"yaxis{suffix}"].update(scaleanchor=f"x{suffix}", scaleratio=1)

    fig.for_each_annotation(
        lambda a: a.update(text=a.text.split("=")[-1], font=dict(size=16, family="IBM Plex Sans"))
    )
    fig.update_layout(
        height=height,
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        font=dict(family="IBM Plex Sans", size=13),
        legend=dict(title=None, orientation="h", y=-0.02, font=dict(size=13)),
        margin=dict(t=40, b=10, l=10, r=10),
    )
    return fig


def _calendar_grid_figure(df: pd.DataFrame, year_col: str, year_levels: list,
                           columns: int, marker_size: int = 16, row_px: int = 24):
    """Year-faceted tile grid sized so squares never overlap, regardless of how
    many votes fall in a given year (each grid row gets a fixed pixel height)."""
    row_counts = {
        y: max(1, math.ceil((df[year_col] == y).sum() / columns)) for y in year_levels
    }
    # Domain proportions get a floor so a sparse year's facet doesn't collapse to a
    # sliver — its data range below still uses the real row_counts, so tiles stay
    # left/top-aligned at the correct size instead of stretching to fill the floor.
    min_facet_rows = 3
    row_heights = [max(row_counts[y], min_facet_rows) for y in year_levels]

    fig = make_subplots(
        rows=len(year_levels), cols=1,
        row_heights=row_heights, vertical_spacing=0.06,
        subplot_titles=[str(y) for y in year_levels],
    )

    for i, y in enumerate(year_levels, start=1):
        sub = df[df[year_col] == y]
        for vote_level in VOTE_LEVELS:
            s = sub[sub["vote_display"] == vote_level]
            if s.empty:
                continue
            fig.add_trace(
                go.Scatter(
                    x=s["x"], y=s["y"], mode="markers",
                    marker=dict(size=marker_size, symbol="square", color=VOTE_COLORS[vote_level],
                                line=dict(width=0.4, color="white")),
                    name=vote_level, legendgroup=vote_level, showlegend=(i == 1),
                    customdata=s[["tooltip"]],
                    hovertemplate="%{customdata[0]}<extra></extra>",
                ),
                row=i, col=1,
            )
        fig.update_xaxes(visible=False, showticklabels=False, constrain="domain",
                          constraintoward="left", range=[0.3, columns + 0.7], row=i, col=1)
        x_axis_id = "x" if i == 1 else f"x{i}"
        fig.update_yaxes(visible=False, showticklabels=False, scaleanchor=x_axis_id,
                          scaleratio=1, constrain="domain", constraintoward="top",
                          range=[-(row_counts[y] + 0.7), 0.3], row=i, col=1)

    fig.for_each_annotation(lambda a: a.update(font=dict(size=16, family="IBM Plex Sans")))
    total_height = sum(row_heights) * row_px + 70 * len(year_levels) + 40
    fig.update_layout(
        height=total_height,
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        font=dict(family="IBM Plex Sans", size=13),
        legend=dict(title=None, orientation="h", y=-0.02, font=dict(size=13)),
        margin=dict(t=40, b=10, l=10, r=10),
    )
    return fig


# ── Sub-page renderers ─────────────────────────────────────────────────────────

def render_vote_detail(
    votes_df: pd.DataFrame, vote_id: str, database_version: tuple[tuple[int, int], ...]
):
    """General info + party-vote grid for a single vote, identified by id.
    Used below the Clasificación scatter once a vote is selected."""
    matches = votes_df[votes_df["gaceta_vote_id"] == vote_id]
    if matches.empty:
        st.info("No se encontró información detallada de esta votación.")
        return
    vote = matches.iloc[0]
    vote_date = pd.to_datetime(vote["vote_date"], errors="coerce")

    full_title = _clean_title(vote["title"])
    date_str = vote_date.date() if pd.notna(vote_date) else "s/f"
    date_caption = f"Votación registrada el {date_str}" if date_str != "s/f" else "Fecha de votación no disponible"

    st.markdown(f"##### Gaceta Parlamentaria · Cámara de Diputados · Legislatura {int(vote['legislature'])}")
    st.subheader(full_title)
    st.caption(date_caption)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("A favor", f"{int(vote['favor']):,}")
    c2.metric("En contra", f"{int(vote['contra']):,}")
    c3.metric("Abstenciones", f"{int(vote['abstencion']):,}")
    c4.metric("Ausencias", f"{int(vote['ausente']):,}")

    t1, t2, t3, t4 = st.columns(4)
    t1.markdown(
        f"**Quórum** {_status_icon(vote['quorum_ok'])}  \n"
        f"{int(vote['presentes'])} presentes de {int(vote['total'])}  \n"
        f"Mínimo {int(vote['quorum_requerido'])}"
    )
    t2.markdown(
        f"**Mayoría simple** {_status_icon(vote['mayoria_simple_ok'])}  \n"
        f"{int(vote['favor'])} a favor vs. {int(vote['contra'])} en contra"
    )
    t3.markdown(
        f"**Mayoría absoluta** {_status_icon(vote['mayoria_absoluta_ok'])}  \n"
        f"{int(vote['favor'])} a favor · mínimo {int(vote['mayoria_absoluta_requerida'])}"
    )
    t4.markdown(
        f"**Mayoría calificada** {_status_icon(vote['mayoria_calificada_ok'])}  \n"
        f"{int(vote['favor'])} a favor · mínimo {int(vote['mayoria_calificada_requerida'])}"
    )

    st.markdown("---")

    deputies = load_vote_deputies(vote_id, database_version)
    if deputies.empty:
        st.info("No hay detalle de voto por diputado para esta votación.")
        return

    deputies["vote_display"] = deputies["vote_choice"].map(VOTE_RECODE).fillna(deputies["vote_choice"])
    deputies["vote_display"] = pd.Categorical(deputies["vote_display"], categories=VOTE_LEVELS)
    deputies["party_display"] = _party_display(deputies["party_key"])

    party_order = deputies["party_display"].value_counts().index.tolist()
    deputies["party_display"] = pd.Categorical(deputies["party_display"], categories=party_order)
    deputies = deputies.sort_values(["party_display", "vote_display", "ordinal", "deputy_name"])
    deputies = _add_tile_coords(deputies, "party_display", columns=24)
    deputies["tooltip"] = (
        "<b>" + deputies["deputy_name"] + "</b>"
        + "<br>Grupo parlamentario: " + deputies["party_display"].astype(str)
        + "<br>Voto individual: " + deputies["vote_display"].astype(str)
    )

    n_parties = deputies["party_display"].nunique()
    fig = _tile_grid_figure(deputies, facet_col="party_display", facet_col_wrap=3,
                             height=280 * -(-n_parties // 3) + 60)
    st.plotly_chart(fig, use_container_width=True)


def render_deputy_view(leg_sel: int, database_version: tuple[tuple[int, int], ...]):
    deputies = load_legislature_deputies(leg_sel, database_version)
    if deputies.empty:
        st.info("Sin diputados para esta legislatura.")
        return

    name_to_id = dict(zip(deputies["deputy_name"], deputies["deputy_id"]))
    selected_name = st.selectbox("Diputado", list(name_to_id.keys()))
    deputy_id = name_to_id[selected_name]

    calendar = load_deputy_calendar(deputy_id, leg_sel, database_version)
    if calendar.empty:
        st.info("Sin votaciones registradas para este diputado.")
        return

    calendar["vote_date"] = pd.to_datetime(calendar["vote_date"], errors="coerce")
    calendar = calendar[calendar["vote_date"].notna()].copy()
    if calendar.empty:
        st.info("Sin votaciones con fecha para este diputado.")
        return

    calendar["year"] = calendar["vote_date"].dt.year.astype(str)
    calendar["vote_display"] = calendar["vote_choice"].map(VOTE_RECODE).fillna(calendar["vote_choice"])
    calendar["vote_display"] = pd.Categorical(calendar["vote_display"], categories=VOTE_LEVELS)
    calendar["party_display"] = _party_display(calendar["party_key"])

    attendance = (calendar["vote_display"] != "Ausente").mean()
    parties = list(dict.fromkeys(calendar.sort_values("vote_date")["party_display"]))
    first_vote = calendar["vote_date"].min().date()
    last_vote = calendar["vote_date"].max().date()
    vote_counts = calendar["vote_display"].value_counts().reindex(VOTE_LEVELS, fill_value=0)

    st.markdown(f"##### {selected_name}")
    st.caption(f"Legislatura {leg_sel} · Partido(s): {', '.join(parties)}")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Primera votación", str(first_vote))
    m2.metric("Última votación", str(last_vote))
    m3.metric("Participación", f"{attendance:.1%}")
    m4.metric("Total votaciones", f"{len(calendar):,}")

    v1, v2, v3, v4, v5 = st.columns(5)
    v1.metric("Sí", f"{vote_counts['Sí']:,}")
    v2.metric("No", f"{vote_counts['No']:,}")
    v3.metric("Abstención", f"{vote_counts['Abstención']:,}")
    v4.metric("Ausente", f"{vote_counts['Ausente']:,}")
    v5.metric("Presente s/voto", f"{vote_counts['Presente, sin voto']:,}")

    st.markdown("---")

    year_levels = sorted(calendar["year"].unique(), reverse=True)
    calendar["year"] = pd.Categorical(calendar["year"], categories=year_levels)
    calendar = calendar.sort_values(["year", "vote_date", "gaceta_vote_id"])
    calendar_columns = 20
    calendar = _add_tile_coords(calendar, "year", columns=calendar_columns)
    calendar["tooltip"] = (
        calendar["vote_date"].dt.date.astype(str)
        + " · " + calendar["vote_display"].astype(str)
    )

    fig = _calendar_grid_figure(calendar, year_col="year", year_levels=year_levels,
                                 columns=calendar_columns, marker_size=38, row_px=48)
    st.plotly_chart(fig, use_container_width=True)


CLASSIFICATION_FILTER_COLUMNS = {
    "origen": "Origen",
    "etapa_votacion": "Etapa de votación",
    "tipo_instrumento": "Tipo de instrumento",
    "tema_politica": "Tema de política",
}


def render_classification_view(
    votes_df: pd.DataFrame, database_version: tuple[tuple[int, int], ...]
):
    df = load_classification(database_version)

    legs = sorted(df["legislature"].dropna().unique())

    with st.expander("Filtros", expanded=True):
        leg_sel_clf = st.selectbox(
            "Legislatura", legs,
            index=len(legs) - 1 if legs else 0,
            format_func=lambda x: f"Legislatura {x}",
            key="clf_legislature",
        )

        cols = st.columns(len(CLASSIFICATION_FILTER_COLUMNS))
        selections: dict[str, list[str]] = {}
        for col_widget, (col_name, label) in zip(cols, CLASSIFICATION_FILTER_COLUMNS.items()):
            options = sorted(df[col_name].dropna().unique())
            selections[col_name] = col_widget.multiselect(
                label, options, format_func=_format_label, key=f"clf_{col_name}",
            )

    filtered = df[df["legislature"] == leg_sel_clf].copy()
    for col_name, chosen in selections.items():
        if chosen:
            filtered = filtered[filtered[col_name].isin(chosen)]
    filtered["tema_display"] = filtered["tema_politica"].map(_format_label)

    m1, m2, m3 = st.columns(3)
    m1.metric("Votaciones", f"{len(filtered):,}")
    m2.metric("Aprobadas", f"{filtered['favorable'].sum():,}" if len(filtered) else "0")
    m3.metric("Rechazadas", f"{(~filtered['favorable']).sum():,}" if len(filtered) else "0")

    st.markdown("---")

    tema_col, consenso_col = st.columns(2)

    with tema_col:
        temas_tiempo = (
            filtered.dropna(subset=["tema_display", "year"])
            .loc[lambda d: ~d["tema_politica"].isin(["no_claro", "otro", "no_aplica"])]
            .groupby(["year", "tema_display"])
            .size()
            .reset_index(name="votos")
        )
        if temas_tiempo.empty:
            st.info("Sin datos suficientes para la composición temática por año.")
        else:
            temas_tiempo["porcentaje"] = 100 * temas_tiempo["votos"] / temas_tiempo.groupby(
                "year"
            )["votos"].transform("sum")
            fig = px.area(
                temas_tiempo, x="year", y="porcentaje", color="tema_display",
                title="Composición temática por año",
                labels={"year": "Año", "porcentaje": "% de votaciones", "tema_display": "Tema"},
            )
            # year is numeric, so with only 2-3 distinct years Plotly's
            # default tick spacing inserts fractional ticks like "2024.5".
            fig.update_xaxes(dtick=1, tickformat="d")
            fig.update_layout(
                plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                font=dict(family="IBM Plex Sans", size=12),
                legend=dict(font=dict(size=10)),
            )
            st.plotly_chart(fig, use_container_width=True)

    with consenso_col:
        consenso = (
            filtered[filtered["requiere_revision"] == "No"]
            .dropna(subset=["tema_display", "margen"])
            .loc[lambda d: ~d["tema_politica"].isin(["no_claro", "otro", "no_aplica"])]
            .groupby("tema_display")
            .agg(votos=("margen", "size"), margen_media=("margen", "mean"))
            .reset_index()
            .loc[lambda d: d["votos"] >= 5]
        )
        if consenso.empty:
            st.info("Sin datos suficientes para medir consenso por tema.")
        else:
            fig = px.bar(
                consenso, x="margen_media", y="tema_display", orientation="h",
                title="Margen de consenso promedio por tema",
                labels={"margen_media": "Margen de consenso", "tema_display": "Tema"},
            )
            fig.update_xaxes(tickformat=".0%")
            # Largest consensus at the top, regardless of row order.
            fig.update_yaxes(categoryorder="total ascending")
            fig.update_traces(marker_color="#2C7FB8")
            fig.update_layout(
                plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                font=dict(family="IBM Plex Sans", size=12),
            )
            st.plotly_chart(fig, use_container_width=True)

    st.markdown("---")
    render_consensus_scatter(filtered, votes_df, database_version)


def render_consensus_scatter(
    filtered: pd.DataFrame, votes_df: pd.DataFrame,
    database_version: tuple[tuple[int, int], ...],
) -> None:
    """Margen (consenso) vs. participación, faceted by resultado. Click a point
    to open its source page on gaceta.diputados.gob.mx."""
    st.markdown("##### Consenso y dirección del resultado")

    scatter_df = filtered.dropna(subset=["margen", "votos_efectivos", "tema_display"]).loc[
        lambda d: ~d["tema_politica"].isin(["no_claro", "otro", "no_aplica"])
    ].copy()
    if scatter_df.empty:
        st.info("Sin datos suficientes para esta vista con los filtros actuales.")
        return

    # favor_share folds consensus strength *and* direction into one axis:
    # 0% = unánime en contra, 50% = empate/máxima división, 100% = unánime a
    # favor — so a single chart replaces the old "resultado" facet pair.
    scatter_df["favor_share"] = scatter_df["favor"] / scatter_df["votos_efectivos"]
    scatter_df["resultado"] = scatter_df["favorable"].map({True: "Aprobada", False: "Rechazada"})
    scatter_df["title_clean"] = scatter_df["title"].map(_clean_title).str.slice(0, 90)
    scatter_df["date_str"] = scatter_df["vote_date"].dt.date.astype(str)

    fig = px.scatter(
        scatter_df, x="favor_share", y="votos_efectivos", color="tema_display",
        custom_data=[
            "gaceta_vote_id", "date_str", "legislature", "title_clean",
            "favor", "contra", "tema_display", "etapa_votacion",
            "source_url", "gaceta_date", "title", "resultado",
        ],
        labels={
            "favor_share": "← Rechazada          Votación a favor          Aprobada →",
            "votos_efectivos": "Votos efectivos (favor + contra)",
            "tema_display": "Tema",
        },
    )
    fig.update_traces(
        hovertemplate=(
            "<b>%{customdata[0]}</b><br>"
            "%{customdata[1]} · Legislatura %{customdata[2]}<br>"
            "%{customdata[3]}<br>"
            "Favor: %{customdata[4]} · Contra: %{customdata[5]} · %{customdata[11]}<br>"
            "Tema: %{customdata[6]} · Etapa: %{customdata[7]}"
            "<extra></extra>"
        )
    )
    fig.add_vline(x=0.5, line_dash="dot", line_color="rgba(128,128,128,0.6)")
    fig.update_xaxes(tickformat=".0%", range=[-0.02, 1.02], tickvals=[0, 0.25, 0.5, 0.75, 1.0])
    # Pin the y-axis explicitly (instead of leaving it on Plotly autorange) so
    # switching filters can't leave the viewport stuck at a previous
    # selection's scale — every render computes its own range fresh.
    y_min, y_max = scatter_df["votos_efectivos"].min(), scatter_df["votos_efectivos"].max()
    y_pad = max((y_max - y_min) * 0.05, 5)
    fig.update_yaxes(range=[y_min - y_pad, y_max + y_pad])
    fig.update_layout(
        height=520,
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        font=dict(family="IBM Plex Sans", size=12),
        legend=dict(font=dict(size=10)),
    )

    # st.plotly_chart preserves pan/zoom state across reruns for a stable key,
    # so a chart zoomed/auto-fit to one filter selection stays frozen at that
    # viewport when the filters change — new data outside it looks "missing".
    # Keying on what's actually plotted forces a fresh viewport per selection.
    chart_key = "consensus_scatter_" + "_".join(
        str(v) for v in sorted(scatter_df["legislature"].unique())
    ) + f"_{len(scatter_df)}"
    event = st.plotly_chart(
        fig, use_container_width=True, on_select="rerun",
        selection_mode="points", key=chart_key,
    )

    points = (event.get("selection") or {}).get("points", []) if event else []
    if not points:
        st.caption("Selecciona un punto en la gráfica para ver el detalle de esa votación.")
        return

    custom = points[0].get("customdata")
    if not custom:
        return
    vote_id, date_str, legislature, title_clean = custom[0], custom[1], custom[2], custom[3]
    source_url, gaceta_date, title_raw = custom[8], custom[9], custom[10]
    if len(points) > 1:
        st.caption(f"{len(points)} votaciones seleccionadas — mostrando detalle de la primera.")

    st.markdown(f"**`{vote_id}`** · {date_str} · L{legislature} — {title_clean}")
    col_tabla, col_gaceta, col_pdf = st.columns(3)
    if source_url:
        col_tabla.link_button("Tabla de votos ↗", source_url)

    issue_url = gaceta_issue_url(legislature, gaceta_date)
    if issue_url:
        col_gaceta.link_button("Gaceta del día ↗", issue_url)

    anexo = find_gaceta_document(legislature, gaceta_date, title_raw) if gaceta_date else None
    if anexo:
        col_pdf.link_button("Iniciativa (PDF) ↗", anexo[0])

    st.markdown("---")
    render_vote_detail(votes_df, vote_id, database_version)


# ── Top-level entry point ──────────────────────────────────────────────────────

def render_gaceta():
    """Load data, render controls, dispatch to sub-page renderers."""
    vote_index_version, database_version = get_gaceta_cache_versions()
    try:
        votes_df = load_gaceta_votes(vote_index_version)
    except FileNotFoundError:
        st.error(
            "Archivos de Gaceta no encontrados. "
            "Ejecuta `python ingestion/gaceta_materialize.py` primero."
        )
        return

    all_legs = sorted(votes_df["legislature"].unique(), reverse=True)

    gc1, gc2 = st.columns([1, 2])
    with gc2:
        page = st.radio("Vista", ["Diputado", "Clasificación"], horizontal=True)
    with gc1:
        if page == "Diputado":
            leg_sel = st.selectbox(
                "Legislatura", all_legs, format_func=lambda x: f"Legislatura {x}", key="dep_legislature",
            )

    st.markdown("---")

    if page == "Diputado":
        render_deputy_view(leg_sel, database_version)
    else:
        render_classification_view(votes_df, database_version)
