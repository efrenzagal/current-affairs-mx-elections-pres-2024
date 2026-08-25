"""
Fetch one El Financiero "Encuesta EF" approval article for manual transcription.

This is a helper, not a scraper. It does only the mechanical parts that are
stable — pull the article, dump its prose, download its charts — and stops
short of reading any numbers. Extracting figures from the prose was tried and
abandoned: the wording changes between waves and it silently failed on 4 of 10
articles. The charts have been reliable, so the numbers are read off the
images and recorded by hand in chart_transcriptions.csv.

El Financiero runs on Arc Publishing, so each article embeds a
Fusion.globalContent JSON blob holding the body text, the methodology note, and
the article's own images with none of the surrounding recirculation photos.

The first body image is always the headline approval chart, and it is
CUMULATIVE — it redraws the series back to Oct 2024 rather than showing only
the current month. One image therefore reconstructs the whole series, and every
new article restates months already on file, which is the overlap that
approval/ingest.py reconciles against.

See aux_scripts/approval_rates/approval_refresh_runbook.md for the full procedure.

Usage:
    /usr/bin/python3 aux_scripts/approval_rates/el_financiero/fetch_ef_approval_article.py URL
    /usr/bin/python3 aux_scripts/approval_rates/el_financiero/fetch_ef_approval_article.py URL --no-images
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT_DIR = ROOT / "data" / "clean_approval"

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"


def fetch(url: str, timeout: int = 30) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def fetch_arc_content(url: str) -> dict:
    try:
        html = fetch(url).decode("utf-8", "replace")
    except (urllib.error.URLError, urllib.error.HTTPError) as exc:
        sys.exit(f"ERROR: could not fetch {url}: {exc}")
    match = re.search(r"Fusion\.globalContent\s*=\s*(\{.*?\});", html, re.S)
    if not match:
        sys.exit(
            "ERROR: no Fusion.globalContent found. El Financiero may have "
            "moved off Arc Publishing — see the runbook's 'When the format "
            "changes' section."
        )
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        sys.exit(f"ERROR: globalContent did not parse as JSON: {exc}")


def strip_tags(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html or "").strip()


def body_text(content: dict) -> list[str]:
    return [
        strip_tags(el.get("content", ""))
        for el in content.get("content_elements", [])
        if el.get("type") == "text" and strip_tags(el.get("content", ""))
    ]


def body_images(content: dict) -> list[dict]:
    """Article images only.

    Arc keeps recirculation photos out of content_elements, so everything here
    belongs to the article. Captions are inconsistent between waves
    ("(Gráfica: El Financiero)", "Encuesta EF", or empty), so this deliberately
    does not filter on them.
    """
    return [
        el for el in content.get("content_elements", [])
        if el.get("type") == "image" and el.get("url")
    ]


def methodology_note(texts: list[str]) -> str:
    return next(
        (t for t in texts if t.lower().lstrip().startswith("metodolog")), ""
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="Article URL")
    parser.add_argument("--out-dir", default=str(OUT_DIR))
    parser.add_argument(
        "--no-images", action="store_true", help="Skip chart downloads"
    )
    args = parser.parse_args()

    content = fetch_arc_content(args.url)
    slug = args.url.rstrip("/").rsplit("/", 1)[-1][:60]

    out_dir = Path(args.out_dir)
    chart_dir = out_dir / "charts"
    text_dir = out_dir / "text"
    chart_dir.mkdir(parents=True, exist_ok=True)
    text_dir.mkdir(parents=True, exist_ok=True)

    headline = (content.get("headlines") or {}).get("basic", "").strip()
    published = content.get("publish_date", "")
    texts = body_text(content)
    images = body_images(content)

    transcript = text_dir / f"{slug}.txt"
    transcript.write_text(
        "\n\n".join([
            f"URL: {args.url}",
            f"HEADLINE: {headline}",
            f"PUBLISHED: {published}",
            f"RETRIEVED: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
            "-" * 70,
            *texts,
        ]),
        encoding="utf-8",
    )

    print(f"Headline : {headline}")
    print(f"Published: {published}")
    print(f"Prose    : {transcript.relative_to(ROOT)}")
    note = methodology_note(texts)
    print(f"Method   : {note[:200] if note else '(no methodology note found)'}")
    print(f"\nCharts ({len(images)}):")

    for index, image in enumerate(images):
        url = image["url"]
        suffix = Path(url).suffix or ".jpg"
        local = chart_dir / f"{slug}__{index:02d}{suffix}"
        status = "skipped"
        if not args.no_images:
            if local.exists():
                status = "cached"
            else:
                try:
                    local.write_bytes(fetch(url))
                    status = "downloaded"
                except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
                    status = f"FAILED ({exc})"
        label = "headline approval chart" if index == 0 else "per-topic panel"
        print(f"  [{index:02d}] {label:<24s} {status:<12s} {local.relative_to(ROOT)}")

    print(
        "\nNext: open the chart images and transcribe values into\n"
        "  aux_scripts/approval_rates/chart_transcriptions.csv\n"
        "then run: /usr/bin/python3 approval/ingest.py"
    )


if __name__ == "__main__":
    main()
