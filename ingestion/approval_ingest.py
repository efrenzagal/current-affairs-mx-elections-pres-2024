"""
Load presidential approval data into the warehouse.

Populates four tables in election_data.db:

    dim_approval_pollster — one row per polling house
    dim_approval_source   — one row per source document (spreadsheet or article)
    fact_approval_poll    — headline "do you approve of the president" series
    fact_approval_topic   — issue-by-issue performance evaluations

Two inputs, deliberately kept apart:

  * The Oraculus-compiled spreadsheets under aux_scripts/approval_rates/ supply
    history from Feb 1995 to Sep 2025. Oraculus stopped publishing, so this is
    a frozen seed.
  * aux_scripts/approval_rates/chart_transcriptions.csv supplies values read by
    hand off chart images, which carry the series forward. Each row names its
    own pollster and president, so this is not tied to one house.

Transcription is the one step no script performs, so overlap is what keeps a
misread digit from entering the warehouse silently. Two overlaps are checked,
and either disagreeing aborts the load:

  * Chart vs spreadsheet, for months the Oraculus seed also covers.
  * Article vs article, for months two chart images both restate. Through the
    2026-05 wave the El Financiero headline chart was cumulative and one image
    redrew the whole series; since 2026-06 it shows a short window instead, so
    consecutive waves overlapping each other carry the check for everything
    published after the seed ends.

Note that fact_approval_topic uses bien/mal rather than aprueba/desaprueba. It
is a different question on a four-point scale with the middle category
suppressed, and the columns are named apart so the two never get joined by
accident.

Usage:
    /usr/bin/python3 ingestion/approval_ingest.py
    /usr/bin/python3 ingestion/approval_ingest.py --force
    /usr/bin/python3 ingestion/approval_ingest.py --db path/to/other.db
"""

from __future__ import annotations

import argparse
import csv
import re
import sqlite3
import sys
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "election_data.db"
APPROVAL_DIR = ROOT / "aux_scripts" / "approval_rates"
ARCHIVE_XLSX = APPROVAL_DIR / "table-aprobacion_archivo.xlsx"
RECENT_XLSX = APPROVAL_DIR / "table-aprobacion.xlsx"
TRANSCRIPTIONS_CSV = APPROVAL_DIR / "chart_transcriptions.csv"
TEXT_DIR = ROOT / "data" / "clean_approval" / "text"

SHEINBAUM_START = date(2024, 10, 1)

MONTHS_EN = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}

MEDIA_HOUSES = {"Reforma", "El Universal", "El Financiero"}
GOVERNMENT_HOUSES = {"Presidencia"}

# Raw names that split one firm into several. Recorded as a family label rather
# than merged, because collapsing them changes house-effect estimates.
POLLSTER_FAMILY = {
    "BGC": "BGC", "BGC Telefonica": "BGC", "BGC Vivienda": "BGC",
    "Ipsos": "Ipsos", "Ipsos Bimsa": "Ipsos",
    "Buendia y Laredo": "Buendía (Laredo / Márquez)",
    "Buendia y Marquez": "Buendía (Laredo / Márquez)",
}

# Groups each house's topic wording under a shared slug, so issue evaluations
# stay comparable when a second house starts publishing them. The vocabulary is
# borrowed from the gaceta classifier's tema_politica so the two sides can be
# joined ad hoc; nothing here depends on that code, and 'corrupcion' is local to
# approval. Same idea as POLLSTER_FAMILY: the house's own label survives in the
# tema column, and this only adds a grouping.
TEMA_POLITICA = {
    "Economía": "economia_e_industria",
    "Corrupción": "corrupcion",
    "Seguridad pública": "justicia_y_seguridad",
    "Crimen organizado": "justicia_y_seguridad",
    "Apoyos sociales": "desarrollo_social_y_vivienda",
    "Relación con Trump": "relaciones_exteriores",
    "Revisiones al T-MEC": "relaciones_exteriores",
    "Los derechos de las mujeres": "derechos_humanos_e_igualdad",
    "La atención a niños y jóvenes": "desarrollo_social_y_vivienda",
    "Los programas sociales": "desarrollo_social_y_vivienda",
    "El combate a la pobreza": "desarrollo_social_y_vivienda",
    "La construcción de obras e infraestructura": "infraestructura_y_transporte",
    "La educación pública": "educacion",
    "La protección al medio ambiente": "medio_ambiente",
    "La economía": "economia_e_industria",
    "Las relaciones internacionales": "relaciones_exteriores",
    "Los servicios de salud pública": "salud",
    "La seguridad": "justicia_y_seguridad",
    "El combate a la corrupción": "corrupcion",
}

