"""Fetch one Enkoll presidential-approval PDF for manual transcription.

Enkoll publishes its national report as a PDF, including the recurring
approval time series.  This helper keeps an exact local copy for visual
inspection; it deliberately does not try to read values from the PDF.

Usage:
    /usr/bin/python3 aux_scripts/approval_rates/enkoll/fetch_enkoll_approval_pdf.py URL
"""

from __future__ import annotations

import argparse
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[3]
OUT_DIR = ROOT / "data" / "clean_approval" / "pdfs"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="Direct Enkoll PDF URL")
    parser.add_argument("--out-dir", default=str(OUT_DIR))
    args = parser.parse_args()

    name = Path(urlparse(args.url).path).name
    if not name.lower().endswith(".pdf"):
        sys.exit("ERROR: expected a direct PDF URL")

    destination = Path(args.out_dir) / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(args.url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = response.read()
    except (urllib.error.URLError, urllib.error.HTTPError) as exc:
        sys.exit(f"ERROR: could not fetch {args.url}: {exc}")

    if not payload.startswith(b"%PDF"):
        sys.exit("ERROR: response was not a PDF")
    destination.write_bytes(payload)
    print(f"PDF      : {destination.relative_to(ROOT)}")
    print(f"Source   : {args.url}")
    print(f"Retrieved: {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    print("Next: inspect the printed labels and append rows to chart_transcriptions.csv")


if __name__ == "__main__":
    main()
