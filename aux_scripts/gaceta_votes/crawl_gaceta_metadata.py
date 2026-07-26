"""
Metadata-only crawler for Camara de Diputados Gaceta voting pages.

This deliberately mirrors the cautious fetch/cache pattern used by
ingestion/dat_to_arrow_2000.py:
  - every fetched page is cached to disk;
  - cache hits do not sleep or re-hit the server;
  - 429s back off and retry;
  - the default run only fetches the top index and period pages;
  - vote-summary page fetching is opt-in and capped.

Examples:
    python3 aux_scripts/gaceta_votes/crawl_gaceta_metadata.py --max-periods 3

    python3 aux_scripts/gaceta_votes/crawl_gaceta_metadata.py \
      --max-periods 3 --fetch-vote-pages --max-vote-pages 10
"""

from __future__ import annotations

import argparse
import re
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urljoin, urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup

from parse_gaceta_vote import (
    clean_text,
    detail_action_url,
    legislature_from_url,
    parse_summary,
    slug_from_url,
    vote_id_from_url,
)


BASE_URL = "https://gaceta.diputados.gob.mx"
INDEX_URL = f"{BASE_URL}/gp_votaciones.html"
CACHE_DIR = Path("data/raw_gaceta_votes")
OUT_DIR = Path("data/clean_gaceta_votes")

REQUEST_DELAY_SECONDS = 1.5
MAX_RETRIES_429 = 4
RETRY_BACKOFF_BASE_SECONDS = 20

MONTHS = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}
MONTH_PATTERN = "|".join(MONTHS)
SPANISH_DATE_RE = re.compile(
    r"(?:(?:el|,)?\s*)?"
    r"(?:lunes|martes|miércoles|miercoles|jueves|viernes|sábado|sabado|domingo)?"
    rf"\s*(\d{{1,2}})\s+de\s+({MONTH_PATTERN})(?:\s+de\s+(\d{{4}}))?",
    re.IGNORECASE,
)
GACETA_RE = re.compile(
    r"Gaceta Parlamentaria\s*,\s*número\s+([^,]+),\s*(.+?)(?:\.|$)",
    re.IGNORECASE,
)
# Known typos in gaceta.diputados.gob.mx's own source text that parse
# correctly but produce a wrong date. Verified case by case — do not add an
# entry here without confirming the true date independently (e.g. against
# gaceta_date, the weekday text, or the vote's title).
KNOWN_SOURCE_DATE_TYPOS: dict[str, str] = {
    # Source text reads "...el jueves 29 de agosto de 2021", but Aug 29 2021
    # was a Sunday while Aug 29 2024 was a Thursday — matches the weekday,
    # gaceta_date, and title text ("29 de agosto de 2024"). The vote is for
    # the LXVI Legislatura's first Mesa Directiva, so 2024 is correct.
    "GACETA_L66_TABLA1OR1_1": "2024-08-29",
}

STATUS_RE = re.compile(r"\b(Aprobad[oa]|Desechad[oa]|No aprobado|Rechazad[oa])\b", re.IGNORECASE)
FAVOR_RE = re.compile(r"(\d+)\s+votos?\s+en\s+pro", re.IGNORECASE)
CONTRA_RE = re.compile(r"(\d+)\s+en\s+contra", re.IGNORECASE)
ABSTENTION_RE = re.compile(r"(\d+)\s+abstenci(?:ón|ones)", re.IGNORECASE)


@dataclass(frozen=True)
class FetchResult:
    text: str
    from_cache: bool


def cache_path_for_url(url: str) -> Path:
    parsed = urlparse(url)
    path = parsed.path.strip("/") or "index.html"
    safe = re.sub(r"[^A-Za-z0-9._/-]+", "_", path)
    if not Path(safe).suffix:
        safe = f"{safe}.html"
    return CACHE_DIR / safe


def fetch_html_cached(url: str, session: requests.Session) -> FetchResult:
    cache_path = cache_path_for_url(url)
    if cache_path.exists():
        if cache_path.stat().st_size == 0:
            cache_path.unlink()
        else:
            return FetchResult(cache_path.read_text(encoding="utf-8"), True)

    for attempt in range(MAX_RETRIES_429 + 1):
        response = session.get(url, timeout=30)
        if response.status_code == 429:
            wait = RETRY_BACKOFF_BASE_SECONDS * (2 ** attempt)
            print(f"    429 rate limited on {url}, backing off {wait}s")
            time.sleep(wait)
            continue
        response.raise_for_status()
        response.encoding = "latin-1"
        text = response.text
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(text, encoding="utf-8")
        time.sleep(REQUEST_DELAY_SECONDS)
        return FetchResult(text, False)

    raise RuntimeError(f"Gave up on {url} after {MAX_RETRIES_429} retries")