# resto is whatever a house does not report as explicitly positive or negative,
# so its size is a property of the instrument, not a defect. El Financiero
# suppresses only "No sabe" and runs ~2; Demotecnia and BGC also fold in an
# explicit neutral category and run ~10. One global threshold would either
# excuse misread digits at El Financiero or cry wolf on every Demotecnia row.
RESTO_LIMIT = {
    "El Financiero": 8,
    "Demotecnia": 20,
    # Enkoll prints a small "No sabe / No respondió" category (2--5 in the
    # first two reports checked), so its residual should remain tight.
    "Enkoll": 8,
    # Buendía y Márquez prints a neutral category as well as NS/NC, so its
    # residual is materially wider than Enkoll's.
    "Buendia y Marquez": 20,
    # Covarrubias reports an explicit neutral category as well as NS/NC.
    "Covarrubias y Asoc": 20,
    "BGC": 20, "BGC Telefonica": 20, "BGC Vivienda": 20,
}
RESTO_LIMIT_DEFAULT = 25

SCHEMA = """
CREATE TABLE IF NOT EXISTS dim_approval_pollster (
    pollster_id   INTEGER PRIMARY KEY,
    pollster_name TEXT NOT NULL UNIQUE,
    pollster_type TEXT,
    familia       TEXT
);

CREATE TABLE IF NOT EXISTS dim_approval_source (
    source_id    INTEGER PRIMARY KEY,
    source_kind  TEXT NOT NULL,
    source_ref   TEXT NOT NULL UNIQUE,
    published_at TEXT,
    headline     TEXT,
    methodology  TEXT,
    retrieved_at TEXT
);

-- occurrence distinguishes repeated waves by the same house in one month.
-- Parametría ran four in a single month, and keying on (month, pollster)
-- alone silently discarded 33 real observations.
-- metodo is per-wave, not per-house: Demotecnia alone has run both /tel and
-- /viv waves under the same cleaned name, so it cannot live on the pollster
-- dimension. Only the Oraculus seed carries it (parsed off the raw
-- "Casa/tel" style label before cleaning), and chart transcriptions don't record
-- it and load as NULL.
CREATE TABLE IF NOT EXISTS fact_approval_poll (
    poll_month  TEXT NOT NULL,
    pollster_id INTEGER NOT NULL,
    occurrence  INTEGER NOT NULL DEFAULT 1,
    president   TEXT,
    aprueba     REAL,
    desaprueba  REAL,
    resto       REAL,
    metodo      TEXT,
    source_id   INTEGER,
    extraction  TEXT NOT NULL,
    PRIMARY KEY (poll_month, pollster_id, occurrence)
);

-- tema holds the house's own wording. tema_politica groups those under a
-- shared slug so two houses asking about the same issue stay comparable.
CREATE TABLE IF NOT EXISTS fact_approval_topic (
    poll_month    TEXT NOT NULL,
    pollster_id   INTEGER NOT NULL,
    tema          TEXT NOT NULL,
    tema_politica TEXT,
    bien          REAL,
    mal           REAL,
    resto         REAL,
    source_id     INTEGER,
    extraction    TEXT NOT NULL,
    PRIMARY KEY (poll_month, pollster_id, tema)
);
"""

