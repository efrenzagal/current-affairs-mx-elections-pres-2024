# Current affairs mx 

This repository builds a normalized warehouse of Mexican federal election
results and legislative roll-call votes, and serves analysis-ready files
through a Streamlit dashboard.

The most useful way to understand the project is as a three-stage pipeline:

```text
official INE/raw files
  -> cycle-specific converters in ingestion/raw_electoral_data_converters/
  -> clean per-cycle parquet folders in data/electoral_data_clean/clean_<year>/
  -> ingestion/electoral_ingest.py builds SQLite warehouse
  -> ingestion/electoral_materialize.py builds Streamlit parquet/GeoJSON artifacts
  -> ine_explorer_v2.py renders the app
```

Roll-call votes for both chambers run on a parallel track into the same
warehouse. Those pipelines have their own documentation:

- `documentation/diputados_infra.md` — Cámara de Diputados / Gaceta
  Parlamentaria (legislatures 58–66).
- `documentation/senado_infra.md` — Senado de la República (legislature 66).

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
python3 run_streamlit.py
```

## Dashboard at a Glance

The Streamlit dashboard has five sections in a segmented navigation control:

- **Trayectoria** — municipal presidential-election trajectories from 1994 to
  2024, shown as a three-way ideological composition alongside state trends,
  turnout metrics, charts, and municipal winner maps.
- **Aprobación** — presidential approval observations, monthly medians, and
  pollster house-effect views from Zedillo through Sheinbaum.
- **Congreso · Composición** — official Chamber of Deputies and Senate seat
  assignments from INE, shown side by side as pre-built hemicycles and
  summaries. Electoral, latest-directory, and dated-directory views retain the
  same stable seat coordinates. Licenses and vacancies are shown separately
  from active parliamentary groups. Selecting a seat opens the occupant's
  roll-call history when a reliable date-appropriate identity match exists:
  deputy seats drill into Gaceta Parlamentaria and senator seats into
  Senado.gob.mx.
- **Congreso · Votos por diputado** — a deputy-level Cámara de Diputados
  roll-call voting calendar. Each new visit starts with a random
  deputy/legislature pair; selectors remain available for deliberate lookup.
- **Congreso · Clasificación de votos** — an LLM-classified view of roll-call
  votes covering topic, origin, and legislative stage, with topic-composition
  and consensus-vs-participation charts that link out to each vote's source
  page.

Only the selected section is rendered, so changing a control in one section
does not also load and execute the other four sections.

The initial section is **Trayectoria**. Its state and municipality are chosen at
random for a new session; the election deep dive defaults to the latest
available cycle.

### Data sources and interpretation

- Federal election results originate in official **INE** files, then pass
  through the cycle-specific converters and the normalized warehouse.
- Municipality geometry originates in the **INEGI Marco Geoestadístico 2024**.
- Cámara de Diputados roll calls originate in the **Gaceta Parlamentaria**;
  Senado roll calls originate in **Senado.gob.mx**. Individual vote pages
  retain their source URLs in both cases.
- The charts include every LXVI vote record published and downloaded from the
  official source, mapped to a constitutional seat. Seats omitted by the source
  remain **“Sin registro”**; they are not imputed as absent or assigned a
  fabricated vote.
- Presidential approval history originates in **Oraculus**-compiled
  spreadsheets covering Feb 1995 – Sep 2025. Oraculus has stopped publishing,
  so the series is now carried forward from **El Financiero**'s monthly
  *Encuesta EF*. It is refreshed on request rather than collected in real time:
  the figures are printed only inside chart images, and transcribing them is a
  manual step guarded by an overlap check against the historical spreadsheets.
  See `documentation/approval_refresh_runbook.md`.

The ideological trajectory is an analytical construct. Its Left / Right /
Center party and coalition assignments are maintained in `ui/common.py` and
should be treated as documented methodology—not as a raw INE classification.
Historical coalition mappings can be incomplete or contestable, so use the
view for exploration and pair any published claim with the underlying results
and methodology.

Useful partial materialization commands:

```bash
python -m ingestion.electoral_materialize views
python -m ingestion.electoral_materialize timeseries
python -m ingestion.electoral_materialize --force
# Refresh only municipal CVEGEO joins after editing overrides:
python -m ingestion.electoral_materialize municipios --force
```

## What To Open First

Open only the files needed for the task:

- Page flow, data loading, controls: `ine_explorer_v2.py`
- Maps and choropleth rendering: `ui/maps.py`
- Trajectory charts and state time series: `ui/trajectory.py`, `ui/charts.py`
- Presidential approval views: `ui/approval.py`
- Metric cards and badges used by the current app, plus reusable scorecard and
  results-table components retained for future views: `ui/tables.py`
- Gaceta Parlamentaria section: `ui/gaceta.py`
- Senado roll-call section: `ui/senado.py`
- Congressional composition hemicycles and seat drill-down: `ui/hemicycle.py`
- Person-name normalization and cross-source matching: `ui/person_names.py`
- Shared constants and pure helpers: `ui/common.py`
- Streamlit-ready parquet and GeoJSON generation: `ingestion/electoral_materialize.py`
- Clean parquet to SQLite ingestion: `ingestion/electoral_ingest.py`
- Raw/cycle-specific parsing: the matching converter in
  `ingestion/raw_electoral_data_converters/`
- Warehouse schema meaning: `documentation/table_dictionaries/overview.csv`
- Column-level table dictionaries: `documentation/table_dictionaries/*.csv`
- Roll-call pipeline detail: `documentation/diputados_infra.md`,
  `documentation/senado_infra.md`
- Validation/reference scripts: `aux_scripts/`
- Unit tests: `tests/`

Avoid reading large `data/` artifacts unless the task explicitly requires
debugging data values, joins, row counts, or generated output.

## Main Files

### `ine_explorer_v2.py`

Streamlit app entry point — page config, CSS, data loaders, and page routing.

Important responsibilities:

- Loads and caches materialized parquet views.
- Defines the five-section navigation and dispatches only to the selected
  section's render function.
- Loads the pre-built congressional-composition hemicycle assets.
- Provides shared page configuration and visual styling.

Open this file for page flow, selector logic, caching behavior, or Streamlit
deployment issues. For chart or map changes, open the relevant `ui/` module.

### `ui/` — UI modules

The render functions are split across focused modules:

- `ui/common.py` — shared party/coalition mappings, historical ideological
  assignments, colors, and pure aggregation helpers.
  No streamlit dependency — safe to import anywhere.
- `ui/maps.py` — shared bloc winner-map renderer for every presidential cycle,
  plus the GeoJSON loader and geometry utilities.
- `ui/charts.py` — historical bar charts and state-level time-series charts.
- `ui/trajectory.py` — the municipal ideological-trajectory tab, including
  ternary trajectories, turnout metrics, and state election deep dives.
- `ui/approval.py` — the presidential-approval tab and its polling views.
- `ui/tables.py` — metric cards and header badges used by the current app,
  plus reusable scorecard and results-table components retained for future
  views.
- `ui/gaceta.py` — the entire Gaceta Parlamentaria section: the deputy voting
  calendar and the LLM-classified vote explorer (filters, topic/consensus
  charts, vote-detail drilldown). Call `render_gaceta()` from the main app.
- `ui/senado.py` — the Senado de la República roll-call section: senator vote
  calendar and party-grid vote detail. Self-contained loaders and controls,
  but reuses `ui/gaceta.py`'s tile/calendar grid helpers rather than
  re-deriving the same layout math. Call `render_senado()`.
- `ui/hemicycle.py` — the **Congreso · Composición** section: loads the
  pre-built hemicycle figures from `data/cache/hemicycles/`, renders both
  chambers side by side, and routes a seat click into `render_gaceta()` or
  `render_senado()`. Call `render_hemicycle_composition()`.
- `ui/person_names.py` — normalization, display formatting, and
  order-independent token matching for person names. Used by both identity
  bridges to reconcile INE's "given names + surnames" ordering against the
  chambers' "surnames + given names" ordering. No streamlit dependency.

### `ingestion/electoral_materialize.py`

Turns `election_data.db` into files under `data/materialized/`.

Important responsibilities:

- Builds `view_<level>_<election_id>.parquet` files for:
  `casilla`, `seccion`, `municipio`, `estado`, and `nacional`.
- Writes `data/materialized/dim_candidatos.parquet`.
- Writes `data/materialized/timeseries_estados.parquet`.
- Builds `data/materialized/municipios_processed.geojson` from INEGI Marco
  Geoestadístico 2024.
- Canonicalizes state names and resolves municipality map joins against the
  INEGI `CVEGEO` code by state/name—not by the raw INE municipality number.
  Manual exceptions live in `data/municipio_cvegeo_overrides.csv`; unresolved
  names and non-applied fuzzy suggestions are written to
  `data/materialized/municipio_cvegeo_review.csv` for review.
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
- `csv_to_arrow_2009.py`
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

Provides official seat-integration readers plus experimental Cámara de
Diputados and Senado reconstructions from warehouse vote data. The dashboard
uses the official INE integration, not the reconstructions. The per-chamber
seat readers now live under `camara_de_diputados/escanos/` and
`camara_de_senadores/escanos/` (see below); this folder keeps only what's
genuinely shared across both chambers.

- `hemicycle_explorer.py` — generates an interactive in-browser hemicycle
  visualization with party summaries and a state filter, comparing both
  chambers side by side. Imports the two chamber-specific seat readers.
- `common.py` — shared helpers for official integration counts, actor totals,
  and natural-quotient/largest-remainder QA calculations, used by both
  chamber-specific seat readers.

`build_hemicycle_cache.py` (one level up, `aux_scripts/build_hemicycle_cache.py`)
materializes the official 2024 Congreso composition figures and summaries
used by Streamlit — kept as one shared pipeline since it renders both
chambers from the same parameterized function.

### `aux_scripts/qa_reports/`

R scripts for election-cycle QA checks:

- `qa_2012.R`
- `qa_2018.R`
- `qa_2024.R`

### `aux_scripts/approval_rates/`

Approval rating analysis for the 2018–2024 presidential period (`approval_rating.R`
and supporting Excel files).

### `aux_scripts/election_comparison/`

Cross-cycle party vote-share comparison script (`2018 vs 2024 presidential
party change.R`).

### `aux_scripts/turnout/`

State-level turnout change analysis across cycles (`turnout_change_by_state.R`).

### `aux_scripts/update_legislative_tracker.py`

One-shot refresh of everything MIEL (the legislative tracker) depends on:
current Diputados/Senado rosters plus Diputados/Senado roll-call votes.
Chains the individually documented crawl/parse/ingest steps below in the
right order, and reads the current (highest) LXVI legislature number for the
Gaceta parse/ingest steps instead of hardcoding it, so it keeps working
unchanged once a new legislature starts.

```bash
/usr/bin/python3 aux_scripts/update_legislative_tracker.py
```

Writes a rows/last-vote-date summary per source to
`documentation/legislative_tracker_status.md` after each run.

## Camara de Diputados / Camara de Senadores

Chamber-specific scrape/parse pipelines live in two top-level folders, one
per chamber, each organized by process:

```
camara_de_diputados/
  votos/         crawl/parse/classify Gaceta roll-call votes -> dim_gaceta_*
  iniciativas/   crawl initiative proposers -> dim_gaceta_iniciativa
  composicion/   crawl the current Camara roster -> data/clean_congress_rosters/
  escanos/       official MR/RP seat readers (diputados.py)

camara_de_senadores/
  votos/         crawl/parse/classify Senado roll-call votes -> dim_senado_*
  iniciativas/   crawl initiative proposers -> dim_senado_iniciativa
  composicion/   crawl the current Senado roster -> data/clean_congress_rosters/
  escanos/       official MR/FM/RP seat readers (senadores.py)
```

`ingestion/*.py` (warehouse loading) is unaffected by this split and still
lives at the repo root, one script per table family — see the workflow
sections below. Full detail per chamber: `documentation/diputados_infra.md`,
`documentation/senado_infra.md`.

### Scrape initiative proposers

Diputados (Gaceta `/Gaceta/Iniciativas/` pages — lists proposer name/party,
committee referral, and a direct link to the resulting vote page when one
exists):

```bash
python3 camara_de_diputados/iniciativas/crawl_gaceta_iniciativas.py --legislature 66
python3 -m ingestion.gaceta_iniciativas_ingest --force
```

Senado (the "Listado de Asuntos Publicados" tool — a single response
already scoped to the current legislature; no `--legislature` flag, since
the source's own filter doesn't narrow it):

```bash
python3 camara_de_senadores/iniciativas/crawl_senado_iniciativas.py
python3 -m ingestion.senado_iniciativas_ingest --force
```

Both write a `needs_review` flag for proposer text that doesn't match a
known template (joint/collective sponsorships, mostly) — raw text is always
preserved regardless. `dim_gaceta_iniciativa.vote_url` joins to
`dim_gaceta_vote.source_url`; `dim_senado_iniciativa` has no vote join yet.

## `articles/` — Long-form Quarto write-ups

Standalone Quarto/R analysis pieces, one subfolder per article. These read
the warehouse read-only and are not part of the Streamlit app or the
`ingestion/` pipeline.

- `articles/article_brujula_politica/` — the Brújula Política ideological-
  trajectory piece. `quarto/brujula_politica.qmd` renders to
  `quarto/brujula_politica.html`; `01_party_counts_per_election.R` and
  `graveyard/` hold earlier drafts and exploratory work.
- `articles/intro_MIEL/queries.R` — exploratory SQL/R for a MIEL (Monitor
  Integral y Estadístico Legislativo) piece: per-vote party-cohesion and
  initiative-proposer queries across both chambers. No `.qmd` yet.

Published articles reach the public site via
`web/scripts/build_article_pages.py`, which wraps the rendered Quarto HTML
with site chrome — see `web/README.md`.

## Data Layout

### Raw and Clean Inputs

These are local/intermediate data folders:

- `data/electoral_data_raw/raw_<year>/`: official source files, often large and inconsistent by
  year.
- `data/electoral_data_clean/clean_<year>/`: normalized per-cycle parquet outputs from the converter
  scripts.
- `election_data.db`: SQLite warehouse generated by `ingestion/electoral_ingest.py`.

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
codes. The municipio parquet views carry `_mun_code` for these joins,
resolved from the INEGI state/name catalog. `_mun_code_method` identifies an
exact-name match or a manual override; `_join_key` remains a diagnostic field.
Do not treat raw INE `ID_MUNICIPIO` as INEGI `CVE_MUN`.

The warehouse table `dim_municipio_map_crosswalk` records the same mapping at
one row per source municipio and election contest, including the raw INE ID,
resolved GeoJSON feature ID, matching method, and any non-applied fuzzy review
suggestion.

## Election IDs

Election IDs follow this pattern:

```text
PRE_<year>
DIP_MR_<year>
SEN_MR_<year>
```

Examples:

- `PRE_1994`, `PRE_2000`, `PRE_2006`, `PRE_2012`, `PRE_2018`, `PRE_2024`
- `DIP_MR_2000`, `DIP_MR_2006`, `DIP_MR_2009`, `DIP_MR_2012`, `DIP_MR_2015`,
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

**Cámara de Diputados roll-call votes (Gaceta Parlamentaria)**

- `dim_gaceta_vote`: one row per Gaceta roll-call vote page (metadata, URL,
  legislature, chamber).
- `dim_gaceta_deputy`: normalized deputy names observed across roll-call lists.
- `dim_diputados`: 500 official 2024 deputy seat assignments and the audited
  identity bridge to `dim_gaceta_deputy`.
- `fact_gaceta_vote_summary`: summary vote counts by choice and parliamentary
  group for each roll-call vote.
- `fact_gaceta_deputy_vote`: individual deputy vote records per roll-call vote.
- `fact_gaceta_vote_classification`: LLM-assigned topic, origin, and stage
  labels per vote, with model/prompt provenance.

**Senado de la República roll-call votes (Senado.gob.mx)**

- `dim_senado_vote`: one row per Senado roll-call vote page.
- `dim_senador`: normalized senator names observed across roll-call lists.
- `dim_senadores`: 128 official 2024 senator seat assignments and the identity
  bridge to `dim_senador`.
- `fact_senador_vote`: individual senator vote records per roll-call vote.

**Current Congreso rosters**

- `dim_congress_roster_snapshot`: provenance and cutoff for each official
  Cámara/Senado directory snapshot.
- `fact_congress_roster_seat`: all 500/128 constitutional seats at that
  cutoff, including the current occupant/group or an explicit vacancy.

These tables do not replace the INE dimensions. They keep current tenure and
parliamentary affiliation separate from the person and party elected in 2024.

The two chambers are separate namespaces end to end — vote IDs, identity
tables, and vote-choice vocabularies do not overlap. Never merge or aggregate
across them.

Start with `documentation/table_dictionaries/overview.csv` for row grain,
primary keys, joins, and purpose. Use the other CSVs in
`documentation/table_dictionaries/` for column-level details.

> **Join pitfall:** `dim_geography` is grained per **sección**, not per
> state — thousands of rows share the same `id_estado` within an
> `election_id`. Joining `fact_casilla_vote`/`dim_casilla` to
> `dim_geography` on `id_estado + election_id` fans out every vote row
> to every sección in that state (a cartesian blowup), which silently
> turns a few-second query into one that never finishes. To get
> `id_estado` for vote rows, join `dim_casilla` on `casilla_id +
> election_id` (it already carries `id_estado`) and skip `dim_geography`
> entirely. If you need `nombre_estado`, fetch it separately with
> `SELECT DISTINCT id_estado, nombre_estado FROM dim_geography WHERE
> election_id = ?` rather than joining it into the vote query.

Rebuild the identity bridges after refreshing either the official INE
integration or a chamber's roll-call warehouse:

```bash
python -m ingestion.diputados_ingest
python -m ingestion.senadores_ingest
```

Both bridges are strict: they refuse to commit unless every official seat
resolves, IDs are unique, and no seat maps to more than one roll-call
identity. Manually verified aliases live in `AUDITED_GACETA_NAME_OVERRIDES`
in `ingestion/diputados_ingest.py`, each annotated with why. Do not loosen
the global fuzzy-match threshold to absorb a one-off — that risks linking two
different legislators with similar names.

## Common Workflows

### Change app UI or charts

Usually open:

- `ine_explorer_v2.py` — for page flow, selectors, or caching
- `ui/maps.py` — for map or choropleth changes
- `ui/charts.py` — for bar chart, ternary, or timeseries changes
- `ui/tables.py` — for scorecards or results table changes
- `ui/gaceta.py` — for anything in the Gaceta Parlamentaria section
- `ui/senado.py` — for anything in the Senado roll-call section
- `ui/hemicycle.py` — for the composition hemicycles or seat drill-down
- `ui/common.py` — for constants like `CYCLE_BLOCS`, `PARTY_GROUPS`, colors

Then run:

```bash
python3 run_streamlit.py
```

### Fix map joins or missing municipios

Usually open:

- `ingestion/electoral_materialize.py`

Then run:

```bash
python -m ingestion.electoral_materialize views --force
python3 run_streamlit.py
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

- The matching converter in `ingestion/raw_electoral_data_converters/`
- `ingestion/electoral_ingest.py` if the clean schema or election metadata changes
- `documentation/table_dictionaries/raw_cycle_examples.csv` for source quirks

Then run the converter, followed by:

```bash
python -m ingestion.electoral_ingest
python -m ingestion.electoral_materialize --force
```


### Explore seat allocations and hemicycle

Usually open:

- `camara_de_diputados/escanos/diputados.py`
- `aux_scripts/seat_allocations/hemicycle_explorer.py`

Then run:

```bash
python3 aux_scripts/seat_allocations/hemicycle_explorer.py
```

This opens an interactive hemicycle in the browser. Use
`camara_de_diputados/escanos/diputados.py` or
`camara_de_senadores/escanos/senadores.py` directly for tabular seat counts
or QA against official results.

To refresh the official rosters and pre-built assets used by the
**Congreso · Composición** tab (no vote recrawl is involved):

```bash
python3 camara_de_diputados/composicion/crawl_diputados_roster.py --refresh
python3 camara_de_senadores/composicion/crawl_senadores_roster.py --refresh
python3 -m ingestion.congress_roster_ingest
python3 aux_scripts/build_hemicycle_cache.py
```

The roster collector downloads only the official member directories. The
cache builder combines their latest audited snapshot with the local copy of INE's
`INTEGRACION_CARGOS_PEF_2024.csv` and writes ignored files under
`data/cache/hemicycles/`. Streamlit offers separate current-composition and
2024-electoral-result views. Vacancies and directory entries marked as on
leave remain explicit rather than being silently assigned to an old occupant.
Source for the electoral baseline:
[INE · Integración de diputaciones y senadurías, PEF 2023–2024](https://ine.mx/integracion-de-diputaciones-y-senadurias-pef-2023-2024/).

### Scrape and ingest Gaceta roll-call votes

Usually open:

- `camara_de_diputados/votos/crawl_gaceta_metadata.py`
- `camara_de_diputados/votos/parse_gaceta_vote_batch.py`

Then run:

```bash
# Crawl vote-page metadata (polite cache/backoff included)
python3 camara_de_diputados/votos/crawl_gaceta_metadata.py --fetch-vote-pages

# Parse cached pages into parquet
python3 camara_de_diputados/votos/parse_gaceta_vote_batch.py

# Load parsed roll calls into the local SQLite warehouse, then refresh the app data
python3 ingestion/gaceta_ingest.py
python3 ingestion/gaceta_materialize.py --force
```

### Scrape and ingest Senado roll-call votes

Usually open:

- `camara_de_senadores/votos/crawl_senado_votes.py`
- `ingestion/senado_ingest.py`

Then run:

```bash
# Crawl + parse in one step (cache/backoff included; omit --all-votes to sample)
python3 camara_de_senadores/votos/crawl_senado_votes.py --all-votes

# Load parsed CSVs into the warehouse, then rebuild the identity bridge
/usr/bin/python3 ingestion/senado_ingest.py
python -m ingestion.senadores_ingest
```

For optional Senado semantic classification, use `prepare` → `submit` →
`retrieve` → `review` → `apply`. The review step preserves the raw model CSV
and writes `data/senado_vote_classification/classifications_reviewed.csv` with
documented audited corrections. Applied classifications appear under the
Senado switch in **Congreso · Clasificación de votos** and as filters on senator
vote calendars.

There is no Senado equivalent of `gaceta_materialize.py` yet — alignment,
cohesion, and classification metrics exist for the Cámara only. See
`documentation/senado_infra.md`.

### Export classified legislative votes to Google Sheets

Open
`aux_scripts/legislative_vote_review/export_legislative_votes_to_google_sheets.R`
in RStudio,
edit its configuration block, and click **Source**. The script reads the local
SQLite warehouse without modifying it and writes `Cobertura`, `Diputados`, and
`Senado` tabs. It includes the source vote totals, the applied OpenAI labels,
review fields, and model/prompt/timestamp provenance. Authentication is handled
interactively by `googlesheets4`; no project key is needed.

The Google account hint and Sheet ID are loaded from the gitignored
`config/legislative_vote_review.env`; copy the tracked `.env.example` file on a
new checkout. Do not store passwords, OAuth tokens, or API keys there.

The current configuration exports legislature 66. Set, for example,
`LEGISLATURES <- c(64, 65, 66)` for a larger validation cut or `NULL` for every
available legislature. Set `LEGISLATIVE_REVIEW_SHEET_ID` in the private env file
to update an existing Sheet.

To promote manual classification edits from those tabs back into the local
warehouse, source
`aux_scripts/legislative_vote_review/apply_google_sheet_classification_revisions.R`.
Calling `apply_google_sheet_revisions(spreadsheet = "...")` produces a
read-only before/after preview. Rerun with `apply = TRUE` only after inspection;
the importer validates IDs and taxonomy values, updates both chambers in one
transaction, preserves the original OpenAI provenance, and writes a local CSV
audit under `data/legislative_vote_manual_revisions/`. Vote totals and other
official-source fields are never overwritten from the Sheet. A changed Cámara
classification must also set `review_status` to `audited` and explain the
decision in `review_notes`. After applying revisions, rerun
`python3 web/scripts/export_gaceta_web.py` to refresh the website's static data.
The complete entry/exit contract, Parquet cache layout, and recovery guidance
are documented in `aux_scripts/legislative_vote_review/README.md`.

### Update warehouse schema

Usually open:

- `ingestion/electoral_ingest.py`
- `ingestion/electoral_materialize.py`
- `documentation/table_dictionaries/*.csv`
- `ine_explorer_v2.py` only if app-facing columns changed

## Tests

`tests/` holds unit tests for pure logic that is expensive to verify by
running the pipeline — currently the identity-bridge resolution rules in
`ingestion/diputados_ingest.py`.

```bash
/usr/bin/python3 -m unittest discover tests
```

## `web/` — Brújula Legislativa (prototype)

`web/` is a separate, **prototype-stage** Next.js single-page app that
presents the legislature-66 roll-call data as an interactive hemicycle for
both chambers. It is not part of the Streamlit pipeline and does not run in
this repo's Python environment.

It is a **static snapshot** application: `web/scripts/export_gaceta_web.py`
reads `election_data.db` plus the INE integration CSV and materializes the
roll-call hemicycle JSON files under `web/public/data/`.
`web/scripts/export_iniciativas_web.py` separately reads
`dim_gaceta_iniciativa`/`dim_senado_iniciativa` and writes
`web/public/data/iniciativas.json` — who proposed each initiative, kept apart
from the roll-call vote data. The browser loads these directly — there is no
live database at runtime.

```bash
python3 web/scripts/export_gaceta_web.py       # refresh roll-call snapshots
python3 web/scripts/export_iniciativas_web.py  # refresh initiative-proposer snapshot
cd web && npm test                              # production build + data invariants
```

It deploys to **Cloudflare Workers** (`npm run deploy`). It was originally
scaffolded onto OpenAI Sites; that coupling has been removed.

Read `web/README.md` before changing anything there: it documents the export
shape, the interaction invariants, and the deploy steps. Two things that
matter from outside `web/`:

- Changing warehouse schema for the deputy/senator seat, vote, or
  classification tables can break the exporter. Rerun it and `npm test`.
- A Cloudflare deploy is public — there is no login gate. Publishing is a
  deliberate choice, not a default.

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

`web/` carries its own `.gitignore` (`node_modules/`, `dist/`, `.wrangler/`).
Its generated snapshots under `web/public/data/` are several MB of derived
JSON — treat them like other materialized artifacts and stage them only when
deliberately refreshed.

## Notes for AI Assistants

- Prefer reading this README, `documentation/table_dictionaries/overview.csv`,
  and the specific target file before scanning the repo broadly.
- For roll-call work, read `documentation/diputados_infra.md` or
  `documentation/senado_infra.md` first — they are more specific than this
  file. For `web/`, read `web/README.md`.
- Never traverse `web/node_modules/`; it is ~30k files. Use `web/`'s own
  source paths listed in `web/README.md`.
- Do not open `data/materialized/*.parquet` or large raw files unless the task
  is explicitly about data debugging.
- For generated GeoJSON diffs, use summary commands (`git diff --stat`,
  row/key checks, or targeted JSON inspection) instead of reading the whole
  one-line file.
- If behavior depends on currently generated data, inspect the materialization
  code first, then query the relevant parquet only as needed.
- Keep README updates high-signal: this file is an orientation map, not a full
  data dictionary.
