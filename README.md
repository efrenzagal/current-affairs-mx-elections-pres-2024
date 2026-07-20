# Mexican Federal Election Results Explorer

This repository builds a normalized warehouse of Mexican federal election
results and serves analysis-ready files through a Streamlit dashboard.

The most useful way to understand the project is as a three-stage pipeline:

```text
official INE/raw files
  -> cycle-specific converters in ingestion/*_to_arrow_*.py
  -> clean per-cycle parquet folders in data/electoral_data_clean/clean_<year>/
  -> ingestion/electoral_ingest.py builds SQLite warehouse
  -> ingestion/electoral_materialize.py builds Streamlit parquet/GeoJSON artifacts
  -> ine_explorer_v2.py renders the app
```

## Quick Start

Install dependencies:

```bash
pip install -r requirements.txt
```

Rebuild the SQLite warehouse from the clean parquet folders:

```bash
python -m ingestion.electoral_ingest
```

Refresh only one election cycle while preserving all other cycles:

```bash
python -m ingestion.electoral_ingest --year 2000
```

Materialize the files used by Streamlit:

```bash
python -m ingestion.electoral_materialize
```

Run the app:

```bash
streamlit run ine_explorer_v2.py
```

Useful partial materialization commands:

```bash
python -m ingestion.electoral_materialize views
python -m ingestion.electoral_materialize timeseries
python -m ingestion.electoral_materialize --force
```

## What To Open First

Open only the files needed for the task:

- Page flow, data loading, controls: `ine_explorer_v2.py`
- Maps and choropleth rendering: `ui/maps.py`
- Bar charts, ternary plots, timeseries: `ui/charts.py`
- Scorecards, result tables, badges: `ui/tables.py`
- Gaceta Parlamentaria section: `ui/gaceta.py`
- Shared constants and pure helpers: `ui/common.py`
- Streamlit-ready parquet and GeoJSON generation: `ingestion/electoral_materialize.py`
- Clean parquet to SQLite ingestion: `ingestion/electoral_ingest.py`
- Raw/cycle-specific parsing: the matching converter in `ingestion/`
- Warehouse schema meaning: `documentation/table_dictionaries/overview.csv`
- Column-level table dictionaries: `documentation/table_dictionaries/*.csv`
- Validation/reference scripts: `aux_scripts/`

Avoid reading large `data/` artifacts unless the task explicitly requires
debugging data values, joins, row counts, or generated output.

## Main Files

### `ine_explorer_v2.py`

Streamlit app entry point — page config, CSS, data loaders, and page routing.

Important responsibilities:

- Loads and caches materialized parquet views.
- Provides year/type/unit selectors.
- Orchestrates the three page views (Estado, Municipio, Nacional · Histórico)
  via `render_results_tab`, which sequences map → scorecards → bars → tables.
- Dispatches rendering to the `ui/` modules.

Open this file for page flow, selector logic, caching behavior, or Streamlit
deployment issues. For chart or map changes, open the relevant `ui/` module.

### `ui/` — UI modules

The render functions are split across four focused modules:

- `ui/common.py` — shared constants (`PARTY_GROUPS`, `CYCLE_BLOCS`,
  `MAP_METRICS`, timeseries colors) and pure helpers (`election_label`,
  `agg_blocs`, `pivot_candidates`, `resolve_candidate_name`, etc.).
  No streamlit dependency — safe to import anywhere.
- `ui/maps.py` — shared bloc winner-map renderer for every presidential cycle,
  plus the GeoJSON loader and geometry utilities.
- `ui/charts.py` — bar charts (by candidate and by party), ternary bubble plots
  for 2024 and historical elections, and the multi-year timeseries chart.
- `ui/tables.py` — scorecards, metric cards, header badges, and the
  winners-only results dataframe with progress bar.
- `ui/gaceta.py` — the entire Gaceta Parlamentaria section: deputy alignment
  scatter, party cohesion heatmap, and vote browser. Call `render_gaceta()`
  from the main app.