APPROVAL_TABLES = [
    "fact_approval_topic", "fact_approval_poll",
    "dim_approval_source", "dim_approval_pollster",
]

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def read_xlsx(path: Path) -> list[list[str]]:
    """Minimal first-sheet reader, so ingestion needs no Excel dependency."""
    with zipfile.ZipFile(path) as archive:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            shared = [
                "".join(t.text or "" for t in si.iter(NS + "t")) for si in root
            ]
        root = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))

    rows: list[list[str]] = []
    for row in root.iter(NS + "row"):
        values: list[str] = []
        for cell in row.iter(NS + "c"):
            value = cell.find(NS + "v")
            if value is None:
                inline = cell.find(NS + "is")
                values.append(
                    "".join(t.text or "" for t in inline.iter(NS + "t"))
                    if inline is not None else ""
                )
            elif cell.get("t") == "s":
                values.append(shared[int(value.text)])
            else:
                values.append(value.text or "")
        rows.append(values)
    return rows


def parse_month(label: str) -> str | None:
    """'Sep 2025' -> '2025-09'. The spreadsheets use English abbreviations."""
    parts = (label or "").split()
    if len(parts) != 2 or parts[0] not in MONTHS_EN:
        return None
    try:
        return f"{int(parts[1]):04d}-{MONTHS_EN[parts[0]]:02d}"
    except ValueError:
        return None


def clean_pollster(raw: str) -> str:
    return re.sub(r"/(tel|viv|online)", "", raw or "", flags=re.I).strip()


def extract_metodo(raw: str) -> str | None:
    """Fieldwork method, read off the raw Oraculus label before cleaning."""
    text = (raw or "").lower()
    if re.search(r"telef|/tel|telefon", text):
        return "Telefónica"
    if re.search(r"vivien|/viv", text):
        return "Vivienda"
    if re.search(r"online|web|internet", text):
        return "Online"
    return None


def classify_pollster(name: str) -> str:
    if name in GOVERNMENT_HOUSES:
        return "Gobierno"
    if name in MEDIA_HOUSES:
        return "Medio"
    return "Casa encuestadora"


def to_number(text: str) -> float | None:
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def read_oraculus_rows() -> list[dict]:
    """Headline approval rows from the two Oraculus-compiled spreadsheets."""
    rows: list[dict] = []
    for path, has_president in ((ARCHIVE_XLSX, True), (RECENT_XLSX, False)):
        if not path.exists():
            print(f"  WARNING: {path.name} not found, skipping")
            continue
        raw = read_xlsx(path)[1:]
        for record in raw:
            if has_president:
                president, month_label, pollster = record[0], record[1], record[2]
                aprueba, desaprueba = to_number(record[3]), to_number(record[4])
            else:
                month_label, pollster = record[0], record[1]
                aprueba, desaprueba = to_number(record[2]), to_number(record[3])
                president = None
            month = parse_month(month_label)
            if not month or not pollster:
                continue
            if president is None:
                year, mon = int(month[:4]), int(month[5:])
                president = (
                    "Sheinbaum" if date(year, mon, 1) >= SHEINBAUM_START else "AMLO"
                )
            rows.append({
                "poll_month": month,
                "pollster": clean_pollster(pollster),
                "metodo": extract_metodo(pollster),
                "president": president,
                "aprueba": aprueba,
                "desaprueba": desaprueba,
                "source_ref": path.name,
            })

    # Number repeated waves by the same house within a month, in file order, so
    # reruns assign the same occurrence to the same row.
    seen: dict[tuple[str, str], int] = {}
    for row in rows:
        key = (row["poll_month"], row["pollster"])
        seen[key] = seen.get(key, 0) + 1
        row["occurrence"] = seen[key]
    return rows


