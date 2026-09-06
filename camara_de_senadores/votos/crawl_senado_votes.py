"""
Crawler for Senado de la Republica roll-call votes, LXVI Legislatura only.

Mirrors the cautious fetch/cache pattern used by
camara_de_diputados/votos/crawl_gaceta_metadata.py:
  - every fetched page (and AJAX response) is cached to disk;
  - cache hits do not sleep or re-hit the server;
  - 429s back off and retry;
  - the default run only fetches a handful of votes; use --all-votes for
    the full legislature.

The vote list page (https://www.senado.gob.mx/66/votaciones/por_legislatura/LXVI/)
is a single static page with no pagination: it links every roll call as
/66/votacion/{id}. Each vote's own page renders only the header/footer
totals server-side; the senator-by-senator breakdown is loaded client-side
via an AJAX endpoint (/66/app/votaciones/functions/viewTableVot.php), which
we call directly.

Examples:
    python3 camara_de_senadores/votos/crawl_senado_votes.py --max-votes 10

    python3 camara_de_senadores/votos/crawl_senado_votes.py --all-votes
"""

from __future__ import annotations

import argparse
import re
import time
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.senado.gob.mx"
LIST_URL = f"{BASE_URL}/66/votaciones/por_legislatura/LXVI/"
AJAX_URL = f"{BASE_URL}/66/app/votaciones/functions/viewTableVot.php"
CACHE_DIR = Path("data/raw_senado_votes")
OUT_DIR = Path("data/clean_senado_votes")

REQUEST_DELAY_SECONDS = 1.0
MAX_RETRIES_429 = 4
RETRY_BACKOFF_BASE_SECONDS = 20

MONTHS = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11,
    "diciembre": 12,
}
DATE_RE = re.compile(
    rf"(\d{{1,2}})\s+de\s+({'|'.join(MONTHS)})\s+de\s+(\d{{4}})", re.IGNORECASE
)
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}


def fetch_cached(
    url: str,
    session: requests.Session,
    cache_name: str,
    params: dict | None = None,
    force_refresh: bool = False,
) -> tuple[str, bool]:
    cache_path = CACHE_DIR / cache_name
    if cache_path.exists() and not force_refresh:
        if cache_path.stat().st_size == 0:
            cache_path.unlink()
        else:
            return cache_path.read_text(encoding="utf-8"), True

    for attempt in range(MAX_RETRIES_429 + 1):
        response = session.get(url, headers=HEADERS, params=params, timeout=30)
        if response.status_code == 429:
            wait = RETRY_BACKOFF_BASE_SECONDS * (2 ** attempt)
            print(f"    429 rate limited on {url}, backing off {wait}s")
            time.sleep(wait)
            continue
        response.raise_for_status()
        text = response.text
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(text, encoding="utf-8")
        time.sleep(REQUEST_DELAY_SECONDS)
        return text, False

    raise RuntimeError(f"Gave up on {url} after {MAX_RETRIES_429} retries")


def parse_vote_list(html: str) -> list[int]:
    soup = BeautifulSoup(html, "html.parser")
    ids: set[int] = set()
    for a in soup.find_all("a", href=True):
        match = re.search(r"/66/votacion/(\d+)", a["href"])
        if match:
            ids.add(int(match.group(1)))
    return sorted(ids)


def parse_spanish_date(text: str) -> str | None:
    match = DATE_RE.search(text or "")
    if not match:
        return None
    day = int(match.group(1))
    month = MONTHS[match.group(2).lower()]
    year = int(match.group(3))
    return f"{year:04d}-{month:02d}-{day:02d}"


def parse_vote_page(vote_id: int, html: str) -> dict[str, object]:
    soup = BeautifulSoup(html, "html.parser")

    h3 = soup.find("h3")
    period_text = h3.get_text(" ", strip=True) if h3 else ""
    # Two source formats seen in the wild:
    #   "SEGUNDO AÑO DE EJERCICIO PRIMER PERIODO ORDINARIO"
    #   "Periodo EXTRAORDINARIO PRIMER AÑO DE EJERCICIO"
    period_type = "EXTRAORDINARIO" if re.search(r"EXTRAORDINARIO", period_text, re.IGNORECASE) else (
        "ORDINARIO" if re.search(r"ORDINARIO", period_text, re.IGNORECASE) else None
    )
    ordinal_match = re.search(r"(PRIMER|SEGUNDO)\s+PERIODO\s+ORDINARIO", period_text, re.IGNORECASE)
    period = ordinal_match.group(1).upper() if ordinal_match else None
    # The source apostrophizes the ordinal before "AÑO": the pages read
    # "TERCER AÑO DE EJERCICIO", never "TERCERO", so the old TERCERO
    # alternative never matched and left exercise_year NULL for every vote
    # from the tercer año onward (first seen on votacion 5123, 2026-09-02).
    year_match = re.search(
        r"(PRIMER|SEGUNDO|TERCER)\s+A[ÑN]O\s+DE\s+EJERCICIO", period_text, re.IGNORECASE
    )
    exercise_year = year_match.group(1).upper() if year_match else None

    vote_date = None
    for candidate in soup.select("div.col-sm-12.text-center strong"):
        vote_date = parse_spanish_date(candidate.get_text(" ", strip=True))
        if vote_date:
            break

    body_div = soup.select_one("div.col-sm-12.text-justify")
    description, vote_type = None, None
    if body_div is not None:
        parts = [p.strip() for p in body_div.get_text("\n", strip=True).split("\n") if p.strip()]
        if parts:
            # Some pages wrap one description across source lines, while others
            # contain several matters in a single roll call. The old first/last
            # split truncated both layouts and occasionally put another bill in
            # vote_type. A genuine stage starts with one of these Senate labels;
            # subsequent lines belong to that stage text.
            stage_index = next(
                (
                    index
                    for index, part in enumerate(parts)
                    if re.match(r"^(?:VOTACI[ÓO]N\b|EN\s+LO\b)", part, re.IGNORECASE)
                ),
                None,
            )
            if stage_index is None:
                description = "\n".join(parts)
            else:
                description = "\n".join(parts[:stage_index]) or None
                vote_type = "\n".join(parts[stage_index:]) or None

    en_pro = en_contra = abstencion = None
    footer = soup.select_one("table.table tfoot")
    if footer is not None:
        cells = footer.find_all("td")
        values = [c.find("span").get_text(strip=True) if c.find("span") else None for c in cells]
        if len(values) == 3:
            en_pro, en_contra, abstencion = (int(v) if v else None for v in values)

    return {
        "votacion_id": vote_id,
        "url": f"{BASE_URL}/66/votacion/{vote_id}",
        "vote_date": vote_date,
        "period_type": period_type,
        "ordinal_period": period,
        "exercise_year": exercise_year,
        "description": description,
        "vote_type": vote_type,
        "en_pro": en_pro,
        "en_contra": en_contra,
        "abstencion": abstencion,
    }


