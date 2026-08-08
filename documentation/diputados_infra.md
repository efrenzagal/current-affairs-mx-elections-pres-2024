# Cámara de Diputados — roll-call vote infrastructure

End-to-end pipeline that scrapes Gaceta Parlamentaria roll-call votes,
bridges them to official INE seat identities, and serves them through
Streamlit and the `web/` static export. Covers legislatures 58–66
(2000–2026), 5,245 votes, 5,169 distinct deputy names, 2.6M individual
vote records.

## Pipeline

```text
gaceta.diputados.gob.mx (HTML pages)
  -> aux_scripts/gaceta_votes/crawl_gaceta_metadata.py   (fetch + cache raw HTML)
  -> aux_scripts/gaceta_votes/parse_gaceta_vote_batch.py (parse cached HTML -> parquet)
  -> ingestion/gaceta_ingest.py                          (parquet -> election_data.db)
  -> ingestion/diputados_ingest.py                       (INE seats -> dim_diputados bridge)
  -> aux_scripts/gaceta_votes/classify_gaceta_votes.py    (optional: LLM topic/stage labels)
  -> ingestion/gaceta_materialize.py                      (db -> Streamlit-ready parquet)
  -> ui/gaceta.py                                         (Streamlit rendering)
```

## File map

| Path | Responsibility |
| --- | --- |
| `aux_scripts/gaceta_votes/crawl_gaceta_metadata.py` | Polite fetch/cache/backoff crawler. Caches every page under `data/raw_gaceta_votes/`; cache hits never re-hit the server. Vote-summary page fetching is opt-in and capped. |
| `aux_scripts/gaceta_votes/parse_gaceta_vote.py` | Parses a single cached vote page into summary counts and deputy-level rows. Pure parsing helpers, reused by the batch script. |
| `aux_scripts/gaceta_votes/parse_gaceta_vote_batch.py` | Walks cached pages, applies `parse_gaceta_vote.py`, writes per-legislature parquet under `data/gaceta_votes/clean/by_legislature/`. |
| `ingestion/gaceta_ingest.py` | Loads the per-legislature parquet into `election_data.db`, deduplicates deputies across legislatures, runs hard/soft QA (referential integrity, duplicate keys, unexpected vote-choice values, summary/detail reconciliation). |
| `ingestion/diputados_ingest.py` | Builds `dim_diputados`: matches all 500 official 2024 INE seats to `dim_gaceta_deputy` identities. |
| `aux_scripts/gaceta_votes/classify_gaceta_votes.py` | LLM classification via OpenAI Batch API: `prepare` (local, no network) → `submit` → `retrieve BATCH_ID` → `apply classifications.csv`. Writes `fact_gaceta_vote_classification`. |
| `ingestion/gaceta_materialize.py` | Computes alignment/cohesion/correlation metrics from the warehouse and writes Streamlit-ready parquet to `data/materialized/`. |
| `ui/gaceta.py` | Deputy voting-calendar view and LLM-classification explorer. Entry point: `render_gaceta()`. |

## Warehouse tables

| Table | Grain | Rows | Notes |
| --- | --- | --- | --- |
| `dim_gaceta_vote` | one roll-call vote page | 5,245 | URL, legislature, chamber, title, vote date, gaceta number/date, status text. |
| `dim_gaceta_deputy` | one normalized deputy name | 5,169 | `deputy_id` is deterministic from the normalized name; union across all legislatures. |
| `dim_diputados` | one official 2024 seat | 500 | Bridges INE seat/candidate identity to `dim_gaceta_deputy`. See "Identity bridge" below. |
| `fact_gaceta_vote_summary` | `(gaceta_vote_id, vote_choice, party_key)` | 291,252 | Summary matrix as shown on the Gaceta page, including `Total` rows/columns. |
| `fact_gaceta_deputy_vote` | `(gaceta_vote_id, deputy_id)` | 2,603,711 | Individual deputy vote choices. `party_key` is recorded per-fact because affiliation is time-specific. |
| `fact_gaceta_vote_classification` | one row per vote | 5,245 | LLM-assigned `origen`, `etapa_votacion`, `tipo_instrumento`, `tema_politica`, `confianza`, `requiere_revision`, `evidencia`, plus `model`/`prompt_version`/`classified_at` provenance. |

Column-level detail: `documentation/table_dictionaries/*.csv`, starting with `overview.csv`.

## Identity bridge (`dim_diputados`)

Matches each official INE seat's `PERSONA_CANDIDATA` (titular) — falling back
to `PERSONA_CANDIDATA_SUPLENTE` when the substitute actually took the seat —
against the Gaceta deputy roster, using order-independent, accent-free token
matching (`ui/person_names.match_person_name`). This handles INE's
"given names + surnames" order vs. Gaceta's "surnames + given names" order.

- Exact token-set match: preferred outcome.
- Approximate (Jaccard ≥ 0.67, gap ≥ 0.15 to the runner-up): covers initials
  and omitted middle names.
