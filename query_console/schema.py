"""Schema browsing, backed by the data dictionary this repo already builds.

Column descriptions come from web/public/data/dictionary.json and table
purposes from documentation/table_dictionaries/overview.csv, so the sidebar
stays in sync with the dictionary instead of duplicating it.

A DuckDB database documents itself instead: table and column descriptions are
read from its own COMMENTs (see state_scorecards/ingestion/eic_2025/build_duckdb.py).
"""

from __future__ import annotations

import csv
import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

SAFE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class Catalog:
    def __init__(self, repo_root: Path):
        self.repo_root = Path(repo_root)
        self.dictionary_path = self.repo_root / "web/public/data/dictionary.json"
        self.overview_path = self.repo_root / "documentation/table_dictionaries/overview.csv"
        self.saved_dir = self.repo_root / "documentation/queries"
        self._dictionary: Optional[Dict[str, Any]] = None
        self._dictionary_mtime = 0.0
        self._overview: Optional[Dict[str, Dict[str, str]]] = None
        self._overview_mtime = 0.0

    # ── dictionary sources ────────────────────────────────────────────────

    def _load_dictionary(self) -> Dict[str, Any]:
        if not self.dictionary_path.exists():
            return {}
        mtime = self.dictionary_path.stat().st_mtime
        if self._dictionary is None or mtime != self._dictionary_mtime:
            with open(self.dictionary_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            self._dictionary = payload.get("tables", {}) or {}
            self._dictionary_mtime = mtime
        return self._dictionary

    def _load_overview(self) -> Dict[str, Dict[str, str]]:
        if not self.overview_path.exists():
            return {}
        mtime = self.overview_path.stat().st_mtime
        if self._overview is None or mtime != self._overview_mtime:
            with open(self.overview_path, "r", newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self._overview = {row.get("Table Name", ""): row for row in rows}
            self._overview_mtime = mtime
        return self._overview

    def _column_notes(self, table: str) -> Dict[str, str]:
        entry = self._load_dictionary().get(table) or {}
        notes: Dict[str, str] = {}
        for column in entry.get("columns", []) or []:
            name = column.get("Column Name")
            if not name:
                continue
            text = column.get("Description (ENG)") or column.get("Description (SPA)") or ""
            role = column.get("Role") or ""
            if role and role.lower() not in ("", "attribute"):
                text = "{} — {}".format(role, text) if text else role
            notes[name] = text
        return notes

    # ── live schema ───────────────────────────────────────────────────────

    @staticmethod
    def _is_sqlite(conn: Any) -> bool:
        return isinstance(conn, sqlite3.Connection)

    def objects(self, conn: Any) -> List[Dict[str, Any]]:
        if not self._is_sqlite(conn):
            rows = conn.execute(
                "SELECT 'table', table_name, comment FROM duckdb_tables()"
                " WHERE schema_name = 'main' AND NOT internal"
                " UNION ALL SELECT 'view', view_name, comment FROM duckdb_views()"
                " WHERE schema_name = 'main' AND NOT internal ORDER BY 2"
            ).fetchall()
            return [
                {"name": name, "type": kind, "purpose": comment or "", "grain": "",
                 "approx_rows": "", "primary_key": ""}
                for kind, name, comment in rows
            ]
        overview = self._load_overview()
        rows = conn.execute(
            "SELECT type, name FROM sqlite_master"
            " WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%'"
            " ORDER BY name"
        ).fetchall()
        out = []
        for kind, name in rows:
            meta = overview.get(name, {})
            out.append(
                {
                    "name": name,
                    "type": kind,
                    "purpose": meta.get("Purpose", ""),
                    "grain": meta.get("Row Grain", ""),
                    "approx_rows": meta.get("Approx. Row Count", ""),
                    "primary_key": meta.get("Primary Key", ""),
                }
            )
        return out

    def columns(self, conn: Any, table: str) -> List[Dict[str, Any]]:
        if not SAFE_NAME.match(table):
            raise ValueError("unsupported table name")
        if not self._is_sqlite(conn):
            rows = conn.execute(
                "SELECT column_name, data_type, NOT is_nullable, comment FROM duckdb_columns()"
                " WHERE schema_name = 'main' AND NOT internal AND table_name = ? ORDER BY column_index",
                [table],
            ).fetchall()
            if not rows:
                raise ValueError("unknown table")
            return [
                {"name": name, "type": dtype, "notnull": bool(notnull), "pk": False,
                 "description": comment or ""}
                for name, dtype, notnull, comment in rows
            ]
        known = {row[0] for row in conn.execute("SELECT name FROM sqlite_master")}
        if table not in known:
            raise ValueError("unknown table")
        notes = self._column_notes(table)
        out = []
        for row in conn.execute('PRAGMA table_info("{}")'.format(table)):
            _, name, decl_type, notnull, _default, pk = row
            out.append(
                {
                    "name": name,
                    "type": decl_type or "",
                    "notnull": bool(notnull),
                    "pk": bool(pk),
                    "description": notes.get(name, ""),
                }
            )
        return out

    def full_schema(self, conn: Any) -> Dict[str, List[str]]:
        """Every table with its column names — the editor's completion source."""
        out: Dict[str, List[str]] = {}
        if not self._is_sqlite(conn):
            for table, column in conn.execute(
                "SELECT table_name, column_name FROM duckdb_columns()"
                " WHERE schema_name = 'main' AND NOT internal ORDER BY table_name, column_index"
            ).fetchall():
                out.setdefault(table, []).append(column)
            return out
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            " AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ):
            table = row[0]
            if not SAFE_NAME.match(table):
                continue
            out[table] = [c[1] for c in conn.execute('PRAGMA table_info("{}")'.format(table))]
        return out

    # ── saved queries ─────────────────────────────────────────────────────

    def saved_queries(self) -> List[Dict[str, str]]:
        if not self.saved_dir.exists():
            return []
        out = []
        for path in sorted(self.saved_dir.glob("*.sql")):
            text = path.read_text(encoding="utf-8")
            title = ""
            for line in text.splitlines():
                if line.strip().startswith("--"):
                    title = line.strip().lstrip("- ").strip()
                    break
            out.append({"name": path.stem, "title": title, "sql": text})
        return out

    def save_query(self, name: str, sql_text: str) -> Dict[str, str]:
        slug = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
        if not slug:
            raise ValueError("give the query a name")
        self.saved_dir.mkdir(parents=True, exist_ok=True)
        path = self.saved_dir / (slug + ".sql")
        path.write_text(sql_text.rstrip() + "\n", encoding="utf-8")
        return {"name": slug, "path": str(path.relative_to(self.repo_root))}
