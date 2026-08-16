"""Crawler for Senado initiative proposers.

Uses the "Listado de Asuntos Publicados" tool behind
senado.gob.mx/66/emergente/asuntosTurnados/asuntos.php (tipo=Inic), which
returns every currently-tracked initiative in one response: proposer
name/title, parliamentary group, committee referral, date, and a numeric
"ID Publicación" used here as the primary key.

Unlike the Diputados Gaceta Iniciativas pages, this endpoint's own
`legislatura` filter does not narrow the result set in practice -- it
already only returns LXVI-legislature rows regardless of what's passed, so
there is no --legislature flag here to parametrize.

The tipo=Inic feed only covers legislator-authored initiatives; Ejecutivo
federal and Minuta items live under the separate tipo=PEF / tipo=Minutas
feeds on the same tool, which this crawler does not cover.

Mirrors the cautious fetch/cache pattern used elsewhere in this repo, with
one difference: the single list response grows over time exactly like the
vote-list pages that motivated fixing that caching bug, so it is always
force-refreshed.

Usage:
    python3 camara_de_senadores/iniciativas/crawl_senado_iniciativas.py
"""

from __future__ import annotations

import re
import time
from datetime import date
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.senado.gob.mx"
ASUNTOS_URL = f"{BASE_URL}/66/emergente/asuntosTurnados/asuntos.php"
CACHE_DIR = Path("data/raw_senado_iniciativas")
OUT_DIR = Path("data/clean_senado_iniciativas")

MAX_RETRIES_429 = 4
RETRY_BACKOFF_BASE_SECONDS = 20
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}

MONTHS = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11,
    "diciembre": 12,
}
DATE_RE = re.compile(rf"(\d{{1,2}})\s+de\s+({'|'.join(MONTHS)})\s+de\s+(\d{{4}})", re.IGNORECASE)

LEGISLADOR_RE = re.compile(
    r"^(?:Del|De\s+(?:la|las|los))\s+(D[ií]p\.|Dp\.|Sen\.|diputad[oa]s?|senador[a]?s?)\s+"
    r"(.+?),?\s*(?:y suscrit[ao]s? por[^,]+?,?\s*)?"
    r"(?:integrante(?:s)? del|del|de la)\s+Grupo Parlamentario\s+(?:del|de)?\s*([^,]+?),\s*(.+)$",
    re.IGNORECASE,
)


def fetch_cached(cache_name: str, params: dict, session: requests.Session, force_refresh: bool = False) -> tuple[str, bool]:
    cache_path = CACHE_DIR / cache_name
    if cache_path.exists() and not force_refresh:
        if cache_path.stat().st_size == 0:
            cache_path.unlink()
        else:
            return cache_path.read_text(encoding="utf-8"), True

    for attempt in range(MAX_RETRIES_429 + 1):
        response = session.get(ASUNTOS_URL, headers=HEADERS, params=params, timeout=60)
        if response.status_code == 429:
            wait = RETRY_BACKOFF_BASE_SECONDS * (2 ** attempt)
            print(f"    429 rate limited, backing off {wait}s")
            time.sleep(wait)
            continue
        response.raise_for_status()
        text = response.text
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(text, encoding="utf-8")
        return text, False

    raise RuntimeError(f"Gave up on {ASUNTOS_URL} after {MAX_RETRIES_429} retries")


def parse_spanish_date(text: str) -> str | None:
    match = DATE_RE.search(text or "")
    if not match:
        return None
    day, month_name, year = match.groups()
    try:
        return date(int(year), MONTHS[month_name.lower()], int(day)).isoformat()
    except ValueError:
        return None


def parse_proposer(entry_text: str) -> dict[str, object]:
    match = LEGISLADOR_RE.match(entry_text)
    if match:
        _, name, party, title = match.groups()
        return {
            "proposer_type": "legislador",
            "proposer_name": name.strip().rstrip(","),
            "proposer_party": party.strip(),
            "proposer_raw": entry_text[: match.end(3)].strip(),
            "title": title.strip(),
            "needs_review": 0,
        }
    return {
        "proposer_type": "otro",
        "proposer_name": None,
        "proposer_party": None,
        "proposer_raw": None,
        "title": entry_text,
        "needs_review": 1,
    }


def parse_asuntos(html: str) -> pd.DataFrame:
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict[str, object]] = []

    for tr in soup.select("tbody tr"):
        cell = tr.select_one("td.text-justify")
        link = tr.select_one("a.ancla")
        if cell is None or link is None:
            continue

        category_p = cell.select_one("p")
        category = category_p.get_text(" ", strip=True) if category_p else None

        entry_text = re.sub(r"\s+", " ", link.get_text(" ", strip=True)).strip()
        proposer = parse_proposer(entry_text)

        doc_id_match = re.search(r"/documento/(\d+)", link.get("href", ""))
        doc_id = int(doc_id_match.group(1)) if doc_id_match else None

        full_text = cell.get_text(" ", strip=True)
        comision_match = re.search(
            r"turno directo a la Comisi[oó]n de ([^.]+)\.", full_text, re.IGNORECASE
        )
        comision = comision_match.group(1).strip() if comision_match else None

        fecha_match = re.search(r"Fecha:\s*(.+?)\s*ID Publicaci[oó]n", full_text, re.IGNORECASE)
        fecha_text = fecha_match.group(1).strip() if fecha_match else None

        pub_id_match = re.search(r"ID Publicaci[oó]n:\s*(\d+)", full_text, re.IGNORECASE)
        publication_id = int(pub_id_match.group(1)) if pub_id_match else doc_id

        rows.append(
            {
                "senado_iniciativa_id": publication_id,
                "category": category,
                "title": proposer["title"],
                "proposer_type": proposer["proposer_type"],
                "proposer_name": proposer["proposer_name"],
                "proposer_party": proposer["proposer_party"],
                "proposer_raw": proposer["proposer_raw"],
                "comision": comision,
                "fecha": parse_spanish_date(fecha_text or ""),
                "source_url": urljoin(BASE_URL, link.get("href", "")),
                "needs_review": proposer["needs_review"],
            }
        )
    return pd.DataFrame(rows)


def crawl() -> None:
    session = requests.Session()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Fetching: {ASUNTOS_URL}?tipo=Inic")
    # This single response is the entire growing list of tracked
    # initiatives -- same "don't cache the list forever" fix already
    # applied to the vote crawlers.
    html, from_cache = fetch_cached(
        "asuntos_Inic.html",
        {"tipo": "Inic", "idSenador": 0, "idComision": 0, "Fecha": 0, "legislatura": 0, "anio": 0},
        session,
        force_refresh=True,
    )
    print(f"from_cache={from_cache}")

    iniciativas = parse_asuntos(html)
    iniciativas.to_csv(OUT_DIR / "dim_senado_iniciativa.csv", index=False)
    print(f"Wrote {len(iniciativas):,} initiative rows -> {OUT_DIR / 'dim_senado_iniciativa.csv'}")
    if len(iniciativas):
        review_share = iniciativas["needs_review"].mean()
        print(f"  needs_review: {review_share:.1%}")


def main() -> None:
    crawl()


if __name__ == "__main__":
    main()