### `ingestion/electoral_materialize.py`

Turns `election_data.db` into files under `data/materialized/`.

Important responsibilities:

- Builds `view_<level>_<election_id>.parquet` files for:
  `casilla`, `seccion`, `municipio`, `estado`, and `nacional`.
- Writes `data/materialized/dim_candidatos.parquet`.
- Writes `data/materialized/timeseries_estados.parquet`.
- Builds `data/materialized/municipios_processed.geojson` from INEGI Marco
  Geoestadístico 2024.
- Canonicalizes state names and municipio join keys, including INEGI `CVEGEO`
  municipality codes for map joins.
- Splits coalition votes for historical party time series.

Open this file when app data is missing, joins do not match, maps fail,
historical time series look wrong, or new materialized outputs are needed.

### `ingestion/electoral_ingest.py`

Builds the normalized SQLite warehouse from clean per-cycle parquets.

Important responsibilities:

- Defines `ELECTION_META`, the authoritative list of ingested election IDs,
  years, offices, and clean parquet directories.
- Defines `SCHEMA_MAP`, which maps cycle-specific clean parquet columns into
  the common warehouse schema.
- Creates and populates:
  `dim_election`, `dim_geography`, `dim_casilla`, `dim_party`,
  `dim_candidatos`, and `fact_casilla_vote`.

Open this file when adding a new election cycle, changing warehouse schema,
fixing ingestion into SQLite, or checking which election IDs exist.

### `ingestion/raw_electoral_data_converters/`

Cycle-specific converters from raw INE files to clean parquet folders.

Current converters:

- `dat_to_arrow_1994.py`
- `dat_to_arrow_2000.py`
- `dat_to_arrow_2006.py`
- `csv_to_arrow_2012.py`
- `csv_to_arrow_2015.py`
- `csv_to_arrow_2018.py`
- `csv_to_arrow_2021.py`
- `csv_to_arrow_2024.py`

These scripts handle source quirks by year. Open one only when changing raw
file parsing or regenerating `data/electoral_data_clean/clean_<year>/`.

### `ingestion/shared.py`

Constants shared across the ingestion layer: `CANONICAL_ESTADO_NOMBRES`
(the single authoritative 32-state name mapping), `DB_PATH`, and
`canonical_estado()`. Import from here — never redeclare in other scripts.

## Auxiliary Scripts

`aux_scripts/` contains standalone analysis and reference tools that operate
outside the main pipeline. They read from the warehouse or from external
sources but do not write back into it.

### `aux_scripts/seat_allocations/`

Reconstructs Cámara de Diputados and Senado seat allocations from warehouse
vote data.

- `diputados.py` — computes 300 MR district winners and approximates 200 RP
  seats using D'Hondt allocation per circunscripción, with Mexico's
  sobrerrepresentación cap (no party may hold more than 300 seats or exceed
  its national vote share by more than 8 percentage points).
- `senadores.py` — reconstructs Senado seat counts from MR and first-minority
  results.
- `hemicycle_explorer.py` — generates an interactive in-browser hemicycle
  visualization (500 seats) with tabs by year, partido/coalición toggle, and
  state filter. Run with `python3 aux_scripts/seat_allocations/hemicycle_explorer.py`.
- `district_audit.py` — cross-checks computed MR winners against official
  district results for QA purposes.
- `common.py` — shared helpers for actor totals and D'Hondt computation.

### `aux_scripts/gaceta_votes/`

Scrapes and parses Cámara de Diputados roll-call votes from Gaceta
Parlamentaria. Results feed the `dim_gaceta_*` and `fact_gaceta_*` tables
documented in `documentation/table_dictionaries/`.

- `crawl_gaceta_metadata.py` — fetches and caches vote-page metadata (title,
  legislature, URL, detail endpoint). Uses polite fetch/cache/backoff pattern;
  vote-summary fetching is opt-in.
