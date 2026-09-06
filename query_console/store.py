"""Persistent log of every query the console has run.

The log lives in its own small SQLite file so it never touches the warehouse,
and each successful run keeps its full result set on disk as CSV. That is what
makes it possible to reopen a query from last week and still see what it
returned.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS query_log (
    query_id     TEXT PRIMARY KEY,
    sql_text     TEXT NOT NULL,
    status       TEXT NOT NULL,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    duration_ms  INTEGER,
    row_count    INTEGER,
    truncated    INTEGER NOT NULL DEFAULT 0,
    columns_json TEXT,
    error        TEXT,
    steps        INTEGER,
    row_limit    INTEGER,
    result_path  TEXT
);
CREATE INDEX IF NOT EXISTS ix_query_log_started ON query_log (started_at DESC);
"""


class HistoryStore:
    """Append-only query history with a bounded cache of result files."""

    def __init__(self, state_dir: Path, keep_results: int = 50) -> None:
        self.state_dir = Path(state_dir)
        self.results_dir = self.state_dir / "results"
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.keep_results = keep_results
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            str(self.state_dir / "history.db"), check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    # ── writes ────────────────────────────────────────────────────────────

    def start(self, query_id: str, sql_text: str, started_at: str, row_limit: int) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO query_log"
                " (query_id, sql_text, status, started_at, row_limit)"
                " VALUES (?, ?, 'running', ?, ?)",
                (query_id, sql_text, started_at, row_limit),
            )
            self._conn.commit()

    def finish(
        self,
        query_id: str,
        status: str,
        finished_at: str,
        duration_ms: int,
        row_count: Optional[int],
        truncated: bool,
        columns: Optional[List[str]],
        error: Optional[str],
        steps: int,
        result_path: Optional[str],
    ) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE query_log SET status = ?, finished_at = ?, duration_ms = ?,"
                " row_count = ?, truncated = ?, columns_json = ?, error = ?, steps = ?,"
                " result_path = ? WHERE query_id = ?",
                (
                    status,
                    finished_at,
                    duration_ms,
                    row_count,
                    1 if truncated else 0,
                    json.dumps(columns) if columns is not None else None,
                    error,
                    steps,
                    result_path,
                    query_id,
                ),
            )
            self._conn.commit()
        self.prune_results()

    def clear_results(self) -> None:
        """Drop every cached result set.

        Result rows are session-scoped: a heavy query can put tens of megabytes
        on disk, and keeping that around for weeks buys little. The log entry —
        SQL, status, duration, row count, error — is text and stays forever.
        """
        for path in self.results_dir.glob("*.csv"):
            try:
                path.unlink()
            except OSError:
                pass
        with self._lock:
            self._conn.execute(
                "UPDATE query_log SET result_path = NULL WHERE result_path IS NOT NULL"
            )
            self._conn.commit()

    def mark_orphans_unknown(self) -> None:
        """A query left 'running' by a console restart never finished."""
        with self._lock:
            self._conn.execute(
                "UPDATE query_log SET status = 'interrupted',"
                " error = 'Console exited while this query was running.'"
                " WHERE status = 'running'"
            )
            self._conn.commit()

    # ── reads ─────────────────────────────────────────────────────────────

    def recent(self, limit: int = 50, search: str = "") -> List[Dict[str, Any]]:
        sql = "SELECT * FROM query_log"
        params: List[Any] = []
        if search:
            sql += " WHERE sql_text LIKE ?"
            params.append("%" + search + "%")
        sql += " ORDER BY started_at DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._as_dict(row) for row in rows]

    def get(self, query_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM query_log WHERE query_id = ?", (query_id,)
            ).fetchone()
        return self._as_dict(row) if row else None

    @staticmethod
    def _as_dict(row: sqlite3.Row) -> Dict[str, Any]:
        entry = dict(row)
        entry["columns"] = json.loads(entry.pop("columns_json") or "null")
        entry["truncated"] = bool(entry["truncated"])
        entry["has_result"] = bool(
            entry.get("result_path") and os.path.exists(entry["result_path"])
        )
        return entry

    # ── housekeeping ──────────────────────────────────────────────────────

    def result_path_for(self, query_id: str) -> Path:
        return self.results_dir / (query_id + ".csv")

    def prune_results(self) -> None:
        """Keep only the newest `keep_results` result files on disk."""
        with self._lock:
            keepers = {
                row[0]
                for row in self._conn.execute(
                    "SELECT result_path FROM query_log WHERE result_path IS NOT NULL"
                    " ORDER BY started_at DESC LIMIT ?",
                    (self.keep_results,),
                )
            }
        for path in self.results_dir.glob("*.csv"):
            if str(path) not in keepers:
                try:
                    path.unlink()
                except OSError:
                    pass
