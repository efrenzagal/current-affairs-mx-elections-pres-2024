"""Export state/national presidential trajectories for the public web dashboard."""

from __future__ import annotations

import json
import sys
import unicodedata
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from ui.common import (  # noqa: E402
    CYCLE_BLOCS,
    IDEOLOGY_MAP,
    TIMESERIES_PATH,
    classify_ternary,
    split_coalition_votes_by_geo,
)
from ui.trajectory import (  # noqa: E402
    PRE_YEARS,
    _prep_ternary_df,
    aggregate_geo,
    load_raw_year,
    load_trajectory_data,
)


OUT_PATH = ROOT / "web" / "public" / "data" / "electoral-trajectory.json"
GEO_SOURCE = ROOT / "data" / "materialized" / "estados_processed.geojson"
GEO_OUT_PATH = ROOT / "web" / "public" / "data" / "electoral-states.geojson"

CONGRESSIONAL_YEARS = {
    "SEN": [2000, 2006, 2012, 2018, 2024],
    "DIP": [2000, 2006, 2012, 2015, 2018, 2021, 2024],
}
CONGRESSIONAL_SOURCE_PREFIX = {"SEN": "SEN_MR", "DIP": "DIP_MR"}
CONGRESSIONAL_BLOCS = {
    "A": {"label": "Izquierda · PRD/Morena", "color": "#8B0000"},
    "B": {"label": "Derecha · PAN", "color": "#1E90FF"},
    "C": {"label": "Otros partidos", "color": "#006847"},
}
# The triangle's third vertex is explicitly residual: parties outside the
# PRD/Morena and PAN traditions belong here without implying one ideology.
CONGRESSIONAL_IDEOLOGY = {
    **IDEOLOGY_MAP,
    "CONVERGENCIA": "C",
    "PAS": "C",
    "PSN": "C",
    "PES": "C",
    "PH": "C",
    "FXM": "C",
    "RSP": "C",
    "CI": "C",
    "CAND_IND_1": "C",
    "CAND_IND_2": "C",
}

STATE_ALIASES = {
    "COAHUILA": "COAHUILA DE ZARAGOZA",
    "MICHOACAN": "MICHOACAN DE OCAMPO",
    "VERACRUZ": "VERACRUZ DE IGNACIO DE LA LLAVE",
}


def normalize(value: str) -> str:
    plain = unicodedata.normalize("NFD", str(value))
    return " ".join("".join(char for char in plain if unicodedata.category(char) != "Mn").upper().split())


def number(value) -> int:
    return 0 if pd.isna(value) else int(round(float(value)))


def trajectory_rows(data: pd.DataFrame, state_id: int | None) -> list[dict]:
    prepared = _prep_ternary_df(aggregate_geo(data, state_id))
    return [
        {
            "year": int(row.year),
            "left": float(row.pct_L),
            "right": float(row.pct_R),
            "other": float(row.pct_C),
            "category": str(row.category).replace("Centro", "Otros"),
            "votes": number(row.total_votos),
        }
        for row in prepared.itertuples()
    ]


def election_result(raw: pd.DataFrame, year: int, state_id: int | None) -> dict:
    scoped = raw if state_id is None else raw[raw["id_estado"] == state_id]
    blocs = CYCLE_BLOCS[f"PRE_{year}"]
    by_raw_party = scoped.groupby("party_key")["votes"].sum()

    candidacies = []
    for key in ("A", "B", "C"):
        party_keys = [party for party, bloc in blocs["map"].items() if bloc == key]
        votes = number(by_raw_party.reindex(party_keys, fill_value=0).sum())
        if votes:
            candidacies.append(
                {
                    "key": key,
                    "label": blocs[key]["label"],
                    "color": blocs[key]["color"],
                    "votes": votes,
                }
            )

    # Mixed-coalition rows are allocated back to their member parties with the
    # same state-specific weights as the Streamlit trajectory. Cycles whose
    # source only reports an alliance total remain an indivisible alliance.
    split = split_coalition_votes_by_geo(
        scoped[["id_estado", "party_key", "votes"]],
        ["id_estado"],
    )
    parties = [
        {"key": str(row.party_key), "votes": number(row.votes)}
        for row in (
            split.groupby("party_key", as_index=False)["votes"]
            .sum()
            .sort_values("votes", ascending=False)
            .itertuples()
        )
        if number(row.votes)
    ]

    # Historical sources sometimes contain two separately reported rows whose
    # municipality labels differ only in casing/accents (for example AMECA and
    # Ameca). Their party votes are additive, so retain each source label here;
    # the normalized key is only for trajectory matching across years.
    units = scoped.drop_duplicates(["id_estado", "municipio"])
    total_votes = number(units["total_votos"].sum())
    nominal = number(units["lista_nominal_part"].sum(min_count=1))
    null_votes = number(units["num_votos_nulos"].sum())
    return {
        "totalVotes": total_votes,
        "nominalList": nominal,
        "nullVotes": null_votes,
        "turnout": round(total_votes / nominal * 100, 1) if nominal else None,
        "candidacies": candidacies,
        "parties": parties,
    }