- `parse_gaceta_vote.py` — parses a single Gaceta vote page into summary
  counts and deputy-level roll-call rows.
- `parse_gaceta_vote_batch.py` — batch version; walks cached metadata and
  produces parquet outputs ready for warehouse ingestion.

### `aux_scripts/qa_reports/`

District-level QA audit CSVs comparing computed seat winners against official
results, one file per election cycle: `district_audit_2000.csv` through
`district_audit_2024.csv`.

### `aux_scripts/approval_rates/`

Approval rating analysis for the 2018–2024 presidential period (`approval_rating.R`
and supporting Excel files).

### `aux_scripts/election_comparison/`

Cross-cycle party vote-share comparison script (`2018 vs 2024 presidential
party change.R`).

### `aux_scripts/turnout/`

State-level turnout change analysis across cycles (`turnout_change_by_state.R`).

## Data Layout

### Raw and Clean Inputs

These are local/intermediate data folders:

- `data/electoral_data_raw/raw_<year>/`: official source files, often large and inconsistent by
  year.
- `data/electoral_data_clean/clean_<year>/`: normalized per-cycle parquet outputs from the converter
  scripts.
- `data/election_data.db`: SQLite warehouse generated by `ingestion/electoral_ingest.py`.

### Streamlit Artifacts

`data/materialized/` contains the app-facing files generated by
`ingestion/electoral_materialize.py`.

Important patterns:

- `view_casilla_<election_id>.parquet`
- `view_seccion_<election_id>.parquet`
- `view_municipio_<election_id>.parquet`
- `view_estado_<election_id>.parquet`
- `view_nacional_<election_id>.parquet`
- `timeseries_estados.parquet`
- `dim_candidatos.parquet`
- `municipios_processed.geojson`

`municipios_processed.geojson` is generated from INEGI Marco Geoestadístico
2024 and contains feature `id` values keyed by 5-digit municipality `CVEGEO`
codes. The municipio parquet views carry `_mun_code` for these joins, plus
`_join_key` as a normalized-name diagnostic field.

## Election IDs

Election IDs follow this pattern:

```text
PRE_<year>
DIP_MR_<year>
SEN_MR_<year>
```

Examples:

- `PRE_1994`, `PRE_2000`, `PRE_2006`, `PRE_2012`, `PRE_2018`, `PRE_2024`
- `DIP_MR_2000`, `DIP_MR_2006`, `DIP_MR_2012`, `DIP_MR_2015`,
  `DIP_MR_2018`, `DIP_MR_2021`, `DIP_MR_2024`
- `SEN_MR_2000`, `SEN_MR_2006`, `SEN_MR_2012`, `SEN_MR_2018`, `SEN_MR_2024`

The authoritative source is `ELECTION_META` in `ingestion/electoral_ingest.py`.

## Warehouse Schema

The normalized warehouse uses these tables:

**Electoral results**

- `dim_election`: one row per election contest.
- `dim_geography`: normalized geography keys, scoped per election cycle.
- `dim_casilla`: polling-station/acta identifiers and attributes.
- `dim_party`: parties, coalitions, and vote options.
- `dim_candidatos`: candidate catalog rows where available.
- `fact_casilla_vote`: long-format vote totals by election/casilla/party.

**Legislative roll-call votes (Gaceta Parlamentaria)**

- `dim_gaceta_vote`: one row per Gaceta roll-call vote page (metadata, URL,
  legislature, chamber).
- `dim_gaceta_deputy`: normalized deputy names observed across roll-call lists.
- `fact_gaceta_vote_summary`: summary vote counts by choice and parliamentary
  group for each roll-call vote.
- `fact_gaceta_deputy_vote`: individual deputy vote records per roll-call vote.

Start with `documentation/table_dictionaries/overview.csv` for row grain,
primary keys, joins, and purpose. Use the other CSVs in
`documentation/table_dictionaries/` for column-level details.

