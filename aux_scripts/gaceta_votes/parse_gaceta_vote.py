"""
Parse Camara de Diputados Gaceta voting tables.

Example:
    python3 aux_scripts/parse_gaceta_vote.py \
      https://gaceta.diputados.gob.mx/Gaceta/Votaciones/66/tabla2ex1-1.php3

Outputs:
    aux_scripts/gaceta_votes/<slug>_dim_gaceta_vote.csv
    aux_scripts/gaceta_votes/<slug>_dim_gaceta_deputy.csv
    aux_scripts/gaceta_votes/<slug>_fact_gaceta_vote_summary.csv
    aux_scripts/gaceta_votes/<slug>_fact_gaceta_deputy_vote.csv
"""

from __future__ import annotations

import argparse
import hashlib
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup


DEFAULT_URL = "https://gaceta.diputados.gob.mx/Gaceta/Votaciones/66/tabla2ex1-1.php3"
OUT_DIR = Path("aux_scripts/gaceta_votes")


PARTY_ALIASES = {
    "Morena": "MRN",
    "Partido Accion Nacional": "PAN",
    "Partido Acción Nacional": "PAN",
    "Partido Revolucionario Institucional": "PRI",
    "Partido de la Revolucion Democratica": "PRD",
    "Partido de la Revolución Democrática": "PRD",
    "Partido Verde Ecologista de Mexico": "PVEM",
    "Partido Verde Ecologista de México": "PVEM",
    "Partido del Trabajo": "PT",
    "Partido Alianza Social": "PAS",
    "Partido Sociedad Nacionalista": "PSN",
    "Convergencia por la Democracia": "CONV",
    "Movimiento Ciudadano": "MC",
    "independientes": "IND",
    "Independientes": "IND",
    "sin partido": "IND",
}


@dataclass(frozen=True)
class VoteLink:
    vote: str
    party: str
    count: int
    lola_key: str | None


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def key_text(text: str) -> str:
    text = unicodedata.normalize("NFD", clean_text(text).upper())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return re.sub(r"[^A-Z0-9]+", "_", text).strip("_")


def fetch_html(url: str, session: requests.Session | None = None, **kwargs) -> str:
    sess = session or requests.Session()
    response = sess.get(url, timeout=30, **kwargs)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "latin-1"
    return response.text


def parse_summary(html: str) -> tuple[str, str | None, list[VoteLink]]:
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form")
    if form is None:
        raise ValueError("Could not find voting form in page")

    title_input = form.find("input", {"name": "nomtit"})
    title = clean_text(title_input.get("value", "")) if title_input else ""
    event_input = form.find("input", {"name": "evento"})
    event = event_input.get("value") if event_input else None

    rows = form.find_all("tr")
    header_cells = rows[1].find_all("td")
    parties = [clean_text(cell.get_text(" ")) for cell in header_cells][1:]

    links: list[VoteLink] = []
    for row in rows[2:]:
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        vote = clean_text(cells[0].get_text(" "))
        if vote.startswith("*") or vote == "":
            continue
        if vote.lower() == "total":
            for party, cell in zip(parties, cells[1:]):
                count = int(clean_text(cell.get_text(" ") or "0"))
                links.append(VoteLink(vote=vote, party=party, count=count, lola_key=None))
            continue
        for party, cell in zip(parties, cells[1:]):
            input_tag = cell.find("input")
            if input_tag:
                count = int(clean_text(input_tag.get("value", "0")) or "0")
                name = input_tag.get("name", "")
                match = re.search(r"lola\[(\d+)\]", name)
                lola_key = match.group(1) if match else None
            else:
                count = int(clean_text(cell.get_text(" ") or "0"))
                lola_key = None
            links.append(VoteLink(vote=vote, party=party, count=count, lola_key=lola_key))
    return title, event, links


