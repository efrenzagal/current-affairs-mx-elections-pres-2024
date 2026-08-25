"""Fetch the official current LXVI Camara de Diputados roster.

The Camara publishes one member list per parliamentary group. Raw HTML is
cached for auditability and the parsed snapshot is written as a CSV file
consumed by ``camara_de_diputados.composicion.ingest``.

Run from the repository root::

    python3 camara_de_diputados/composicion/crawl_diputados_roster.py
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

    diputados.to_csv(OUT_DIR / "diputados_current.csv", index=False)
    print(f"Wrote Camara snapshot: {len(diputados)} rows")


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch current official Camara de Diputados roster")
    parser.add_argument("--refresh", action="store_true", help="Replace cached roster HTML")
    args = parser.parse_args()
    crawl(refresh=args.refresh)


if __name__ == "__main__":
    main()