def read_transcriptions() -> tuple[list[dict], list[dict]]:
    """Chart-read values, split into headline rows and per-topic rows."""
    if not TRANSCRIPTIONS_CSV.exists():
        print(f"  WARNING: {TRANSCRIPTIONS_CSV.name} not found")
        return [], []
    headline: list[dict] = []
    topics: list[dict] = []
    problems: list[str] = []
    with TRANSCRIPTIONS_CSV.open(encoding="utf-8") as handle:
        for line_no, record in enumerate(csv.DictReader(handle), start=2):
            month = (record.get("poll_month") or "").strip()
            positive = to_number(record.get("positivo", ""))
            negative = to_number(record.get("negativo", ""))
            if not month:
                continue

            # pollster and president are transcribed, never inferred. Deriving
            # the president from the month would be right for a house that only
            # rates the sitting one and silently wrong for any wave that rates a
            # predecessor — Demotecnia publishes exactly that comparison.
            pollster = (record.get("pollster") or "").strip()
            president = (record.get("president") or "").strip()
            if not pollster:
                problems.append(f"line {line_no}: no pollster")
            if not president:
                problems.append(f"line {line_no}: no president")

            common = {
                "poll_month": month,
                "pollster": pollster,
                "source_ref": (record.get("source_url") or "").strip(),
            }
            if record.get("serie") == "aprobacion":
                headline.append({
                    **common,
                    "president": president,
                    "aprueba": positive,
                    "desaprueba": negative,
                    # Repeat waves in one month are rare off the charts, but the
                    # column exists so a house that runs them is not a blocker.
                    "occurrence": int(record.get("occurrence") or 1),
                })
            else:
                tema = (record.get("tema") or "").strip()
                if tema and tema not in TEMA_POLITICA:
                    problems.append(
                        f"line {line_no}: tema {tema!r} has no TEMA_POLITICA entry"
                    )
                topics.append({
                    **common,
                    "tema": tema,
                    "tema_politica": TEMA_POLITICA.get(tema),
                    "bien": positive,
                    "mal": negative,
                })

    if problems:
        for problem in problems:
            print(f"  ERROR:   {problem}")
        raise ValueError(
            f"{len(problems)} transcription row(s) are incomplete. Fill the "
            "missing column, or add the tema to TEMA_POLITICA — an unmapped "
            "topic would load as an orphan series nothing else can group."
        )
    return headline, topics


def collapse_transcriptions(
    rows: list[dict],
    key_fields: tuple[str, ...],
    value_fields: tuple[str, ...],
) -> tuple[list[dict], set[tuple], list[str]]:
    """Collapse repeat readings of the same cell down to one row per key.

    El Financiero stopped publishing a cumulative headline chart after the
    2026-05 wave; consecutive waves now show a short overlapping window
    instead. That makes the same month readable from several articles, which
    is what replaced the Oraculus cross-check for months the spreadsheets
    never covered: where two articles describe a month they must agree, and a
    month carried by two of them is corroborated rather than resting on a
    single reading. Duplicates are also collapsed here so the row-count
    assertion in load() still counts one stored row per input key.
    """
    grouped: dict[tuple, list[dict]] = {}
    for row in rows:
        grouped.setdefault(tuple(row[f] for f in key_fields), []).append(row)

    collapsed: list[dict] = []
    corroborated: set[tuple] = set()
    problems: list[str] = []
    for key, group in grouped.items():
        readings = {tuple(row[f] for f in value_fields) for row in group}
        if len(readings) > 1:
            shown = " vs ".join(
                "/".join("?" if v is None else f"{v:.0f}" for v in reading)
                for reading in sorted(readings, key=str)
            )
            problems.append(f"{' '.join(str(part) for part in key)}: {shown}")
        if len({row["source_ref"] for row in group}) > 1:
            corroborated.add(key)
        collapsed.append(group[0])
    return collapsed, corroborated, problems


def read_methodology(source_url: str) -> str:
    """Best-effort methodology note from a fetched article transcript."""
    if not source_url or not TEXT_DIR.exists():
        return ""
    slug = source_url.rstrip("/").rsplit("/", 1)[-1][:60]
    path = TEXT_DIR / f"{slug}.txt"
    if not path.exists():
        return ""
    for block in path.read_text(encoding="utf-8").split("\n\n"):
        if block.lower().lstrip().startswith("metodolog"):
            return block.strip()
    return ""


