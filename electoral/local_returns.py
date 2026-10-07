"""
Local election returns — governors and ayuntamientos — into SQLite
===================================================================
Loads Eric Magar's elecRetrns compilation (https://github.com/emagar/elecRetrns,
ITAM) into election_data.db. INE's casilla data in this DB only covers federal
races; this adds the state-level offices:

  goed1961-on.csv               governor returns, one row per state race
  goed1970-on.incumbents.csv    elected governors
  aymu1970-on.coalAgg.csv       ayuntamiento returns by municipio, coalition
                                votes aggregated by candidate
  aymu1970-on.coalSplit.csv     same races, coalition votes split among the
                                member parties where the source permits
  aymu1989-on.incumbents.csv    elected municipal presidents
  aymu1989-on.concurrence.csv   concurrence of each municipal race with
                                presidential / federal deputy / governor /
                                state assembly elections

Tables (rebuilt on every run):

  fact_local_race             one row per race (office = 'gobernador' or
                              'ayuntamiento'); turnout fields, winner,
                              concurrence flags
  fact_local_race_vote        one row per race x version x ballot label.
                              version = 'as_reported' (governors),
                              'coal_agg' or 'coal_split' (ayuntamientos)
  fact_local_race_vote_party  the parties inside each label ('pt-pvem-morena'
                              -> pt, pvem, morena), for building blocs later
  fact_local_incumbent        elected governors and municipal presidents

Label conventions differ by file. Governor rows list party-only ballots and
joint coalition ballots separately (pan, pri, prd and pan-pri-prd), so a
coalition candidate's total is the sum over every label whose parties belong
to the coalition. coal_agg rows already carry one total per candidate.
Labels are lowercased; Magar's spelling is otherwise kept (independents
appear as names or indep_*).

efec is the source's valid-vote total; it is blank for about half the
governor races, so efec_calc (sum of labelled votes, write-ins and nulos
excluded) is the denominator to use.

elecRetrns lists Edomex 2023 and the nine 2024 governor races without votes.
Those come from data/local_returns_supplement.csv: statewide official totals
transcribed from Spanish Wikipedia's results tables (each cites the state
OPLE's cómputo; page revision in the source column), checked so parties add
up to each candidate and to the total, and against same-day 2024
presidential turnout. Their labels are single parties (coalition votes
already distributed), unlike Magar's ballot-level governor rows.

Usage:
    python -m electoral.local_returns            # download (if missing) + load
    python -m electoral.local_returns --refresh  # re-download at the latest commit
"""

import argparse
import csv
import json
import re
import sqlite3
import urllib.request
from pathlib import Path

from electoral.states import DB_PATH

RAW_DIR = Path("data/electoral_data_raw/elecRetrns")
SUPPLEMENT = Path("data/local_returns_supplement.csv")   # hand-entered, see fill_from_supplement
REPO = "emagar/elecRetrns"
FILES = [
    "goed1961-on.csv",
    "goed1970-on.incumbents.csv",
    "aymu1970-on.coalAgg.csv",
    "aymu1970-on.coalSplit.csv",
    "aymu1989-on.incumbents.csv",
    "aymu1989-on.concurrence.csv",
]
MESES = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
         "jul": 7, "ago": 8, "sep": 9, "oct": 10, "nov": 11, "dic": 12}


# ── Download ───────────────────────────────────────────────────────────────────
def download(refresh: bool) -> str:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    commit_file = RAW_DIR / "SOURCE_COMMIT"
    if not refresh and commit_file.exists() and all((RAW_DIR / f).exists() for f in FILES):
        return commit_file.read_text().strip()
    with urllib.request.urlopen(f"https://api.github.com/repos/{REPO}/commits/master") as r:
        sha = json.load(r)["sha"]
    for f in FILES:
        url = f"https://raw.githubusercontent.com/{REPO}/{sha}/data/{f}"
        urllib.request.urlretrieve(url, RAW_DIR / f)
        print(f"  downloaded {f}")
    commit_file.write_text(sha + "\n")
    return sha


# ── Parsing helpers ────────────────────────────────────────────────────────────
def read(name: str) -> list[dict]:
    return read_path(RAW_DIR / name)


