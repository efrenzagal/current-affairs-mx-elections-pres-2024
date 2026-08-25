"""
Senado de la Republica section — vote detail (party grid) and senator vote
calendar. Self-contained: owns its own loaders and controls, but reuses the
tile/calendar grid plotting helpers from ui.gaceta rather than re-deriving
the same layout math for a second chamber.
Call render_senado() from the main app.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st

from ui.gaceta import _add_tile_coords, _calendar_grid_figure, _format_label, _tile_grid_figure
from lib.person_names import display_person_name

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "election_data.db"
LEGISLATURE = 66

VOTE_LEVELS = ["Sí", "No", "Abstención", "Ausente"]
VOTE_COLORS = {
    "Sí": "#2E7D32",
    "No": "#C62828",
    "Abstención": "#9E9E9E",
    "Ausente": "#111111",
}
VOTE_RECODE = {
    "PRO": "Sí",
    "CONTRA": "No",
    "ABSTENCIÓN": "Abstención",
    "AUSENTE": "Ausente",
}


# ── Cached loaders ─────────────────────────────────────────────────────────────

def _file_version(path: Path) -> tuple[int, int]:
    """Return a cheap, hashable cache key that changes when a file is rebuilt."""
    try:
        stat = path.stat()
    except FileNotFoundError:
        return (0, 0)
    return (stat.st_mtime_ns, stat.st_size)


def get_senado_db_version() -> tuple[tuple[int, int], ...]:
    """Version the SQLite read, including its WAL sidecar."""
    return tuple(
        _file_version(path)
        for path in (DB_PATH, DB_PATH.with_name(f"{DB_PATH.name}-wal"), DB_PATH.with_name(f"{DB_PATH.name}-shm"))
    )


@st.cache_resource(show_spinner=False)
def get_senado_connection(database_version: tuple[tuple[int, int], ...]) -> sqlite3.Connection:
    """Open a new connection whenever an ingest changes the SQLite files."""
    del database_version
    return sqlite3.connect(DB_PATH, check_same_thread=False)


@st.cache_data
def load_senado_roster(database_version: tuple[tuple[int, int], ...]) -> pd.DataFrame:
    conn = get_senado_connection(database_version)
    return pd.read_sql_query(
        """
        SELECT DISTINCT f.senador_id, d.senador_name
        FROM fact_senador_vote AS f
        JOIN dim_senador AS d ON d.senador_id = f.senador_id
        ORDER BY d.senador_name
        """,
        conn,
    )


@st.cache_data
def load_dim_senador(
    senador_seat_id: str,
    database_version: tuple[tuple[int, int], ...],
) -> pd.DataFrame:
    """Resolve one official INE seat through the persisted identity bridge."""
    conn = get_senado_connection(database_version)
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='dim_senadores'"
    ).fetchone()
    if not exists:
        return pd.DataFrame()
    return pd.read_sql_query(
        """
        SELECT
            senador_seat_id, legislature, ine_candidate_name, display_name,
            source_name_role, senador_id, senador_name,
            match_method, match_score
        FROM dim_senadores
        WHERE senador_seat_id = ?
        """,
        conn,
        params=(senador_seat_id,),
    )


@st.cache_data
def load_senador_calendar(
    senador_id: int, database_version: tuple[tuple[int, int], ...]
) -> pd.DataFrame:
    conn = get_senado_connection(database_version)
    classified = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' "
        "AND name='fact_senado_vote_classification'"
    ).fetchone()
    classification_join = (
        "LEFT JOIN fact_senado_vote_classification AS c "
        "ON c.votacion_id = v.votacion_id"
        if classified else ""
    )
    classification_columns = (
        "c.origen, c.etapa_votacion, c.tipo_instrumento, c.tema_politica, "
        "c.requiere_revision"
        if classified else
        "NULL AS origen, NULL AS etapa_votacion, NULL AS tipo_instrumento, "
        "NULL AS tema_politica, NULL AS requiere_revision"
    )
    return pd.read_sql_query(
        f"""
        SELECT v.votacion_id, v.vote_date, v.description, v.vote_type,
               f.grupo_parlamentario, f.voto, {classification_columns}
        FROM fact_senador_vote AS f
        JOIN dim_senado_vote AS v ON v.votacion_id = f.votacion_id
        {classification_join}
        WHERE f.senador_id = ?
        ORDER BY v.vote_date, v.votacion_id
        """,
        conn, params=(int(senador_id),),
    )


@st.cache_data
def load_senado_classification(
    database_version: tuple[tuple[int, int], ...]
) -> pd.DataFrame:
    conn = get_senado_connection(database_version)
    classified = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' "
        "AND name='fact_senado_vote_classification'"
    ).fetchone()
    if not classified:
        return pd.DataFrame()
    return pd.read_sql_query(
        """
        SELECT v.votacion_id, v.vote_date, v.description, v.vote_type,
               v.en_pro, v.en_contra, v.abstencion,
               c.origen, c.etapa_votacion, c.tipo_instrumento,
               c.tema_politica, c.confianza, c.requiere_revision,
               c.evidencia, c.model, c.prompt_version, c.classified_at
        FROM dim_senado_vote AS v
        JOIN fact_senado_vote_classification AS c USING(votacion_id)
        ORDER BY v.vote_date, v.votacion_id
        """,
        conn,
    )


@st.cache_data
def load_iniciativas_senado(database_version: tuple[tuple[int, int], ...]) -> pd.DataFrame:
    conn = get_senado_connection(database_version)
    df = pd.read_sql_query(
        """
        SELECT
            senado_iniciativa_id, category, title, proposer_type,
            proposer_name, proposer_party, proposer_party_canonical,
            comision, fecha, source_url, needs_review
        FROM dim_senado_iniciativa
        ORDER BY fecha DESC, senado_iniciativa_id DESC
        """,
        conn,
    )
    df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce")
    return df


@st.cache_data
def load_vote_senadores(
    votacion_id: int, database_version: tuple[tuple[int, int], ...]
) -> pd.DataFrame:
    conn = get_senado_connection(database_version)
    return pd.read_sql_query(
        """
        SELECT f.senador_id, f.grupo_parlamentario, f.voto, d.senador_name
        FROM fact_senador_vote AS f
        JOIN dim_senador AS d ON d.senador_id = f.senador_id
        WHERE f.votacion_id = ?
        ORDER BY f.grupo_parlamentario, d.senador_name
        """,
        conn, params=(int(votacion_id),),
    )


@st.cache_data
def load_vote_meta(
    votacion_id: int, database_version: tuple[tuple[int, int], ...]
) -> pd.DataFrame:
    conn = get_senado_connection(database_version)
    classified = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' "
        "AND name='fact_senado_vote_classification'"
    ).fetchone()
    if classified:
        return pd.read_sql_query(
            """
            SELECT v.*, c.origen, c.etapa_votacion, c.tipo_instrumento,
                   c.tema_politica, c.confianza, c.requiere_revision,
                   c.evidencia, c.model, c.prompt_version, c.classified_at
            FROM dim_senado_vote v
            LEFT JOIN fact_senado_vote_classification c USING(votacion_id)
            WHERE v.votacion_id = ?
            """,
            conn,
            params=(int(votacion_id),),
        )
    return pd.read_sql_query(
        """
        SELECT v.*, NULL AS origen, NULL AS etapa_votacion,
               NULL AS tipo_instrumento, NULL AS tema_politica,
               NULL AS confianza, NULL AS requiere_revision,
               NULL AS evidencia, NULL AS model, NULL AS prompt_version,
               NULL AS classified_at
        FROM dim_senado_vote v WHERE v.votacion_id = ?
        """,
        conn,
        params=(int(votacion_id),),
    )


# ── Sub-page renderers ───────────────────────────────────────────────────────

def render_vote_detail(votacion_id: int, database_version: tuple[tuple[int, int], ...]) -> None:
    """General info + party-vote grid for a single Senado roll call."""
    meta = load_vote_meta(votacion_id, database_version)
    if meta.empty:
        st.info("No se encontró información detallada de esta votación.")
        return
    vote = meta.iloc[0]
    vote_date = pd.to_datetime(vote["vote_date"], errors="coerce")
    date_str = vote_date.date() if pd.notna(vote_date) else "s/f"

    st.markdown(f"##### Senado de la República · Legislatura {int(vote['legislature'])}")
    st.subheader(vote["description"] or "(Sin descripción)")
    if vote["vote_type"]:
        st.caption(vote["vote_type"])
    st.caption(f"Votación registrada el {date_str}" if date_str != "s/f" else "Fecha de votación no disponible")
    if pd.notna(vote["tema_politica"]):
        tags = [
            ("Origen", vote["origen"]),
            ("Etapa", vote["etapa_votacion"]),
            ("Instrumento", vote["tipo_instrumento"]),
            ("Tema", vote["tema_politica"]),
        ]
        st.caption(
            " · ".join(f"{label}: {_format_label(str(value))}" for label, value in tags)
        )
        review = " · requiere revisión" if bool(vote["requiere_revision"]) else ""
        st.caption(f"Confianza del clasificador: {float(vote['confianza']):.0%}{review}")
        if vote["evidencia"]:
            with st.expander("Evidencia de la clasificación"):
                st.write(vote["evidencia"])
    if vote["url"]:
        st.link_button("Ver en senado.gob.mx ↗", vote["url"])

    c1, c2, c3 = st.columns(3)
    c1.metric("A favor", f"{int(vote['en_pro']):,}" if pd.notna(vote["en_pro"]) else "—")
    c2.metric("En contra", f"{int(vote['en_contra']):,}" if pd.notna(vote["en_contra"]) else "—")
    c3.metric("Abstenciones", f"{int(vote['abstencion']):,}" if pd.notna(vote["abstencion"]) else "—")

    st.markdown("---")

    senadores = load_vote_senadores(votacion_id, database_version)
    if senadores.empty:
        st.info("No hay detalle de voto por senador para esta votación.")
        return

    senadores["vote_display"] = senadores["voto"].map(VOTE_RECODE).fillna(senadores["voto"])
    senadores["vote_display"] = pd.Categorical(senadores["vote_display"], categories=VOTE_LEVELS)
    senadores["party_display"] = senadores["grupo_parlamentario"].fillna("Sin grupo")

    party_order = senadores["party_display"].value_counts().index.tolist()
    senadores["party_display"] = pd.Categorical(senadores["party_display"], categories=party_order)
    senadores = senadores.sort_values(["party_display", "vote_display", "senador_name"])
    senadores = _add_tile_coords(senadores, "party_display", columns=12)
    senadores["senador_display"] = senadores["senador_name"].map(display_person_name)
    senadores["tooltip"] = (
        "<b>" + senadores["senador_display"] + "</b>"
        + "<br>Grupo parlamentario: " + senadores["party_display"].astype(str)
        + "<br>Voto individual: " + senadores["vote_display"].astype(str)
    )

    n_parties = senadores["party_display"].nunique()
    fig = _tile_grid_figure(senadores, facet_col="party_display", facet_col_wrap=3,
                             height=280 * -(-n_parties // 3) + 60, marker_size=13)
    st.plotly_chart(fig, use_container_width=True)


def render_senador_view(
    database_version: tuple[tuple[int, int], ...],
    requested_name: str | None = None,
    requested_senador_id: int | None = None,
    show_selector: bool = True,
) -> bool:
    """Render one senator's voting history.

    The composition hemicycle supplies a persisted Senado ``senador_id`` from
    ``dim_senadores``; the normal selector supplies the ID directly.
    """
    senadores = load_senado_roster(database_version)
    if senadores.empty:
        st.info("Sin senadores cargados. Ejecuta `python -m camara_de_senadores.votos.ingest`.")
        return False

    if requested_senador_id:
        matches = senadores[senadores["senador_id"] == requested_senador_id]
        if matches.empty:
            st.info(
                f"No encontré historial de votaciones para "
                f"**{display_person_name(requested_name)}** en la Legislatura {LEGISLATURE}."
            )
            return False
        selected = matches.iloc[0]
        senador_id = selected["senador_id"]
        selected_name = selected["senador_name"]
    else:
        id_to_name = dict(zip(senadores["senador_id"], senadores["senador_name"]))
        senador_ids = list(id_to_name)
        if st.session_state.get("sen_senador_id") not in senador_ids:
            st.session_state["sen_senador_id"] = senador_ids[0]
        if show_selector:
            senador_id = st.selectbox(
                "Senador(a)",
                senador_ids,
                format_func=lambda senador_key: display_person_name(id_to_name[senador_key]),
                key="sen_senador_id",
            )
        else:
            senador_id = st.session_state["sen_senador_id"]
        selected_name = id_to_name[senador_id]

    calendar = load_senador_calendar(senador_id, database_version)
    calendar = calendar[calendar["voto"].notna()].copy()
    if calendar.empty:
        st.info("Sin votaciones registradas para este senador.")
        return False

    calendar["vote_date"] = pd.to_datetime(calendar["vote_date"], errors="coerce")
    calendar = calendar[calendar["vote_date"].notna()].copy()
    if calendar.empty:
        st.info("Sin votaciones con fecha para este senador.")
        return False

    calendar["year"] = calendar["vote_date"].dt.year.astype(str)
    calendar["vote_display"] = calendar["voto"].map(VOTE_RECODE).fillna(calendar["voto"])
    calendar["vote_display"] = pd.Categorical(calendar["vote_display"], categories=VOTE_LEVELS)
    calendar["party_display"] = calendar["grupo_parlamentario"].fillna("Sin grupo")

    attendance = (calendar["vote_display"] != "Ausente").mean()
    parties = list(dict.fromkeys(calendar.sort_values("vote_date")["party_display"]))
    first_vote = calendar["vote_date"].min().date()
    last_vote = calendar["vote_date"].max().date()
    vote_counts = calendar["vote_display"].value_counts().reindex(VOTE_LEVELS, fill_value=0)
    directional_votes = int(vote_counts["Sí"] + vote_counts["No"])
    favor_pct = vote_counts["Sí"] / directional_votes if directional_votes else None
    contra_pct = vote_counts["No"] / directional_votes if directional_votes else None

    def count_and_pct(count: int, pct: float | None) -> str:
        return f"{count:,} · {pct:.1%}" if pct is not None else f"{count:,} · —"

    heading_name = requested_name or selected_name
    st.markdown(f"##### {display_person_name(heading_name)}")
    st.caption(f"Legislatura {LEGISLATURE} · Grupo(s) parlamentario(s): {', '.join(parties)}")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Primera votación", str(first_vote))
    m2.metric("Última votación", str(last_vote))
    m3.metric("Participación", f"{attendance:.1%}")
    m4.metric("Total votaciones", f"{len(calendar):,}")

    v1, v2, v3, v4 = st.columns(4)
    v1.metric("A favor", count_and_pct(int(vote_counts["Sí"]), favor_pct))
    v2.metric("En contra", count_and_pct(int(vote_counts["No"]), contra_pct))
    v3.metric("Abstención", f"{vote_counts['Abstención']:,}")
    v4.metric("Ausente", f"{vote_counts['Ausente']:,}")
    st.caption(
        "Porcentajes sobre votos con dirección (a favor + en contra); "
        "excluyen abstenciones y ausencias."
    )

    st.markdown("---")

    calendar_view = calendar.copy()
    classified_calendar = calendar_view["tema_politica"].notna().any()
    if classified_calendar:
        with st.expander("Filtrar calendario por clasificación", expanded=False):
            filter_cols = st.columns(2)
            topic_options = sorted(calendar_view["tema_politica"].dropna().unique())
            stage_options = sorted(calendar_view["etapa_votacion"].dropna().unique())
            selected_topics = filter_cols[0].multiselect(
                "Tema de política", topic_options, format_func=_format_label,
                key=f"sen_topic_filter_{senador_id}",
            )
            selected_stages = filter_cols[1].multiselect(
                "Etapa de votación", stage_options, format_func=_format_label,
                key=f"sen_stage_filter_{senador_id}",
            )
        if selected_topics:
            calendar_view = calendar_view[calendar_view["tema_politica"].isin(selected_topics)]
        if selected_stages:
            calendar_view = calendar_view[calendar_view["etapa_votacion"].isin(selected_stages)]
        if calendar_view.empty:
            st.info("Ninguna votación coincide con los filtros de clasificación.")
            return True
        st.caption(f"Mostrando {len(calendar_view):,} de {len(calendar):,} votaciones.")

    year_levels = sorted(calendar_view["year"].unique(), reverse=True)
    calendar_view["year"] = pd.Categorical(calendar_view["year"], categories=year_levels)
    calendar_view = calendar_view.sort_values(["year", "vote_date", "votacion_id"])
    calendar_columns = 20
    calendar_view = _add_tile_coords(calendar_view, "year", columns=calendar_columns)
    calendar_view["tooltip"] = (
        calendar_view["vote_date"].dt.date.astype(str)
        + " · " + calendar_view["vote_display"].astype(str)
    )
    if classified_calendar:
        calendar_view["tooltip"] += (
            "<br>Tema: " + calendar_view["tema_politica"].fillna("sin clasificación").map(_format_label)
            + "<br>Etapa: " + calendar_view["etapa_votacion"].fillna("sin clasificación").map(_format_label)
        )

    fig = _calendar_grid_figure(calendar_view, year_col="year", year_levels=year_levels,
                                 columns=calendar_columns, marker_size=38, row_px=48,
                                 id_col="votacion_id")
    chart_key = (
        f"senador_calendar_{senador_id}_"
        f"{len(calendar_view)}_{first_vote}_{last_vote}"
    )
    event = st.plotly_chart(
        fig,
        use_container_width=True,
        on_select="rerun",
        selection_mode="points",
        key=chart_key,
        config={"displayModeBar": False, "scrollZoom": False},
    )

    points = (event.get("selection") or {}).get("points", []) if event else []
    st.markdown("---")
    if not points:
        st.caption("Selecciona una votación en el calendario para ver su detalle.")
        return True

    custom = points[0].get("customdata")
    if not custom or len(custom) < 2:
        return True
    votacion_id = custom[1]
    if len(points) > 1:
        st.caption(f"{len(points)} votaciones seleccionadas — mostrando detalle de la primera.")
    render_vote_detail(votacion_id, database_version)
    return True


CLASSIFICATION_FILTER_COLUMNS = {
    "origen": "Origen",
    "etapa_votacion": "Etapa",
    "tipo_instrumento": "Instrumento",
    "tema_politica": "Tema",
}


def render_classification_view(database_version: tuple[tuple[int, int], ...]) -> None:
    """Filter and inspect the applied Senate classification warehouse table."""
    df = load_senado_classification(database_version)
    if df.empty:
        st.info(
            "No hay clasificaciones del Senado aplicadas. Ejecuta el comando `apply` "
            "del clasificador antes de abrir esta vista."
        )
        return
    df["vote_date"] = pd.to_datetime(df["vote_date"], errors="coerce")

    with st.expander("Filtros", expanded=True):
        cols = st.columns(len(CLASSIFICATION_FILTER_COLUMNS))
        selections: dict[str, list[str]] = {}
        for widget_col, (field, label) in zip(cols, CLASSIFICATION_FILTER_COLUMNS.items()):
            options = sorted(df[field].dropna().unique())
            selections[field] = widget_col.multiselect(
                label, options, format_func=_format_label, key=f"sen_clf_{field}"
            )
        only_review = st.checkbox("Solo clasificaciones que requieren revisión", value=False)

    filtered = df.copy()
    for field, selected in selections.items():
        if selected:
            filtered = filtered[filtered[field].isin(selected)]
    if only_review:
        filtered = filtered[filtered["requiere_revision"].astype(bool)]

    m1, m2, m3 = st.columns(3)
    m1.metric("Votaciones", f"{len(filtered):,}")
    m2.metric("Temas", f"{filtered['tema_politica'].nunique():,}")
    m3.metric(
        "Requieren revisión",
        f"{filtered['requiere_revision'].astype(bool).sum():,}",
    )
    if filtered.empty:
        st.info("Ninguna votación coincide con los filtros actuales.")
        return

    topic_counts = (
        filtered.assign(tema=filtered["tema_politica"].map(_format_label))
        .groupby("tema").size().sort_values(ascending=False).rename("Votaciones")
    )
    st.markdown("##### Votaciones por tema")
    st.bar_chart(topic_counts, horizontal=True)

    st.markdown("##### Explorar una votación")
    vote_options = filtered.sort_values(
        ["vote_date", "votacion_id"], ascending=[False, False]
    )["votacion_id"].tolist()
    labels = {
        int(row.votacion_id): (
            f"{row.vote_date.date() if pd.notna(row.vote_date) else 's/f'} · "
            f"{_format_label(row.tema_politica)} · {str(row.description)[:100]}"
        )
        for row in filtered.itertuples()
    }
    selected_vote = st.selectbox(
        "Votación", vote_options, format_func=lambda vote_id: labels[int(vote_id)],
        key="sen_clf_vote_id",
    )
    render_vote_detail(int(selected_vote), database_version)


# ── Top-level entry point ────────────────────────────────────────────────────

def render_senado(
    view: str,
    *,
    senador_seat_id: str | None = None,
    senador_id: int | None = None,
    candidate_name: str | None = None,
):
    """Load Senado data and render one lazily selected top-level view."""
    database_version = get_senado_db_version()

    if view == "Clasificación":
        render_classification_view(database_version)
        return
    if view != "Senador":
        raise ValueError(f"Vista de Senado desconocida: {view}")

    if senador_id is not None:
        render_senador_view(
            database_version,
            requested_name=candidate_name,
            requested_senador_id=int(senador_id),
            show_selector=False,
        )
        return

    if senador_seat_id is not None:
        mapping = load_dim_senador(senador_seat_id, database_version)
        if mapping.empty:
            st.info(
                "Este escaño todavía no está en `dim_senadores`. Ejecuta "
                "`python -m camara_de_senadores.escanos.ingest` para reconstruir el puente."
            )
            return
        mapped = mapping.iloc[0]
        mapped_name = mapped["display_name"] or candidate_name or ""
        if not mapped["senador_id"]:
            st.info(
                f"No hay una correspondencia confiable con senado.gob.mx para "
                f"**{display_person_name(mapped_name)}** "
                f"(`{mapped['match_method']}`)."
            )
            return
        if mapped["source_name_role"] == "suplente":
            st.caption(
                "El historial nominal corresponde a la suplencia registrada que aparece "
                "en las votaciones; el titular electoral se conserva en el escaño."
            )
        if mapped["match_method"] == "approximate_tokens":
            st.caption(
                f"Correspondencia auditada: {display_person_name(mapped_name)} → "
                f"{display_person_name(mapped['senador_name'])} "
                f"(similitud {mapped['match_score']:.0%})"
            )
        render_senador_view(
            database_version,
            requested_name=mapped_name,
            requested_senador_id=int(mapped["senador_id"]),
            show_selector=False,
        )
        return

    render_senador_view(database_version)