def reconcile(oraculus: list[dict], transcribed: list[dict]) -> list[str]:
    """Compare months both sources cover. Any disagreement is a hard error."""
    by_key = {
        (row["poll_month"], row["pollster"], row["occurrence"]): row
        for row in oraculus
    }
    problems: list[str] = []
    checked = 0
    for row in transcribed:
        key = (row["poll_month"], row["pollster"], row["occurrence"])
        existing = by_key.get(key)
        if not existing:
            continue
        checked += 1
        if (existing["aprueba"], existing["desaprueba"]) != (
            row["aprueba"], row["desaprueba"]
        ):
            problems.append(
                f"{key[0]} {key[1]}: spreadsheet "
                f"{existing['aprueba']:.0f}/{existing['desaprueba']:.0f} vs chart "
                f"{row['aprueba']:.0f}/{row['desaprueba']:.0f}"
            )
    print(f"  reconciled {checked} overlapping month(s); {len(problems)} disagreement(s)")
    return problems


def upsert_pollsters(conn: sqlite3.Connection, names: set[str]) -> dict[str, int]:
    for name in sorted(names):
        conn.execute(
            "INSERT OR IGNORE INTO dim_approval_pollster "
            "(pollster_name, pollster_type, familia) VALUES (?, ?, ?)",
            (name, classify_pollster(name), POLLSTER_FAMILY.get(name, name)),
        )
    conn.commit()
    return {
        row[1]: row[0]
        for row in conn.execute(
            "SELECT pollster_id, pollster_name FROM dim_approval_pollster"
        )
    }


def upsert_sources(conn: sqlite3.Connection, refs: set[str]) -> dict[str, int]:
    retrieved = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for ref in sorted(refs):
        is_article = ref.startswith("http")
        conn.execute(
            "INSERT OR IGNORE INTO dim_approval_source "
            "(source_kind, source_ref, methodology, retrieved_at) VALUES (?, ?, ?, ?)",
            (
                "articulo" if is_article else "oraculus_xlsx",
                ref,
                read_methodology(ref) if is_article else "",
                retrieved,
            ),
        )
    conn.commit()
    return {
        row[1]: row[0]
        for row in conn.execute(
            "SELECT source_id, source_ref FROM dim_approval_source"
        )
    }


def residual(positive: float | None, negative: float | None) -> float | None:
    if positive is None or negative is None:
        return None
    return round(100.0 - positive - negative, 1)