## Common Workflows

### Change app UI or charts

Usually open:

- `ine_explorer_v2.py` — for page flow, selectors, or caching
- `ui/maps.py` — for map or choropleth changes
- `ui/charts.py` — for bar chart, ternary, or timeseries changes
- `ui/tables.py` — for scorecards or results table changes
- `ui/gaceta.py` — for anything in the Gaceta Parlamentaria section
- `ui/common.py` — for constants like `CYCLE_BLOCS`, `PARTY_GROUPS`, colors

Then run:

```bash
streamlit run ine_explorer_v2.py
```

### Fix map joins or missing municipios

Usually open:

- `ingestion/electoral_materialize.py`

Then run:

```bash
python -m ingestion.electoral_materialize views --force
streamlit run ine_explorer_v2.py
```

### Fix historical party time series

Usually open:

- `ingestion/electoral_materialize.py`
- `documentation/table_dictionaries/dim_party.csv` if party/coalition meaning
  is unclear

Then run:

```bash
python -m ingestion.electoral_materialize timeseries
```

### Add or fix a raw election converter

Usually open:

- The matching `ingestion/*_to_arrow_*.py`
- `ingestion/electoral_ingest.py` if the clean schema or election metadata changes
- `documentation/table_dictionaries/raw_cycle_examples.csv` for source quirks

Then run the converter, followed by:

```bash
python -m ingestion.electoral_ingest
python -m ingestion.electoral_materialize --force
```


### Explore seat allocations and hemicycle

Usually open:

- `aux_scripts/seat_allocations/diputados.py`
- `aux_scripts/seat_allocations/hemicycle_explorer.py`

Then run:

```bash
python3 aux_scripts/seat_allocations/hemicycle_explorer.py
```

This opens an interactive hemicycle in the browser. Use `diputados.py` or
`senadores.py` directly for tabular seat counts or QA against official results.

### Scrape and ingest Gaceta roll-call votes

Usually open:

- `aux_scripts/gaceta_votes/crawl_gaceta_metadata.py`
- `aux_scripts/gaceta_votes/parse_gaceta_vote_batch.py`

Then run:

```bash
# Crawl vote-page metadata (polite cache/backoff included)
python3 aux_scripts/gaceta_votes/crawl_gaceta_metadata.py --fetch-vote-pages

# Parse cached pages into parquet
python3 aux_scripts/gaceta_votes/parse_gaceta_vote_batch.py

# Load parsed roll calls into the local SQLite warehouse, then refresh the app data
python3 ingestion/gaceta_ingest.py
python3 ingestion/gaceta_materialize.py --force
```

### Update warehouse schema

Usually open:

- `ingestion/electoral_ingest.py`
- `ingestion/electoral_materialize.py`
- `documentation/table_dictionaries/*.csv`
- `ine_explorer_v2.py` only if app-facing columns changed

## Git and Data Policy

Large raw files, clean intermediate parquets, and local SQLite databases should
not be treated as source code. The repo's `.gitignore` ignores `data/`, so use
care when staging generated artifacts.

The intended code review surface is usually:

- Python source files
- Documentation files
- Selected app-facing materialized artifacts when deliberately refreshed

If Git says a path under `data/` is ignored, existing tracked files can still
record modifications, but new ignored files require `git add -f`.

## Notes for AI Assistants

- Prefer reading this README, `documentation/table_dictionaries/overview.csv`,
  and the specific target file before scanning the repo broadly.
- Do not open `data/materialized/*.parquet` or large raw files unless the task
  is explicitly about data debugging.
- For generated GeoJSON diffs, use summary commands (`git diff --stat`,
  row/key checks, or targeted JSON inspection) instead of reading the whole
  one-line file.
- If behavior depends on currently generated data, inspect the materialization
  code first, then query the relevant parquet only as needed.
- Keep README updates high-signal: this file is an orientation map, not a full
  data dictionary.