- `AUDITED_GACETA_NAME_OVERRIDES` in `ingestion/diputados_ingest.py`: manually
  verified aliases for cases the conservative matcher can't prove (source
  typos, unusual abbreviations). Every entry is comment-annotated with why.
- Validation is strict: `materialize_dim_diputados` refuses to commit unless
  all 500 seats resolve, IDs are unique, no seat maps to more than one
  Gaceta identity, and every approximate match clears the score threshold.

Rebuild after refreshing the INE integration file or the Gaceta warehouse:

```bash
python -m ingestion.diputados_ingest
```

## Current roster overlay

The election bridge remains immutable historical context. Current occupants
and parliamentary groups come from the official LXVI SITL group directories:

```bash
python3 aux_scripts/congress_rosters/crawl_congress_rosters.py --refresh
python3 -m ingestion.congress_roster_ingest
python3 aux_scripts/build_hemicycle_cache.py
```

`dim_congress_roster_snapshot` records the observation cutoff and source hash;
`fact_congress_roster_seat` resolves the 500 directory profiles to stable INE
seat IDs. MR seats use state+district; RP seats use audited titular/suplente
identity matching. Directory `LICENCIA` markers are preserved. This workflow
does not fetch or rebuild any roll-call votes.

## Materialized outputs (`data/materialized/`)

| File | Content |
| --- | --- |
| `gaceta_vote_index.parquet` | One row per vote with quorum/majority thresholds computed (`mayoria_simple_ok`, `mayoria_absoluta_ok`, `mayoria_calificada_ok`). |
| `gaceta_vote_quality.parquet` | Summary-vs-detail reconciliation flags; used to exclude incomplete roll calls from person/party metrics. |
| `gaceta_deputy_alignment.parquet` | Per deputy × legislature: alignment rate with party majority, absence rate. |
| `gaceta_party_cohesion.parquet` | Per party × legislature: cohesion score (share voting the majority direction). |
| `gaceta_party_vote_positions.parquet` | Per party × complete roll call: `(Favor − Contra) / (Favor + Contra)`. |
| `gaceta_party_vote_correlations.parquet` / `..._rolling.parquet` | Pearson correlation between party positions, overall and in trailing 6-month windows. |

Alignment/cohesion only use votes where `detail_complete = 1` in
`gaceta_vote_quality` — incomplete detail scrapes are excluded, not
zero-filled. Deputies need ≥10 active votes to appear in alignment output.

## Known quirks

- **Vote-choice vocabulary drifts by legislature era**: `Favor`/`Contra` vs.
  `A favor`/`En contra`, plus `Quórum *` (present but not voting) and
  `Abstención`/`Abstencion`. `gaceta_ingest.py`'s `KNOWN_VOTE_CHOICES` and
  `gaceta_materialize.py`'s `ACTIVE_CHOICES`/`ABSENCE_CHOICES` both need
  updating together if a new legislature introduces a new label.
- **Source date typos**: `crawl_gaceta_metadata.py`'s
  `KNOWN_SOURCE_DATE_TYPOS` holds case-by-case verified corrections where
  Gaceta's own source text has an internally inconsistent date (verified
  against weekday text and vote title, never applied speculatively).
- **`confianza`/`requiere_revision` measure model certainty, not correctness.**
  Review `requiere_revision = true` rows and spot-check a hand-labelled
  sample before treating classification labels as ground truth.

## Refresh

```bash
# 1. Crawl (polite cache/backoff; safe to interrupt and resume)
python3 aux_scripts/gaceta_votes/crawl_gaceta_metadata.py --fetch-vote-pages

# 2. Parse cached pages into parquet
python3 aux_scripts/gaceta_votes/parse_gaceta_vote_batch.py

# 3. Load into the warehouse + rebuild the identity bridge
python3 ingestion/gaceta_ingest.py
python -m ingestion.diputados_ingest

# 4. (optional) LLM classification
python3 aux_scripts/gaceta_votes/classify_gaceta_votes.py prepare
python3 aux_scripts/gaceta_votes/classify_gaceta_votes.py submit
python3 aux_scripts/gaceta_votes/classify_gaceta_votes.py retrieve BATCH_ID
python3 aux_scripts/gaceta_votes/classify_gaceta_votes.py apply data/gaceta_vote_classification/classifications.csv

# 5. Rebuild Streamlit-ready parquet
python3 ingestion/gaceta_materialize.py --force
```

## Consumers

- **Streamlit** (`ui/gaceta.py`): deputy voting calendar (`render_gaceta("Diputado", ...)`, driven from
  the `ui/hemicycle.py` seat click via `dim_diputados`) and the LLM-classification explorer
  (`render_gaceta("Clasificación")`).
- **`web/`** (static Next.js export): `web/scripts/export_gaceta_web.py` reads `dim_diputados`,
  `dim_gaceta_vote`, `fact_gaceta_deputy_vote`, `fact_gaceta_vote_summary`, and classifications
  directly from `election_data.db` — see `web/README.md`.
