"""Export federal vote-intention polls and pollster accuracy for the web.

Writes two files to web/public/data/:

    vote-intention.json     every "encuesta" poll of every cycle, with each
                            option as published and on the effective basis
                            (undecided removed), plus the links needed to
                            check it: the poll's own source and the pinned
                            Wikipedia revision it was read from
    pollster-accuracy.json  the accuracy analysis of
                            vote_intention/analysis/pollster_accuracy.R —
                            firm scorecard, industry error per cycle, house
                            effects by bloc and every house's last poll

The accuracy section mirrors the R script step for step (same filters, same
benchmark, same per-cycle weighting) so the site and the RStudio analysis
agree. It departs in two places where the R summarise() reuses a column it
has just overwritten; see firm_cycle() and house_summary().

Run after vote_intention/ingest.py:
    /usr/bin/python3 web/scripts/export_vote_intention.py
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "election_data.db"
QUERIES = ROOT / "vote_intention" / "analysis" / "queries"
OUT_DIR = ROOT / "web" / "public" / "data"
POLLS_PATH = OUT_DIR / "vote-intention.json"
ACCURACY_PATH = OUT_DIR / "pollster-accuracy.json"

CURRENT_CYCLE = "DIP_MR_2027"

# Same parameters as pollster_accuracy.R
WINDOW_DAYS = 30        # "final polls": reference date this close to election day
TRAJECTORY_DAYS = 365   # how far back poll_bloque goes
MIN_ELECTIONS = 2       # cycles a firm needs to enter the scorecard

BLOCS = ["Izquierda", "PAN", "PRI", "Otros"]

CANDIDATO_BLOQUE = {
    "Ernesto Zedillo": "PRI", "Francisco Labastida": "PRI", "Roberto Madrazo": "PRI",
    "Enrique Peña Nieto": "PRI", "José Antonio Meade": "PRI",
    "Diego Fernández de Cevallos": "PAN", "Vicente Fox": "PAN", "Felipe Calderón": "PAN",
    "Josefina Vázquez Mota": "PAN", "Ricardo Anaya": "PAN",
    "Xóchitl Gálvez": "PAN",   # PAN-PRI-PRD, PAN-nominated
    "Cuauhtémoc Cárdenas": "Izquierda", "Andrés Manuel López Obrador": "Izquierda",
    "Claudia Sheinbaum": "Izquierda",
}

# A table column for every option that shows up in at least this share of a
# cycle's polls; rarer ones (hypothetical candidates, one-off coalitions) are
# listed together in the row.
COLUMN_MIN_SHARE = 0.2


def cycle_label(election_id: str) -> str:
    year = re.search(r"\d{4}", election_id).group()
    return f"{'Presidencial' if election_id.startswith('PRE') else 'Diputados'} {year}"


def clean(value):
    """JSON-safe scalar: NaN -> None, numpy -> python, floats rounded."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float):
        if math.isnan(value):
            return None
        return round(value, 2)
    return value


# ── read ──────────────────────────────────────────────────────────────────────

def read(conn: sqlite3.Connection) -> dict[str, pd.DataFrame]:
    polls = pd.read_sql_query(
        """SELECT p.poll_id, p.election_id, p.election_date, p.reference_date,
                  p.reference_date_type, p.date_precision, p.days_before_election,
                  p.fieldwork_start, p.fieldwork_end, p.published_date,
                  p.poll_kind, p.intention_level, p.phase, p.share_basis,
                  d.pollster_name AS pollster, d.familia, p.cliente, p.sample_size,
                  p.poll_source_url, s.source_kind, s.source_ref
           FROM fact_vote_intention_poll p
           JOIN dim_vote_intention_pollster d USING (pollster_id)
           JOIN dim_vote_intention_source s USING (source_id)""",
        conn,
    )
    options = pd.read_sql_query(
        """SELECT poll_id, option_order, opcion, option_kind, candidato,
                  partido_coalicion, party_keys, pct
           FROM fact_vote_intention_option""",
        conn,
    )
    view = pd.read_sql_query((QUERIES / "poll_options.sql").read_text(encoding="utf-8"), conn)
    effective = pd.read_sql_query(
        "SELECT poll_id, option_order, pct_efectiva FROM view_vote_intention_vs_result", conn
    )
    # Slow: aggregates fact_casilla_vote for every election with polls
    party_votes = pd.read_sql_query(
        (QUERIES / "national_party_votes.sql").read_text(encoding="utf-8"), conn
    )
    return {"polls": polls, "options": options, "view": view, "effective": effective,
            "party_votes": party_votes}


