"""One-shot refresh of everything MIEL (the legislative tracker) depends on:
current Diputados/Senado rosters, and Diputados/Senado roll-call votes.

Chains the individually-documented crawl/parse/ingest steps from README.md
("Common Workflows") in the right order, using the current (highest) LXVI
legislature number for the Gaceta parse/ingest steps instead of a hardcoded
one, so this keeps working unchanged once a new legislature starts.

Usage:
    /usr/bin/python3 aux_scripts/update_legislative_tracker.py
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PYTHON = "/usr/bin/python3"
DB_PATH = ROOT / "election_data.db"
STATUS_PATH = ROOT / "documentation" / "legislative_tracker_status.md"
GACETA_CATALOG = ROOT / "data" / "clean_gaceta_votes" / "gaceta_vote_url_catalog.csv"


def run(*args: str) -> None:
    print(f"\n$ {' '.join(args)}")
    subprocess.run(args, cwd=ROOT, check=True)


def current_gaceta_legislature() -> int:
    catalog = pd.read_csv(GACETA_CATALOG)
    return int(catalog["legislature"].max())


def refresh_rosters() -> None:
    print("\n=== Rosters (Diputados + Senado composition) ===")
    run(PYTHON, "camara_de_diputados/composicion/crawl_diputados_roster.py", "--refresh")
    run(PYTHON, "camara_de_senadores/composicion/crawl_senadores_roster.py", "--refresh")
    run(PYTHON, "-m", "camara_de_diputados.composicion.ingest")
    run(PYTHON, "-m", "camara_de_senadores.composicion.ingest")
    run(PYTHON, "aux_scripts/build_hemicycle_cache.py")


def refresh_senado_votes() -> None:
    print("\n=== Senado roll-call votes ===")
    run(PYTHON, "camara_de_senadores/votos/crawl_senado_votes.py", "--all-votes")
    run(PYTHON, "-m", "camara_de_senadores.votos.ingest", "--force")


def refresh_gaceta_votes() -> int:
    print("\n=== Diputados (Gaceta) roll-call votes ===")
    # --max-vote-pages is generous on purpose: crawl_gaceta_metadata.py now
    # puts the current legislature's votes first in the catalog and only
    # force-refreshes that legislature's pages, so older legislatures are
    # cheap cache hits rather than real network requests.
    run(
        PYTHON,
        "camara_de_diputados/votos/crawl_gaceta_metadata.py",
        "--all-periods",
        "--fetch-vote-pages",
        "--max-vote-pages",
        "10000",
    )
    legislature = current_gaceta_legislature()
    print(f"Current legislature: {legislature}")
    run(
        PYTHON,
        "camara_de_diputados/votos/parse_gaceta_vote_batch.py",
        "--legislature",
        str(legislature),
        "--all",
        "--out-dir",
        f"data/gaceta_votes/clean/by_legislature/legislature_{legislature}",
    )
    run(PYTHON, "-m", "camara_de_diputados.votos.ingest", "--force")
    run(PYTHON, "-m", "camara_de_diputados.escanos.ingest")
    run(PYTHON, "camara_de_diputados/votos/materialize.py", "--force")
    return legislature


def write_status(current_legislature: int) -> None:
    conn = sqlite3.connect(DB_PATH)
    senado_votes, senado_max_date = conn.execute(
        "SELECT COUNT(*), MAX(vote_date) FROM dim_senado_vote"
    ).fetchone()
    gaceta_votes, gaceta_max_date = conn.execute(
        "SELECT COUNT(*), MAX(vote_date) FROM dim_gaceta_vote WHERE legislature = ?",
        (current_legislature,),
    ).fetchone()
    n_diputados = conn.execute("SELECT COUNT(*) FROM dim_diputados").fetchone()[0]
    n_senadores = conn.execute("SELECT COUNT(*) FROM dim_senadores").fetchone()[0]
    conn.close()

    STATUS_PATH.write_text(
        "# Legislative tracker (MIEL) — last update\n\n"
        f"Last refreshed: **{date.today().isoformat()}**, "
        f"via `aux_scripts/update_legislative_tracker.py`.\n\n"
        "| Source | Rows | Latest date |\n"
        "| --- | --- | --- |\n"
        f"| Senado roll-call votes | {senado_votes:,} | {senado_max_date} |\n"
        f"| Diputados roll-call votes (legislatura {current_legislature}) | {gaceta_votes:,} | {gaceta_max_date} |\n"
        f"| Diputados seats (`dim_diputados`) | {n_diputados:,} | — |\n"
        f"| Senadores seats (`dim_senadores`) | {n_senadores:,} | — |\n\n"
        "Re-run the update with:\n\n"
        "```bash\n"
        "/usr/bin/python3 aux_scripts/update_legislative_tracker.py\n"
        "```\n"
    )
    print(f"\nWrote status file -> {STATUS_PATH}")


def main() -> None:
    refresh_rosters()
    refresh_senado_votes()
    current_legislature = refresh_gaceta_votes()
    write_status(current_legislature)
    print("\nDone.")


if __name__ == "__main__":
    sys.exit(main())
