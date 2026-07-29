"""
Presidential approval ratings page.
"""

from __future__ import annotations

import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st


DATA_DIR = Path("aux_scripts/approval_rates")
RECENT_PATH = DATA_DIR / "table-aprobacion.xlsx"
ARCHIVE_PATH = DATA_DIR / "table-aprobacion_archivo.xlsx"

PRESIDENT_COLORS = {
    "EZPL": "#2E7D32",
    "VFQ": "#1565C0",
    "FCH": "#1E90FF",
    "EPN": "#C62828",
    "AMLO": "#8B0000",
    "Sheinbaum": "#C84B31",
}

PRESIDENT_LABELS = {
    "EZPL": "Zedillo",
    "VFQ": "Fox",
    "FCH": "Calderón",
    "EPN": "Peña Nieto",
    "AMLO": "AMLO",
    "Sheinbaum": "Sheinbaum",
}

PRESIDENT_ORDER = list(PRESIDENT_LABELS)


def _col_to_idx(cell_ref: str) -> int:
    letters = "".join(ch for ch in cell_ref if ch.isalpha())
    idx = 0
    for ch in letters:
        idx = idx * 26 + ord(ch.upper()) - 64
    return idx - 1


def _read_xlsx_first_sheet(path: Path) -> pd.DataFrame:
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(path) as zf:
        root = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))

    rows: list[list[str]] = []
    for row in root.findall(".//m:sheetData/m:row", ns):
        cells: dict[int, str] = {}
        for cell in row.findall("m:c", ns):
            idx = _col_to_idx(cell.attrib["r"])
            if cell.attrib.get("t") == "inlineStr":
                node = cell.find("m:is/m:t", ns)
            else:
                node = cell.find("m:v", ns)
            cells[idx] = node.text if node is not None and node.text is not None else ""
        if cells:
            rows.append([cells.get(i, "") for i in range(max(cells) + 1)])

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows[1:], columns=rows[0])


def _clean_pollster(value: str) -> str:
    return re.sub(r"/(?:tel|viv|online)\b", "", str(value), flags=re.IGNORECASE).strip()


def _method(value: str) -> str:
    text = str(value).lower()
    if re.search(r"telef|/tel|telefon", text):
        return "Telefónica"
    if re.search(r"vivien|/viv", text):
        return "Vivienda"
    if re.search(r"online|web|internet", text):
        return "Online"
    return "No especificado"


@st.cache_data(show_spinner="Cargando aprobación presidencial...")
def load_approval_data() -> pd.DataFrame:
    if not RECENT_PATH.exists() or not ARCHIVE_PATH.exists():
        return pd.DataFrame()

    archive = _read_xlsx_first_sheet(ARCHIVE_PATH)
    archive["source"] = "archive"

    recent = _read_xlsx_first_sheet(RECENT_PATH)
    recent["fecha"] = pd.to_datetime(recent["Mes"], format="%b %Y", errors="coerce")
    recent["Presidente"] = recent["fecha"].apply(
        lambda x: "Sheinbaum" if pd.notna(x) and x >= pd.Timestamp("2024-10-01") else "AMLO"
    )
    recent["source"] = "recent"

    df = pd.concat([archive, recent], ignore_index=True, sort=False)
    df["fecha"] = pd.to_datetime(df["Mes"], format="%b %Y", errors="coerce")
    df["Aprueba"] = pd.to_numeric(df["Aprueba"], errors="coerce")
    df["Desaprueba"] = pd.to_numeric(df["Desaprueba"], errors="coerce")
    df["ratio_ad"] = pd.to_numeric(df.get("(A) / (D)"), errors="coerce")
    df["metodo"] = df["Encuestadora"].map(_method)
    df["encuestadora_clean"] = df["Encuestadora"].map(_clean_pollster)
    df["Presidente"] = pd.Categorical(df["Presidente"], categories=PRESIDENT_ORDER, ordered=True)
    return df.dropna(subset=["fecha", "Aprueba"]).sort_values("fecha")


def _monthly_trend(df: pd.DataFrame) -> pd.DataFrame:
    return (
        df.groupby(["Presidente", "fecha"], observed=True)
        .agg(
            mediana=("Aprueba", "median"),
            q25=("Aprueba", lambda x: x.quantile(0.25)),
            q75=("Aprueba", lambda x: x.quantile(0.75)),
            n=("Aprueba", "size"),
        )
        .reset_index()
        .query("n >= 2")
    )


def _house_effects(df: pd.DataFrame, min_n: int = 5) -> pd.DataFrame:
    out = df.copy()
    out["mediana_mes"] = out.groupby("fecha")["Aprueba"].transform("median")
    out["desviacion"] = out["Aprueba"] - out["mediana_mes"]
    return (
        out.groupby("encuestadora_clean")
        .agg(
            efecto_casa=("desviacion", "mean"),
            sd_efecto=("desviacion", "std"),
            n=("desviacion", "size"),
        )
        .reset_index()
        .query("n >= @min_n")
        .sort_values("efecto_casa")
    )


