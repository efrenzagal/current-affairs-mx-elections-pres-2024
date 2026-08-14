"""Export presidential approval polls for the public web dashboard.

Polls are re-keyed from calendar date to months since inauguration, so the
web chart can compare presidents at the same point in their term rather than
the same point on the calendar.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "election_data.db"
OUT_PATH = ROOT / "web" / "public" / "data" / "approval.json"

PRESIDENT_LABELS = {
    "EZPL": "Ernesto Zedillo",
    "VFQ": "Vicente Fox",
    "FCH": "Felipe Calderón",
    "EPN": "Enrique Peña Nieto",
    "AMLO": "Andrés Manuel López Obrador",
    "Sheinbaum": "Claudia Sheinbaum",
}
PRESIDENT_COLORS = {
    "EZPL": "#2E7D32",
    "VFQ": "#1565C0",
    "FCH": "#1E90FF",
    "EPN": "#C62828",
    "AMLO": "#8B0000",
    "Sheinbaum": "#C84B31",
}
PRESIDENT_ORDER = list(PRESIDENT_LABELS)

# Oraculus stopped publishing, so its polls have no single article to link to;
# every chart-transcribed poll instead carries its own article's URL.
ORACULUS_URL = "https://oraculus.mx/aprobacion-presidencial/"

# Inauguration month ("YYYY-MM") -- month 0 of each term's approval series.
# Sheinbaum is the first term under the reformed Oct 1 inauguration date;
# every prior term here started Dec 1.
PRESIDENT_START = {
    "EZPL": "1994-12",
    "VFQ": "2000-12",
    "FCH": "2006-12",
    "EPN": "2012-12",
    "AMLO": "2018-12",
    "Sheinbaum": "2024-10",
}


def months_since_start(poll_month: str, president: str) -> int | None:
    start = PRESIDENT_START.get(president)
    if start is None:
        return None
    start_year, start_month = (int(part) for part in start.split("-"))
    poll_year, poll_month_num = (int(part) for part in poll_month.split("-"))
    return (poll_year - start_year) * 12 + (poll_month_num - start_month)


def export() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql_query(
            """
            SELECT
                f.poll_month, f.president, d.familia AS pollster,
                f.aprueba AS approve, f.desaprueba AS disapprove, f.resto AS residual,
                s.source_kind, s.source_ref
            FROM fact_approval_poll f
            JOIN dim_approval_pollster d USING (pollster_id)
            JOIN dim_approval_source s USING (source_id)
            WHERE f.aprueba IS NOT NULL AND f.president IS NOT NULL
            """,
            conn,
        )
    finally:
        conn.close()

    df["monthsInOffice"] = df.apply(
        lambda row: months_since_start(row["poll_month"], row["president"]), axis=1
    )
    # Drops both unrecognized president codes and pre-inauguration polls
    # (a term's approval series starts the month a president takes office).
    df = df.dropna(subset=["monthsInOffice"])
    df = df[df["monthsInOffice"] >= 0].copy()
    df["monthsInOffice"] = df["monthsInOffice"].astype(int)

    points = [
        {
            "president": row.president,
            "month": row.monthsInOffice,
            "date": row.poll_month,
            "approve": round(float(row.approve), 1),
            "disapprove": round(float(row.disapprove), 1),
            "residual": round(float(row.residual), 1),
            "pollster": row.pollster,
            "sourceUrl": row.source_ref if row.source_kind == "articulo" else ORACULUS_URL,
        }
        for row in df.itertuples()
    ]
    points.sort(key=lambda point: (PRESIDENT_ORDER.index(point["president"]), point["month"]))

    payload = {
        "schemaVersion": 1,
        "sourceThrough": df["poll_month"].max(),
        "presidents": [
            {"key": key, "label": PRESIDENT_LABELS[key], "color": PRESIDENT_COLORS[key]}
            for key in PRESIDENT_ORDER
            if key in set(df["president"])
        ],
        "pollsters": sorted(df["pollster"].dropna().unique()),
        "points": points,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(f"Wrote {OUT_PATH} ({OUT_PATH.stat().st_size / 1024:.0f} KB, {len(points)} points)")


if __name__ == "__main__":
    export()