# ── accuracy (pollster_accuracy.R) ────────────────────────────────────────────

def benchmark(view: pd.DataFrame, party_votes: pd.DataFrame) -> pd.DataFrame:
    """Each option's share of the valid vote, joint coalition marks split
    equally among the parties marked (R: "Benchmark")."""
    key_members = party_votes.assign(member=party_votes["members"].str.split(",")).explode("member")
    key_members["n_members"] = key_members.groupby(["election_id", "party_key"])["member"].transform("size")

    option_keys = view.dropna(subset=["party_keys"])[["election_id", "party_keys"]].drop_duplicates()
    option_keys = option_keys.assign(party_key=option_keys["party_keys"].str.split("|")).explode("party_key")
    option_members = option_keys.merge(
        key_members[["election_id", "party_key", "member"]].drop_duplicates(),
        on=["election_id", "party_key"],
    )[["election_id", "party_keys", "member"]].drop_duplicates()

    joined = option_members.merge(
        key_members[["election_id", "member", "votes", "n_members", "valid_votes"]],
        on=["election_id", "member"],
    )
    joined["share"] = joined["votes"] / joined["n_members"]
    out = joined.groupby(["election_id", "party_keys"], as_index=False).agg(
        votes=("share", "sum"), valid_votes=("valid_votes", "first")
    )
    out["result"] = 100 * out["votes"] / out["valid_votes"]

    # Candidates own every key they are credited with, so splitting must
    # leave their result where the warehouse view has it (R: stopifnot)
    check = (
        view[(view["option_kind"] == "candidato") & view["party_keys"].notna()]
        [["election_id", "party_keys", "result_pct_valid"]].drop_duplicates()
        .merge(out, on=["election_id", "party_keys"])
    )
    assert (check["result"] - check["result_pct_valid"]).abs().max() < 0.01
    return out[["election_id", "party_keys", "result"]]


def bloque(row) -> str:
    if isinstance(row["candidato"], str) and row["candidato"]:
        return CANDIDATO_BLOQUE.get(row["candidato"], "Otros")
    partido = row["partido_coalicion"] or ""
    if partido in ("PRI", "PAN"):
        return partido
    if partido == "PAN-PRD-MC":          # 2017 "probable coalition" polls
        return "PAN"
    if partido == "PRI-PVEM-NA":
        return "PRI"
    if partido in ("MORENA-PT", "MORENA", "PT") or "Salvemos" in partido:
        return "Izquierda"
    if partido == "PRD" and row["year"] < 2018:   # PRD ran with PAN-PRI from 2018
        return "Izquierda"
    return "Otros"


def clean_options(view: pd.DataFrame, bench: pd.DataFrame) -> pd.DataFrame:
    """R: "Clean" — one row per ballot option, with its error."""
    keep = (
        (view["poll_kind"] == "encuesta")
        & (view["share_basis"] != "inconsistente")
        & view["party_keys"].notna()
        & view["pct_efectiva"].notna()
        & (
            (view["intention_level"] != "partido")
            | view["election_id"].str.startswith("DIP")
            | (view["election_id"] == "PRE_1994")
        )
    )
    options = view[keep].merge(bench, on=["election_id", "party_keys"])
    options["firm"] = options["familia"]
    options["year"] = options["election_id"].str.extract(r"(\d{4})")[0].astype(int)
    options["ciclo"] = options["election_id"].map(cycle_label)
    options["bloque"] = options.apply(bloque, axis=1)
    options["error"] = options["pct_efectiva"] - options["result"]
    return options


POLL_KEYS = ["election_id", "ciclo", "year", "poll_id", "firm", "pollster",
             "reference_date", "days_before_election", "sample_size"]