def load(conn: sqlite3.Connection) -> dict[str, int]:
    oraculus = read_oraculus_rows()
    transcribed_headline, transcribed_topics = read_transcriptions()

    transcribed_headline, headline_corroborated, headline_problems = (
        collapse_transcriptions(
            transcribed_headline,
            ("poll_month", "pollster", "occurrence"),
            ("aprueba", "desaprueba"),
        )
    )
    transcribed_topics, topic_corroborated, topic_problems = collapse_transcriptions(
        transcribed_topics,
        ("poll_month", "pollster", "tema"),
        ("bien", "mal"),
    )
    chart_problems = headline_problems + topic_problems
    print(
        f"  cross-read {len(headline_corroborated)} month(s) and "
        f"{len(topic_corroborated)} topic cell(s) from 2+ articles; "
        f"{len(chart_problems)} disagreement(s)"
    )
    if chart_problems:
        for problem in chart_problems:
            print(f"  ERROR:   {problem}")
        raise ValueError(
            f"{len(chart_problems)} cell(s) were read differently from two "
            "articles covering the same month. Re-read the charts before "
            "loading; do not delete a reading to force agreement."
        )

    problems = reconcile(oraculus, transcribed_headline)
    if problems:
        for problem in problems:
            print(f"  ERROR:   {problem}")
        raise ValueError(
            f"{len(problems)} overlapping month(s) disagree between the "
            "spreadsheets and the chart transcription. Re-read the chart before "
            "loading; do not overwrite either source to force agreement."
        )

    pollsters = upsert_pollsters(
        conn,
        {row["pollster"] for row in oraculus}
        | {row["pollster"] for row in transcribed_headline + transcribed_topics},
    )
    sources = upsert_sources(
        conn,
        {row["source_ref"] for row in oraculus}
        | {row["source_ref"] for row in transcribed_headline + transcribed_topics},
    )

    def key_of(row: dict) -> tuple[str, str, int]:
        return row["poll_month"], row["pollster"], row["occurrence"]

    verified = {key_of(row) for row in transcribed_headline} & {
        key_of(row) for row in oraculus
    }

    for row in oraculus:
        conn.execute(
            "INSERT OR REPLACE INTO fact_approval_poll "
            "(poll_month, pollster_id, occurrence, president, aprueba, desaprueba, "
            " resto, metodo, source_id, extraction) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row["poll_month"], pollsters[row["pollster"]], row["occurrence"],
                row["president"], row["aprueba"], row["desaprueba"],
                residual(row["aprueba"], row["desaprueba"]),
                row["metodo"],
                sources.get(row["source_ref"]),
                "oraculus+grafica" if key_of(row) in verified else "oraculus",
            ),
        )

    seeded = {key_of(row) for row in oraculus}
    added = 0
    for row in transcribed_headline:
        if key_of(row) in seeded:
            continue
        conn.execute(
            "INSERT OR REPLACE INTO fact_approval_poll "
            "(poll_month, pollster_id, occurrence, president, aprueba, desaprueba, "
            " resto, metodo, source_id, extraction) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row["poll_month"], pollsters[row["pollster"]], row["occurrence"],
                row["president"], row["aprueba"], row["desaprueba"],
                residual(row["aprueba"], row["desaprueba"]),
                None,
                sources.get(row["source_ref"]),
                "grafica+grafica" if key_of(row) in headline_corroborated
                else "grafica",
            ),
        )
        added += 1

    for row in transcribed_topics:
        conn.execute(
            "INSERT OR REPLACE INTO fact_approval_topic "
            "(poll_month, pollster_id, tema, tema_politica, bien, mal, resto, "
            " source_id, extraction) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row["poll_month"], pollsters[row["pollster"]], row["tema"],
                row["tema_politica"],
                row["bien"], row["mal"], residual(row["bien"], row["mal"]),
                sources.get(row["source_ref"]),
                "grafica+grafica"
                if (row["poll_month"], row["pollster"], row["tema"])
                in topic_corroborated
                else "grafica",
            ),
        )

    conn.commit()

    # Every input row must survive the load. An earlier key of just
    # (month, pollster) collapsed 33 legitimate repeat waves without a word,
    # so the count is asserted rather than trusted.
    expected = len(oraculus) + added
    stored = conn.execute("SELECT COUNT(*) FROM fact_approval_poll").fetchone()[0]
    if stored != expected:
        raise ValueError(
            f"fact_approval_poll holds {stored:,} rows but {expected:,} were "
            "loaded — rows collided on the primary key and were discarded."
        )

    return {
        "oraculus": len(oraculus),
        "verified": len(verified),
        "new_months": added,
        "topics": len(transcribed_topics),
    }


def _warn(msg: str) -> None:
    print(f"  WARNING: {msg}")


def _fail(msg: str) -> None:
    print(f"  ERROR:   {msg}")


