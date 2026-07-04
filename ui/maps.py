"""
Choropleth map rendering (Mapbox-backed via Plotly).
Covers both the interactive multi-metric map (PRE_2024) and
the historical bloc-colored winner maps.
"""

import json

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from ui.common import (
    MATERIALIZED_DIR, MAP_METRICS, PARTY_GROUPS, TS_PARTY_COLORS,
    _norm, _ts_fallback_color, agg_blocs,
)

# ── Map-specific sub-selections of MAP_METRICS ─────────────────────────────────

_CAND_METRICS = {
    "SHH": MAP_METRICS["% SHH"],
    "FCM": MAP_METRICS["% FCM"],
    "MC":  MAP_METRICS["% MC"],
}
_CAND_LABELS = {
    "SHH": "Sheinbaum (SHH)",
    "FCM": "Galvez (FCM)",
    "MC":  "Alvarez Maynez (MC)",
}
_INFO_METRICS = {k: MAP_METRICS[k] for k in ["Participación", "Votos totales", "Lista nominal"]}

_MAP_BASE_LAYOUT = dict(
    plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
    font_family="IBM Plex Sans", title_font_family="IBM Plex Mono",
    font_color="#888888", margin=dict(l=0, r=0, t=50, b=10),
)


# ── GeoJSON loader ─────────────────────────────────────────────────────────────

@st.cache_data(show_spinner="Cargando GeoJSON...")
def load_municipios_geojson(geojson_path: str = None) -> dict:
    """Load the INEGI-backed pre-processed municipios GeoJSON."""
    path = (
        __import__("pathlib").Path(geojson_path)
        if geojson_path
        else MATERIALIZED_DIR / "municipios_processed.geojson"
    )
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ── Geometry utilities ─────────────────────────────────────────────────────────

def _bbox_to_zoom_center(features: list) -> tuple[dict, float]:
    lats, lons = [], []
    for feat in features:
        geom = feat.get("geometry") or {}
        if geom.get("type") == "Polygon":
            rings = geom["coordinates"]
        elif geom.get("type") == "MultiPolygon":
            rings = [r for poly in geom["coordinates"] for r in poly]
        else:
            continue
        for ring in rings:
            for lon, lat in ring:
                lats.append(lat); lons.append(lon)
    if not lats:
        return {"lat": 23.5, "lon": -102}, 4.0
    clat = (min(lats) + max(lats)) / 2
    clon = (min(lons) + max(lons)) / 2
    span = max(max(lats) - min(lats), max(lons) - min(lons))
    zoom = max(3.0, min(9.0, np.log2(360 / span) - 0.4)) if span > 0 else 4.0
    return {"lat": clat, "lon": clon}, round(zoom, 1)


def _map_location_col(agg: pd.DataFrame, geo: dict) -> str:
    """Use INEGI CVEGEO municipality codes; keep name keys for older parquets."""
    geo_keys = {f["id"] for f in geo["features"]}
    if "_mun_code" in agg.columns:
        codes = agg["_mun_code"].dropna().astype(str)
        if not codes.empty and codes.isin(geo_keys).any():
            return "_mun_code"
    return "_join_key"


# ── Aggregation ────────────────────────────────────────────────────────────────