def poll_bloque(options: pd.DataFrame) -> pd.DataFrame:
    """Bloc totals per poll: a bloc can be two options (PRD + MORENA in 2015)."""
    recent = options[options["days_before_election"] <= TRAJECTORY_DAYS]
    out = recent.groupby(POLL_KEYS + ["intention_level", "bloque"], as_index=False, dropna=False).agg(
        estimate=("pct_efectiva", "sum"), result=("result", "sum")
    )
    out["error"] = out["estimate"] - out["result"]
    return out


def poll_metrics(final: pd.DataFrame) -> pd.DataFrame:
    """Per poll: average miss across options and miss on the lead."""
    top_two = (
        final[["election_id", "party_keys", "result"]].drop_duplicates()
        .sort_values("result", ascending=False, kind="stable")
        .groupby("election_id").head(2)
    )
    top_two["rank"] = top_two.groupby("election_id").cumcount() + 1
    df = final.merge(top_two[["election_id", "party_keys", "rank"]],
                     on=["election_id", "party_keys"], how="left")

    def per_poll(g: pd.DataFrame) -> pd.Series:
        r1, r2 = g["rank"] == 1, g["rank"] == 2
        return pd.Series({
            "n_options": len(g),
            "mae": g["error"].abs().mean(),
            "has_top_two": int(r1.sum() + r2.sum()) == 2,
            "margin_poll": g.loc[r1, "pct_efectiva"].sum() - g.loc[r2, "pct_efectiva"].sum(),
            "margin_real": g.loc[r1, "result"].sum() - g.loc[r2, "result"].sum(),
            "p1": g.loc[r1, "result"].sum() / 100,
            "p2": g.loc[r2, "result"].sum() / 100,
        })

    out = df.groupby(POLL_KEYS, dropna=False).apply(per_poll, include_groups=False).reset_index()
    out = out[out["has_top_two"].astype(bool)].copy()
    out["margin_error"] = out["margin_poll"] - out["margin_real"]   # > 0: overstated the lead
    out["called_winner"] = out["margin_poll"] > 0
    p1, p2 = out["p1"], out["p2"]
    out["se_margin"] = 100 * ((p1 + p2 - (p1 - p2) ** 2) / out["sample_size"]) ** 0.5
    return out


def firm_cycle(metrics: pd.DataFrame) -> pd.DataFrame:
    """Each cycle counts once per firm.

    sq_error is the mean of each poll's squared lead error, as the R comment
    intends. The R summarise() computes it after overwriting margin_error with
    its mean, so there it is the squared cycle mean instead.
    """
    g = metrics.groupby(["firm", "election_id", "ciclo", "year"])
    out = g.agg(
        n_polls=("poll_id", "size"),
        mae=("mae", "mean"),
        margin_error=("margin_error", "mean"),
        called_winner=("called_winner", "mean"),
    ).reset_index()
    out["sq_error"] = g["margin_error"].apply(lambda s: (s ** 2).mean()).values
    # NA when any poll lacks a sample size, as mean() without na.rm in R
    out["se_margin"] = g["se_margin"].apply(lambda s: math.sqrt((s ** 2).mean(skipna=False))).values
    return out


def firm_scorecard(fc: pd.DataFrame) -> pd.DataFrame:
    g = fc.groupby("firm")
    out = g.agg(
        n_cycles=("election_id", "size"),
        n_polls=("n_polls", "sum"),
        mae_se=("mae", lambda s: s.std() / math.sqrt(len(s))),
        mae=("mae", "mean"),
        margin_bias=("margin_error", "mean"),
        margin_bias_se=("margin_error", lambda s: s.std() / math.sqrt(len(s))),
        margin_rmse=("sq_error", lambda s: math.sqrt(s.mean())),
        sampling_se=("se_margin", lambda s: math.sqrt((s ** 2).mean())),
        called_winner=("called_winner", "mean"),
    ).reset_index()
    out["cycles"] = g["year"].apply(lambda s: sorted(s.tolist())).values
    out["error_ratio"] = out["margin_rmse"] / out["sampling_se"]
    return out.sort_values("mae")


