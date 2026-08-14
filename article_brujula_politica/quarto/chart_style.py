"""Shared visual language for the article's Plotly figures.

The Streamlit app has its own, denser style (`ui.common.ts_base_layout`) tuned
for a dashboard viewport. This module is the article's: larger type, titles and
subtitles inside the figure, and direct labels instead of a legend wherever the
series separate cleanly. Kept as a separate module so retuning the article never
moves the app's charts.

`from __future__ import annotations` is required: Quarto renders this document
through a Jupyter kernel on a Python older than 3.10, where the `X | None`
annotations below would otherwise raise at import time.

Sizes here follow a conventional editorial chart scale. The published article
already gives figures more room than the prose column and modestly scales type
with that room, so oversized authored values compound quickly on desktop.
"""

from __future__ import annotations

# ── Type ──────────────────────────────────────────────────────────────────────
# The charts use the same face as the prose. A sans-on-serif split is the more
# common editorial convention, but it makes the figures read as imported from
# somewhere else; matching the body ties them to the argument around them.
# One family throughout — no mono, which the app used for ticks and which only
# added a third face to the page.
FONT = '"Times New Roman", Times, serif'

# The website adds restrained responsive scaling on wide figures. These are the
# authored sizes at the 920px Quarto canvas; supporting labels land around
# 15–17px at the site's widest normal view.
SIZE_AXIS_TITLE = 13
SIZE_TICK = 13
SIZE_DIRECT_LABEL = 14
SIZE_VERTEX = 15
SIZE_POINT_LABEL = 13
SIZE_NOTE = 11

# ── Ink ───────────────────────────────────────────────────────────────────────
# Four steps only. Titles get near-black, supporting text steps back, and the
# grid sits one shade off the surface so it never competes with the data.
INK = "#141414"
INK_SOFT = "#565656"
INK_MUTED = "#8A8A8A"
GRID = "rgba(20,20,20,0.08)"
AXIS_LINE = "rgba(20,20,20,0.22)"
SURFACE = "#FFFFFF"

# ── Bloc colors ───────────────────────────────────────────────────────────────
# Deepened from the app's originals for legibility at article scale: #8B0000 was
# dark enough to read as brown-black rather than red, and #006847 was desaturated
# enough to read as gray. Hue identity (red-left / blue-right / green-center) is
# unchanged, so these stay readable against the rest of the project.
BLOC = {"L": "#B32D2E", "R": "#2176C7", "C": "#17805E"}
BLOC_LABEL = {"L": "Izquierda", "R": "Derecha", "C": "Centro"}

# Party hues, re-toned to the same footing. PT keeps a red (it is a left party)
# but a lighter one, so it separates from MORENA in a 7-series legend.
PARTY = {
    "MORENA": "#B32D2E",
    "PAN": "#2176C7",
    "PRI": "#17805E",
    "PRD": "#D4A017",
    "MC": "#E8730C",
    "PT": "#D9645F",
    "PVEM": "#5B9E3F",
}

# Ternary-zone categories, re-toned to match BLOC. Base = the bloc's own hue;
# Plural = a lighter step of it; Contenciosa = a blend hue for the contested
# pair; Empate = neutral.
CATEGORY = {
    "Base Izquierda": "#B32D2E",
    "Base Derecha": "#2176C7",
    "Base Centro": "#17805E",
    "Plural Izquierda": "#D3736F",
    "Plural Derecha": "#6BA3D6",
    "Plural Centro": "#58A98A",
    "Contenciosa Izquierda-Centro": "#C77B1E",
    "Contenciosa Izquierda-Derecha": "#7B4B9E",
    "Contenciosa Centro-Derecha": "#1F8A8A",
    "Empate": "#9E9E9E",
}


def base_layout(height: int = 520, margin: dict | None = None) -> dict:
    """Layout shared by every figure in the article."""
    return dict(
        font=dict(family=FONT, size=SIZE_TICK, color=INK_SOFT),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        height=height,
        margin=margin or dict(l=10, r=10, t=40, b=40),
        hoverlabel=dict(font=dict(family=FONT, size=12), bgcolor="white"),
    )


def time_axis(years: list[int], pad_right: int = 0) -> dict:
    """X axis for the two time series: solid hairline rule, no axis title.

    The tick labels are years — an "Año" title underneath only repeats them.
    `pad_right` reserves room past the last year for direct end-labels.
    """
    return dict(
        tickvals=years,
        ticktext=[str(y) for y in years],
        tickfont=dict(size=SIZE_TICK, color=INK_SOFT),
        range=[years[0] - 2, years[-1] + pad_right],
        showgrid=False,
        showline=True, linecolor=AXIS_LINE, linewidth=1,
        ticks="outside", tickcolor=AXIS_LINE, ticklen=5,
        zeroline=False,
    )


def value_axis(rng: list[float], tickvals: list[float] | None = None,
               ticksuffix: str = "") -> dict:
    """Y axis: hairline solid gridlines, no rotated title, tight to the data.

    The unit rides the tick labels ("60%", "30M") instead of a rotated axis
    title, which is both easier to read and one less piece of chrome. It is
    baked into `ticktext` rather than passed as `ticksuffix` because plotly.js
    ignores the suffix once `tickvals` puts the axis in array tickmode.
    """
    axis = dict(
        range=rng,
        tickfont=dict(size=SIZE_TICK, color=INK_SOFT),
        showgrid=True, gridcolor=GRID, gridwidth=1,
        showline=False, zeroline=False,
    )
    if tickvals is not None:
        axis["tickvals"] = tickvals
        # Zero carries no unit — "0M" and "0%" read as noise at the baseline.
        axis["ticktext"] = [f"{v:g}{ticksuffix}" if v else "0" for v in tickvals]
    elif ticksuffix:
        axis["ticksuffix"] = ticksuffix
    return axis


def locked_aspect_meta(xrange: list[float], yrange: list[float]) -> dict:
    """Publish a locked-aspect figure's authored ranges for the website to restore.

    When x and y are pinned to a common scale (`scaleanchor`), plotly re-solves
    both ranges to fill whatever box it is handed — and writes the result back
    over `layout.xaxis.range`, so the authored values are gone by the time any
    script can read them. That is fine here, where the figure gets the box it
    was designed for, but the site renders these same figures much wider (see
    `web/scripts/build_article_pages.py`), and there the re-solve leaves the
    triangle at about a third of the frame, marooned in white space.

    `layout.meta` is free-form and plotly never touches it, so it survives as
    the one durable record of what the ranges were meant to be.
    """
    return dict(ca_xrange=list(xrange), ca_yrange=list(yrange))


def note(text: str) -> dict:
    """The source/method line under a figure, in the Silver Bulletin idiom."""
    return dict(
        text=text, xref="paper", yref="paper", x=0, y=-0.13,
        xanchor="left", yanchor="top", showarrow=False, align="left",
        font=dict(size=SIZE_NOTE, color=INK_MUTED),
    )