def compact_party_label(party_key: str) -> str:
    if party_key.startswith("CAND_IND"):
        suffix = party_key.replace("CAND_IND", "").strip("_")
        return f"Independiente {suffix}" if suffix else "Independiente"
    if party_key == "CI":
        return "Independiente"
    return party_key.replace("_", " + ")


def congressional_bloc_totals(scoped: pd.DataFrame) -> dict[str, float]:
    parties = scoped[scoped["votes_split"].notna()].copy()
    parties["bloc"] = parties["party_key"].map(CONGRESSIONAL_IDEOLOGY)
    missing = sorted(parties.loc[parties["bloc"].isna(), "party_key"].unique())
    if missing:
        raise ValueError(f"Unclassified congressional party keys: {missing}")
    totals = parties.groupby("bloc")["votes_split"].sum()
    return {key: float(totals.get(bloc, 0)) for key, bloc in (("A", "L"), ("B", "R"), ("C", "C"))}


def congressional_trajectory_rows(
    contest: pd.DataFrame,
    years: list[int],
    state_id: int | None,
) -> list[dict]:
    rows = []
    for year in years:
        scoped = contest[contest["year"] == year]
        if state_id is not None:
            scoped = scoped[scoped["id_estado"] == state_id]
        totals = congressional_bloc_totals(scoped)
        total = sum(totals.values())
        left = totals["A"] / total * 100 if total else 0
        right = totals["B"] / total * 100 if total else 0
        other = totals["C"] / total * 100 if total else 0
        rows.append({
            "year": year,
            "left": round(left, 1),
            "right": round(right, 1),
            "other": round(other, 1),
            "category": classify_ternary(left, right, other).replace("Centro", "Otros"),
            "votes": number(total),
        })
    return rows


def congressional_election_result(
    scoped: pd.DataFrame,
    null_votes: int,
) -> dict:
    raw = (
        scoped.groupby("party_key", as_index=False)["votes_raw"]
        .sum()
        .sort_values("votes_raw", ascending=False)
    )
    split = (
        scoped[scoped["votes_split"].notna()]
        .groupby("party_key", as_index=False)["votes_split"]
        .sum()
        .sort_values("votes_split", ascending=False)
    )
    units = scoped.drop_duplicates("id_estado")
    total_votes = number(units["total_votos_estado"].sum())
    nominal = number(units["lista_nominal"].sum(min_count=1))
    return {
        "totalVotes": total_votes,
        "nominalList": nominal,
        "nullVotes": null_votes,
        "turnout": round(total_votes / nominal * 100, 1) if nominal else None,
        "candidacies": [
            {
                "key": str(row.party_key),
                "label": compact_party_label(str(row.party_key)),
                "votes": number(row.votes_raw),
            }
            for row in raw.itertuples()
            if number(row.votes_raw)
        ],
        "parties": [
            {
                "key": str(row.party_key),
                "label": compact_party_label(str(row.party_key)),
                "votes": number(row.votes_split),
            }
            for row in split.itertuples()
            if number(row.votes_split)
        ],
    }


def congressional_null_votes(contest_key: str, years: list[int]) -> dict[tuple[int, int], int]:
    values = {}
    prefix = CONGRESSIONAL_SOURCE_PREFIX[contest_key]
    for year in years:
        path = ROOT / "data" / "materialized" / f"view_estado_{prefix}_{year}.parquet"
        frame = pd.read_parquet(path, columns=["id_estado", "num_votos_nulos"])
        by_state = frame.groupby("id_estado")["num_votos_nulos"].max()
        for state_id, votes in by_state.items():
            values[(year, int(state_id))] = number(votes)
    return values