def read_path(path: Path) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def num(s):
    """'' / 'NA' / '?' / '.' (unknown) -> None; integral -> int; else float."""
    if s is None or s.strip() in ("", "NA", "?", "."):
        return None
    x = float(s)
    return int(x) if x.is_integer() else x


def text(s):
    s = (s or "").strip()
    return None if s in ("", "NA") else s


def cvegeo(inegi):
    return str(int(float(inegi))).zfill(5) if text(inegi) else None


def iso_date(raw: str):
    """Magar's YYYYMMDD (or YYYYMM / YYYY when the day is unknown) -> ISO."""
    raw = (raw or "").strip()
    if not re.fullmatch(r"\d{4}(\d{2}(\d{2})?)?", raw):
        return None
    return "-".join(p for p in (raw[:4], raw[4:6], raw[6:8]) if p)


def vote_rows(rec: dict, race_id: str, version: str, n_max: int):
    """Yield (race_id, version, position, label_raw, label, votes) per labelled ballot."""
    for i in range(1, n_max + 1):
        label = text(rec.get(f"l{i:02d}"))
        if label is None or label == "0":
            continue
        yield (race_id, version, i, label, label.lower(), num(rec.get(f"v{i:02d}")) or 0)


def n_slots(rows: list[dict]) -> int:
    return max(int(k[1:]) for k in rows[0] if re.fullmatch(r"l\d+", k))


# ── Supplement: governor races Magar lists without votes ──────────────────────
def fill_from_supplement(races: list[dict], votes: list[tuple]):
    """Fill placeholder governor races from SUPPLEMENT (statewide totals).

    One row per race x party, plus _nr / _nulos / _nr_nulos (the source
    reports them combined) / _lisnom. Coalition votes are already distributed
    to the member parties, so every label is a single party. A race is only
    filled while Magar's own row has no votes; once his file covers it, his
    data wins and the supplement rows are ignored.
    """
    rows = read_path(SUPPLEMENT)
    by_race = {}
    for r in rows:
        by_race.setdefault(r["race_id"], []).append(r)
    race_index = {r["race_id"]: r for r in races}
    for race_id, rs in by_race.items():
        race = race_index[race_id]
        if race["efec_calc"]:
            print(f"  supplement skipped for {race_id}: elecRetrns now has votes")
            continue
        extra = {r["label"]: int(r["votes"]) for r in rs if r["label"].startswith("_")}
        parties = [r for r in rs if not r["label"].startswith("_")]
        for pos, r in enumerate(parties, 1):
            votes.append((race_id, "as_reported", pos, r["label"], r["label"], int(r["votes"])))
        by_cand = {}
        for r in parties:
            by_cand.setdefault(r["candidate"], []).append(r)
        winner = max(by_cand.values(), key=lambda ps: sum(int(p["votes"]) for p in ps))
        efec_calc = sum(int(r["votes"]) for r in parties)
        nr, nulos = extra.get("_nr"), extra.get("_nulos")
        nr_nulos = extra.get("_nr_nulos")
        race.update(
            efec_calc=efec_calc, nr=nr, nulos=nulos,
            vtot=efec_calc + (nr_nulos if nr_nulos is not None else (nr or 0) + (nulos or 0)),
            lisnom=extra.get("_lisnom"), ncand=len(by_cand),
            win="-".join(p["label"] for p in winner),
            fuente=rs[0]["source"],
            nota=("supplement: statewide totals, coalition votes distributed to parties"
                  + (f"; nulos + no registrados reported combined = {nr_nulos}" if nr_nulos is not None else "")),
        )


