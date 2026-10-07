"""Fetch PollsMX approval history and load election_data.db; standard library only."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from approval.pollsmx import download, export_web, ingest, write_json


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "election_data.db")
    parser.add_argument("--president", action="append", help="API key; repeat to select several. Default: all.")
    parser.add_argument("--from-file", type=Path, help="Replay an archived snapshot without network access")
    parser.add_argument("--download-only", action="store_true", help="Archive without touching SQLite")
    parser.add_argument("--raw-dir", type=Path, default=ROOT / "data/clean_approval/pollsmx")
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--export-web", action="store_true", help="Also export website-ready JSON")
    parser.add_argument("--web-output", type=Path, default=ROOT / "web/public/data/approval-pollsmx.json")
    args = parser.parse_args()
    if args.from_file and args.president:
        parser.error("--president cannot be combined with --from-file")
    if args.download_only and args.export_web:
        parser.error("--download-only cannot be combined with --export-web")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    try:
        if args.from_file:
            snapshot = args.from_file
            bundle = json.loads(snapshot.read_text(encoding="utf-8"))
        else:
            bundle = download(args.president, args.timeout)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            snapshot = args.raw_dir / f"snapshot-{stamp}.json"
            write_json(snapshot, bundle)
            print(f"Archived {snapshot}")
        if not args.download_only:
            result = ingest(bundle, args.db, snapshot)
            print(f"Loaded {result['points']:,} points: {result['changed']} changed runs, "
                  f"{result['unchanged']} unchanged runs into {args.db}")
            if args.export_web:
                export_web(args.db, args.web_output)
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error, urllib.error.URLError) as exc:
        sys.exit(f"ERROR: {exc}")


if __name__ == "__main__":
    main()