def build_congressional_contest(
    timeseries: pd.DataFrame,
    contest_key: str,
    states: list[dict],
) -> dict:
    years = CONGRESSIONAL_YEARS[contest_key]
    contest = timeseries[
        (timeseries["election_type"] == contest_key)
        & (timeseries["year"].isin(years))
    ].copy()
    nulls = congressional_null_votes(contest_key, years)
    cycles = {
        str(year): {key: dict(value) for key, value in CONGRESSIONAL_BLOCS.items()}
        for year in years
    }

    geographies = {}
    for geography_key, state_id, name in [
        ("national", None, "Nacional"),
        *((str(state["id"]), state["id"], state["name"]) for state in states),
    ]:
        geographies[geography_key] = {
            "name": name,
            "trajectory": congressional_trajectory_rows(contest, years, state_id),
            "elections": {},
        }
        for year in years:
            scoped = contest[contest["year"] == year]
            if state_id is not None:
                scoped = scoped[scoped["id_estado"] == state_id]
                null_votes = nulls[(year, state_id)]
            else:
                null_votes = sum(nulls[(year, state["id"])] for state in states)
            geographies[geography_key]["elections"][str(year)] = (
                congressional_election_result(scoped, null_votes)
            )

    maps = {}
    for year in years:
        rows = []
        for state in states:
            scoped = contest[
                (contest["year"] == year)
                & (contest["id_estado"] == state["id"])
            ]
            totals = congressional_bloc_totals(scoped)
            winner = max(totals, key=totals.get)
            total = sum(totals.values())
            rows.append({
                "stateId": state["id"],
                "winner": winner,
                "winnerLabel": CONGRESSIONAL_BLOCS[winner]["label"],
                "winnerPct": round(totals[winner] / total * 100, 1) if total else 0,
            })
        maps[str(year)] = rows

    return {
        "years": years,
        "cycles": cycles,
        "maps": maps,
        "geographies": geographies,
    }


def export() -> None:
    trajectory = load_trajectory_data()
    latest_states = (
        trajectory[["id_estado", "nombre_estado", "year"]]
        .dropna(subset=["id_estado", "nombre_estado"])
        .sort_values("year", ascending=False)
        .drop_duplicates("id_estado")
    )
    states = [
        {"id": int(row.id_estado), "name": str(row.nombre_estado).title()}
        for row in latest_states.itertuples()
    ]
    state_name_to_id = {normalize(state["name"]): state["id"] for state in states}

    raw_by_year = {year: load_raw_year(year) for year in PRE_YEARS}
    geographies = {
        "national": {
            "name": "Nacional",
            "trajectory": trajectory_rows(trajectory, None),
            "elections": {
                str(year): election_result(raw_by_year[year], year, None)
                for year in PRE_YEARS
            },
        }
    }
    for state in states:
        geographies[str(state["id"])] = {
            "name": state["name"],
            "trajectory": trajectory_rows(trajectory, state["id"]),
            "elections": {
                str(year): election_result(raw_by_year[year], year, state["id"])
                for year in PRE_YEARS
            },
        }

    maps = {}
    for year, raw in raw_by_year.items():
        blocs = CYCLE_BLOCS[f"PRE_{year}"]
        rows = []
        for state in states:
            result = geographies[str(state["id"])]["elections"][str(year)]
            if not result["candidacies"]:
                continue
            winner = max(result["candidacies"], key=lambda item: item["votes"])
            total = sum(item["votes"] for item in result["candidacies"])
            rows.append(
                {
                    "stateId": state["id"],
                    "winner": winner["key"],
                    "winnerLabel": winner["label"],
                    "winnerPct": round(winner["votes"] / total * 100, 1) if total else 0,
                }
            )
        maps[str(year)] = rows

    presidential = {
        "years": PRE_YEARS,
        "cycles": {
            str(year): {
                key: {
                    "label": CYCLE_BLOCS[f"PRE_{year}"][key]["label"],
                    "color": CYCLE_BLOCS[f"PRE_{year}"][key]["color"],
                }
                for key in ("A", "B", "C")
            }
            for year in PRE_YEARS
        },
        "maps": maps,
        "geographies": geographies,
    }
    timeseries = pd.read_parquet(TIMESERIES_PATH)
    payload = {
        "schemaVersion": 2,
        "states": states,
        "contests": {
            "PRE": presidential,
            "SEN": build_congressional_contest(timeseries, "SEN", states),
            "DIP": build_congressional_contest(timeseries, "DIP", states),
        },
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )

    geojson = json.loads(GEO_SOURCE.read_text(encoding="utf-8"))
    for feature in geojson["features"]:
        source_name = normalize(feature["properties"].get("name", ""))
        canonical_name = STATE_ALIASES.get(source_name, source_name)
        feature["properties"]["stateId"] = state_name_to_id.get(canonical_name)
    geojson["features"] = [
        feature for feature in geojson["features"]
        if feature["properties"].get("stateId") is not None
    ]
    GEO_OUT_PATH.write_text(
        json.dumps(geojson, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(f"Wrote {OUT_PATH} ({OUT_PATH.stat().st_size / 1024:.0f} KB)")
    print(f"Wrote {GEO_OUT_PATH} ({GEO_OUT_PATH.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    export()
