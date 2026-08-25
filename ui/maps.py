"""Choropleth map rendering (Mapbox-backed via Plotly)."""

import json

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from ui.common import (
    MATERIALIZED_DIR, TS_PARTY_COLORS, _norm, _ts_fallback_color, agg_blocs,
    title_case_es,
)


# ── GeoJSON loader ─────────────────────────────────────────────────────────────

@st.cache_data(show_spinner="Cargando GeoJSON...")
def load_municipios_geojson(geojson_path: str = None) -> dict:
    """Load the INEGI-backed pre-processed municipios GeoJSON for display."""
    path = (
        __import__("pathlib").Path(geojson_path)
        if geojson_path
        else MATERIALIZED_DIR / "municipios_processed.geojson"
    )
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        geojson = json.load(f)
    return _trim_colima_remote_islands(geojson)


# State names in our election data that don't literally match the state
# GeoJSON's `name` property (which uses shorter common-use forms).
_ESTADO_NAME_ALIASES = {
    "COAHUILA DE ZARAGOZA": "COAHUILA",
    "MICHOACAN DE OCAMPO": "MICHOACAN",
    "VERACRUZ DE IGNACIO DE LA LLAVE": "VERACRUZ",
}


@st.cache_data(show_spinner="Cargando GeoJSON de estados...")
def load_estados_geojson(geojson_path: str = None) -> dict:
    """Load the 32-state Mexico GeoJSON, keying each feature by its normalized name."""
    path = (
        __import__("pathlib").Path(geojson_path)
        if geojson_path
        else MATERIALIZED_DIR / "estados_processed.geojson"
    )
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        geojson = json.load(f)
    for feature in geojson.get("features", []):
        feature["id"] = _norm(feature["properties"].get("name", ""))
    return geojson


# ── Geometry utilities ─────────────────────────────────────────────────────────

def _trim_colima_remote_islands(geojson: dict) -> dict:
    """Hide Colima's distant ocean-island polygons in mainland municipality maps.

    The INEGI geometry correctly assigns the Revillagigedo islands to Colima,
    but they sit hundreds of kilometres west of the state and make a mainland
    state view appear to contain stray points in the ocean.
    """
    for feature in geojson.get("features", []):
        if (
            feature.get("properties", {}).get("CVE_ENT") != "06"
            or feature.get("geometry", {}).get("type") != "MultiPolygon"
        ):
            continue
        mainland_polygons = [
            polygon for polygon in feature["geometry"]["coordinates"]
            if max(point[0] for point in polygon[0]) >= -105
        ]
        if mainland_polygons:
            feature["geometry"] = {
                "type": "MultiPolygon",
                "coordinates": mainland_polygons,
            }
    return geojson

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
    """Resolve map locations, falling back to normalized state/municipality names."""
    geo_keys = {f["id"] for f in geo["features"]}
    if "_mun_code" in agg.columns:
        codes = agg["_mun_code"].dropna().astype(str)
        if not codes.empty and codes.isin(geo_keys).any():
            return "_mun_code"
    if "_join_key" in agg.columns:
        keys = agg["_join_key"].dropna().astype(str)
        if not keys.empty and keys.isin(geo_keys).any():
            return "_join_key"

    # Older election files do not carry INEGI municipality codes. Their state
    # IDs and municipality names still allow a stable match to the GeoJSON.
    if {"id_estado", "municipio"}.issubset(agg.columns):
        geo_by_name = {
            f"{str(f['properties'].get('CVE_ENT', '')).zfill(2)}||{_norm(f['properties'].get('NOMGEO', ''))}": f["id"]
            for f in geo["features"]
        }
        agg["_geo_id"] = [
            geo_by_name.get(f"{int(state_id):02d}||{_norm(municipio)}")
            if pd.notna(state_id) and pd.notna(municipio) else None
            for state_id, municipio in zip(agg["id_estado"], agg["municipio"])
        ]
        return "_geo_id"
    return "_join_key"


# ── Winner map ────────────────────────────────────────────────────────────────

