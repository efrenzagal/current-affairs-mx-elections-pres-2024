"""PollsMX approval API: validated snapshots, versioned SQLite tables, web JSON.

The scatter values are the provider's chart observations, not verified original
poll reports. Keep them and the model estimates separate from fact_approval_poll.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = "https://prep.polls.mx/api/v1"
SOURCE_URL = "https://polls.politico.mx/aprobacion/"
PRESIDENTS = {
    "005": ("CSG", "1988-12-01"), "004": ("EZPL", "1994-12-01"),
    "003": ("VFQ", "2000-12-01"), "002": ("FCH", "2006-12-01"),
    "001": ("EPN", "2012-12-01"), "006": ("AMLO", "2018-12-01"),
    "00": ("Sheinbaum", "2024-10-01"),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS dim_approval_pollsmx_president (
    president_key TEXT PRIMARY KEY, president TEXT, president_name TEXT NOT NULL,
    term_start TEXT, current_run_id INTEGER NOT NULL, last_checked_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dim_approval_pollsmx_run (
    run_id INTEGER PRIMARY KEY, president_key TEXT NOT NULL,
    external_run_id TEXT NOT NULL, activated_at TEXT, api_url TEXT NOT NULL,
    retrieved_at TEXT NOT NULL, snapshot_path TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fact_approval_pollsmx_point (
    run_id INTEGER NOT NULL REFERENCES dim_approval_pollsmx_run(run_id),
    series_order INTEGER NOT NULL, point_order INTEGER NOT NULL,
    metric TEXT NOT NULL CHECK(metric IN ('aprobacion','desaprobacion')),
    series_type TEXT NOT NULL CHECK(series_type IN ('line','arearange','scatter')),
    observation_date TEXT NOT NULL, estimate REAL NOT NULL CHECK(estimate BETWEEN 0 AND 100),
    lower_bound REAL, upper_bound REAL, source_key TEXT, timestamp_ms INTEGER NOT NULL,
    PRIMARY KEY (run_id, series_order, point_order)
);
CREATE INDEX IF NOT EXISTS idx_approval_pollsmx_date
ON fact_approval_pollsmx_point(observation_date, series_type);
CREATE VIEW IF NOT EXISTS view_approval_pollsmx_current AS
SELECT p.president_key, p.president, p.president_name, p.term_start,
       f.*, r.external_run_id, r.activated_at, r.retrieved_at, r.api_url
FROM dim_approval_pollsmx_president p
JOIN fact_approval_pollsmx_point f ON f.run_id = p.current_run_id
JOIN dim_approval_pollsmx_run r ON r.run_id = f.run_id;
"""


def fetch_json(url: str, timeout: float = 60, retries: int = 3) -> dict:
    for attempt in range(retries + 1):
        try:
            request = urllib.request.Request(url, headers={
                "Accept": "application/json", "User-Agent": "MIEL-approval-ingest/1.0",
            })
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == retries:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == retries:
                raise
        time.sleep(min(2 ** attempt, 8))
    raise RuntimeError("Unreachable retry state")