# ── Build ──────────────────────────────────────────────────────────────────────
def build(con: sqlite3.Connection, sha: str):
    races, votes, incumbents = [], [], []

    # Governors
    goed = read("goed1961-on.csv")
    gov_ids = {}
    for rec in goed:
        edon, yr = int(rec["edon"]), int(rec["yr"])
        race_id = f"{rec['edo']}-{yr}.gob"
        if race_id in gov_ids.values():          # Colima 2003: annulled + extraordinary
            race_id = f"{rec['edo']}-{yr}b.gob"
        gov_ids[rec["ord"]] = race_id
        mo, dy = MESES.get(rec["mo"]), num(rec["dy"])
        date = f"{yr}-{mo:02d}-{dy:02d}" if mo and dy else (f"{yr}-{mo:02d}" if mo else str(yr))
        rv = list(vote_rows(rec, race_id, "as_reported", n_slots(goed)))
        votes += rv
        races.append(dict(
            race_id=race_id, office="gobernador", id_estado=edon, edo=rec["edo"], yr=yr,
            election_date=date, cvegeo=None, ife_municipio=None, municipio=None,
            status=None, dextra=1 if race_id.endswith("b.gob") else 0,
            ncand=num(rec["ncand"]), ncoal=None, win=text(rec["win"]), mg=None,
            efec=num(rec["efec"]), efec_calc=sum(v[5] for v in rv),
            nr=num(rec["nr"]), nulos=num(rec["nulos"]), vtot=num(rec["vtot"]),
            lisnom=num(rec["lisnom"]),
            conc_pres=None, conc_dipf=None, conc_gob=1, conc_dipl=None,
            dfake=num(rec["dfake"]), fuente=text(rec["fuente"]),
            nota=text(rec["nota"]) if rec["nota"] != "0" else None,
        ))
    fill_from_supplement(races, votes)

    gov_by_state_yr = {}
    for rec in goed:
        gov_by_state_yr.setdefault((int(rec["edon"]), int(rec["yr"])), gov_ids[rec["ord"]])
    for rec in read("goed1970-on.incumbents.csv"):
        incumbents.append(dict(
            race_id=gov_by_state_yr.get((int(rec["edon"]), int(rec["yr"]))),
            office="gobernador", source_key=rec["emm"], id_estado=int(rec["edon"]),
            yr=int(rec["yr"]), cvegeo=None, part=text(rec["part"]),
            part_elec=text(rec["part.elec"]), incumbent=text(rec["incumbent"]),
            dmujer=None, race_after=None, runnerup=None, part_2nd=None, mg=None,
            date_in=iso_date(rec["date.in"]), notas=text(rec["observaciones"]), fuente=None,
        ))

    # Ayuntamientos
    conc = {r["emm"]: r for r in read("aymu1989-on.concurrence.csv")}
    agg = read("aymu1970-on.coalAgg.csv")
    for rec in agg:
        race_id = rec["emm"]
        rv = list(vote_rows(rec, race_id, "coal_agg", n_slots(agg)))
        votes += rv
        c = conc.get(race_id, {})
        races.append(dict(
            race_id=race_id, office="ayuntamiento", id_estado=int(rec["edon"]),
            edo=race_id.split("-")[0], yr=int(rec["yr"]), election_date=iso_date(rec["date"]),
            cvegeo=cvegeo(rec["inegi"]), ife_municipio=num(rec["ife"]), municipio=text(rec["mun"]),
            status=text(rec["status"]), dextra=num(rec["dextra"]),
            ncand=num(rec["ncand"]), ncoal=num(rec["ncoal"]), win=text(rec["win"]),
            mg=num(rec["mg"]), efec=num(rec["efec"]), efec_calc=sum(v[5] for v in rv),
            nr=num(rec["nr"]), nulos=num(rec["nulos"]), vtot=num(rec["tot"]),
            lisnom=num(rec["lisnom"]),
            conc_pres=num(c.get("dconcpres")), conc_dipf=num(c.get("dconcdipf")),
            conc_gob=num(c.get("dconcgob")), conc_dipl=num(c.get("dconcdipl")),
            dfake=None, fuente=text(rec["fuente"]), nota=text(rec["notas"]),
        ))

    split = read("aymu1970-on.coalSplit.csv")
    for rec in split:
        votes += vote_rows(rec, rec["emm"], "coal_split", n_slots(split))

    for rec in read("aymu1989-on.incumbents.csv"):
        incumbents.append(dict(
            race_id=rec["emm"], office="ayuntamiento", source_key=rec["emm"],
            id_estado=int(rec["edon"]), yr=int(rec["yr"]), cvegeo=cvegeo(rec["inegi"]),
            part=text(rec["part"]), part_elec=None, incumbent=text(rec["incumbent"]),
            dmujer=num(rec["dmujer"]), race_after=text(rec["race.after"]),
            runnerup=text(rec["runnerup"]), part_2nd=text(rec["part2nd"]), mg=num(rec["mg"]),
            date_in=None, notas=text(rec["notas"]), fuente=text(rec["fuente"]),
        ))

    parties = [(race_id, version, pos, p.strip())
               for race_id, version, pos, _, label, _ in votes
               for p in label.split("-") if p.strip()]

    # ── Write ──
    cur = con.cursor()
    for t in ("fact_local_race", "fact_local_race_vote",
              "fact_local_race_vote_party", "fact_local_incumbent"):
        cur.execute(f"DROP TABLE IF EXISTS {t}")
    cur.executescript("""
        CREATE TABLE fact_local_race (
            race_id TEXT PRIMARY KEY, office TEXT NOT NULL, id_estado INTEGER NOT NULL,
            edo TEXT, yr INTEGER NOT NULL, election_date TEXT,
            cvegeo TEXT, ife_municipio INTEGER, municipio TEXT,
            status TEXT, dextra INTEGER, ncand INTEGER, ncoal INTEGER, win TEXT, mg REAL,
            efec REAL, efec_calc REAL, nr REAL, nulos REAL, vtot REAL, lisnom REAL,
            conc_pres INTEGER, conc_dipf INTEGER, conc_gob INTEGER, conc_dipl INTEGER,
            dfake INTEGER, fuente TEXT, nota TEXT, source_commit TEXT
        );
        CREATE TABLE fact_local_race_vote (
            race_id TEXT NOT NULL, version TEXT NOT NULL, position INTEGER NOT NULL,
            label_raw TEXT NOT NULL, label TEXT NOT NULL, votes REAL NOT NULL,
            PRIMARY KEY (race_id, version, position)
        );
        CREATE TABLE fact_local_race_vote_party (
            race_id TEXT NOT NULL, version TEXT NOT NULL, position INTEGER NOT NULL,
            party TEXT NOT NULL
        );
        CREATE TABLE fact_local_incumbent (
            race_id TEXT, office TEXT NOT NULL, source_key TEXT, id_estado INTEGER NOT NULL,
            yr INTEGER NOT NULL, cvegeo TEXT, part TEXT, part_elec TEXT, incumbent TEXT,
            dmujer INTEGER, race_after TEXT, runnerup TEXT, part_2nd TEXT, mg REAL,
            date_in TEXT, notas TEXT, fuente TEXT
        );
    """)
    cols = list(races[0]) + ["source_commit"]
    cur.executemany(f"INSERT INTO fact_local_race ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                    [tuple(r.values()) + (sha,) for r in races])
    cur.executemany("INSERT INTO fact_local_race_vote VALUES (?,?,?,?,?,?)", votes)
    cur.executemany("INSERT INTO fact_local_race_vote_party VALUES (?,?,?,?)", parties)
    icols = list(incumbents[0])
    cur.executemany(f"INSERT INTO fact_local_incumbent ({','.join(icols)}) VALUES ({','.join('?' * len(icols))})",
                    [tuple(r.values()) for r in incumbents])
    cur.executescript("""
        CREATE INDEX idx_local_race_office_yr ON fact_local_race (office, yr);
        CREATE INDEX idx_local_race_cvegeo ON fact_local_race (cvegeo);
        CREATE INDEX idx_local_vote_party ON fact_local_race_vote_party (party, version);
        CREATE INDEX idx_local_vote_party_race ON fact_local_race_vote_party (race_id, version, position);
        CREATE INDEX idx_local_incumbent_race ON fact_local_incumbent (race_id);
    """)
    con.commit()

    for t in ("fact_local_race", "fact_local_race_vote",
              "fact_local_race_vote_party", "fact_local_incumbent"):
        print(f"  {t}: {cur.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]:,} rows")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refresh", action="store_true", help="re-download at the latest commit")
    args = ap.parse_args()
    sha = download(args.refresh)
    print(f"elecRetrns @ {sha[:10]}")
    with sqlite3.connect(DB_PATH) as con:
        build(con, sha)


if __name__ == "__main__":
    main()
