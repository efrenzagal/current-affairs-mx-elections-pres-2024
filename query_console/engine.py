"""Background execution of warehouse queries.

Each submitted query runs on its own thread with its own read-only connection,
so the console stays responsive, several queries can be in flight at once, and
a slow one can be cancelled. SQLite's progress handler gives us both the live
"still working" signal and the cancellation hook.
"""

from __future__ import annotations

import csv
import math
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .store import HistoryStore

# VM instructions between progress-handler callbacks. Small enough that cancel
# feels immediate, large enough that the callback costs nothing measurable.
PROGRESS_INTERVAL = 20_000

# Rows held in memory (and handed to the browser) for the results grid. The
# full result always goes to the CSV on disk.
PREVIEW_ROWS = 2_000

FETCH_CHUNK = 500

# Small by default: most questions are answered by a handful of rows, and a
# modest cap keeps a mistyped join from writing a 60 MB CSV.
DEFAULT_ROW_LIMIT = 100

# Standing in for "no limit" — larger than any result this warehouse can produce.
NO_ROW_LIMIT = 10 ** 12

# Half an hour is longer than any sane exploratory query on this warehouse.
DEFAULT_TIMEOUT_S = 30 * 60


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _duration(seconds: float) -> str:
    if seconds >= 60:
        return "{:g} min".format(seconds / 60)
    return "{:g}s".format(seconds)