def _house_effects_by_president(df: pd.DataFrame, min_n: int = 3) -> pd.DataFrame:
    out = df.copy()
    out["mediana_mes"] = out.groupby(["Presidente", "fecha"], observed=True)["Aprueba"].transform("median")
    out["desviacion"] = out["Aprueba"] - out["mediana_mes"]
    return (
        out.groupby(["encuestadora_clean", "Presidente"], observed=True)
        .agg(efecto_casa=("desviacion", "mean"), n=("desviacion", "size"))
        .reset_index()
        .query("n >= @min_n")
    )


def render_approval():
    df = load_approval_data()
    if df.empty:
        st.error("No se encontraron los archivos de aprobación presidencial.")
        return

    c1, c2, c3 = st.columns(3)
    c1.metric("Encuestas", f"{len(df):,}")
    c2.metric("Encuestadoras", f"{df['encuestadora_clean'].nunique():,}")
    c3.metric("Periodo", f"{df['fecha'].min():%Y}–{df['fecha'].max():%Y}")

    presidents = st.multiselect(
        "Presidentes",
        PRESIDENT_ORDER,
        default=PRESIDENT_ORDER,
        format_func=lambda x: PRESIDENT_LABELS.get(x, x),
    )
    methods = st.multiselect(
        "Método",
        sorted(df["metodo"].dropna().unique()),
        default=sorted(df["metodo"].dropna().unique()),
    )
    view = df[df["Presidente"].astype(str).isin(presidents) & df["metodo"].isin(methods)].copy()
    if view.empty:
        st.info("Sin datos para esta selección.")
        return

    st.markdown('<div class="section-label">Serie mensual</div>', unsafe_allow_html=True)
    trend = _monthly_trend(view)
    fig = px.scatter(
        view,
        x="fecha",
        y="Aprueba",
        color="Presidente",
        hover_name="encuestadora_clean",
        hover_data={"Encuestadora": True, "metodo": True, "Desaprueba": True},
        color_discrete_map=PRESIDENT_COLORS,
        labels={"fecha": "", "Aprueba": "Aprobación (%)", "Presidente": "Presidente"},
    )
    for president in PRESIDENT_ORDER:
        sub = trend[trend["Presidente"].astype(str) == president]
        if sub.empty:
            continue
        fig.add_scatter(
            x=sub["fecha"],
            y=sub["mediana"],
            mode="lines",
            line=dict(width=3, color=PRESIDENT_COLORS.get(president, "#444")),
            name=f"{PRESIDENT_LABELS.get(president, president)} · mediana",
            legendgroup=president,
            showlegend=False,
        )
    fig.update_layout(
        height=520,
        yaxis=dict(range=[0, 100], ticksuffix="%"),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(family="IBM Plex Sans"),
    )
    st.plotly_chart(fig, use_container_width=True)

    left, right = st.columns([1, 1], gap="large")
    with left:
        st.markdown('<div class="section-label">Efecto de casa</div>', unsafe_allow_html=True)
        house = _house_effects(view)
        fig_house = px.bar(
            house,
            x="efecto_casa",
            y="encuestadora_clean",
            orientation="h",
            color="efecto_casa",
            color_continuous_scale=["#1565C0", "#F7F7F5", "#C84B31"],
            color_continuous_midpoint=0,
            hover_data={"n": True, "sd_efecto": ":.1f"},
            labels={"efecto_casa": "pp vs mediana mensual", "encuestadora_clean": ""},
        )
        fig_house.update_layout(height=520, showlegend=False, coloraxis_showscale=False)
        st.plotly_chart(fig_house, use_container_width=True)

    with right:
        st.markdown('<div class="section-label">Efecto por sexenio</div>', unsafe_allow_html=True)
        heat = _house_effects_by_president(view)
        pivot = heat.pivot(index="encuestadora_clean", columns="Presidente", values="efecto_casa")
        fig_heat = px.imshow(
            pivot,
            color_continuous_scale=["#1565C0", "white", "#C84B31"],
            zmin=-10,
            zmax=10,
            labels={"x": "Presidente", "y": "Encuestadora", "color": "pp"},
            aspect="auto",
        )
        fig_heat.update_layout(height=520, font=dict(family="IBM Plex Sans"))
        st.plotly_chart(fig_heat, use_container_width=True)

    st.markdown('<div class="section-label">Datos</div>', unsafe_allow_html=True)
    display = view[[
        "fecha", "Presidente", "Encuestadora", "metodo", "Aprueba",
        "Desaprueba", "ratio_ad", "source",
    ]].sort_values("fecha", ascending=False)
    st.dataframe(
        display.rename(columns={
            "fecha": "Fecha",
            "metodo": "Método",
            "ratio_ad": "A/D",
            "source": "Fuente archivo",
        }),
        use_container_width=True,
        hide_index=True,
    )
