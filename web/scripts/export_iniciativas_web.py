"""Export initiative-proposer data for the public website.

Reads dim_gaceta_iniciativa and dim_senado_iniciativa -- who proposed each
initiative, separate from the roll-call vote data in legislature-66.json /
senate-66.json. party is already canonicalized at ingest time
(ingestion.congress_roster_ingest.canonical_party_from_text); this script
only reshapes and writes JSON, it does not re-derive anything.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "election_data.db"
OUT_PATH = ROOT / "web" / "public" / "data" / "iniciativas.json"


def _chamber_payload(df: pd.DataFrame) -> dict:
    df = df.where(pd.notna(df), None)
    by_party = (
        df[df["proposerType"] == "legislador"]["proposerParty"]
        .dropna()
        .value_counts()
        .rename_axis("party")
        .reset_index(name="count")
        .sort_values("count", ascending=False)
    )
    return {
        "total": len(df),
        "needsReviewShare": round(float(df["needsReview"].mean()), 4) if len(df) else 0.0,
        "byParty": by_party.to_dict("records"),
        "items": df.to_dict("records"),
    }


def export() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        dip = pd.read_sql_query(
            """
            SELECT
                i.gaceta_iniciativa_id AS id, i.title,
                i.proposer_type AS proposerType, i.proposer_name AS proposerName,
                i.proposer_party_canonical AS proposerParty, i.comision,
                i.gaceta_date AS date, i.needs_review AS needsReview,
                v.gaceta_vote_id AS voteId, i.vote_url AS voteUrl
            FROM dim_gaceta_iniciativa AS i
            LEFT JOIN dim_gaceta_vote AS v ON v.source_url = i.vote_url
            WHERE i.legislature = 66
            ORDER BY i.sequence_number
            """,
            conn,
        )
        sen = pd.read_sql_query(
            """
            SELECT
                senado_iniciativa_id AS id, title,
                proposer_type AS proposerType, proposer_name AS proposerName,
                proposer_party_canonical AS proposerParty, comision,
                fecha AS date, needs_review AS needsReview,
                source_url AS sourceUrl
            FROM dim_senado_iniciativa
            ORDER BY fecha DESC, senado_iniciativa_id DESC
            """,
            conn,
        )
    finally:
        conn.close()

    dip["needsReview"] = dip["needsReview"].astype(bool)
    sen["needsReview"] = sen["needsReview"].astype(bool)

    payload = {
        "schemaVersion": 1,
        "diputados": _chamber_payload(dip),
        "senado": _chamber_payload(sen),
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(
        f"Wrote {OUT_PATH} ({OUT_PATH.stat().st_size / 1024:.0f} KB): "
        f"{len(dip):,} Diputados, {len(sen):,} Senado"
    )


if __name__ == "__main__":
    export()
