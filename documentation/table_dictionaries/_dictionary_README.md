# Table Dictionaries

This folder documents the normalized tables in `election_data.db`, the clean
electoral parquet inputs used to build them, and representative raw INE source
layouts.

Start with `overview.csv`. It lists all 18 normalized tables currently present
in the SQLite warehouse, including their primary keys, row grains, purposes,
and important joins. The table-specific CSVs define columns and domains.

## HTML Viewer

Open `viewer.html` directly in a browser to browse and search the dictionaries.
It is a self-contained file: it does not need a web server or an internet
connection.

After editing `overview.csv`, a table dictionary, or a raw-source reference,
regenerate the viewer from the repository root:

```bash
python3 documentation/table_dictionaries/build_viewer.py
```

When `election_data.db` is present, the overview also derives its federal
election coverage matrix from `dim_election` and shows up to five distinct,
non-null example values for every documented warehouse column. Raw-source
dictionaries use the representative row in `raw_cycle_examples.csv`. The
viewer still builds without the database; in that case it omits the coverage
matrix and warehouse examples. To generate a preview somewhere else, pass
`--output`, and use `--db` to select another warehouse:

```bash
python3 documentation/table_dictionaries/build_viewer.py \
  --db path/to/election_data.db \
  --output /tmp/dictionary-viewer.html
```

## Storage Layers

The project uses three related but distinct data layers:

1. `data/electoral_data_clean/clean_*` contains normalized, cycle-specific
   parquet inputs produced from raw electoral files. These are inputs to
   `ingestion/electoral_ingest.py`; they are not a complete mirror of SQLite.
2. `election_data.db` is the normalized warehouse and source of truth after
   ingestion. It also contains Cámara, Senado, mapping, classification, and
   state-calendar tables that do not come from the clean electoral parquets.
3. `data/materialized/` contains denormalized parquet products generated for
   the application and analysis, including geographic views, time series,
   Gaceta analytics, and municipio classifications. These are derived outputs,
   not additional normalized warehouse tables.

## Federal Election Coverage

`dim_election` currently holds 18 contests across eight federal cycles, all
backed by populated `fact_casilla_vote` rows:

| Year | President · 6 years | Deputies MR · 3 years | Senate MR · 6 years |
|---|---|---|---|
| 1994 | Loaded | Missing | Missing |
| 1997 | Not held | **Missing** | Not held |
| 2000 | Loaded | Loaded | Loaded |
| 2003 | Not held | **Missing** | Not held |
| 2006 | Loaded | Loaded | Loaded |
| 2009 | Not held | **Missing** | Not held |
| 2012 | Loaded | Loaded | Loaded |
| 2015 | Not held | Loaded | Not held |
| 2018 | Loaded | Loaded | Loaded |
| 2021 | Not held | Loaded | Not held |
| 2024 | Loaded | Loaded | Loaded |

The 1997, 2003, and 2009 midterm gaps apply only to Deputies results.
President and Senate are marked “Not held” because those contests follow
six-year cycles. This election-result coverage is separate from the legislative
roll-call coverage described below.

Only mayoría-relativa election results are loaded for federal deputies and
senators. RP result files exist for 2024, but RP vote-result ingestion has not
been implemented. This is separate from the official chamber-composition
dimensions: `dim_diputados` includes 300 MR and 200 RP seats, and
`dim_senadores` includes 64 MR, 32 first-minority (FM), and 32 RP seats.

Candidate catalogs in `dim_candidatos` are loaded from the 2015, 2021, and
2024 clean cycles. The earlier clean cycles do not contain candidate catalogs.

## Normalized Warehouse Tables

### Federal Electoral Results

These tables are created and populated by `ingestion/electoral_ingest.py`:

- `dim_election.csv` — one row per ingested federal election contest.
- `dim_geography.csv` — election-scoped geography combinations. Its primary
  key is `(geo_id, election_id)` because boundaries and assignments can change.
- `dim_casilla.csv` — one polling station or acta per election.
- `dim_party.csv` — normalized party, coalition, independent-candidate, and
  historical vote-option keys.
