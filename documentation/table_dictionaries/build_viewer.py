#!/usr/bin/env python3
"""Build the self-contained HTML data-dictionary viewer from the CSV sources."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
from collections import OrderedDict
from pathlib import Path


DICTIONARY_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = DICTIONARY_DIR.parents[1]
DEFAULT_OUTPUT = DICTIONARY_DIR / "viewer.html"
DEFAULT_JSON = PROJECT_ROOT / "web" / "public" / "data" / "dictionary.json"
DEFAULT_DB = PROJECT_ROOT / "election_data.db"
SAMPLE_SIZE = 5
SAMPLE_SCAN_LIMIT = 500

TABLE_GROUPS: OrderedDict[str, list[str]] = OrderedDict(
    {
        "Federal Electoral Results": [
            "dim_election",
            "dim_geography",
            "dim_casilla",
            "dim_party",
            "dim_candidatos",
            "fact_casilla_vote",
        ],
        "Geography & Election Calendar": [
            "dim_municipio_map_crosswalk",
            "dim_state_election_calendar",
        ],
        "Presidential Approval": [
            "dim_approval_pollster",
            "dim_approval_source",
            "fact_approval_poll",
            "fact_approval_topic",
        ],
        "Cámara de Diputados Roll Calls": [
            "dim_gaceta_vote",
            "dim_gaceta_deputy",
            "dim_diputados",
            "fact_gaceta_vote_summary",
            "fact_gaceta_deputy_vote",
            "fact_gaceta_vote_classification",
        ],
        "Senado de la República Roll Calls": [
            "dim_senado_vote",
            "dim_senador",
            "dim_senadores",
            "fact_senador_vote",
            "fact_senado_vote_classification",
        ],
        "Current Congreso Rosters": [
            "dim_congress_roster_snapshot",
            "fact_congress_roster_seat",
            "fact_congress_seat_occupancy",
            "fact_congress_party_membership",
            "fact_legislature_66_seat_resolved",
            "fact_legislature_66_seat_member",
            "fact_legislature_66_former_member",
            "fact_legislature_66_person_alias",
            "fact_legislature_66_seat_vote_conflict",
            "fact_legislature_66_seat_election_result",
            "fact_legislature_66_vote_threshold",
        ],
    }
)

RAW_DICTIONARY_RE = re.compile(r"^\[(\d{4})\]\s+(.+)\.csv$")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def raw_key(year: str, label: str) -> str:
    stem = label.removesuffix(".csv")
    return stem if stem.startswith(f"{year}_") else f"{year}_{stem}"


def quote_identifier(value: str) -> str:
    """Quote a SQLite identifier that came from a dictionary or schema."""
    return '"' + value.replace('"', '""') + '"'


def display_sample(value: object, max_length: int = 100) -> str:
    """Format a compact, single-line sample for embedding in the viewer."""
    if isinstance(value, bytes):
        rendered = "0x" + value.hex()
    else:
        rendered = " ".join(str(value).split())
    if len(rendered) > max_length:
        return rendered[: max_length - 1] + "…"
    return rendered


def open_read_connection(db_path: Path) -> sqlite3.Connection:
    """Open the warehouse for dictionary samples without requiring URI support.

    SQLite URI read-only mode is preferable, but some mounted workspaces reject
    it even though a normal connection can read the same database.  This script
    executes SELECT-only queries, so the fallback remains non-mutating.
    """
    uri = f"file:{db_path.resolve()}?mode=ro"
    try:
        return sqlite3.connect(uri, uri=True)
    except sqlite3.OperationalError:
        return sqlite3.connect(str(db_path))


def load_table_examples(
    db_path: Path, tables: OrderedDict[str, dict[str, object]]
) -> dict[str, dict[str, list[str]]]:
    """Read a small, deterministic sample of distinct values from SQLite."""
    if not db_path.exists():
        return {}

    examples: dict[str, dict[str, list[str]]] = {}
    with open_read_connection(db_path) as conn:
        available_tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        for table_name, table in tables.items():
            if table["raw"] or table_name not in available_tables:
                continue
            schema_columns = {
                row[1]
                for row in conn.execute(
                    f"PRAGMA table_info({quote_identifier(table_name)})"
                ).fetchall()
            }
            table_examples: dict[str, list[str]] = {}
            for column in table["columns"]:
                column_name = column.get("Column Name", "")
                if column_name not in schema_columns:
                    continue
                sql = (
                    f"SELECT {quote_identifier(column_name)} "
                    f"FROM {quote_identifier(table_name)} "
                    f"WHERE {quote_identifier(column_name)} IS NOT NULL "
                    f"LIMIT {SAMPLE_SCAN_LIMIT}"
                )
                distinct: list[str] = []
                seen: set[str] = set()
                for (value,) in conn.execute(sql):
                    rendered = display_sample(value)
                    if rendered in seen:
                        continue
                    seen.add(rendered)
                    distinct.append(rendered)
                    if len(distinct) == SAMPLE_SIZE:
                        break
                table_examples[column_name] = distinct
            examples[table_name] = table_examples
    return examples


def warehouse_tables(db_path: Path) -> set[str]:
    """Names of the tables that actually exist in the warehouse right now."""
    if not db_path.exists():
        return set()
    with open_read_connection(db_path) as conn:
        return {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }


def raw_row_examples(metadata: dict[str, str]) -> dict[str, list[str]]:
    """Map the documented representative raw row to its source headers."""
    header = metadata.get("example_header", "")
    row = metadata.get("example_first_data_row", "")
    delimiter = "|" if header.count("|") > header.count(";") else ";"
    headers = next(csv.reader([header], delimiter=delimiter), [])
    values = next(csv.reader([row], delimiter=delimiter), [])
    return {
        name: [display_sample(values[index])]
        for index, name in enumerate(headers)
        if index < len(values) and values[index].strip()
    }


def load_election_coverage(db_path: Path) -> list[dict[str, object]]:
    if not db_path.exists():
        return []
    with open_read_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT year, election_type FROM dim_election ORDER BY year, election_type"
        ).fetchall()
    by_year: dict[int, set[str]] = {}
    for year, election_type in rows:
        by_year.setdefault(int(year), set()).add(str(election_type))

    first_cycle = 1994
    latest_cycle = max(by_year, default=first_cycle)

    def status(year: int, election_type: str) -> str:
        if election_type in by_year.get(year, set()):
            return "loaded"
        held_that_year = election_type == "DIP" or (year - first_cycle) % 6 == 0
        return "missing" if held_that_year else "not_held"

    return [
        {
            "year": year,
            "PRE": status(year, "PRE"),
            "DIP": status(year, "DIP"),
            "SEN": status(year, "SEN"),
        }
        for year in range(first_cycle, latest_cycle + 1, 3)
    ]


def roman_numeral(value: int) -> str:
    numerals = (
        (50, "L"),
        (40, "XL"),
        (10, "X"),
        (9, "IX"),
        (5, "V"),
        (4, "IV"),
        (1, "I"),
    )
    result: list[str] = []
    for amount, numeral in numerals:
        while value >= amount:
            result.append(numeral)
            value -= amount
    return "".join(result)


def load_legislative_coverage(db_path: Path) -> dict[str, list[dict[str, object]]]:
    coverage: dict[str, list[dict[str, object]]] = {"deputies": [], "senate": []}
    if not db_path.exists():
        return coverage

    with open_read_connection(db_path) as conn:
        available_tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        for chamber, table_name in (
            ("deputies", "dim_gaceta_vote"),
            ("senate", "dim_senado_vote"),
        ):
            if table_name not in available_tables:
                continue
            rows = conn.execute(
                f"SELECT legislature, COUNT(*), MIN(vote_date), MAX(vote_date) "
                f"FROM {quote_identifier(table_name)} "
                "WHERE legislature IS NOT NULL "
                "GROUP BY legislature ORDER BY legislature"
            ).fetchall()
            coverage[chamber] = [
                {
                    "legislature": int(legislature),
                    "label": roman_numeral(int(legislature)),
                    "voteCount": int(vote_count),
                    "firstVote": first_vote,
                    "latestVote": latest_vote,
                }
                for legislature, vote_count, first_vote, latest_vote in rows
            ]
    return coverage


def build_payload(db_path: Path) -> dict[str, object]:
    overview_rows = read_csv(DICTIONARY_DIR / "overview.csv")
    overview = {row["Table Name"]: row for row in overview_rows}

    tables: OrderedDict[str, dict[str, object]] = OrderedDict()
    for row in overview_rows:
        table_name = row["Table Name"]
        dictionary_path = DICTIONARY_DIR / f"{table_name}.csv"
        if not dictionary_path.exists():
            raise FileNotFoundError(
                f"overview.csv references {table_name!r}, but {dictionary_path.name} is missing"
            )
        tables[table_name] = {
            "columns": read_csv(dictionary_path),
            "overview": overview[table_name],
            "source": f"documentation/table_dictionaries/{dictionary_path.name}",
            "raw": False,
        }

    raw_metadata_rows = read_csv(DICTIONARY_DIR / "raw_cycle_examples.csv")
    raw_metadata = {row["cycle"]: row for row in raw_metadata_rows}
    raw_names: list[str] = []
    for path in sorted(DICTIONARY_DIR.glob("*.csv")):
        match = RAW_DICTIONARY_RE.match(path.name)
        if not match:
            continue
        year, label = match.groups()
        key = raw_key(year, label)
        raw_names.append(key)
        tables[key] = {
            "columns": read_csv(path),
            "overview": None,
            "rawMeta": raw_metadata.get(year),
            "examples": raw_row_examples(raw_metadata.get(year, {})),
            "source": f"documentation/table_dictionaries/{path.name}",
            "raw": True,
        }

    table_examples = load_table_examples(db_path, tables)
    for table_name, examples in table_examples.items():
        tables[table_name]["examples"] = examples

    # A table can be documented before it is built. That is a normal state to be
    # in, but readers should not be shown an empty Examples column with no
    # explanation, so record it rather than hiding it.
    present = warehouse_tables(db_path)
    pending: list[str] = []
    for table_name, table in tables.items():
        if table["raw"]:
            continue
        exists = not present or table_name in present
        table["inWarehouse"] = exists
        if not exists:
            pending.append(table_name)
    if pending:
        print(
            "Note: documented but not yet in the warehouse: " + ", ".join(sorted(pending))
        )

    # Reading the warehouse can come back empty even when the file is right
    # there -- a concurrent writer holding SQLite is enough to do it. That used
    # to pass silently and ship an example-less dictionary to the website, so
    # an unexplained empty read is now a hard failure. Missing database is
    # still fine: that path is documented and drops examples on purpose.
    if db_path.exists():
        sampled = sum(
            1
            for table in tables.values()
            if not table["raw"] and any((table.get("examples") or {}).values())
        )
        if not sampled:
            raise RuntimeError(
                f"{db_path} exists but yielded no column examples. Something is "
                "holding the warehouse (a running Streamlit app will do it). "
                "Refusing to write a dictionary with every example blank -- "
                "close the other reader and rerun, or pass --db /dev/null to "
                "build without examples deliberately."
            )

    assigned = {name for names in TABLE_GROUPS.values() for name in names}
    missing_from_groups = [name for name in overview if name not in assigned]
    unknown_group_entries = sorted(assigned - set(overview))
    if unknown_group_entries:
        raise ValueError(
            "TABLE_GROUPS references tables absent from overview.csv: "
            + ", ".join(unknown_group_entries)
        )

    groups = [
        {"name": group_name, "tables": names}
        for group_name, names in TABLE_GROUPS.items()
    ]
    if missing_from_groups:
        groups.append({"name": "Other Warehouse Tables", "tables": missing_from_groups})
    groups.append({"name": "Raw Source References", "tables": raw_names})

    return {
        "tables": tables,
        "groups": groups,
        "coverage": load_election_coverage(db_path),
        "legislativeCoverage": load_legislative_coverage(db_path),
        "warehouseTableCount": len(overview_rows),
        "rawReferenceCount": len(raw_names),
        "sampleSize": SAMPLE_SIZE,
    }


HTML_TEMPLATE = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Warehouse Data Dictionary</title>
<style>
:root{--bg:#f3f4f0;--surface:#fff;--surface2:#eceee8;--text:#1b211d;--muted:#5c6660;--accent:#2f5d50;--accent2:#204238;--soft:#dde8e1;--border:#d7dbd3;--mono:#f0efe9;--pk:#a8632c;--pksoft:#f3e4d3;--raw:#6b5a9a;--rawsoft:#e7e2f2;--display:"Iowan Old Style","Palatino Linotype",Palatino,Georgia,serif;--body:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;--code:"SF Mono",ui-monospace,"Cascadia Code",Menlo,Consolas,monospace}
@media(prefers-color-scheme:dark){:root{--bg:#12140f;--surface:#191c15;--surface2:#20241b;--text:#e7ebe2;--muted:#9aa596;--accent:#7ec0a6;--accent2:#a3d8c1;--soft:#213a30;--border:#2c332a;--mono:#1c2018;--pk:#e0a45f;--pksoft:#3a2c1a;--raw:#b7a6e0;--rawsoft:#2b2540}}
*{box-sizing:border-box}html,body{margin:0}body{background:var(--bg);color:var(--text);font-family:var(--body);min-height:100vh}.app{display:grid;grid-template-columns:290px minmax(0,1fr);min-height:100vh}.sidebar{background:var(--surface);border-right:1px solid var(--border);height:100vh;overflow-y:auto;padding:27px 19px 38px;position:sticky;top:0}.brand{font-family:var(--display);font-size:1.3rem;font-weight:600;margin:0 0 4px}.subtitle{color:var(--muted);font-size:.78rem;line-height:1.45;margin:0 0 20px}.search{background:var(--mono);border:1px solid var(--border);border-radius:8px;color:var(--text);font:inherit;font-size:.86rem;margin-bottom:18px;outline:0;padding:9px 11px;width:100%}.search:focus{border-color:var(--accent)}.group-title{color:var(--muted);font-size:.66rem;font-weight:700;letter-spacing:.085em;margin:19px 9px 7px;text-transform:uppercase}.nav-button{align-items:baseline;background:0;border:0;border-radius:7px;color:var(--text);cursor:pointer;display:flex;font-family:var(--code);font-size:.79rem;gap:8px;justify-content:space-between;margin:1px 0;padding:7px 9px;text-align:left;width:100%}.nav-button:hover{background:var(--surface2)}.nav-button.active{background:var(--soft);color:var(--accent2);font-weight:700}.nav-button.raw.active{background:var(--rawsoft);color:var(--raw)}.count{color:var(--muted);font-family:var(--body);font-size:.67rem}.empty{color:var(--muted);font-size:.8rem;padding:8px 9px}main{max-width:1180px;padding:40px 46px 80px;width:100%}.eyebrow{color:var(--accent);font-size:.7rem;font-weight:750;letter-spacing:.1em;margin:0 0 8px;text-transform:uppercase}.eyebrow.raw{color:var(--raw)}h1{font-family:var(--code);font-size:clamp(1.45rem,3vw,1.95rem);font-weight:650;margin:0 0 12px;overflow-wrap:anywhere}.purpose{font-size:1rem;line-height:1.62;margin:0 0 20px;max-width:75ch}.chips{display:flex;flex-wrap:wrap;gap:9px;margin:0 0 8px}.chip{background:var(--surface);border:1px solid var(--border);border-radius:9px;display:flex;flex-direction:column;gap:3px;min-width:150px;padding:9px 13px}.chip.wide{flex:1 1 100%}.chip-key{color:var(--muted);font-size:.63rem;font-weight:700;letter-spacing:.07em;text-transform:uppercase}.chip-value{font-family:var(--code);font-size:.8rem;line-height:1.45;overflow-wrap:anywhere}.note{background:var(--surface);border-left:3px solid var(--accent);border-radius:0 8px 8px 0;color:var(--muted);font-size:.87rem;line-height:1.55;margin-top:17px;padding:12px 15px}.note.raw{border-left-color:var(--raw)}.note b{color:var(--text)}.toggle{border:1px solid var(--border);border-radius:8px;display:inline-flex;margin:20px 0 0;overflow:hidden}.toggle button{background:var(--surface);border:0;color:var(--muted);cursor:pointer;font-size:.76rem;font-weight:700;padding:7px 13px}.toggle button.active{background:var(--accent);color:#fff}.toggle button.raw.active{background:var(--raw)}.table-wrap{background:var(--surface);border:1px solid var(--border);border-radius:12px;margin-top:25px;overflow-x:auto}table{border-collapse:collapse;width:100%}th{background:var(--surface2);color:var(--muted);font-size:.64rem;letter-spacing:.065em;padding:11px 13px;text-align:left;text-transform:uppercase;white-space:nowrap}td{border-top:1px solid var(--border);font-size:.81rem;line-height:1.48;padding:12px 13px;vertical-align:top}tbody tr:hover{background:var(--surface2)}.column{font-family:var(--code);font-weight:650;min-width:160px}.type,.role{color:var(--muted);font-family:var(--code);white-space:nowrap}.description{min-width:250px}.domain,.notes{color:var(--muted);min-width:190px}.examples{min-width:180px}.sample-value{background:var(--mono);border-radius:4px;display:table;font-family:var(--code);font-size:.72rem;margin:0 0 5px;max-width:260px;overflow-wrap:anywhere;padding:3px 5px}.sample-value:last-child{margin-bottom:0}.no-sample{color:var(--muted)}.badge{background:var(--pksoft);border-radius:4px;color:var(--pk);font-family:var(--body);font-size:.57rem;font-weight:800;margin-left:7px;padding:2px 4px;vertical-align:1px}.footer{color:var(--muted);font-size:.72rem;margin-top:13px}.footer code{background:var(--mono);border-radius:4px;font-family:var(--code);padding:2px 5px}.stats{display:grid;gap:12px;grid-template-columns:repeat(3,minmax(0,1fr));margin:23px 0}.stat{background:var(--surface);border:1px solid var(--border);border-radius:11px;padding:17px}.stat strong{display:block;font-family:var(--display);font-size:1.8rem}.stat span{color:var(--muted);font-size:.75rem}.overview-grid{display:grid;gap:12px;grid-template-columns:repeat(2,minmax(0,1fr));margin-top:22px}.overview-card{background:var(--surface);border:1px solid var(--border);border-radius:11px;padding:16px}.overview-card h2{font-family:var(--display);font-size:1.03rem;margin:0 0 8px}.overview-card p{color:var(--muted);font-size:.8rem;line-height:1.5;margin:0}.coverage td,.coverage th{text-align:center}.coverage td:first-child,.coverage th:first-child{text-align:left}.yes,.missing{border-radius:999px;display:inline-block;font-size:.7rem;font-weight:800;min-width:58px;padding:3px 8px}.yes{background:var(--soft);color:var(--accent2)}.missing{background:var(--pksoft);color:var(--pk)}.not-held{color:var(--muted);font-size:.72rem}
@media(max-width:850px){.app{grid-template-columns:1fr}.sidebar{border-bottom:1px solid var(--border);border-right:0;height:auto;max-height:46vh;position:static}main{padding:28px 20px 60px}.stats{grid-template-columns:1fr}.overview-grid{grid-template-columns:1fr}}
</style>
</head>
<body>
<div class="app">
  <nav class="sidebar" aria-label="Dictionary tables">
    <p class="brand">Warehouse Data Dictionary</p>
    <p class="subtitle">Mexico elections and federal legislative data</p>
    <input class="search" id="search" type="search" placeholder="Filter tables or columns…" autocomplete="off">
    <div id="navigation"></div>
  </nav>
  <main id="content"></main>
</div>
<script>
const MODEL=__PAYLOAD__;
let current="_overview";
let language="eng";
let query="";
const navigation=document.getElementById("navigation");
const content=document.getElementById("content");
const search=document.getElementById("search");
const escapeHtml=value=>String(value??"").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
const entry=name=>MODEL.tables[name];
const primaryKeys=overview=>String(overview?.["Primary Key"]||"").split(",").map(x=>x.trim());
function matches(name){
  if(!query)return true;
  const q=query.toLowerCase();
  if(name.toLowerCase().includes(q))return true;
  return entry(name).columns.some(column=>Object.values(column).some(value=>String(value||"").toLowerCase().includes(q)));
}
function renderNavigation(){
  navigation.innerHTML="";
  const overviewButton=document.createElement("button");
  overviewButton.className="nav-button"+(current==="_overview"?" active":"");
  overviewButton.innerHTML="<span>Warehouse overview</span>";
  overviewButton.onclick=()=>{current="_overview";renderNavigation();renderContent()};
  navigation.appendChild(overviewButton);
  let visibleCount=0;
  for(const group of MODEL.groups){
    const visible=group.tables.filter(matches);
    if(!visible.length)continue;
    visibleCount+=visible.length;
    const title=document.createElement("div");
    title.className="group-title";
    title.textContent=group.name;
    navigation.appendChild(title);
    for(const name of visible){
      const item=entry(name);
      const button=document.createElement("button");
      button.className="nav-button"+(item.raw?" raw":"")+(current===name?" active":"");
      button.innerHTML=`<span>${escapeHtml(name)}</span><span class="count">${item.columns.length}</span>`;
      button.onclick=()=>{current=name;renderNavigation();renderContent()};
      navigation.appendChild(button);
    }
  }
  if(query&&!visibleCount){
    const empty=document.createElement("div");empty.className="empty";empty.textContent=`No matches for “${query}”.`;navigation.appendChild(empty);
  }
}
function renderOverview(){
  const columnCount=Object.values(MODEL.tables).filter(x=>!x.raw).reduce((sum,x)=>sum+x.columns.length,0);
  const warehouseGroups=MODEL.groups.filter(group=>group.name!=="Raw Source References");
  let html=`<p class="eyebrow">Overview</p><h1>Warehouse data dictionary</h1><p class="purpose">A self-contained reference generated from the CSV dictionaries. The normalized SQLite warehouse is documented separately from raw source layouts and application-facing materializations.</p>`;
  html+=`<div class="stats"><div class="stat"><strong>${MODEL.warehouseTableCount}</strong><span>normalized warehouse tables</span></div><div class="stat"><strong>${columnCount}</strong><span>documented warehouse columns</span></div><div class="stat"><strong>${MODEL.rawReferenceCount}</strong><span>representative raw layouts</span></div></div>`;
  if(MODEL.coverage.length){
    html+=`<h1 style="font-family:var(--display);font-size:1.35rem;margin-top:30px">Federal election coverage</h1><p class="purpose">Deputies are elected every three years; presidential and Senate elections occur every six years.</p><div class="table-wrap"><table class="coverage"><thead><tr><th>Year</th><th>President · 6 years</th><th>Deputies MR · 3 years</th><th>Senate MR · 6 years</th></tr></thead><tbody>`;
    for(const row of MODEL.coverage){
      const mark=value=>value==="loaded"?'<span class="yes">Loaded</span>':value==="missing"?'<span class="missing">Missing</span>':'<span class="not-held">Not held</span>';
      html+=`<tr><td class="column">${row.year}</td><td>${mark(row.PRE)}</td><td>${mark(row.DIP)}</td><td>${mark(row.SEN)}</td></tr>`;
    }
    html+="</tbody></table></div>";
    html+=`<div class="note"><b>Midterm gap.</b> The 1997, 2003, and 2009 rows are missing only Deputies results; President and Senate show “Not held” because those contests were not scheduled in those midterm years.</div>`;
  }
  const legislativeTable=rows=>{
    let table='<div class="table-wrap"><table><thead><tr><th>Legislature</th><th>Roll-call votes</th><th>First vote</th><th>Latest vote</th></tr></thead><tbody>';
    for(const row of rows){
      table+=`<tr><td class="column">${escapeHtml(row.label)}</td><td>${row.voteCount.toLocaleString()}</td><td>${escapeHtml(row.firstVote)}</td><td>${escapeHtml(row.latestVote)}</td></tr>`;
    }
    return table+'</tbody></table></div>';
  };
  if(MODEL.legislativeCoverage.deputies.length||MODEL.legislativeCoverage.senate.length){
    html+=`<h1 style="font-family:var(--display);font-size:1.35rem;margin-top:34px">Legislative roll-call coverage</h1>`;
    html+=`<p class="purpose">Coverage below describes recorded plenary roll-call votes, not federal election results.</p>`;
    if(MODEL.legislativeCoverage.deputies.length){
      html+=`<h2 style="font-family:var(--display);font-size:1.05rem;margin:22px 0 -12px">Cámara de Diputados</h2>`;
      html+=legislativeTable(MODEL.legislativeCoverage.deputies);
    }
    if(MODEL.legislativeCoverage.senate.length){
      html+=`<h2 style="font-family:var(--display);font-size:1.05rem;margin:26px 0 -12px">Senado de la República</h2>`;
      html+=legislativeTable(MODEL.legislativeCoverage.senate);
      html+=`<div class="note"><b>Senate scope.</b> Senado roll-call data currently covers only the most recent legislature, LXVI.</div>`;
    }
  }
  html+='<div class="overview-grid">';
  for(const group of warehouseGroups){
    html+=`<div class="overview-card"><h2>${escapeHtml(group.name)}</h2><p>${group.tables.length} tables · ${group.tables.map(x=>`<code>${escapeHtml(x)}</code>`).join(", ")}</p></div>`;
  }
  html+='</div><p class="footer">Generated by <code>documentation/table_dictionaries/build_viewer.py</code></p>';
  content.innerHTML=html;
}
function renderTable(){
  const item=entry(current);
  const overview=item.overview;
  const raw=item.rawMeta;
  const descriptionKey=language==="eng"?"Description (ENG)":"Description (SPA)";
  let html=`<p class="eyebrow${item.raw?' raw':''}">${item.raw?'Raw source reference':'Table dictionary'}</p><h1>${escapeHtml(current)}</h1>`;
  if(overview){
    html+=`<p class="purpose">${escapeHtml(overview.Purpose)}</p><div class="chips"><div class="chip"><span class="chip-key">Primary key</span><span class="chip-value">${escapeHtml(overview["Primary Key"])}</span></div><div class="chip"><span class="chip-key">Row grain</span><span class="chip-value">${escapeHtml(overview["Row Grain"])}</span></div><div class="chip"><span class="chip-key">Approx. rows</span><span class="chip-value">${escapeHtml(overview["Approx. Row Count"])}</span></div></div>`;
    if(overview["Key Foreign Keys"]&&overview["Key Foreign Keys"]!=="None")html+=`<div class="chips"><div class="chip wide"><span class="chip-key">Important joins</span><span class="chip-value">${escapeHtml(overview["Key Foreign Keys"])}</span></div></div>`;
    if(overview.Notes)html+=`<div class="note"><b>Notes.</b> ${escapeHtml(overview.Notes)}</div>`;
  }else if(raw){
    html+=`<p class="purpose">Representative ${escapeHtml(raw.cycle)} source layout before normalization into the warehouse.</p><div class="chips"><div class="chip wide"><span class="chip-key">Source file</span><span class="chip-value">${escapeHtml(raw.source_file)}</span></div><div class="chip"><span class="chip-key">Election scope</span><span class="chip-value">${escapeHtml(raw.election_scope)}</span></div><div class="chip"><span class="chip-key">Delimiter</span><span class="chip-value">${escapeHtml(raw.delimiter)}</span></div><div class="chip"><span class="chip-key">Header line</span><span class="chip-value">${escapeHtml(raw.header_line_number)}</span></div></div>`;
    if(raw.important_information)html+=`<div class="note raw"><b>Notes.</b> ${escapeHtml(raw.important_information)}</div>`;
  }
  html+=`<div class="toggle"><button data-language="eng" class="${language==='eng'?'active':''}${item.raw?' raw':''}">English</button><button data-language="spa" class="${language==='spa'?'active':''}${item.raw?' raw':''}">Español</button></div>`;
  html+=`<div class="table-wrap"><table><thead><tr><th>Column</th><th>Type</th><th>Role</th><th>Description</th><th>Values / Domain</th><th>Examples</th><th>Notes</th></tr></thead><tbody>`;
  const keys=primaryKeys(overview);
  for(const column of item.columns){
    const name=column["Column Name"]||"";
    const examples=(item.examples?.[name]||[]).map(value=>`<span class="sample-value">${escapeHtml(value)}</span>`).join("")||'<span class="no-sample">—</span>';
    html+=`<tr><td class="column">${escapeHtml(name)}${keys.includes(name)?'<span class="badge">PK</span>':''}</td><td class="type">${escapeHtml(column["Data Type"])}</td><td class="role">${escapeHtml(column.Role)}</td><td class="description">${escapeHtml(column[descriptionKey])}</td><td class="domain">${escapeHtml(column["Values / Domain"])}</td><td class="examples">${examples}</td><td class="notes">${escapeHtml(column.Notes)}</td></tr>`;
  }
  html+=`</tbody></table></div><p class="footer">${item.columns.length} columns · up to ${MODEL.sampleSize} distinct non-null examples per column · source: <code>${escapeHtml(item.source)}</code></p>`;
  content.innerHTML=html;
  content.querySelectorAll("[data-language]").forEach(button=>button.onclick=()=>{language=button.dataset.language;renderContent()});
}
function renderContent(){current==="_overview"?renderOverview():renderTable()}
search.addEventListener("input",event=>{query=event.target.value.trim();renderNavigation()});
renderNavigation();renderContent();
</script>
</body>
</html>
'''


def render_html(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    encoded = encoded.replace("</", "<\\/")
    return HTML_TEMPLATE.replace("__PAYLOAD__", encoded)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--json",
        type=Path,
        default=DEFAULT_JSON,
        help="Payload snapshot consumed by the /diccionario route on the website.",
    )
    parser.add_argument(
        "--no-json",
        action="store_true",
        help="Build only the standalone viewer, leaving the website snapshot alone.",
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()

    payload = build_payload(args.db)
    html = render_html(payload)
    args.output.write_text(html, encoding="utf-8")
    print(
        f"Wrote {args.output} with {payload['warehouseTableCount']} warehouse tables "
        f"and {payload['rawReferenceCount']} raw references."
    )

    # The standalone viewer and the website render the same payload; writing both
    # from one run is what keeps them from drifting apart.
    if not args.no_json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        print(f"Wrote {args.json} ({args.json.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
