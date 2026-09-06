"""HTTP layer: a small JSON API plus the static single-page console."""

from __future__ import annotations

import json
import mimetypes
import os
import posixpath
import sqlite3
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, unquote, urlparse

from .engine import (
    DEFAULT_ROW_LIMIT,
    DEFAULT_TIMEOUT_S,
    NO_ROW_LIMIT,
    PREVIEW_ROWS,
    QueryRunner,
)
from .schema import Catalog
from .store import HistoryStore

STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_BODY = 4 * 1024 * 1024


class ConsoleContext:
    def __init__(self, repo_root: Path, db_path: Path, state_dir: Path, keep_results: int):
        self.repo_root = repo_root
        self.db_path = db_path
        self.store = HistoryStore(state_dir, keep_results=keep_results)
        self.store.mark_orphans_unknown()
        # Cached rows belong to the session that produced them.
        self.store.clear_results()
        self.runner = QueryRunner(db_path, self.store)
        self.catalog = Catalog(repo_root)


class ConsoleHandler(BaseHTTPRequestHandler):
    context: ConsoleContext = None  # type: ignore[assignment]
    server_version = "QueryConsole/1.0"

    # ── plumbing ──────────────────────────────────────────────────────────

    def log_message(self, fmt: str, *args: Any) -> None:  # quieter console
        if "/api/query/" in (args[0] if args else ""):
            return
        super().log_message(fmt, *args)

    def _send_json(self, payload: Dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, message: str, status: int = 400) -> None:
        self._send_json({"error": message}, status=status)

    def _read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _serve_static(self, rel: str) -> None:
        rel = posixpath.normpath(rel).lstrip("/")
        path = (STATIC_DIR / rel).resolve()
        if not str(path).startswith(str(STATIC_DIR)) or not path.is_file():
            self._error("not found", 404)
            return
        data = path.read_bytes()
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    # ── routes ────────────────────────────────────────────────────────────

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        route = unquote(parsed.path)
        params = parse_qs(parsed.query)
        ctx = self.context
        try:
            if route in ("/", "/index.html"):
                self._serve_static("index.html")
            elif route.startswith("/static/"):
                self._serve_static(route[len("/static/"):])
            elif route == "/api/meta":
                self._send_json(self._meta())
            elif route == "/api/schema":
                conn = ctx.runner.connect()
                try:
                    self._send_json({"objects": ctx.catalog.objects(conn)})
                finally:
                    conn.close()
            elif route == "/api/schema/all":
                conn = ctx.runner.connect()
                try:
                    self._send_json({"tables": ctx.catalog.full_schema(conn)})
                finally:
                    conn.close()
            elif route.startswith("/api/schema/"):
                table = route[len("/api/schema/"):]
                conn = ctx.runner.connect()
                try:
                    self._send_json({"table": table, "columns": ctx.catalog.columns(conn, table)})
                finally:
                    conn.close()
            elif route == "/api/saved":
                self._send_json({"queries": ctx.catalog.saved_queries()})
            elif route == "/api/history":
                limit = int((params.get("limit") or ["50"])[0])
                search = (params.get("q") or [""])[0]
                self._send_json({"entries": ctx.store.recent(min(limit, 500), search)})
            elif route.startswith("/api/query/") and route.endswith("/rows"):
                self._send_json(self._rows(route.split("/")[3], params))
            elif route.startswith("/api/query/") and route.endswith("/csv"):
                self._send_csv(route.split("/")[3])
            elif route.startswith("/api/query/"):
                self._send_json(self._status(route.split("/")[3]))
            else:
                self._error("not found", 404)
        except ValueError as exc:
            self._error(str(exc), 400)
        except FileNotFoundError:
            self._error("result file is no longer cached", 404)
        except BrokenPipeError:
            pass

    def do_POST(self) -> None:  # noqa: N802
        route = unquote(urlparse(self.path).path)
        ctx = self.context
        try:
            body = self._read_json()
            if route == "/api/run":
                sql_text = (body.get("sql") or "").strip().rstrip(";")
                if not sql_text:
                    self._error("write a query first")
                    return
                # An explicit null means "no limit"; a missing key takes the
                # conservative default.
                raw_limit = body.get("row_limit", DEFAULT_ROW_LIMIT)
                row_limit = NO_ROW_LIMIT if raw_limit in (None, "", 0) else int(raw_limit)
                timeout = body.get("timeout_s", DEFAULT_TIMEOUT_S)
                timeout_s = float(timeout) if timeout else None
                job = ctx.runner.submit(sql_text, row_limit, timeout_s)
                self._send_json(job.snapshot())
            elif route == "/api/explain":
                sql_text = (body.get("sql") or "").strip().rstrip(";")
                if not sql_text:
                    self._error("write a query first")
                    return
                self._send_json(ctx.runner.explain(sql_text))
            elif route == "/api/saved":
                saved = ctx.catalog.save_query(body.get("name") or "", body.get("sql") or "")
                self._send_json(saved)
            elif route.startswith("/api/query/") and route.endswith("/cancel"):
                query_id = route.split("/")[3]
                self._send_json({"cancelled": ctx.runner.cancel(query_id)})
            else:
                self._error("not found", 404)
        except sqlite3.Error as exc:
            self._error(str(exc), 400)
        except ValueError as exc:
            self._error(str(exc), 400)
        except BrokenPipeError:
            pass

    # ── route helpers ─────────────────────────────────────────────────────

    def _meta(self) -> Dict[str, Any]:
        ctx = self.context
        stat = ctx.db_path.stat()
        return {
            "database": str(ctx.db_path),
            "size_bytes": stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            "preview_rows": PREVIEW_ROWS,
        }

    def _status(self, query_id: str) -> Dict[str, Any]:
        ctx = self.context
        job = ctx.runner.get(query_id)
        if job is not None:
            snapshot = job.snapshot()
            snapshot["has_result"] = bool(job.preview)
            return snapshot
        entry = ctx.store.get(query_id)
        if entry is None:
            raise ValueError("unknown query id")
        return entry

    def _rows(self, query_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
        ctx = self.context
        limit = int((params.get("limit") or [str(PREVIEW_ROWS)])[0])
        job = ctx.runner.get(query_id)
        if job is not None:
            return {
                "columns": job.columns,
                "rows": job.preview[:limit],
                "shown": min(len(job.preview), limit),
                "row_count": job.rows_fetched,
                "truncated": job.truncated,
                "status": job.status,
            }
        entry = ctx.store.get(query_id)
        if entry is None:
            raise ValueError("unknown query id")
        if not entry.get("has_result"):
            return {
                "columns": entry.get("columns") or [],
                "rows": [],
                "shown": 0,
                "row_count": entry.get("row_count") or 0,
                "truncated": bool(entry.get("truncated")),
                "status": entry.get("status"),
                "note": "Rows are kept only for the session that ran the query."
                        " Press Run to fetch them again.",
            }
        payload = ctx.runner.read_result(entry["result_path"], limit)
        payload.update(
            {
                "row_count": entry.get("row_count") or 0,
                "truncated": bool(entry.get("truncated")),
                "status": entry.get("status"),
            }
        )
        return payload

    def _send_csv(self, query_id: str) -> None:
        ctx = self.context
        entry = ctx.store.get(query_id)
        if entry is None or not entry.get("has_result"):
            self._error("no cached result for that query", 404)
            return
        path = entry["result_path"]
        size = os.path.getsize(path)
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Length", str(size))
        self.send_header(
            "Content-Disposition",
            'attachment; filename="query_{}.csv"'.format(query_id),
        )
        self.end_headers()
        with open(path, "rb") as handle:
            while True:
                chunk = handle.read(64 * 1024)
                if not chunk:
                    break
                self.wfile.write(chunk)


def serve(context: ConsoleContext, host: str, port: int) -> ThreadingHTTPServer:
    handler = type("BoundConsoleHandler", (ConsoleHandler,), {"context": context})
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True
    return httpd
