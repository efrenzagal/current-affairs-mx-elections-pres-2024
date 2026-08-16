"""Crawler for Camara de Diputados initiative proposers.

Separate from the roll-call vote pages under /Gaceta/Votaciones/ — this
walks the /Gaceta/Iniciativas/ page tree instead, which lists who proposed
each initiative (a named deputy/senator + parliamentary group, the
Ejecutivo federal, or a minuta sent over by the other chamber), the
committee it was referred to, and — when the initiative reached a floor
vote — a direct link to the same vote-page URL already used as the join key
for dim_gaceta_vote.

Mirrors the cautious fetch/cache pattern used by crawl_gaceta_metadata.py:
  - every fetched page is cached to disk;
  - cache hits do not sleep or re-hit the server;
  - 429s back off and retry;
  - only the target legislature's period pages are force-refreshed (closed
    legislatures are immutable).

Usage:
    python3 camara_de_diputados/iniciativas/crawl_gaceta_iniciativas.py
    python3 camara_de_diputados/iniciativas/crawl_gaceta_iniciativas.py --legislature 66
"""

from __future__ import annotations

import argparse
import re
import time
from datetime import date
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup

BASE_URL = "https://gaceta.diputados.gob.mx"
INDEX_URL = f"{BASE_URL}/gp_iniciativas.html"
CACHE_DIR = Path("data/raw_gaceta_iniciativas")
OUT_DIR = Path("data/clean_gaceta_iniciativas")

REQUEST_DELAY_SECONDS = 1.5
MAX_RETRIES_429 = 4
RETRY_BACKOFF_BASE_SECONDS = 20

MONTHS = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}
MONTH_PATTERN = "|".join(MONTHS)
DAY_HEADING_RE = re.compile(
    rf"(\d{{1,2}})\s+de\s+({MONTH_PATTERN})\s+de\s+(\d{{4}})", re.IGNORECASE
)
PERIOD_LINK_RE = re.compile(r'href="(/Gaceta/Iniciativas/(\d+)/[^"]+\.html)"', re.IGNORECASE)
DAY_SECTION_RE = re.compile(
    r'<font color="#CC0000">([^<]*\d{4})</font>', re.IGNORECASE
)
LI_RE = re.compile(r"<li>(.*?)</li>", re.IGNORECASE | re.DOTALL)
VOTE_URL_RE = re.compile(r'href="(/Gaceta/Votaciones/\d+/[^"]+\.php3)"', re.IGNORECASE)
GACETA_REF_RE = re.compile(
    r"Gaceta Parlamentaria</a>,\s*n[uú]mero\s*([^,]+),\s*([^.]+)\.\s*\((\d+)\)",
    re.IGNORECASE,
)
COMISION_RE = re.compile(
    r"Turnada a (?:la|las)\s+Comisi[oó]n(?:es)?(?:\s+Unidas)?\s+de\s+([^.<]+)\.",
    re.IGNORECASE,
)
EJECUTIVO_RE = re.compile(r"Presentada por el Ejecutivo federal", re.IGNORECASE)
LEGISLADOR_RE = re.compile(
    r"Presentada por (?:el|la|los|las)\s+(diputad[oa]s?|senador[a]?s?)\s+([^,]+?),\s*([^.<]+)\.",
    re.IGNORECASE,
)
MINUTA_RE = re.compile(
    r"Enviada por la C[aá]mara de (Senadores|Diputados)", re.IGNORECASE
)


def fetch_cached(url: str, session: requests.Session, cache_name: str, force_refresh: bool = False) -> tuple[str, bool]:
    cache_path = CACHE_DIR / cache_name
    if cache_path.exists() and not force_refresh:
        if cache_path.stat().st_size == 0:
            cache_path.unlink()
        else:
            return cache_path.read_text(encoding="utf-8"), True

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
        return text, False

    raise RuntimeError(f"Gave up on {url} after {MAX_RETRIES_429} retries")


def clean_text(html_fragment: str) -> str:
    text = BeautifulSoup(html_fragment, "html.parser").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", text).strip()


def parse_spanish_date(text: str) -> str | None:
    match = DAY_HEADING_RE.search(text or "")
    if not match:
        return None
    day, month_name, year = match.groups()
    try:
        return date(int(year), MONTHS[month_name.lower()], int(day)).isoformat()
    except ValueError:
        return None


def discover_period_urls(index_html: str, legislature: int) -> list[str]:
    urls = []
    seen = set()
    for href, leg in PERIOD_LINK_RE.findall(index_html):
        if int(leg) != legislature or href in seen:
            continue
        seen.add(href)
        urls.append(urljoin(BASE_URL, href))
    return urls


def discover_available_legislatures(index_html: str) -> list[int]:
    return sorted({int(leg) for _, leg in PERIOD_LINK_RE.findall(index_html)})