def validate(conn: sqlite3.Connection) -> bool:
    print("\n── QA ──────────────────────────────────────────────────")
    hard_ok = True

    orphans = conn.execute("""
        SELECT COUNT(*) FROM fact_approval_poll f
        WHERE NOT EXISTS (
            SELECT 1 FROM dim_approval_pollster d WHERE d.pollster_id = f.pollster_id
        )
    """).fetchone()[0]
    if orphans:
        _fail(f"fact_approval_poll has {orphans:,} rows with no pollster")
        hard_ok = False
    else:
        print("  OK: fact_approval_poll → dim_approval_pollster refs intact")

    topic_orphans = conn.execute("""
        SELECT COUNT(*) FROM fact_approval_topic f
        WHERE NOT EXISTS (
            SELECT 1 FROM dim_approval_pollster d WHERE d.pollster_id = f.pollster_id
        )
    """).fetchone()[0]
    if topic_orphans:
        _fail(f"fact_approval_topic has {topic_orphans:,} rows with no pollster")
        hard_ok = False
    else:
        print("  OK: fact_approval_topic → dim_approval_pollster refs intact")

    bad_months = conn.execute("""
        SELECT COUNT(*) FROM fact_approval_poll
        WHERE poll_month NOT GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]'
    """).fetchone()[0]
    if bad_months:
        _fail(f"{bad_months:,} rows have a malformed poll_month")
        hard_ok = False
    else:
        print("  OK: every poll_month is YYYY-MM")

    out_of_range = conn.execute("""
        SELECT COUNT(*) FROM fact_approval_poll
        WHERE aprueba IS NOT NULL AND (aprueba < 0 OR aprueba > 100)
           OR desaprueba IS NOT NULL AND (desaprueba < 0 OR desaprueba > 100)
    """).fetchone()[0]
    if out_of_range:
        _fail(f"{out_of_range:,} rows have a percentage outside 0-100")
        hard_ok = False
    else:
        print("  OK: all percentages within 0-100")

    # A residual that is large *for its house* usually means a misread digit.
    # The bound has to be per-house because resto absorbs whatever categories a
    # house does not report: only "No sabe" for El Financiero, neutral as well
    # for Demotecnia and BGC. A negative residual is an error at any house — it
    # means the two shares sum past 100.
    wide = conn.execute("""
        SELECT d.pollster_name, f.poll_month, f.aprueba, f.desaprueba, f.resto,
               f.extraction
        FROM fact_approval_poll f
        JOIN dim_approval_pollster d USING(pollster_id)
        WHERE f.resto IS NOT NULL
        ORDER BY ABS(f.resto) DESC
    """).fetchall()
    flagged = [
        row for row in wide
        if row[4] < -2 or row[4] > RESTO_LIMIT.get(row[0], RESTO_LIMIT_DEFAULT)
    ]
    # Split by provenance: a chart-read outlier is a re-readable mistake, while
    # a seed outlier is a fact about a spreadsheet compiled years ago. Warning
    # about both together buries the actionable one under ~30 that never change.
    chart_flagged = [row for row in flagged if row[5].startswith("grafica")]
    seed_flagged = [row for row in flagged if not row[5].startswith("grafica")]
    if chart_flagged:
        houses = sorted({row[0] for row in chart_flagged})
        _warn(
            f"{len(chart_flagged)} chart-read row(s) with a residual "
            f"implausible for their house ({', '.join(houses)}), e.g. "
            f"{chart_flagged[0][:5]} — re-read the chart"
        )
    else:
        print("  OK: chart-read residuals plausible for each house")
    if seed_flagged:
        print(
            f"  NOTE: {len(seed_flagged)} Oraculus seed row(s) carry a wide "
            "residual. Inherited and not re-readable "
            "(WHERE extraction = 'oraculus')."
        )

    unverified = conn.execute("""
        SELECT COUNT(*) FROM fact_approval_poll WHERE extraction = 'grafica'
    """).fetchone()[0]
    if unverified:
        _warn(
            f"{unverified:,} month(s) rest on chart transcription alone "
            "(no second source). Expected for months after Sep 2025."
        )

    print("────────────────────────────────────────────────────────")
    return hard_ok


def drop_approval_tables(conn: sqlite3.Connection) -> None:
    for table in APPROVAL_TABLES:
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument(
        "--force", action="store_true",
        help="Drop and recreate the approval tables before loading",
    )
    args = parser.parse_args()

    db_path = Path(args.db)
    print(f"Warehouse: {db_path}")
    conn = sqlite3.connect(db_path)

    if args.force:
        print("--force: dropping existing approval tables")
        drop_approval_tables(conn)

    for statement in SCHEMA.strip().split(";"):
        statement = statement.strip()
        if statement:
            conn.execute(statement)
    conn.commit()

    try:
        counts = load(conn)
    except Exception as exc:
        conn.rollback()
        print(f"  ERROR: {exc}")
        conn.close()
        sys.exit(1)

    print(
        f"  oraculus rows={counts['oraculus']:,}  "
        f"double-verified months={counts['verified']}  "
        f"new months from charts={counts['new_months']}  "
        f"topic rows={counts['topics']}"
    )

    print("\n── Row counts ──────────────────────────────────────────")
    for table in reversed(APPROVAL_TABLES):
        n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {table}: {n:,}")

    qa_ok = validate(conn)
    conn.close()

    if not qa_ok:
        print("Ingest completed with QA errors — see ERROR lines above.")
        sys.exit(1)
    print("Done.")


if __name__ == "__main__":
    main()