def _build_map_agg(df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate long municipio df into one row per municipio with all map columns."""
    df_map = df[~df["municipio"].str.contains("EXTRANJERO", case=False, na=False)].copy()

    shh_keys = [k for k in PARTY_GROUPS if PARTY_GROUPS[k]["cand"] == "CAND_SHH"]
    fcm_keys = [k for k in PARTY_GROUPS if PARTY_GROUPS[k]["cand"] == "CAND_FCM"]
    mc_keys  = [k for k in PARTY_GROUPS if PARTY_GROUPS[k]["cand"] == "CAND_MC"]

    agg = df_map.groupby(["nombre_estado", "municipio"]).apply(lambda g: pd.Series({
        "CAND_SHH":          g[g["party_key"].isin(shh_keys)]["votes"].sum(),
        "CAND_FCM":          g[g["party_key"].isin(fcm_keys)]["votes"].sum(),
        "CAND_MC":           g[g["party_key"].isin(mc_keys)]["votes"].sum(),
        "NUM_VOTOS_VALIDOS": g[g["party_key"] == g["party_key"].iloc[0]]["num_votos_validos"].sum(),
        "total_votos":       g[g["party_key"] == g["party_key"].iloc[0]]["total_votos"].sum(),
        "lista_nominal":     g["lista_nominal_part"].iloc[0] if "lista_nominal_part" in g.columns else 0,
        "_join_key":         g["_join_key"].iloc[0] if "_join_key" in g.columns else "",
        "_mun_code":         g["_mun_code"].iloc[0] if "_mun_code" in g.columns else "",
    })).reset_index()

    agg = agg[agg["NUM_VOTOS_VALIDOS"] > 0].copy()
    cand_total           = agg["CAND_SHH"] + agg["CAND_FCM"] + agg["CAND_MC"]
    agg["pct_shh"]       = (agg["CAND_SHH"] / cand_total * 100).round(1)
    agg["pct_fcm"]       = (agg["CAND_FCM"] / cand_total * 100).round(1)
    agg["pct_mc"]        = (agg["CAND_MC"]  / cand_total * 100).round(1)
    agg["participacion"] = (
        agg["total_votos"] / agg["lista_nominal"].replace(0, float("nan")) * 100
    ).round(1)

    def _winner(row):
        v = {"SHH": row["CAND_SHH"], "FCM": row["CAND_FCM"], "MC": row["CAND_MC"]}
        return max(v, key=v.get)

    agg["winner"]       = agg.apply(_winner, axis=1)
    agg["winner_label"] = agg["winner"].map(
        {"SHH": "Sheinbaum (SHH)", "FCM": "Galvez (FCM)", "MC": "Alvarez Maynez (MC)"}
    )
    if "_join_key" not in agg.columns or agg["_join_key"].eq("").all():
        agg["_join_key"] = (
            agg["nombre_estado"].map(_norm) + "||" + agg["municipio"].map(_norm)
        )
    agg["_label"] = agg["nombre_estado"] + " - " + agg["municipio"]
    return agg


# ── Figure builders ────────────────────────────────────────────────────────────

def _build_winner_fig(agg: pd.DataFrame, geo: dict, center: dict,
                      zoom: float, opacity: float) -> go.Figure:
    """Discrete winner choropleth — one color per candidate."""
    loc_col     = _map_location_col(agg, geo)
    agg_matched = agg[agg[loc_col].isin({f["id"] for f in geo["features"]})].copy()
    fig = go.Figure()
    winner_cfg = {
        "SHH": {"color": "#8B0000", "name": "Sheinbaum (SHH)"},
        "FCM": {"color": "#1E90FF", "name": "Galvez (FCM)"},
        "MC":  {"color": "#FF8C00", "name": "Alvarez Maynez (MC)"},
    }
    for cand_key, cfg in winner_cfg.items():
        subset = agg_matched[agg_matched["winner"] == cand_key]
        if subset.empty:
            continue
        ids_set = set(subset[loc_col])
        geo_sub = {"type": "FeatureCollection",
                   "features": [f for f in geo["features"] if f["id"] in ids_set]}
        fig.add_trace(go.Choroplethmapbox(
            geojson=geo_sub, locations=subset[loc_col],
            z=[1] * len(subset),
            colorscale=[[0, cfg["color"]], [1, cfg["color"]]],
            showscale=False, marker_opacity=opacity,
            marker_line_width=0.3, marker_line_color="rgba(255,255,255,0.15)",
            hovertext=subset["_label"],
            customdata=subset[["pct_shh","pct_fcm","pct_mc","participacion","total_votos"]].values,
            hovertemplate=(
                "<b>%{hovertext}</b><br>"
                f"Ganador: {cfg['name']}<br>"
                "SHH: %{customdata[0]:.1f}%<br>"
                "FCM: %{customdata[1]:.1f}%<br>"
                "MC:  %{customdata[2]:.1f}%<br>"
                "Participacion: %{customdata[3]:.1f}%<br>"
                "Votos: %{customdata[4]:,}<extra></extra>"
            ),
            name=cfg["name"], showlegend=True,
        ))
    fig.update_layout(
        mapbox=dict(style="carto-darkmatter", zoom=zoom, center=center),
        legend=dict(
            orientation="h", yanchor="top", y=-0.05,
            xanchor="center", x=0.5, font=dict(size=12), itemsizing="constant",
        ),
        title="Ganador por Municipio",
        margin=dict(l=0, r=0, t=50, b=60),
    )
    return fig


def _build_continuous_fig(agg: pd.DataFrame, geo: dict, center: dict,
                           zoom: float, metric: dict, opacity: float) -> go.Figure:
    """Continuous choropleth for a single numeric metric."""
    loc_col     = _map_location_col(agg, geo)
    agg_matched = agg[agg[loc_col].isin({f["id"] for f in geo["features"]})].copy()
    hover_cols  = {
        "winner_label": "Ganador",
        "pct_shh":      "SHH %",
        "pct_fcm":      "FCM %",
        "pct_mc":       "MC %",
        "participacion": "Participación %",
        "total_votos":  "Votos totales",
        "lista_nominal": "Lista nominal",
    }
    col     = metric["col"]
    r_color = metric["range"] if metric["range"] else [
        float(agg_matched[col].quantile(0.05)),
        float(agg_matched[col].quantile(0.95)),
    ]
    fig = px.choropleth_mapbox(
        agg_matched, geojson=geo, locations=loc_col, color=col,
        color_continuous_scale=metric["scale"], range_color=r_color,
        mapbox_style="carto-darkmatter", zoom=zoom, center=center,
        opacity=opacity, hover_name="_label",
        hover_data={k: True for k in hover_cols},
        labels=hover_cols,
        title=metric["label"],
    )
    field_lines = "".join(
        f"{label}=%{{customdata[{i}]}}<br>" for i, label in enumerate(hover_cols.values())
    )
    fig.update_traces(
        marker_line_width=0,
        hovertemplate=f"<b>%{{hovertext}}</b><br>{field_lines}<extra></extra>",
    )
    fig.update_layout(coloraxis_colorbar=dict(
        title=metric["cb_title"], thickness=10, len=0.55))
    return fig


# ── Top-level render functions ─────────────────────────────────────────────────

def render_mexico_map(df: pd.DataFrame, map_key_suffix: str = "nacional",
                      height: int = 650):
    """Interactive online map — one map at a time via radio selector."""
    geo = load_municipios_geojson()
    if not geo:
        st.warning(
            "No se encontro el GeoJSON procesado de municipios. "
            "Ejecuta `python ingestion/electoral_materialize.py views --force` "
            "para generarlo desde INEGI Marco Geoestadistico 2024."
        )
        return

    agg       = _build_map_agg(df)
    loc_col   = _map_location_col(agg, geo)
    geo_keys  = {f["id"] for f in geo["features"]}
    unmatched = len(agg) - len(agg[agg[loc_col].isin(geo_keys)])

    matched_ids  = set(agg[agg[loc_col].isin(geo_keys)][loc_col])
    center, zoom = _bbox_to_zoom_center(
        [f for f in geo["features"] if f["id"] in matched_ids]
    )

    st.markdown('<div class="section-label">Mapa municipal</div>', unsafe_allow_html=True)

    map_type = st.radio(
        "Tipo de mapa",
        options=["Ganador", "% votos por candidato", "Información electoral"],
        horizontal=True,
        key=f"map_type_{map_key_suffix}",
    )

    map_cols = st.columns([1, 1])
    with map_cols[0]:
        zoom = st.slider(
            "Zoom", min_value=3.0, max_value=9.0, value=float(zoom), step=0.1,
            key=f"map_zoom_{map_key_suffix}",
        )
    with map_cols[1]:
        opacity = st.slider(
            "Transparencia", min_value=0.05, max_value=1.0, value=0.25, step=0.05,
            key=f"map_opacity_{map_key_suffix}",
        )

    if map_type == "Ganador":
        fig = _build_winner_fig(agg, geo, center, zoom, opacity)
    elif map_type == "% votos por candidato":
        cand_sel = st.selectbox(
            "Candidato",
            options=list(_CAND_LABELS.keys()),
            format_func=lambda k: _CAND_LABELS[k],
            key=f"map_cand_{map_key_suffix}",
        )
        fig = _build_continuous_fig(agg, geo, center, zoom, _CAND_METRICS[cand_sel], opacity)
    else:
        info_sel = st.selectbox(
            "Métrica",
            options=list(_INFO_METRICS.keys()),
            format_func=lambda k: _INFO_METRICS[k]["label"],
            key=f"map_info_{map_key_suffix}",
        )
        fig = _build_continuous_fig(agg, geo, center, zoom, _INFO_METRICS[info_sel], opacity)

    fig.update_layout(**_MAP_BASE_LAYOUT, height=height)
    st.plotly_chart(fig, use_container_width=True)

    if unmatched > 0:
        st.caption(f"{unmatched} municipios sin geometria en el GeoJSON.")


def render_hist_winner_map(df: pd.DataFrame, blocs: dict, geojson: dict,
                           title: str, height: int = 560):
    """Historical bloc-colored winner choropleth."""
    agg = agg_blocs(df, ["nombre_estado", "municipio"], blocs)
    if agg.empty:
        st.info("Sin datos para el mapa.")
        return

    loc_col = _map_location_col(agg, geojson)
    all_ids = {f["id"] for f in geojson["features"]}
    agg     = agg[agg[loc_col].isin(all_ids)].copy()
    agg["_label"] = agg["nombre_estado"] + " — " + agg["municipio"]

    matched_ids = set(agg[loc_col])
    center, zoom = _bbox_to_zoom_center(
        [f for f in geojson["features"] if f["id"] in matched_ids]
    )

    fig = go.Figure()
    for bloc_key in ("A", "B", "C"):
        cfg    = blocs[bloc_key]
        subset = agg[agg["winner"] == bloc_key]
        if subset.empty:
            continue
        ids_set = set(subset[loc_col])
        geo_sub = {"type": "FeatureCollection",
                   "features": [f for f in geojson["features"] if f["id"] in ids_set]}
        fig.add_trace(go.Choroplethmapbox(
            geojson=geo_sub, locations=subset[loc_col],
            z=[1] * len(subset),
            colorscale=[[0, cfg["color"]], [1, cfg["color"]]],
            showscale=False, marker_opacity=0.82,
            marker_line_width=0.25, marker_line_color="rgba(255,255,255,0.1)",
            hovertext=subset["_label"],
            customdata=subset[["pct_A","pct_B","pct_C","total_votos"]].values,
            hovertemplate=(
                "<b>%{hovertext}</b><br>"
                f"<span style='color:{blocs['A']['color']}'>{blocs['A']['label'].split('—')[0].strip()}</span>: %{{customdata[0]:.1f}}%<br>"
                f"<span style='color:{blocs['B']['color']}'>{blocs['B']['label'].split('—')[0].strip()}</span>: %{{customdata[1]:.1f}}%<br>"
                f"<span style='color:{blocs['C']['color']}'>{blocs['C']['label'].split('—')[0].strip()}</span>: %{{customdata[2]:.1f}}%<br>"
                "Votos: %{customdata[3]:,}<extra></extra>"
            ),
            name=cfg["label"], showlegend=True,
        ))

    fig.update_layout(
        mapbox=dict(style="carto-darkmatter", zoom=zoom, center=center),
        legend=dict(orientation="h", yanchor="top", y=-0.04,
                    xanchor="center", x=0.5, font=dict(size=11), itemsizing="constant"),
        title=dict(text=f"<b>{title}</b>", font=dict(family="IBM Plex Mono", size=14)),
        margin=dict(l=0, r=0, t=50, b=70),
        height=height,
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig, use_container_width=True)


def render_national_generic_map(df: pd.DataFrame, geojson: dict,
                                 election_label: str, height: int = 600):
    """
    Non-presidential or election without bloc mapping: winner map using
    generic TS_PARTY_COLORS. Used by the Nacional · Histórico page for
    DIP/SEN elections.
    """
    from ui.common import _ts_fallback_color

    loc_group_cols = ["nombre_estado", "municipio", "_join_key", "party_key"]
    if "_mun_code" in df.columns:
        loc_group_cols.insert(3, "_mun_code")

    agg_gen = df.groupby(loc_group_cols, as_index=False)["votes"].sum()
    loc_col = _map_location_col(agg_gen, geojson)
    idx_win = agg_gen.groupby(loc_col)["votes"].idxmax()
    winners = agg_gen.loc[idx_win].copy()
    winners["_color"] = winners["party_key"].map(
        lambda k: TS_PARTY_COLORS.get(k, _ts_fallback_color(k))
    )
    winners["_label"] = winners["nombre_estado"] + " — " + winners["municipio"]

    all_ids = {f["id"] for f in geojson["features"]}
    winners = winners[winners[loc_col].isin(all_ids)]

    fig = go.Figure()
    for pk in sorted(winners["party_key"].unique()):
        subset  = winners[winners["party_key"] == pk]
        color   = TS_PARTY_COLORS.get(pk, _ts_fallback_color(pk))
        ids_set = set(subset[loc_col])
        geo_sub = {"type": "FeatureCollection",
                   "features": [f for f in geojson["features"] if f["id"] in ids_set]}
        fig.add_trace(go.Choroplethmapbox(
            geojson=geo_sub, locations=subset[loc_col],
            z=[1] * len(subset),
            colorscale=[[0, color], [1, color]],
            showscale=False, marker_opacity=0.82,
            marker_line_width=0.25, marker_line_color="rgba(255,255,255,0.1)",
            hovertext=subset["_label"],
            customdata=subset["votes"].values,
            hovertemplate=(
                "<b>%{hovertext}</b><br>"
                f"Ganador: {pk}<br>"
                "Votos: %{customdata:,}<extra></extra>"
            ),
            name=pk, showlegend=True,
        ))

    fig.update_layout(
        mapbox=dict(style="carto-darkmatter", zoom=4.1, center={"lat": 23.6, "lon": -102.5}),
        legend=dict(orientation="h", yanchor="top", y=-0.04, xanchor="center", x=0.5,
                    font=dict(size=10), itemsizing="constant"),
        title=dict(text=f"<b>Ganador por Municipio · {election_label}</b>",
                   font=dict(family="IBM Plex Mono", size=14)),
        margin=dict(l=0, r=0, t=50, b=80),
        height=height,
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption("Mapa de ganador por municipio. Elecciones sin estructura ternaria (DIP/SEN) muestran el partido con más votos en cada municipio.")