def industry_and_house(pb: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Industry error per cycle and bloc, and each firm's house effect.

    house_summary's se is the spread of a firm's house effects across cycles.
    The R summarise() computes it after overwriting house_effect with its
    mean, which leaves it NA.
    """
    fcb = pb[pb["days_before_election"] <= WINDOW_DAYS].groupby(
        ["firm", "election_id", "ciclo", "year", "bloque"], as_index=False
    ).agg(n_polls=("poll_id", "size"), error=("error", "mean"), result=("result", "first"))

    industry = fcb.groupby(["election_id", "ciclo", "year", "bloque"], as_index=False).agg(
        n_firms=("firm", "size"),
        industry=("error", "mean"),
        se=("error", lambda s: s.std() / math.sqrt(len(s))),
        result=("result", "first"),
    )

    house = fcb.merge(industry[["election_id", "bloque", "industry", "n_firms"]],
                      on=["election_id", "bloque"])
    house = house[house["n_firms"] >= 2].copy()     # no house effect against oneself
    house["house_effect"] = house["error"] - house["industry"]

    summary = house.groupby(["firm", "bloque"], as_index=False).agg(
        n_cycles=("election_id", "size"),
        house_effect=("house_effect", "mean"),
        se=("house_effect", lambda s: s.std() / math.sqrt(len(s))),
        raw_bias=("error", "mean"),
    )
    return industry, house, summary


def last_polls(pb: pd.DataFrame, options: pd.DataFrame, election_id: str) -> dict:
    """R plot 9: every house's latest poll in the year before the election."""
    cycle = pb[pb["election_id"] == election_id]
    polls = (
        cycle[["pollster", "poll_id", "reference_date", "intention_level"]].drop_duplicates()
        .assign(not_candidate=lambda d: d["intention_level"] != "candidato")
        .sort_values(["pollster", "reference_date", "not_candidate", "poll_id"],
                     ascending=[True, False, True, False])
        .groupby("pollster").head(1)
    )
    shown = cycle[cycle["poll_id"].isin(polls["poll_id"])]

    # A bloc's result can differ slightly between polls that list different
    # options; the panel line uses the most common one
    results = (
        shown.groupby(["bloque", "result"]).size().reset_index(name="n")
        .sort_values("n", ascending=False, kind="stable").groupby("bloque").head(1)
    )
    nominees = (
        options[(options["election_id"] == election_id) & options["candidato"].notna()]
        .groupby(["bloque", "candidato"]).size().reset_index(name="n")
        .sort_values("n", ascending=False, kind="stable").groupby("bloque").head(1)
    )
    blocs = results.merge(nominees[["bloque", "candidato"]], on="bloque", how="left") \
        .sort_values("result", ascending=False)

    rows = []
    for poll_id, g in shown.groupby("poll_id"):
        first = g.iloc[0]
        rows.append({
            "pollster": first["pollster"],
            "firm": first["firm"],
            "date": first["reference_date"],
            "daysBefore": clean(first["days_before_election"]),
            "level": first["intention_level"],
            "final": bool(first["days_before_election"] <= WINDOW_DAYS),
            "values": {
                r["bloque"]: [clean(r["estimate"]), clean(r["result"])] for _, r in g.iterrows()
            },
        })
    rows.sort(key=lambda r: r["pollster"])
    return {
        "blocs": [
            {"bloc": r["bloque"], "result": clean(r["result"]),
             "nominee": r["candidato"] if isinstance(r["candidato"], str) else None}
            for _, r in blocs.iterrows()
        ],
        "polls": rows,
    }


def accuracy(data: dict[str, pd.DataFrame]) -> tuple[dict, pd.DataFrame, dict[str, pd.DataFrame]]:
    bench = benchmark(data["view"], data["party_votes"])
    options = clean_options(data["view"], bench)
    final = options[options["days_before_election"] <= WINDOW_DAYS]
    pb = poll_bloque(options)
    metrics = poll_metrics(final)
    fc = firm_cycle(metrics)
    scorecard = firm_scorecard(fc)
    industry, _, house_summary = industry_and_house(pb)

    qualified = set(scorecard.loc[scorecard["n_cycles"] >= MIN_ELECTIONS, "firm"])
    cycles = (
        options[["election_id", "ciclo", "year", "election_date"]].drop_duplicates()
        .sort_values("year")
    )
    payload = {
        "schemaVersion": 1,
        "windowDays": WINDOW_DAYS,
        "minCycles": MIN_ELECTIONS,
        "blocs": BLOCS,
        "cycles": [
            {"id": r.election_id, "label": r.ciclo, "year": int(r.year), "date": r.election_date}
            for r in cycles.itertuples()
        ],
        "scorecard": [
            {
                "firm": r.firm, "cycles": r.cycles, "nCycles": int(r.n_cycles),
                "nPolls": int(r.n_polls), "mae": clean(r.mae), "maeSe": clean(r.mae_se),
                "leadBias": clean(r.margin_bias), "leadBiasSe": clean(r.margin_bias_se),
                "leadRmse": clean(r.margin_rmse), "samplingSe": clean(r.sampling_se),
                "calledWinner": clean(r.called_winner),
            }
            for r in scorecard.itertuples() if r.firm in qualified
        ],
        "industry": [
            {
                "cycle": r.election_id, "label": r.ciclo, "year": int(r.year), "bloc": r.bloque,
                "nFirms": int(r.n_firms), "error": clean(r.industry), "se": clean(r.se),
                "result": clean(r.result),
            }
            for r in industry.sort_values(["year", "bloque"]).itertuples()
        ],
        "houseEffects": [
            {
                "firm": r.firm, "bloc": r.bloque, "nCycles": int(r.n_cycles),
                "effect": clean(r.house_effect), "se": clean(r.se), "rawBias": clean(r.raw_bias),
            }
            for r in house_summary.itertuples() if r.firm in qualified and r.bloque != "Otros"
        ],
        "lastPolls": {
            cycle_id: last_polls(pb, options, cycle_id) for cycle_id in cycles["election_id"]
        },
    }
    return payload, bench, {"scorecard": scorecard, "industry": industry,
                            "house_summary": house_summary, "metrics": metrics, "firm_cycle": fc}


# ── polls for the tracker and the table ───────────────────────────────────────

def option_label(row) -> str:
    if isinstance(row["candidato"], str) and row["candidato"]:
        return row["candidato"]
    return row["partido_coalicion"] or row["opcion"]


def polls_payload(data: dict[str, pd.DataFrame], bench: pd.DataFrame) -> dict:
    polls = data["polls"][data["polls"]["poll_kind"] == "encuesta"].copy()
    options = data["options"].merge(data["effective"], on=["poll_id", "option_order"], how="left")
    options = options[options["poll_id"].isin(polls["poll_id"])]
    options["label"] = options.apply(option_label, axis=1)
    options = options.merge(
        polls[["poll_id", "election_id"]], on="poll_id"
    ).merge(bench, on=["election_id", "party_keys"], how="left")

    ballot = options[options["option_kind"].isin(["candidato", "coalicion", "partido"])]
    residual = options[~options["option_kind"].isin(["candidato", "coalicion", "partido"])]

    cycles = []
    for election_id, g in polls.groupby("election_id"):
        b = ballot[(ballot["election_id"] == election_id) & ballot["pct"].notna()]
        n_polls = g["poll_id"].nunique()
        freq = b.groupby("label")["poll_id"].nunique()
        results = b.dropna(subset=["result"]).groupby("label")["result"].first()
        mean_eff = b.groupby("label")["pct_efectiva"].mean()
        # The current cycle shows every party: one registered mid-cycle (PAZ,
        # SOMOS) is in few polls so far but belongs in the tracker
        min_polls = 0 if election_id == CURRENT_CYCLE else COLUMN_MIN_SHARE * n_polls
        columns = [label for label in freq.index if freq[label] >= min_polls]
        columns.sort(key=lambda label: -(results.get(label, mean_eff.get(label, 0)) or 0))
        sources = (
            g[g["source_kind"] != "prensa"][["source_kind", "source_ref"]].drop_duplicates()
        )
        cycles.append({
            "id": election_id,
            "label": cycle_label(election_id),
            "date": g["election_date"].iloc[0],
            "held": election_id != CURRENT_CYCLE,
            "columns": columns,
            "results": {label: clean(results[label]) for label in columns if label in results}
            or None,
            "sources": [
                {"lang": r.source_kind.split(".")[0], "url": r.source_ref}
                for r in sources.itertuples()
            ],
        })
    cycles.sort(key=lambda c: c["date"], reverse=True)

    by_poll_ballot = {k: v for k, v in ballot.groupby("poll_id")}
    by_poll_residual = {k: v for k, v in residual.groupby("poll_id")}
    rows = []
    for p in polls.sort_values(["reference_date", "poll_id"], ascending=[False, True]).itertuples():
        b = by_poll_ballot.get(p.poll_id, pd.DataFrame(columns=ballot.columns)).sort_values("option_order")
        r = by_poll_residual.get(p.poll_id, pd.DataFrame(columns=residual.columns))
        residual_sum = lambda kinds: clean(r.loc[r["option_kind"].isin(kinds), "pct"].sum(min_count=1))
        rows.append({
            "id": p.poll_id,
            "cycle": p.election_id,
            "firm": p.familia,
            "pollster": p.pollster,
            "client": p.cliente,
            "fieldStart": p.fieldwork_start,
            "fieldEnd": p.fieldwork_end,
            "date": p.reference_date,
            "dateType": p.reference_date_type,
            "precision": p.date_precision,
            "daysBefore": clean(p.days_before_election),
            "n": None if pd.isna(p.sample_size) else int(p.sample_size),
            "basis": p.share_basis,
            "level": p.intention_level,
            "phase": p.phase,
            "url": p.poll_source_url,
            "wiki": p.source_ref if p.source_kind != "prensa" else None,
            "options": [
                [o.label, clean(o.pct), clean(o.pct_efectiva)]
                for o in b.itertuples() if o.pct == o.pct   # skip "did not exist"
            ],
            "others": residual_sum(["otros"]),
            "undecided": residual_sum(["indecisos", "otros_indecisos"]),
            "none": residual_sum(["ninguno", "nulo_blanco"]),
        })

    current = polls[polls["election_id"] == CURRENT_CYCLE]
    return {
        "schemaVersion": 1,
        "current": CURRENT_CYCLE,
        "sourceThrough": current["reference_date"].max(),
        "cycles": cycles,
        "polls": rows,
    }


def rename_firms(polls_df: pd.DataFrame, polls: dict, accuracy_payload: dict) -> None:
    """Show a family under its one firm's own name, in place.

    familia spellings follow dim_approval_pollster ("Consulta", "Parametria",
    "Varela y Asoc"). A family that groups several firms keeps that name
    ("Buendía (Laredo / Márquez)"); one that is a single firm is shown as the
    firm ("Consulta Mitofsky"), as the table's pollster column spells it.
    """
    members = polls_df.groupby("familia")["pollster"].unique()
    names = {familia: firms[0] if len(firms) == 1 else familia for familia, firms in members.items()}
    for row in polls["polls"]:
        row["firm"] = names.get(row["firm"], row["firm"])
    for key in ("scorecard", "houseEffects"):
        for row in accuracy_payload[key]:
            row["firm"] = names.get(row["firm"], row["firm"])
    for detail in accuracy_payload["lastPolls"].values():
        for row in detail["polls"]:
            row["firm"] = names.get(row["firm"], row["firm"])


def write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {path} ({path.stat().st_size / 1024:.0f} KB)")


def export() -> dict:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=600)
    try:
        data = read(conn)
    finally:
        conn.close()
    accuracy_payload, bench, frames = accuracy(data)
    polls = polls_payload(data, bench)
    rename_firms(data["polls"], polls, accuracy_payload)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    write(POLLS_PATH, polls)
    write(ACCURACY_PATH, accuracy_payload)
    current = [p for p in polls["polls"] if p["cycle"] == CURRENT_CYCLE]
    print(f"  {len(polls['polls'])} polls ({len(current)} for {CURRENT_CYCLE}, "
          f"through {polls['sourceThrough']}); "
          f"{len(accuracy_payload['scorecard'])} firms in the scorecard")
    return frames


if __name__ == "__main__":
    export()
