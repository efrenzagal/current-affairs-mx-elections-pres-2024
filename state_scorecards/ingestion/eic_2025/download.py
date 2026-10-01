"""Download and unzip the INEGI Encuesta Intercensal 2025 microdata, one state per zip.

INEGI publishes one archive per state, each holding three CSV tables that join
on ID_VIV (see the "MODELO LÓGICO" sheet of eic2025_micro_fd.xlsx):

* viviendasNN.csv  -- one row per sampled dwelling
* personasNN.csv   -- one row per resident
* migrantesNN.csv  -- one row per person who left for another country since 2020

Each state lands in its own folder with the CSVs unchanged, and the zip is
deleted once the tables are verified. Every download is recorded in
manifest.csv (URL, ETag, Last-Modified, SHA-256, rows per table) so a later
revision by INEGI is detectable rather than silently mixed in.

States already present in the manifest are skipped unless --force is given.

Usage:
    python3 state_scorecards/ingestion/eic_2025/download.py              # all 32 states
    python3 state_scorecards/ingestion/eic_2025/download.py --states 1 32
    python3 state_scorecards/ingestion/eic_2025/download.py --states 9 --force
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import shutil
import sys
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import requests


REPO_ROOT = Path(__file__).resolve().parents[3]
OUTPUT_ROOT = REPO_ROOT / "state_scorecards" / "data" / "eic_2025" / "microdatos"
MANIFEST_PATH = OUTPUT_ROOT / "manifest.csv"

URL_TEMPLATE = (
    "https://www.inegi.org.mx/contenidos/programas/eic/2025/microdatos/"
    "eic2025_micro_{state}_csv.zip"
)
TABLES = ("viviendas", "personas", "migrantes")
# INEGI answers a missing file with a small HTML error page and HTTP 200, so
# the content type is the only reliable signal that the archive exists.
ZIP_CONTENT_TYPES = {"application/x-zip-compressed", "application/zip"}
HEADERS = {"User-Agent": "Mozilla/5.0 (state_scorecards research pipeline)"}
CHUNK_SIZE = 1 << 20
RETRIES = 3
PAUSE_SECONDS = 2

MANIFEST_COLUMNS = [
    "state",
    "url",
    "etag",
    "last_modified",
    "zip_bytes",
    "zip_sha256",
    "downloaded_at",
    *(f"{table}_rows" for table in TABLES),
]


def read_manifest() -> dict[str, dict[str, str]]:
    if not MANIFEST_PATH.exists():
        return {}
    with MANIFEST_PATH.open(newline="", encoding="utf-8") as stream:
        return {row["state"]: row for row in csv.DictReader(stream)}


def write_manifest(manifest: dict[str, dict[str, str]]) -> None:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    temporary = MANIFEST_PATH.with_suffix(".csv.tmp")
    with temporary.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=MANIFEST_COLUMNS)
        writer.writeheader()
        for state in sorted(manifest):
            writer.writerow(manifest[state])
    temporary.replace(MANIFEST_PATH)


def download(url: str, destination: Path) -> dict[str, str]:
    """Stream ``url`` to ``destination``; return provenance headers and digest."""
    last_error: Exception | None = None
    for attempt in range(1, RETRIES + 1):
        try:
            with requests.get(url, headers=HEADERS, stream=True, timeout=60) as response:
                response.raise_for_status()
                content_type = response.headers.get("Content-Type", "").split(";")[0]
                if content_type not in ZIP_CONTENT_TYPES:
                    raise RuntimeError(
                        f"{url} returned {content_type or 'no content type'}, not a zip; "
                        "INEGI may have moved or renamed the file"
                    )
                digest = hashlib.sha256()
                size = 0
                with destination.open("wb") as stream:
                    for chunk in response.iter_content(CHUNK_SIZE):
                        stream.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)
                expected = response.headers.get("Content-Length")
                if expected and int(expected) != size:
                    raise RuntimeError(f"Truncated download: {size:,} of {int(expected):,} bytes")
                return {
                    "etag": response.headers.get("ETag", "").strip('"'),
                    "last_modified": response.headers.get("Last-Modified", ""),
                    "zip_bytes": str(size),
                    "zip_sha256": digest.hexdigest(),
                }
        except (requests.RequestException, RuntimeError) as error:
            last_error = error
            if isinstance(error, RuntimeError) and "not a zip" in str(error):
                break
            print(f"    attempt {attempt}/{RETRIES} failed: {error}")
            time.sleep(PAUSE_SECONDS * attempt)
    raise RuntimeError(f"Could not download {url}: {last_error}")


def extract_tables(archive_path: Path, state: str, destination: Path) -> dict[str, int]:
    """Extract the three expected CSVs, flattened, and return rows per table."""
    with zipfile.ZipFile(archive_path) as archive:
        broken = archive.testzip()
        if broken:
            raise RuntimeError(f"Corrupt member in {archive_path.name}: {broken}")
        members = {
            Path(info.filename).name.lower(): info
            for info in archive.infolist()
            if not info.is_dir() and info.filename.lower().endswith(".csv")
        }
        expected = {f"{table}{state}.csv": table for table in TABLES}
        missing = sorted(set(expected) - set(members))
        if missing:
            raise RuntimeError(f"{archive_path.name} is missing {missing}; found {sorted(members)}")
        unexpected = sorted(set(members) - set(expected))
        if unexpected:
            print(f"    note: ignoring unexpected files {unexpected}")

        rows = {}
        for filename, table in expected.items():
            # Flatten to a fixed name; never trust the archive's own paths.
            target = destination / filename
            with archive.open(members[filename]) as source, target.open("wb") as sink:
                shutil.copyfileobj(source, sink, CHUNK_SIZE)
            with target.open("rb") as stream:
                rows[table] = sum(1 for _ in stream) - 1
    return rows


def fetch_state(state: str) -> dict[str, str]:
    url = URL_TEMPLATE.format(state=state)
    final_dir = OUTPUT_ROOT / f"eic2025_micro_{state}"
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

    # Stage everything next to the destination so the final move is a rename,
    # and a failed run never leaves a half-written state folder behind.
    with tempfile.TemporaryDirectory(dir=OUTPUT_ROOT, prefix=f".{state}_") as staging:
        staging_dir = Path(staging)
        archive_path = staging_dir / f"eic2025_micro_{state}_csv.zip"
        provenance = download(url, archive_path)
        tables_dir = staging_dir / "tables"
        tables_dir.mkdir()
        rows = extract_tables(archive_path, state, tables_dir)

        if final_dir.exists():
            shutil.rmtree(final_dir)
        tables_dir.rename(final_dir)

    return {
        "state": state,
        "url": url,
        **provenance,
        "downloaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **{f"{table}_rows": str(count) for table, count in rows.items()},
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--states",
        type=int,
        nargs="+",
        default=list(range(1, 33)),
        metavar="N",
        help="INEGI state codes 1-32 (default: all)",
    )
    parser.add_argument("--force", action="store_true", help="Re-download states already in the manifest")
    args = parser.parse_args()
    invalid = [state for state in args.states if not 1 <= state <= 32]
    if invalid:
        parser.error(f"State codes must be 1-32, got {invalid}")
    return args


def main() -> None:
    args = parse_args()
    manifest = read_manifest()
    failures = []

    for number in args.states:
        state = f"{number:02d}"
        if state in manifest and not args.force:
            print(f"[{state}] already downloaded {manifest[state]['downloaded_at']}; skipping")
            continue
        print(f"[{state}] downloading {URL_TEMPLATE.format(state=state)}")
        try:
            entry = fetch_state(state)
        except Exception as error:  # Keep going: one bad state should not stop the other 31.
            print(f"[{state}] FAILED: {error}")
            failures.append(state)
            continue
        manifest[state] = entry
        write_manifest(manifest)
        print(
            f"[{state}] {int(entry['zip_bytes']) / 1_048_576:.1f} MB zip -> "
            + ", ".join(f"{table} {int(entry[f'{table}_rows']):,} rows" for table in TABLES)
        )
        time.sleep(PAUSE_SECONDS)

    print(f"Manifest: {MANIFEST_PATH}")
    if failures:
        print(f"Failed states: {', '.join(failures)} (rerun with --states {' '.join(str(int(s)) for s in failures)})")
        sys.exit(1)


if __name__ == "__main__":
    main()