def parse_proposer(entry_text: str) -> dict[str, object]:
    if EJECUTIVO_RE.search(entry_text):
        return {"proposer_type": "ejecutivo", "proposer_name": None, "proposer_party": None,
                "proposer_raw": "Presentada por el Ejecutivo federal", "needs_review": 0}

    match = LEGISLADOR_RE.search(entry_text)
    if match:
        role, name, party = match.groups()
        return {
            "proposer_type": "legislador",
            "proposer_name": name.strip(),
            "proposer_party": party.strip(),
            "proposer_raw": match.group(0).strip(),
            "needs_review": 0,
        }

    match = MINUTA_RE.search(entry_text)
    if match:
        return {
            "proposer_type": "minuta",
            "proposer_name": None,
            "proposer_party": None,
            "proposer_raw": match.group(0).strip(),
            "needs_review": 0,
        }

    return {"proposer_type": "otro", "proposer_name": None, "proposer_party": None,
             "proposer_raw": None, "needs_review": 1}


def parse_period_page(html: str, legislature: int, period_url: str) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    current_date: str | None = None
    cursor = 0
    day_matches = list(DAY_SECTION_RE.finditer(html))
    li_matches = list(LI_RE.finditer(html))
    day_idx = 0

    for li_match in li_matches:
        while day_idx < len(day_matches) and day_matches[day_idx].start() < li_match.start():
            current_date = parse_spanish_date(day_matches[day_idx].group(1))
            day_idx += 1

        raw_html = li_match.group(1)
        title = clean_text(raw_html.split("<br", 1)[0])
        entry_text = clean_text(raw_html)
        if not title:
            continue

        proposer = parse_proposer(entry_text)

        vote_match = VOTE_URL_RE.search(raw_html)
        vote_url = urljoin(BASE_URL, vote_match.group(1)) if vote_match else None

        comision_match = COMISION_RE.search(entry_text)
        comision = comision_match.group(1).strip() if comision_match else None

        gaceta_match = GACETA_REF_RE.search(raw_html)
        gaceta_number = gaceta_date_text = sequence_number = None
        if gaceta_match:
            gaceta_number = gaceta_match.group(1).strip()
            gaceta_date_text = gaceta_match.group(2).strip()
            sequence_number = int(gaceta_match.group(3))

        rows.append(
            {
                "legislature": legislature,
                "sequence_number": sequence_number,
                "title": title,
                "proposer_type": proposer["proposer_type"],
                "proposer_name": proposer["proposer_name"],
                "proposer_party": proposer["proposer_party"],
                "proposer_raw": proposer["proposer_raw"],
                "comision": comision,
                "gaceta_number": gaceta_number,
                "gaceta_date": parse_spanish_date(gaceta_date_text or ""),
                "vote_url": vote_url,
                "period_date": current_date,
                "period_url": period_url,
                "needs_review": proposer["needs_review"],
            }
        )
    return pd.DataFrame(rows)


def crawl(legislature: int | None) -> None:
    session = requests.Session()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Fetching index: {INDEX_URL}")
    # New periods appear on the index as the current legislature progresses,
    # so it must always be re-fetched live rather than trusted from cache.
    index_html, _ = fetch_cached(INDEX_URL, session, "gp_iniciativas.html", force_refresh=True)

    if legislature is None:
        legislature = max(discover_available_legislatures(index_html))
    print(f"Target legislature: {legislature}")

    period_urls = discover_period_urls(index_html, legislature)
    print(f"Period pages found: {len(period_urls)}")

    frames = []
    for i, period_url in enumerate(period_urls):
        cache_name = period_url.split("/Gaceta/Iniciativas/", 1)[1].replace("/", "_")
        print(f"  [{i + 1}/{len(period_urls)}] {period_url}")
        # Every period page belonging to the target legislature is
        # force-refreshed -- closed legislatures never change, but any
        # period within the current one can still gain new entries.
        html, _ = fetch_cached(period_url, session, cache_name, force_refresh=True)
        frames.append(parse_period_page(html, legislature, period_url))

    iniciativas = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    iniciativas.to_csv(OUT_DIR / "dim_gaceta_iniciativa.csv", index=False)
    print(f"Wrote {len(iniciativas):,} initiative rows -> {OUT_DIR / 'dim_gaceta_iniciativa.csv'}")
    if len(iniciativas):
        review_share = iniciativas["needs_review"].mean()
        with_vote = iniciativas["vote_url"].notna().mean()
        print(f"  needs_review: {review_share:.1%}, resolved to a vote_url: {with_vote:.1%}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--legislature", type=int, default=None,
                         help="Defaults to the current (highest) legislature on the index")
    args = parser.parse_args()
    crawl(args.legislature)


if __name__ == "__main__":
    main()