def _cell(value: Any) -> Any:
    """Make a SQLite value safe for JSON."""
    if value is None or isinstance(value, (int, str, bool)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "<blob {} bytes>".format(len(bytes(value)))
    return str(value)


class QueryJob:
    """Live state for one running or just-finished query."""

    def __init__(self, query_id: str, sql_text: str, row_limit: int, timeout_s: Optional[float]):
        self.query_id = query_id
        self.sql_text = sql_text
        self.row_limit = row_limit
        self.timeout_s = timeout_s
        self.status = "running"
        self.started_at = _utcnow()
        self.started_monotonic = time.monotonic()
        self.duration_ms = 0
        self.steps = 0
        self.rows_fetched = 0
        self.columns: List[str] = []
        self.preview: List[List[Any]] = []
        self.truncated = False
        self.error: Optional[str] = None
        self.cancel_requested = False
        self.timed_out = False

    def snapshot(self) -> Dict[str, Any]:
        elapsed = (
            self.duration_ms
            if self.status != "running"
            else int((time.monotonic() - self.started_monotonic) * 1000)
        )
        return {
            "query_id": self.query_id,
            "sql_text": self.sql_text,
            "status": self.status,
            "started_at": self.started_at,
            "duration_ms": elapsed,
            "steps": self.steps,
            "rows_fetched": self.rows_fetched,
            "row_count": self.rows_fetched,
            "row_limit": self.row_limit,
            "columns": self.columns,
            "truncated": self.truncated,
            "error": self.error,
        }


class QueryRunner:
    def __init__(self, db_path: Path, store: HistoryStore):
        self.db_path = Path(db_path)
        self.store = store
        self._jobs: Dict[str, QueryJob] = {}
        self._lock = threading.Lock()

    # ── public API ────────────────────────────────────────────────────────

    def submit(self, sql_text: str, row_limit: int, timeout_s: Optional[float] = None) -> QueryJob:
        query_id = uuid.uuid4().hex[:12]
        job = QueryJob(query_id, sql_text, row_limit, timeout_s)
        with self._lock:
            self._jobs[query_id] = job
        self.store.start(query_id, sql_text, job.started_at, row_limit)
        thread = threading.Thread(target=self._run, args=(job,), daemon=True)
        thread.start()
        return job

    def get(self, query_id: str) -> Optional[QueryJob]:
        with self._lock:
            return self._jobs.get(query_id)

    def cancel(self, query_id: str) -> bool:
        job = self.get(query_id)
        if job is None or job.status != "running":
            return False
        job.cancel_requested = True
        return True

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            "file:{}?mode=ro".format(self.db_path), uri=True, timeout=30.0
        )
        conn.row_factory = None
        return conn

    def explain(self, sql_text: str) -> Dict[str, Any]:
        """Run EXPLAIN QUERY PLAN synchronously — it is always cheap."""
        conn = self.connect()
        try:
            cur = conn.execute("EXPLAIN QUERY PLAN " + sql_text)
            columns = [d[0] for d in cur.description]
            rows = [[_cell(v) for v in row] for row in cur.fetchall()]
            return {"columns": columns, "rows": rows}
        finally:
            conn.close()

    # ── worker ────────────────────────────────────────────────────────────

    def _run(self, job: QueryJob) -> None:
        result_path = self.store.result_path_for(job.query_id)
        conn = None
        handle = None
        writer = None
        status = "ok"

        def progress() -> int:
            job.steps += PROGRESS_INTERVAL
            if job.cancel_requested:
                return 1
            if job.timeout_s is not None:
                if time.monotonic() - job.started_monotonic > job.timeout_s:
                    job.timed_out = True
                    return 1
            return 0

        try:
            conn = self.connect()
            conn.set_progress_handler(progress, PROGRESS_INTERVAL)
            cursor = conn.execute(job.sql_text)

            if cursor.description is None:
                job.columns = []
                job.rows_fetched = 0
            else:
                job.columns = [d[0] for d in cursor.description]
                handle = open(result_path, "w", newline="", encoding="utf-8")
                writer = csv.writer(handle)
                writer.writerow(job.columns)
                while True:
                    chunk = cursor.fetchmany(FETCH_CHUNK)
                    if not chunk:
                        break
                    room = job.row_limit - job.rows_fetched
                    if len(chunk) > room:
                        chunk = chunk[:room]
                        job.truncated = True
                    for row in chunk:
                        cells = [_cell(v) for v in row]
                        writer.writerow(cells)
                        if len(job.preview) < PREVIEW_ROWS:
                            job.preview.append(cells)
                    job.rows_fetched += len(chunk)
                    if job.truncated or job.rows_fetched >= job.row_limit:
                        job.truncated = job.truncated or job.rows_fetched >= job.row_limit
                        break
        except sqlite3.OperationalError as exc:
            if job.cancel_requested:
                status, job.error = "cancelled", "Cancelled from the console."
            elif job.timed_out:
                status = "cancelled"
                job.error = "Stopped after the {} time limit.".format(_duration(job.timeout_s or 0))
            else:
                status, job.error = "error", str(exc)
        except Exception as exc:  # noqa: BLE001 - surfaced verbatim in the UI
            status, job.error = "error", "{}: {}".format(type(exc).__name__, exc)
        finally:
            if handle is not None:
                handle.close()
            if conn is not None:
                try:
                    conn.set_progress_handler(None, 0)
                    conn.close()
                except sqlite3.Error:
                    pass

        job.status = status
        job.duration_ms = int((time.monotonic() - job.started_monotonic) * 1000)
        saved = str(result_path) if (status == "ok" and job.columns) else None
        if saved is None and result_path.exists():
            # A failed or cancelled query leaves a partial file behind.
            keep_partial = status in ("cancelled", "error") and job.rows_fetched > 0
            if keep_partial:
                saved = str(result_path)
            else:
                try:
                    result_path.unlink()
                except OSError:
                    pass
        self.store.finish(
            query_id=job.query_id,
            status=status,
            finished_at=_utcnow(),
            duration_ms=job.duration_ms,
            row_count=job.rows_fetched,
            truncated=job.truncated,
            columns=job.columns or None,
            error=job.error,
            steps=job.steps,
            result_path=saved,
        )

    # ── cached results ────────────────────────────────────────────────────

    def read_result(self, path: str, limit: int = PREVIEW_ROWS) -> Dict[str, Any]:
        with open(path, "r", newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle)
            try:
                columns = next(reader)
            except StopIteration:
                return {"columns": [], "rows": [], "shown": 0}
            rows = []
            for row in reader:
                if len(rows) >= limit:
                    break
                rows.append(row)
        return {"columns": columns, "rows": rows, "shown": len(rows)}
