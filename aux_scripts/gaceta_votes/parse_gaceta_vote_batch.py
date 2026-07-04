"""
Batch parser for Gaceta vote pages and deputy detail lists.

Reads data/clean_gaceta_votes/gaceta_vote_url_catalog.csv and writes
consolidated star-schema CSVs:
  - dim_gaceta_vote.csv
  - dim_gaceta_deputy.csv
  - fact_gaceta_vote_summary.csv
  - fact_gaceta_deputy_vote.csv

The default run is intentionally small. Use --all only after inspecting a
sample. Every GET and POST response is cached so reruns are cheap.

Examples:
    python3 aux_scripts/gaceta_votes/parse_gaceta_vote_batch.py --max-vote-pages 10
    python3 aux_scripts/gaceta_votes/parse_gaceta_vote_batch.py --all
    python3 aux_scripts/gaceta_votes/parse_gaceta_vote_batch.py \
      --legislature 58 --all --request-delay 0.25 \
      --out-dir data/clean_gaceta_votes_l58_delay025
"""

from __future__ import annotations

import argparse
import re
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
import requests

from parse_gaceta_vote import (
    deputy_id,
    detail_action_url,
    normalize_party,
    parse_detail,
    parse_summary,
)


CATALOG_PATH = Path("data/clean_gaceta_votes/gaceta_vote_url_catalog.csv")
OUT_DIR = Path("data/clean_gaceta_votes")
SUMMARY_CACHE_DIR = Path("data/raw_gaceta_votes")
DETAIL_CACHE_DIR = Path("data/raw_gaceta_vote_details")

DEFAULT_REQUEST_DELAY_SECONDS = 1.5
MAX_RETRIES_429 = 4
RETRY_BACKOFF_BASE_SECONDS = 20


@dataclass(frozen=True)
class FetchResult:
    text: str
    from_cache: bool


def safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._/-]+", "_", text).strip("_")


def summary_cache_path(url: str) -> Path:
    path = urlparse(url).path.strip("/")
    return SUMMARY_CACHE_DIR / safe_name(path)


def detail_cache_path(gaceta_vote_id: str, lola_key: str) -> Path:
    return DETAIL_CACHE_DIR / safe_name(gaceta_vote_id) / f"lola_{safe_name(str(lola_key))}.html"


def read_cache(path: Path) -> str | None:
    if not path.exists():
        return None
    if path.stat().st_size == 0:
        path.unlink()
        return None
    return path.read_text(encoding="utf-8")


def fetch_get_cached(url: str, session: requests.Session, request_delay: float) -> FetchResult:
    cache_path = summary_cache_path(url)
    cached = read_cache(cache_path)
    if cached is not None:
        return FetchResult(cached, True)

    for attempt in range(MAX_RETRIES_429 + 1):
        response = session.get(url, timeout=30)
        if response.status_code == 429:
            wait = RETRY_BACKOFF_BASE_SECONDS * (2 ** attempt)
            print(f"    429 rate limited on {url}, backing off {wait}s", flush=True)
            time.sleep(wait)
            continue
        response.raise_for_status()
        response.encoding = "latin-1"
        text = response.text
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(text, encoding="utf-8")
        time.sleep(request_delay)
        return FetchResult(text, False)
    raise RuntimeError(f"Gave up on {url} after {MAX_RETRIES_429} retries")


def fetch_post_cached(
    action_url: str,
    gaceta_vote_id: str,
    event: str | None,
    title: str,
    lola_key: str,
    count: int,
    session: requests.Session,
    request_delay: float,
) -> FetchResult:
    cache_path = detail_cache_path(gaceta_vote_id, lola_key)
    cached = read_cache(cache_path)
    if cached is not None:
        return FetchResult(cached, True)

    data = {"nomtit": title, f"lola[{lola_key}]": str(count)}
    if event is not None:
        data["evento"] = event

    for attempt in range(MAX_RETRIES_429 + 1):
        response = session.post(action_url, data=data, timeout=30)
        if response.status_code == 429:
            wait = RETRY_BACKOFF_BASE_SECONDS * (2 ** attempt)
            print(f"    429 rate limited on detail {gaceta_vote_id}/{lola_key}, backing off {wait}s", flush=True)
            time.sleep(wait)
            continue
        response.raise_for_status()
        response.encoding = "latin-1"
        text = response.text
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(text, encoding="utf-8")
        time.sleep(request_delay)
        return FetchResult(text, False)
    raise RuntimeError(f"Gave up on detail {gaceta_vote_id}/{lola_key} after {MAX_RETRIES_429} retries")