def percentage(value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Expected numeric percentage, got {value!r}")
    value = float(value)
    if not math.isfinite(value) or not 0 <= value <= 100:
        raise ValueError(f"Invalid percentage: {value}")
    return value


def validate_chart(payload: dict, key: str) -> tuple[dict, list[tuple]]:
    data = payload["data"]
    if data["source"]["code"] != "approval" or str(data["scope"]["key"]) != key:
        raise ValueError(f"Wrong source or president in response for {key}")
    if data.get("status") != "active":
        raise ValueError(f"Run for {key} is not active")
    run_id = data["run_id"]
    if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id <= 0:
        raise ValueError("Invalid run_id")
    if not data.get("external_run_id") or not data["scope"].get("label"):
        raise ValueError("Missing run identity or president label")
    rows, signatures = [], set()
    for series_order, series in enumerate(data["series"]):
        kind = series["type"]
        metric = {"Aprobación": "aprobacion", "Desaprobación": "desaprobacion"}.get(series["name"])
        if kind not in ("line", "arearange", "scatter") or metric is None:
            raise ValueError(f"Unknown series: {series.get('name')} / {kind}")
        if (metric, kind) in signatures:
            raise ValueError(f"Duplicate series: {metric} / {kind}")
        signatures.add((metric, kind))
        dates = set()
        for order, point in enumerate(series["data"]):
            day = date.fromisoformat(point["fecha"]).isoformat()
            estimate = percentage(point["estimacion"])
            if point.get("partido_alianza") != metric:
                raise ValueError("Point metric disagrees with series")
            if str(point.get("id_corrida")) != str(data["external_run_id"]):
                raise ValueError("Point belongs to a different model run")
            timestamp = point["x"]
            if isinstance(timestamp, bool) or not isinstance(timestamp, int):
                raise ValueError("Invalid chart timestamp")
            if kind != "scatter" and day in dates:
                raise ValueError(f"Duplicate model date: {day}")
            dates.add(day)
            low = high = None
            if kind == "arearange":
                low, high = percentage(point["low"]), percentage(point["high"])
                if not low <= estimate <= high:
                    raise ValueError(f"Estimate outside bounds on {day}")
            elif abs(percentage(point["y"]) - estimate) > 1e-6:
                raise ValueError(f"Chart value disagrees with estimate on {day}")
            source_key = point.get("id_fuente")
            if kind == "scatter" and source_key is None:
                raise ValueError("Scatter observation missing source ID")
            rows.append((run_id, series_order, order, metric, kind, day, estimate,
                         low, high, str(source_key) if source_key is not None else None, timestamp))
    for metric in ("aprobacion", "desaprobacion"):
        if not any(r[3] == metric and r[4] == "line" for r in rows):
            raise ValueError(f"Missing nonempty {metric} model series")
        if any((metric, kind) not in signatures for kind in ("line", "arearange", "scatter")):
            raise ValueError(f"Missing expected series for {metric}")
        estimates = {r[5]: r[6] for r in rows if r[3] == metric and r[4] == "line"}
        bounds = {r[5]: r[6] for r in rows if r[3] == metric and r[4] == "arearange"}
        if estimates != bounds:
            raise ValueError(f"Model estimates and uncertainty dates/values disagree for {metric}")
    return data, rows


def download(keys: list[str] | None = None, timeout: float = 60) -> dict:
    scopes = fetch_json(f"{API}/polls/scopes?source=approval", timeout)
    available = {str(item["key"]) for item in scopes["data"]}
    selected = sorted(available) if keys is None else list(dict.fromkeys(keys))
    if not selected or set(selected) - available:
        raise ValueError(f"Unknown/empty president selection: {selected}; available: {sorted(available)}")
    charts = []
    for index, key in enumerate(selected):
        if index:
            time.sleep(1)
        url = f"{API}/polls/chart?{urllib.parse.urlencode({'source': 'approval', 'president': key})}"
        print(f"Fetching president {key}...", flush=True)
        payload = fetch_json(url, timeout)
        validate_chart(payload, key)
        charts.append({"president_key": key, "api_url": url,
                       "retrieved_at": datetime.now(timezone.utc).isoformat(), "payload": payload})
    return {"schemaVersion": 1, "sourceUrl": SOURCE_URL, "scopes": scopes, "charts": charts}


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Replace only after the complete artifact has been written.
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":"),
                                    allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def ingest(bundle: dict, db: Path, snapshot: Path) -> dict:
    if bundle.get("schemaVersion") != 1 or not bundle.get("charts"):
        raise ValueError("Unsupported or empty snapshot")
    prepared, seen = [], set()
    for chart in bundle["charts"]:
        key = chart["president_key"]
        if key in seen:
            raise ValueError(f"Duplicate president {key} in snapshot")
        seen.add(key)
        data, rows = validate_chart(chart["payload"], key)
        checked = datetime.fromisoformat(chart["retrieved_at"])
        if checked.tzinfo is None:
            raise ValueError("retrieved_at must include a timezone")
        digest = hashlib.sha256(json.dumps(chart["payload"], sort_keys=True,
                                          ensure_ascii=False).encode()).hexdigest()
        prepared.append((chart, data, rows, digest))
    result = {"changed": 0, "unchanged": 0, "points": 0}
    with sqlite3.connect(db, timeout=60) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(SCHEMA)
        conn.execute("BEGIN IMMEDIATE")
        for chart, data, rows, digest in prepared:
            key, run_id = chart["president_key"], data["run_id"]
            current = conn.execute("SELECT last_checked_at FROM dim_approval_pollsmx_president WHERE president_key=?", (key,)).fetchone()
            if current and datetime.fromisoformat(chart["retrieved_at"]) < datetime.fromisoformat(current[0]):
                raise ValueError(f"Snapshot for {key} is older than the current refresh; replay into a separate --db")
            existing = conn.execute("SELECT president_key, payload_sha256 FROM dim_approval_pollsmx_run WHERE run_id=?", (run_id,)).fetchone()
            if existing and existing[0] != key:
                raise ValueError(f"Run {run_id} already belongs to another president")
            if existing and existing[1] == digest:
                result["unchanged"] += 1
            else:
                conn.execute("""INSERT INTO dim_approval_pollsmx_run VALUES (?,?,?,?,?,?,?,?)
                    ON CONFLICT(run_id) DO UPDATE SET external_run_id=excluded.external_run_id,
                    activated_at=excluded.activated_at, api_url=excluded.api_url,
                    retrieved_at=excluded.retrieved_at, snapshot_path=excluded.snapshot_path,
                    payload_sha256=excluded.payload_sha256""",
                    (run_id, key, str(data["external_run_id"]), data.get("activated_at"),
                     chart["api_url"], chart["retrieved_at"], str(snapshot.resolve()), digest))
                conn.execute("DELETE FROM fact_approval_pollsmx_point WHERE run_id=?", (run_id,))
                conn.executemany("INSERT INTO fact_approval_pollsmx_point VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
                result["changed"] += 1
            president, start = PRESIDENTS.get(key, (None, None))
            conn.execute("""INSERT INTO dim_approval_pollsmx_president VALUES (?,?,?,?,?,?)
                ON CONFLICT(president_key) DO UPDATE SET president=excluded.president,
                president_name=excluded.president_name, term_start=excluded.term_start,
                current_run_id=excluded.current_run_id, last_checked_at=excluded.last_checked_at""",
                (key, president, data["scope"]["label"], start, run_id, chart["retrieved_at"]))
            result["points"] += len(rows)
    return result


def export_web(db: Path, output: Path) -> None:
    with sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN")
        presidents = [dict(r) for r in conn.execute("""SELECT p.*, r.external_run_id,
            r.activated_at, r.retrieved_at, r.api_url
            FROM dim_approval_pollsmx_president p JOIN dim_approval_pollsmx_run r
            ON r.run_id=p.current_run_id ORDER BY p.term_start, p.president_key""")]
        points = [dict(r) for r in conn.execute("""SELECT president_key, run_id, metric,
            series_type, observation_date, estimate, lower_bound, upper_bound,
            source_key, series_order, point_order FROM view_approval_pollsmx_current
            ORDER BY president_key, series_order, point_order""")]
    write_json(output, {"schemaVersion": 1, "provider": "PollsMX", "sourceUrl": SOURCE_URL,
                       "sourceThrough": max((p["observation_date"] for p in points), default=None),
                       "presidents": presidents, "points": points})
    print(f"Exported {len(points):,} points to {output}")
    weekly_output = output.with_name(output.stem + "-weekly.json")
    write_json(weekly_output, weekly_payload(presidents, points))
    print(f"Exported weekly aggregate to {weekly_output}")


def weekly_payload(presidents: list[dict], points: list[dict]) -> dict:
    """Latest available approval estimate in each Monday–Sunday calendar week."""
    by_key = {p["president_key"]: p for p in presidents}
    weeks = {}
    for point in points:
        if point["series_type"] != "line" or point["metric"] != "aprobacion":
            continue
        president = by_key[point["president_key"]]
        if not president["president"] or not president["term_start"]:
            continue
        day = date.fromisoformat(point["observation_date"])
        start = date.fromisoformat(president["term_start"])
        if day < start:
            continue
        monday = day - timedelta(days=day.weekday())
        key = (president["president"], monday.isoformat())
        if key not in weeks or point["observation_date"] > weeks[key]["date"]:
            month = (day.year - start.year) * 12 + day.month - start.month
            # Match the existing chart's calendar-month axis, retaining the day.
            month += (day.day - 1) / monthrange(day.year, day.month)[1]
            weeks[key] = {"president": president["president"], "weekStart": monday.isoformat(),
                          "date": day.isoformat(), "month": round(month, 6),
                          "approve": point["estimate"], "runId": point["run_id"]}
    weekly = sorted(weeks.values(), key=lambda p: (p["president"], p["date"]))
    return {"schemaVersion": 1, "provider": "PollsMX", "sourceUrl": SOURCE_URL,
            "frequency": "weekly", "sampling": "last_available_day_monday_sunday",
            "sourceThrough": max((p["date"] for p in weekly), default=None),
            "points": weekly}