def render_winner_map(df: pd.DataFrame, blocs: dict, geojson: dict,
                      title: str, height: int = 560):
    """Render the shared bloc-colored municipal winner map for any cycle."""
    if not geojson:
        st.warning(
            "No se encontró el GeoJSON procesado de municipios. "
            "Ejecuta `python electoral/materialize.py views --force`."
        )
        return

    agg = agg_blocs(df, ["id_estado", "nombre_estado", "municipio"], blocs)
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


def render_winner_map_estado(df: pd.DataFrame, blocs: dict, geojson: dict,
                              title: str, height: int = 560,
                              party_winners: bool = False):
    """Render a state map by bloc, or by the leading party when requested."""
    if not geojson:
        st.warning(
            "No se encontró el GeoJSON procesado de estados. "
            "Descarga `estados_processed.geojson` en data/materialized/."
        )
        return

    if party_winners:
        by_party = df.groupby(
            ["id_estado", "nombre_estado", "party_key"], as_index=False,
        )["votes"].sum()
        totals = by_party.groupby("id_estado")["votes"].transform("sum")
        winners = by_party.loc[by_party.groupby("id_estado")["votes"].idxmax()].copy()
        winners["winnerPct"] = winners["votes"] / totals.loc[winners.index] * 100
        winners["_geo_id"] = winners["nombre_estado"].map(
            lambda n: _norm(_ESTADO_NAME_ALIASES.get(_norm(n), n))
        )
        all_ids = {f["id"] for f in geojson["features"]}
        winners = winners[winners["_geo_id"].isin(all_ids)].copy()
        winners["_label"] = winners["nombre_estado"].apply(title_case_es)

        fig = go.Figure()
        for party_key in sorted(winners["party_key"].unique()):
            subset = winners[winners["party_key"] == party_key]
            color = TS_PARTY_COLORS.get(party_key, _ts_fallback_color(party_key))
            ids_set = set(subset["_geo_id"])
            geo_sub = {"type": "FeatureCollection",
                       "features": [f for f in geojson["features"] if f["id"] in ids_set]}
            fig.add_trace(go.Choroplethmapbox(
                geojson=geo_sub, locations=subset["_geo_id"],
                z=[1] * len(subset),
                colorscale=[[0, color], [1, color]],
                showscale=False, marker_opacity=0.82,
                marker_line_width=0.4, marker_line_color="rgba(255,255,255,0.15)",
                hovertext=subset["_label"],
                customdata=subset[["winnerPct", "votes"]].values,
                hovertemplate=(
                    "<b>%{hovertext}</b><br>"
                    f"Partido con más votos: {party_key}<br>"
                    "Porcentaje: %{customdata[0]:.1f}%<br>"
                    "Votos: %{customdata[1]:,}<extra></extra>"
                ),
                name=party_key, showlegend=True,
            ))

        fig.update_layout(
            mapbox=dict(style="carto-darkmatter", zoom=4.1, center={"lat": 23.6, "lon": -102.5}),
            legend=dict(orientation="h", yanchor="top", y=-0.04,
                        xanchor="center", x=0.5, font=dict(size=10), itemsizing="constant"),
            title=dict(text=f"<b>{title}</b>", font=dict(family="IBM Plex Mono", size=14)),
            margin=dict(l=0, r=0, t=50, b=80), height=height,
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig, use_container_width=True)
        st.caption("Cada estado se colorea según el partido que recibió más votos.")
        return

    agg = agg_blocs(df, ["id_estado", "nombre_estado"], blocs)
    if agg.empty:
        st.info("Sin datos para el mapa.")
        return

    agg["_geo_id"] = agg["nombre_estado"].map(
        lambda n: _norm(_ESTADO_NAME_ALIASES.get(_norm(n), n))
    )
    loc_col = "_geo_id"
    all_ids = {f["id"] for f in geojson["features"]}
    agg     = agg[agg[loc_col].isin(all_ids)].copy()
    agg["_label"] = agg["nombre_estado"].apply(title_case_es)

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
            marker_line_width=0.4, marker_line_color="rgba(255,255,255,0.15)",
            hovertext=subset["_label"],
            customdata=subset[["pct_A", "pct_B", "pct_C", "total_votos"]].values,
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