def detail_action_url(page_url: str, html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form")
    if form is None:
        raise ValueError("Could not find voting form in page")
    return urljoin(page_url, form.get("action", ""))


def post_detail(
    action_url: str,
    event: str | None,
    title: str,
    lola_key: str,
    count: int,
    session: requests.Session,
) -> str:
    data = {"nomtit": title, f"lola[{lola_key}]": str(count)}
    if event is not None:
        data["evento"] = event
    response = session.post(action_url, data=data, timeout=30)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "latin-1"
    return response.text


def normalize_party(label: str) -> str:
    label = clean_text(label)
    return PARTY_ALIASES.get(label, label)


def parse_detail(html: str, vote: str) -> list[dict[str, object]]:
    soup = BeautifulSoup(html, "html.parser")
    headings = soup.find_all(
        string=re.compile(
            r"Diputados .* que "
            r"(?:votaron|estuvieron|se abstuvieron)"
        )
    )
    rows: list[dict[str, object]] = []
    for heading in headings:
        text = clean_text(str(heading))
        if text.startswith(("Diputados independientes", "Diputados Independientes", "Diputados sin partido")):
            match = re.match(
                r"Diputados (?:independientes|sin partido) que "
                r"(?:votaron .+?|estuvieron ausentes|estuvieron presentes y no votaron|se abstuvieron de votar): (\d+)$",
                text,
                flags=re.IGNORECASE,
            )
            party_label = "sin partido" if "sin partido" in text.lower() else "independientes"
            expected_count = int(match.group(1)) if match else 0
        else:
            match = re.match(
                r"Diputados (?:de|del) (.+?) que "
                r"(?:votaron .+?|estuvieron ausentes|estuvieron presentes y no votaron|se abstuvieron de votar): (\d+)$",
                text,
            )
            party_label = match.group(1) if match else ""
            expected_count = int(match.group(2)) if match else 0
        if not match:
            continue
        party = normalize_party(party_label)
        table = heading.find_parent("font")
        if table is not None:
            table = table.find_next("table")
        names_text = table.get_text("\n") if table else ""
        names = [
            clean_text(name)
            for name in re.findall(r"(?:^|\n)\s*\d+\s*:\s*([^\n]+)", names_text)
        ]
        if not names and expected_count == 0:
            continue
        for index, name in enumerate(names, start=1):
            rows.append(
                {
                    "vote": vote,
                    "party": party,
                    "ordinal": index,
                    "deputy_name": name,
                }
            )
    return rows


def slug_from_url(url: str) -> str:
    stem = Path(urlparse(url).path).stem
    return re.sub(r"[^A-Za-z0-9_-]+", "_", stem).strip("_") or "gaceta_vote"


def vote_id_from_url(url: str) -> str:
    parsed = urlparse(url)
    match = re.search(r"/Votaciones/(\d+)/([^/]+)\.php3$", parsed.path)
    if not match:
        return f"GACETA_{key_text(slug_from_url(url))}"
    legislature, table_slug = match.groups()
    return f"GACETA_L{legislature}_{key_text(table_slug)}"


def deputy_id(name: str) -> str:
    digest = hashlib.sha1(key_text(name).encode("utf-8")).hexdigest()[:12].upper()
    return f"DEP_{digest}"


def legislature_from_url(url: str) -> int | None:
    match = re.search(r"/Votaciones/(\d+)/", urlparse(url).path)
    return int(match.group(1)) if match else None


def parse_vote_page(url: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    session = requests.Session()
    html = fetch_html(url, session=session)
    title, event, links = parse_summary(html)
    action_url = detail_action_url(url, html)
    vote_id = vote_id_from_url(url)

    dim_vote = pd.DataFrame(
        [
            {
                "gaceta_vote_id": vote_id,
                "source_url": url,
                "source_path": urlparse(url).path,
                "table_slug": slug_from_url(url),
                "legislature": legislature_from_url(url),
                "chamber": "DIP",
                "title": title,
                "source_event": event,
                "detail_action_url": action_url,
            }
        ]
    )

    fact_summary = pd.DataFrame(
        [
            {
                "gaceta_vote_id": vote_id,
                "vote_choice": link.vote,
                "party_key": normalize_party(link.party),
                "count": link.count,
                "lola_key": link.lola_key,
            }
            for link in links
        ]
    )

    deputy_rows: list[dict[str, object]] = []
    for link in links:
        if link.party != "Total" or link.count == 0 or link.lola_key is None:
            continue
        detail_html = post_detail(action_url, event, title, link.lola_key, link.count, session)
        deputy_rows.extend(parse_detail(detail_html, link.vote))

    if deputy_rows:
        fact_deputy_vote = pd.DataFrame(deputy_rows)
        fact_deputy_vote["gaceta_vote_id"] = vote_id
        fact_deputy_vote["deputy_id"] = fact_deputy_vote["deputy_name"].map(deputy_id)
        fact_deputy_vote = fact_deputy_vote.rename(
            columns={"vote": "vote_choice", "party": "party_key"}
        )
        dim_deputy = (
            fact_deputy_vote[["deputy_id", "deputy_name"]]
            .drop_duplicates()
            .sort_values(["deputy_name", "deputy_id"])
            .reset_index(drop=True)
        )
        fact_deputy_vote = fact_deputy_vote[
            ["gaceta_vote_id", "deputy_id", "vote_choice", "party_key", "ordinal"]
        ]
    else:
        dim_deputy = pd.DataFrame(columns=["deputy_id", "deputy_name"])
        fact_deputy_vote = pd.DataFrame(
            columns=["gaceta_vote_id", "deputy_id", "vote_choice", "party_key", "ordinal"]
        )
    return dim_vote, dim_deputy, fact_summary, fact_deputy_vote


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("url", nargs="?", default=DEFAULT_URL)
    parser.add_argument("--out-dir", default=OUT_DIR, type=Path)
    args = parser.parse_args()

    dim_vote, dim_deputy, fact_summary, fact_deputy_vote = parse_vote_page(args.url)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    slug = slug_from_url(args.url)
    outputs = {
        "dim_gaceta_vote": dim_vote,
        "dim_gaceta_deputy": dim_deputy,
        "fact_gaceta_vote_summary": fact_summary,
        "fact_gaceta_deputy_vote": fact_deputy_vote,
    }
    for name, df in outputs.items():
        path = args.out_dir / f"{slug}_{name}.csv"
        df.to_csv(path, index=False)
        print(f"Wrote {len(df):,} rows -> {path}")


if __name__ == "__main__":
    main()
