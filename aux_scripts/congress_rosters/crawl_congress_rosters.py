"""Fetch the official current LXVI rosters without touching roll-call pages.

The Camara publishes one list per parliamentary group.  The Senate publishes
all senators currently in office on a single group-labelled directory page.
Raw HTML is cached for auditability and the parsed snapshots are written as
CSV files consumed by ``ingestion.congress_roster_ingest``.

Run from the repository root::

    python3 aux_scripts/congress_rosters/crawl_congress_rosters.py
"""

from __future__ import annotations

import argparse
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup


ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = ROOT / "data" / "raw_congress_rosters"
OUT_DIR = ROOT / "data" / "clean_congress_rosters"

DIP_BASE_URL = "https://sitl.diputados.gob.mx/LXVI_leg/"
DIP_GROUPS = {
    "MORENA": "14",
    "PAN": "3",
    "PVEM": "5",
    "PT": "4",
    "PRI": "1",
    "MC": "6",
    "IND": "9",
}
SEN_URL = "https://www.senado.gob.mx/66/senadores/por_grupo_parlamentario"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; BrujulaLegislativa/1.0; roster snapshot)"
}


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _snapshot_id(chamber: str, observed_at: str, content_hash: str) -> str:
    stamp = observed_at.replace("-", "").replace(":", "").replace("+00:00", "Z")
    return f"{chamber}_{stamp}_{content_hash[:10]}"


def fetch(url: str, path: Path, refresh: bool = False) -> tuple[str, str]:
    if path.exists() and not refresh:
        content = path.read_text(encoding="utf-8")
    else:
        response = requests.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        content = response.text
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return content, hashlib.sha256(content.encode("utf-8")).hexdigest()


def parse_diputados_group(html: str, party: str, source_url: str) -> pd.DataFrame:
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict[str, object]] = []
    for tr in soup.find_all("tr"):
        link = tr.find("a", href=re.compile(r"curricula\.php\?dipt=\d+"))
        cells = tr.find_all("td")
        if link is None or len(cells) < 3:
            continue
        source_id_match = re.search(r"dipt=(\d+)", link.get("href", ""))
        raw_name = re.sub(r"^\s*\d+\s+", "", link.get_text(" ", strip=True))
        on_leave = "LICENCIA" in raw_name.upper()
        name = _clean(re.sub(r"(?:\s*\(LICENCIA\)\s*)+", " ", raw_name, flags=re.I))
        location = _clean(cells[2].get_text(" ", strip=True))
        district_match = re.search(r"Dtto\.\s*(\d+)", location, re.I)
        circ_match = re.search(r"Circ\.\s*(\d+)", location, re.I)
        rows.append(
            {
                "member_source_id": source_id_match.group(1) if source_id_match else None,
                "current_name": name,
                "current_party": party,
                "state": _clean(cells[1].get_text(" ", strip=True)),
                "district": int(district_match.group(1)) if district_match else None,
                "circunscripcion": int(circ_match.group(1)) if circ_match else None,
                "status": "licencia" if on_leave else "en_funciones",
                "source_url": source_url,
                "profile_url": urljoin(DIP_BASE_URL, link.get("href", "")),
            }
        )
    return pd.DataFrame(rows)


def parse_senadores(html: str, source_url: str = SEN_URL) -> pd.DataFrame:
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict[str, object]] = []
    for card in soup.select("div.perfil-senador[data_id-senador]"):
        party_match = re.search(r"\bborder1([A-Z_]+)\b", " ".join(card.get("class", [])))
        name_link = card.select_one("h4.nombre-sen a")
        if party_match is None or name_link is None:
            continue
        profile_url = urljoin(source_url, name_link.get("href", ""))
        name = re.sub(r"^\s*Sen\.\s*", "", name_link.get_text(" ", strip=True), flags=re.I)
        state_node = card.select_one("span.estado")
        rows.append(
            {
                "member_source_id": str(card["data_id-senador"]),
                "current_name": _clean(name),
                "current_party": party_match.group(1).rstrip("_"),
                "state": _clean(state_node.get_text(" ", strip=True)) if state_node else None,
                "district": None,
                "circunscripcion": None,
                "status": "en_funciones",
                "source_url": source_url,
                "profile_url": profile_url,
            }
        )
    return pd.DataFrame(rows)


def crawl(refresh: bool = False) -> None:
    observed_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    dip_frames: list[pd.DataFrame] = []
    dip_hashes: list[str] = []
    for party, group_id in DIP_GROUPS.items():
        url = f"{DIP_BASE_URL}listado_diputados_gpnp.php?tipot={group_id}"
        html, content_hash = fetch(url, RAW_DIR / f"diputados_{party}.html", refresh)
        parsed = parse_diputados_group(html, party, url)
        if parsed.empty:
            raise ValueError(f"No deputy rows parsed for {party} from {url}")
        dip_frames.append(parsed)
        dip_hashes.append(content_hash)
        print(f"{party}: {len(parsed)} current directory rows")

    # Building from records avoids pandas' deprecated dtype inference for a
    # group whose district or circunscripcion column happens to be all-null.
    diputados = pd.DataFrame.from_records(
        [record for frame in dip_frames for record in frame.to_dict("records")],
        columns=dip_frames[0].columns,
    )
    dip_combined_hash = hashlib.sha256("".join(dip_hashes).encode()).hexdigest()
    diputados.insert(0, "snapshot_id", _snapshot_id("DIP", observed_at, dip_combined_hash))
    diputados.insert(1, "observed_at", observed_at)
    diputados.insert(2, "source_sha256", dip_combined_hash)
    if len(diputados) != 500 or not diputados["member_source_id"].is_unique:
        raise ValueError(
            f"Official Camara directory must yield 500 unique profiles; got {len(diputados)}"
        )

    sen_html, sen_hash = fetch(SEN_URL, RAW_DIR / "senadores.html", refresh)
    senadores = parse_senadores(sen_html)
    senadores.insert(0, "snapshot_id", _snapshot_id("SEN", observed_at, sen_hash))
    senadores.insert(1, "observed_at", observed_at)
    senadores.insert(2, "source_sha256", sen_hash)
    # The constitutional chamber has 128 seats, but the official page is
    # explicitly a directory of members "en funciones" and can therefore be
    # below 128 while a seat is vacant or a substitute is pending protest.
    if not 120 <= len(senadores) <= 128 or not senadores["member_source_id"].is_unique:
        raise ValueError(
            "Official Senate in-office directory must yield 120-128 unique "
            f"profiles; got {len(senadores)}"
        )

    diputados.to_csv(OUT_DIR / "diputados_current.csv", index=False)
    senadores.to_csv(OUT_DIR / "senadores_current.csv", index=False)
    print(f"Wrote Camara snapshot: {len(diputados)} rows")
    print(f"Wrote Senate snapshot: {len(senadores)} rows")


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch current official Congreso rosters")
    parser.add_argument("--refresh", action="store_true", help="Replace cached roster HTML")
    args = parser.parse_args()
    crawl(refresh=args.refresh)


if __name__ == "__main__":
    main()