def hrefs_matching(html: str, base_url: str, pattern: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    seen: set[str] = set()
    urls: list[str] = []
    regex = re.compile(pattern)
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not regex.search(href):
            continue
        url = urljoin(base_url, href)
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


def parse_period_links(index_html: str) -> pd.DataFrame:
    rows = []
    for url in hrefs_matching(index_html, INDEX_URL, r"/Gaceta/Votaciones/\d+/.*\.html$"):
        rows.append(
            {
                "period_url": url,
                "period_path": urlparse(url).path,
                "period_slug": slug_from_url(url),
                "legislature": legislature_from_url(url),
            }
        )
    return pd.DataFrame(rows).drop_duplicates(subset=["period_url"]).reset_index(drop=True)


def nearest_vote_context(a_tag) -> str:
    li = a_tag.find_parent("li")
    if li is not None:
        return clean_text(li.get_text(" "))
    ul = a_tag.find_parent("ul")
    return clean_text(ul.get_text(" ")) if ul is not None else clean_text(a_tag.get_text(" "))


def parse_spanish_date(text: str, fallback_year: int | None = None) -> str | None:
    matches = list(SPANISH_DATE_RE.finditer(text or ""))
    if not matches:
        return None
    match = matches[-1]
    day = int(match.group(1))
    month = MONTHS[match.group(2).lower()]
    year = int(match.group(3)) if match.group(3) else fallback_year
    if year is None:
        return None
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def last_int(regex: re.Pattern, text: str) -> int | None:
    matches = list(regex.finditer(text or ""))
    return int(matches[-1].group(1)) if matches else None


def split_vote_segments(context: str) -> list[str]:
    parts = re.split(r"\s+Votación\s*\.", context or "")
    return [part.strip() for part in parts[:-1]] or [context]


def add_context_metadata(votes: pd.DataFrame) -> pd.DataFrame:
    if votes.empty:
        return votes

    rows = []
    for _, group in votes.groupby(["period_url", "period_context"], sort=False):
        segments = split_vote_segments(group.iloc[0]["period_context"])
        context = group.iloc[0]["period_context"]
        gaceta_match = GACETA_RE.search(context or "")
        gaceta_number = gaceta_match.group(1).strip() if gaceta_match else None
        gaceta_date_text = gaceta_match.group(2).strip() if gaceta_match else None
        gaceta_date = parse_spanish_date(gaceta_date_text or "")
        fallback_year = int(gaceta_date[:4]) if gaceta_date else None

        carried_date = None
        for i, (_, row) in enumerate(group.iterrows()):
            segment = segments[min(i, len(segments) - 1)]
            segment_date = parse_spanish_date(segment, fallback_year=fallback_year)
            if segment_date:
                carried_date = segment_date
                date_source = "segment"
            elif carried_date:
                segment_date = carried_date
                date_source = "previous_segment"
            else:
                segment_date = gaceta_date
                date_source = "gaceta_date" if gaceta_date else None

            if row["gaceta_vote_id"] in KNOWN_SOURCE_DATE_TYPOS:
                segment_date = KNOWN_SOURCE_DATE_TYPOS[row["gaceta_vote_id"]]
                date_source = "manual_correction"

            status = STATUS_RE.search(segment or "")
            out = row.to_dict()
            out.update(
                {
                    "vote_context": segment,
                    "vote_date": segment_date,
                    "vote_date_source": date_source,
                    "gaceta_number": gaceta_number,
                    "gaceta_date": gaceta_date,
                    "status_text": status.group(1) if status else None,
                    "votes_favor_text": last_int(FAVOR_RE, segment),
                    "votes_contra_text": last_int(CONTRA_RE, segment),
                    "abstentions_text": last_int(ABSTENTION_RE, segment),
                }
            )
            rows.append(out)
    return pd.DataFrame(rows)


def parse_period_page(period_url: str, html: str) -> tuple[dict[str, object], pd.DataFrame]:
    soup = BeautifulSoup(html, "html.parser")
    period_title = clean_text(soup.title.get_text(" ")) if soup.title else ""
    period_meta = {
        "period_url": period_url,
        "period_path": urlparse(period_url).path,
        "period_slug": slug_from_url(period_url),
        "legislature": legislature_from_url(period_url),
        "period_title": period_title,
    }

    rows = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not re.search(r"/Gaceta/Votaciones/\d+/tabla[^\" ]+\.php3$", href):
            continue
        vote_url = urljoin(period_url, href)
        rows.append(
            {
                "gaceta_vote_id": vote_id_from_url(vote_url),
                "vote_url": vote_url,
                "vote_path": urlparse(vote_url).path,
                "table_slug": slug_from_url(vote_url),
                "legislature": legislature_from_url(vote_url),
                "period_url": period_url,
                "link_text": clean_text(a.get_text(" ")),
                "period_context": nearest_vote_context(a),
            }
        )
    votes = pd.DataFrame(rows).drop_duplicates(subset=["vote_url"]).reset_index(drop=True)
    return period_meta, votes


def parse_vote_summary_metadata(vote_url: str, html: str) -> dict[str, object]:
    title, event, links = parse_summary(html)
    total_rows = [link for link in links if link.party == "Total"]
    return {
        "gaceta_vote_id": vote_id_from_url(vote_url),
        "vote_url": vote_url,
        "vote_path": urlparse(vote_url).path,
        "table_slug": slug_from_url(vote_url),
        "legislature": legislature_from_url(vote_url),
        "chamber": "DIP",
        "title": title,
        "source_event": event,
        "detail_action_url": detail_action_url(vote_url, html),
        "summary_total": sum(link.count for link in total_rows if link.vote != "Total"),
        "chamber_total": next((link.count for link in total_rows if link.vote == "Total"), None),
        "summary_cells": len(links),
    }


def crawl(max_periods: int | None, fetch_vote_pages: bool, max_vote_pages: int) -> None:
    session = requests.Session()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Fetching index: {INDEX_URL}")
    index = fetch_html_cached(INDEX_URL, session)
    periods = parse_period_links(index.text)
    if max_periods is not None:
        periods = periods.head(max_periods).copy()
    print(f"Period pages selected: {len(periods):,}")

    period_rows = []
    vote_catalogs = []
    for i, row in periods.iterrows():
        period_url = row["period_url"]
        print(f"  [{i + 1}/{len(periods)}] {period_url}")
        period_html = fetch_html_cached(period_url, session)
        period_meta, votes = parse_period_page(period_url, period_html.text)
        period_meta["from_cache"] = period_html.from_cache
        period_rows.append(period_meta)
        vote_catalogs.append(votes)

    dim_period = pd.DataFrame(period_rows)
    vote_catalog = (
        pd.concat(vote_catalogs, ignore_index=True)
        if vote_catalogs else pd.DataFrame()
    )
    if not vote_catalog.empty:
        vote_catalog = vote_catalog.drop_duplicates(subset=["vote_url"]).reset_index(drop=True)
        vote_catalog = add_context_metadata(vote_catalog)

    dim_period.to_csv(OUT_DIR / "dim_gaceta_period.csv", index=False)
    vote_catalog.to_csv(OUT_DIR / "gaceta_vote_url_catalog.csv", index=False)
    print(f"Wrote {len(dim_period):,} period rows -> {OUT_DIR / 'dim_gaceta_period.csv'}")
    print(f"Wrote {len(vote_catalog):,} vote URL rows -> {OUT_DIR / 'gaceta_vote_url_catalog.csv'}")

    if not fetch_vote_pages:
        print("Skipping vote-summary pages. Use --fetch-vote-pages for the next stage.")
        return

    vote_rows = []
    selected_votes = vote_catalog.head(max_vote_pages)
    print(f"Vote summary pages selected: {len(selected_votes):,}")
    for i, row in selected_votes.iterrows():
        vote_url = row["vote_url"]
        print(f"  [{i + 1}/{len(selected_votes)}] {vote_url}")
        vote_html = fetch_html_cached(vote_url, session)
        meta = parse_vote_summary_metadata(vote_url, vote_html.text)
        meta["from_cache"] = vote_html.from_cache
        vote_rows.append(meta)

    dim_vote = pd.DataFrame(vote_rows)
    dim_vote.to_csv(OUT_DIR / "dim_gaceta_vote_metadata_sample.csv", index=False)
    print(f"Wrote {len(dim_vote):,} vote metadata rows -> {OUT_DIR / 'dim_gaceta_vote_metadata_sample.csv'}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-periods", type=int, default=3)
    parser.add_argument("--all-periods", action="store_true")
    parser.add_argument("--fetch-vote-pages", action="store_true")
    parser.add_argument("--max-vote-pages", type=int, default=10)
    args = parser.parse_args()

    max_periods = None if args.all_periods else args.max_periods
    crawl(max_periods, args.fetch_vote_pages, args.max_vote_pages)


if __name__ == "__main__":
    main()