- `dim_candidatos.csv` — candidate catalog rows where supplied by a clean cycle.
- `fact_casilla_vote.csv` — long-format party votes at election/casilla grain.

### Geography and Election Calendar

- `dim_municipio_map_crosswalk.csv` — auditable mapping from election-cycle
  municipio representations to INEGI GeoJSON features. Fuzzy candidates remain
  suggestions until explicitly reviewed or overridden.
- `dim_state_election_calendar.csv` — state executive, local-congress, and
  municipal election-cycle metadata. Its source field records the research
  vintage; it is not derived from the federal election fact table.

### Cámara de Diputados Roll Calls

These tables are populated from Cámara de Diputados Gaceta Parlamentaria data
using `aux_scripts/gaceta_votes/` and `ingestion/gaceta_ingest.py`:

The warehouse currently contains roll-call votes for Legislatures **LVIII
through LXVI** (2000-2026 coverage dates in the current snapshot).

- `dim_gaceta_vote.csv` — one row per Cámara roll-call vote, including source,
  session, date, status, and context metadata.
- `dim_gaceta_deputy.csv` — normalized deputy identities observed in roll calls.
- `dim_diputados.csv` — all 500 official 2024 seats and the audited bridge from
  INE candidate identity to `dim_gaceta_deputy.deputy_id` where reliable.
- `fact_gaceta_vote_summary.csv` — reported summary counts by vote choice and
  parliamentary group.
- `fact_gaceta_deputy_vote.csv` — one deputy observation per roll call, with
  time-specific parliamentary affiliation on the fact row.
- `fact_gaceta_vote_classification.csv` — one model-produced topical and
  procedural classification per classified Gaceta vote, including confidence,
  review flag, evidence, model, and prompt lineage.

### Senado de la República Roll Calls

These tables are populated from Senado roll-call pages using
`aux_scripts/senado_votes/`, `ingestion/senado_ingest.py`, and
`ingestion/senadores_ingest.py`:

Senado roll-call coverage currently includes only the most recent legislature,
**LXVI**.

- `dim_senado_vote.csv` — one Senado roll-call vote with date, period,
  description, type, and reported totals.
- `dim_senador.csv` — normalized senator identities observed in roll calls.
- `dim_senadores.csv` — all 128 official 2024 Senate seats, including MR, FM,
  and RP, bridged to observed Senado identities with auditable match metadata.
- `fact_senador_vote.csv` — one senator observation per roll call, including
  parliamentary group, normalized vote, and source-detail text.

### Current Congreso Rosters

These tables are populated independently of the roll-call crawlers by
`aux_scripts/congress_rosters/crawl_congress_rosters.py` and
`ingestion/congress_roster_ingest.py`:

- `dim_congress_roster_snapshot.csv` — observation cutoff, official source,
  source hash, and published-vs-constitutional row counts.
- `fact_congress_roster_seat.csv` — current occupant/group or explicit vacancy
  for every stable INE seat, with the electoral identity retained separately.

## Raw Source References

Files prefixed with a year describe representative raw files as received from
INE before normalization. Use them to understand source quirks when editing a
cycle-specific converter in `ingestion/raw_electoral_data_converters/`.

- `[1994] 1994_PRE_CAS_94.csv` — semicolon-delimited 1994 presidential layout.
- `[2000] 2000_DAT_DISTRITALES_CAS.csv` — district DAT layout used in 2000.
- `[2006] 2006_ESTADISTICAS_TXT_CAS.csv` — pipe-delimited 2006 TXT layout.
- `[2015] DIPUTADOS_CAS.csv` — 2015 deputies CSV layout.
- `[2021] DIPUTACIONES_CAS.csv` — 2021 deputies CSV layout.
- `[2024] 2024_SEE_PRE_NAL_CAS.csv` — 2024 presidential CSV layout.

`raw_cycle_examples.csv` summarizes representative source layouts, delimiters,
header positions, example rows, and important ingestion quirks by cycle.