def parse_one_vote(row: pd.Series, session: requests.Session, request_delay: float) -> tuple[dict, list[dict], list[dict]]:
    vote_url = row["vote_url"]
    gaceta_vote_id = row["gaceta_vote_id"]
    summary_html = fetch_get_cached(vote_url, session, request_delay)
    title, event, links = parse_summary(summary_html.text)
    action_url = detail_action_url(vote_url, summary_html.text)

    dim_vote = {
        "gaceta_vote_id": gaceta_vote_id,
        "source_url": vote_url,
        "source_path": row["vote_path"],
        "table_slug": row["table_slug"],
        "legislature": row["legislature"],
        "period_url": row["period_url"],
        "chamber": "DIP",
        "title": title,
        "source_event": event,
        "detail_action_url": action_url,
        "vote_date": row.get("vote_date"),
        "vote_date_source": row.get("vote_date_source"),
        "gaceta_number": row.get("gaceta_number"),
        "gaceta_date": row.get("gaceta_date"),
        "status_text": row.get("status_text"),
        "vote_context": row.get("vote_context"),
        "summary_from_cache": summary_html.from_cache,
    }

    summary_rows = [
        {
            "gaceta_vote_id": gaceta_vote_id,
            "vote_choice": link.vote,
            "party_key": normalize_party(link.party),
            "count": link.count,
            "lola_key": link.lola_key,
        }
        for link in links
    ]

    deputy_rows: list[dict] = []
    for link in links:
        if link.party != "Total" or link.count == 0 or link.lola_key is None:
            continue
        detail_html = fetch_post_cached(
            action_url,
            gaceta_vote_id,
            event,
            title,
            link.lola_key,
            link.count,
            session,
            request_delay,
        )
        for parsed in parse_detail(detail_html.text, link.vote):
            deputy_rows.append(
                {
                    "gaceta_vote_id": gaceta_vote_id,
                    "deputy_id": deputy_id(parsed["deputy_name"]),
                    "deputy_name": parsed["deputy_name"],
                    "vote_choice": parsed["vote"],
                    "party_key": parsed["party"],
                    "ordinal": parsed["ordinal"],
                    "detail_from_cache": detail_html.from_cache,
                }
            )

    return dim_vote, summary_rows, deputy_rows


def select_catalog_rows(catalog: pd.DataFrame, args: argparse.Namespace) -> pd.DataFrame:
    rows = catalog.copy()
    if args.legislature is not None:
        rows = rows[rows["legislature"] == args.legislature]
    if args.start_after:
        matches = rows.index[rows["gaceta_vote_id"] == args.start_after].tolist()
        if matches:
            rows = rows.loc[matches[0] + 1:]
    if args.only_ids:
        wanted = {x.strip() for x in args.only_ids.split(",") if x.strip()}
        rows = rows[rows["gaceta_vote_id"].isin(wanted)]
    if not args.all and args.max_vote_pages is not None:
        rows = rows.head(args.max_vote_pages)
    return rows.reset_index(drop=True)


def write_outputs(
    dim_votes: list[dict],
    summary_rows: list[dict],
    deputy_rows: list[dict],
    out_dir: Path,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    dim_vote = pd.DataFrame(dim_votes)
    fact_summary = pd.DataFrame(summary_rows)
    fact_deputy = pd.DataFrame(deputy_rows)
    if fact_deputy.empty:
        dim_deputy = pd.DataFrame(columns=["deputy_id", "deputy_name"])
        fact_deputy = pd.DataFrame(
            columns=["gaceta_vote_id", "deputy_id", "vote_choice", "party_key", "ordinal", "detail_from_cache"]
        )
    else:
        dim_deputy = (
            fact_deputy[["deputy_id", "deputy_name"]]
            .drop_duplicates()
            .sort_values(["deputy_name", "deputy_id"])
            .reset_index(drop=True)
        )
        fact_deputy = fact_deputy[
            ["gaceta_vote_id", "deputy_id", "vote_choice", "party_key", "ordinal", "detail_from_cache"]
        ]

    outputs = {
        "dim_gaceta_vote.csv": dim_vote,
        "dim_gaceta_deputy.csv": dim_deputy,
        "fact_gaceta_vote_summary.csv": fact_summary,
        "fact_gaceta_deputy_vote.csv": fact_deputy,
    }
    for filename, df in outputs.items():
        path = out_dir / filename
        df.to_csv(path, index=False)
        print(f"Wrote {len(df):,} rows -> {path}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=CATALOG_PATH)
    parser.add_argument("--max-vote-pages", type=int, default=10)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--legislature", type=int)
    parser.add_argument("--start-after")
    parser.add_argument("--only-ids")
    parser.add_argument("--request-delay", type=float, default=DEFAULT_REQUEST_DELAY_SECONDS)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = parser.parse_args()

    catalog = pd.read_csv(args.catalog)
    selected = select_catalog_rows(catalog, args)
    print(f"Vote pages selected: {len(selected):,}", flush=True)
    print(f"Request delay for uncached requests: {args.request_delay}s", flush=True)
    print(f"Output directory: {args.out_dir}", flush=True)

    session = requests.Session()
    dim_votes: list[dict] = []
    summary_rows: list[dict] = []
    deputy_rows: list[dict] = []
    for i, row in selected.iterrows():
        print(f"  [{i + 1}/{len(selected)}] {row['gaceta_vote_id']} {row['vote_url']}", flush=True)
        dim_vote, summary, deputies = parse_one_vote(row, session, args.request_delay)
        dim_votes.append(dim_vote)
        summary_rows.extend(summary)
        deputy_rows.extend(deputies)

    write_outputs(dim_votes, summary_rows, deputy_rows, args.out_dir)


if __name__ == "__main__":
    main()