def parse_vote_detail(vote_id: int, html: str) -> pd.DataFrame:
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for tr in soup.select("tbody tr"):
        tds = tr.find_all("td")
        if len(tds) != 4:
            continue
        senator_link = tds[1].find("a")
        senator_href = senator_link["href"] if senator_link else ""
        senator_id_match = re.search(r"/66/votaciones/(\d+)", senator_href)
        # The voto cell sometimes holds two lines joined by <br> (e.g.
        # "AUSENTE<br>COMISIÓN OFICIAL"); get_text with "|" keeps them
        # separable instead of silently concatenating into one word.
        voto_parts = [p.strip() for p in tds[3].get_text("|", strip=True).split("|") if p.strip()]
        rows.append(
            {
                "votacion_id": vote_id,
                "row_num": tds[0].get_text(strip=True),
                "senator_name": tds[1].get_text(strip=True),
                "senator_id": int(senator_id_match.group(1)) if senator_id_match else None,
                "grupo_parlamentario": tds[2].get_text(strip=True),
                "voto": voto_parts[0] if voto_parts else None,
                "voto_detail": voto_parts[1] if len(voto_parts) > 1 else None,
            }
        )
    return pd.DataFrame(rows)


def crawl(max_votes: int | None) -> None:
    session = requests.Session()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Fetching vote list: {LIST_URL}")
    # The vote list page grows as new roll calls are recorded, so it must
    # always be re-fetched live -- unlike per-vote pages, which are
    # immutable once a vote has happened and are safe to cache forever.
    list_html, _ = fetch_cached(LIST_URL, session, "list_LXVI.html", force_refresh=True)
    vote_ids = parse_vote_list(list_html)
    print(f"Votes found: {len(vote_ids):,}")

    if max_votes is not None:
        vote_ids = vote_ids[-max_votes:]
    print(f"Votes selected: {len(vote_ids):,}")

    vote_meta_rows = []
    vote_detail_frames = []
    for i, vote_id in enumerate(vote_ids):
        print(f"  [{i + 1}/{len(vote_ids)}] votacion/{vote_id}")

        page_html, page_cached = fetch_cached(
            f"{BASE_URL}/66/votacion/{vote_id}", session, f"votacion_{vote_id}.html"
        )
        meta = parse_vote_page(vote_id, page_html)
        meta["from_cache"] = page_cached
        vote_meta_rows.append(meta)

        ajax_html, ajax_cached = fetch_cached(
            AJAX_URL,
            session,
            f"votacion_{vote_id}_detail.html",
            params={"action": "ajax", "cell": 1, "order": "DESC", "votacion": vote_id, "q": ""},
        )
        detail = parse_vote_detail(vote_id, ajax_html)
        detail["from_cache"] = ajax_cached
        vote_detail_frames.append(detail)

    dim_vote = pd.DataFrame(vote_meta_rows)
    fact_detail = (
        pd.concat(vote_detail_frames, ignore_index=True) if vote_detail_frames else pd.DataFrame()
    )

    dim_vote.to_csv(OUT_DIR / "dim_senado_vote.csv", index=False)
    fact_detail.to_csv(OUT_DIR / "senado_vote_detail.csv", index=False)
    print(f"Wrote {len(dim_vote):,} vote rows -> {OUT_DIR / 'dim_senado_vote.csv'}")
    print(f"Wrote {len(fact_detail):,} senator-vote rows -> {OUT_DIR / 'senado_vote_detail.csv'}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-votes", type=int, default=10, help="Fetch only the N most recent votes")
    parser.add_argument("--all-votes", action="store_true", help="Fetch every vote in the LXVI legislature")
    args = parser.parse_args()

    max_votes = None if args.all_votes else args.max_votes
    crawl(max_votes)


if __name__ == "__main__":
    main()
