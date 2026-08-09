# Senado de la República — roll-call vote infrastructure

End-to-end pipeline that scrapes Senado.gob.mx roll-call votes, bridges them
to official INE seat identities, and serves them through Streamlit and the
`web/` static export. Covers legislature 66 only (2024–2026, the only
legislature senado.gob.mx currently publishes structured vote pages for):
378 votes, 184 distinct senator identities, 39,944 individual vote records.

## Pipeline

```text
senado.gob.mx (HTML list page + per-vote pages + AJAX detail endpoint)
  -> aux_scripts/senado_votes/crawl_senado_votes.py  (fetch + cache + parse -> CSV)
  -> ingestion/senado_ingest.py                      (CSV -> election_data.db)
  -> ingestion/senadores_ingest.py                   (INE seats -> dim_senadores bridge)
  -> aux_scripts/senado_votes/classify_senado_votes.py (optional Batch API classification)
  -> ui/senado.py                                    (Streamlit rendering)
```

Semantic classification is optional and independent from vote ingestion.
There is still no alignment/cohesion materialize step; that remains the main
structural gap versus the Diputados pipeline (see `diputados_infra.md`).

## Source shape

- **List page** (`https://www.senado.gob.mx/66/votaciones/por_legislatura/LXVI/`):
  a single static page, no pagination, listing every roll call as a link to
  `/66/votacion/{id}`. This is the only page that needs to be crawled to
  discover all vote IDs for the legislature.
- **Per-vote page** (`/66/votacion/{id}`): renders only the description, vote
  type label, date, período/año de ejercicio, and chamber-wide PRO/CONTRA/
  ABSTENCIÓN totals server-side. The senator-by-senator table is an empty
  skeleton in the static HTML.
- **AJAX detail endpoint**
  (`/66/app/votaciones/functions/viewTableVot.php?action=ajax&cell=1&order=DESC&votacion={id}&q=`):
  what the page's own JS calls to fill that table. Returns all senators for
  the vote in one response (confirmed: no pagination regardless of the
  `cell` parameter) — the crawler calls it directly instead of scraping the
  rendered page.

## File map

| Path | Responsibility |
| --- | --- |
| `aux_scripts/senado_votes/crawl_senado_votes.py` | Fetch/cache/backoff crawler (mirrors the Gaceta crawler's pattern). Caches every page under `data/raw_senado_votes/`; writes `dim_senado_vote.csv` and `senado_vote_detail.csv` to `data/clean_senado_votes/`. |
| `ingestion/senado_ingest.py` | Loads the clean CSVs into `election_data.db` (`dim_senado_vote`, `dim_senador`, `fact_senador_vote`), runs QA, then calls `senadores_ingest.py`. |
| `ingestion/senadores_ingest.py` | Builds `dim_senadores`: matches all 128 official 2024 INE seats to `dim_senador` identities. |
| `ui/senado.py` | Senator voting-calendar view and vote-detail party grid. Entry point: `render_senado()`. Reuses `ui/gaceta.py`'s tile/calendar plotting helpers rather than re-deriving the layout math. |

## Warehouse tables

| Table | Grain | Rows | Notes |
| --- | --- | --- | --- |
| `dim_senado_vote` | one roll-call vote page | 378 | `votacion_id`, url, vote_date, period_type (ORDINARIO/EXTRAORDINARIO), ordinal_period, exercise_year, description, vote_type, en_pro/en_contra/abstencion. |
| `dim_senador` | one normalized senator name | 184 | `senador_id` is the numeric ID senado.gob.mx itself uses (from `/66/votaciones/{id}#info` links) — not a derived hash, unlike `dim_gaceta_deputy`. |
| `dim_senadores` | one official 2024 seat | 128 | Bridges INE seat/candidate identity to `dim_senador`. See "Identity bridge" below. |
| `fact_senador_vote` | `(votacion_id, senador_id)` | 39,944 | `grupo_parlamentario`, `voto` (PRO/CONTRA/ABSTENCIÓN/AUSENTE), `voto_detail` (e.g. "COMISIÓN OFICIAL" reason on some AUSENTE rows). |

No column-level dictionary CSVs exist yet under `documentation/table_dictionaries/`
for these four tables — only `overview.csv`-style prose here.

## Identity bridge (`dim_senadores`)

Same matcher as Diputados (`ui/person_names.match_person_name`), applied to
128 seats: 64 mayoría relativa (2 per state), 32 primera minoría (1 per
state, `seat_type = "FM"`), 32 representación proporcional.

- Seat key: `SEN_MR` uses `(id_estado, numero_lista)`; `SEN_RP` uses
  `(partido_politico, numero_lista)` — both stable regardless of candidate
  spelling.
- senado.gob.mx names come as `"Sen. Apellidos, Nombres"`. The `"Sen. "`
  title is stripped before matching (`strip_senado_title`) — left in place,
  its all-caps token would falsely appear to match any INE name.
- Result on the current data: 105 exact-token matches, 19 approximate
  (Jaccard ≥ 0.67), 4 audited overrides in
  `AUDITED_SENADO_NAME_OVERRIDES` (`ingestion/senadores_ingest.py`) — all
  four are cases where senado.gob.mx's roster omits a middle name or second
  surname that INE spells out in full, pushing the token-set Jaccard score
  just under threshold (e.g. score 0.60–0.667).
- Validation is strict, same shape as `dim_diputados`: refuses to commit
  unless all 128 seats resolve, IDs are unique, no seat maps to more than
  one senator, seat-type split is exactly `{MR: 64, FM: 32, RP: 32}`, and
  every approximate match clears the score threshold.

Rebuild after refreshing the INE integration file or the Senado warehouse:

```bash
python -m ingestion.senadores_ingest
```

## Known quirks

- **Party switching ("chapulineo")** is real and shows up in the data as
  `grupo_parlamentario` changing value across `votacion_id`s for the same
  `senador_id` — there is no separate "party history" table. To analyze
  party cohesion correctly, group by `(senador_id, grupo_parlamentario)`
  per vote, not by senator alone. A few votes right at a switch date carry a
  blank `grupo_parlamentario` because senado.gob.mx's own system hadn't
  updated the field yet; one senator (Beltrones Rivera) is genuinely
  unaffiliated and the site renders that inconsistently as either an
  explicit `SG` (Sin Grupo) or a blank cell for the same status.
- **`voto` on an AUSENTE row can carry a `voto_detail` reason** (e.g.
  `AUSENTE` / `COMISIÓN OFICIAL`) from a `<br>`-separated cell on the source
  page. The crawler splits on `<br>` explicitly — a plain `get_text(strip=True)`
  silently concatenates the two lines into one word (caught and fixed during
  development; see the crawler's inline comment).
- **`period_type` parsing must be case-insensitive.** senado.gob.mx uses two
  different source formats for the same information: `"Periodo EXTRAORDINARIO
  PRIMER AÑO DE EJERCICIO"` (mixed case) for extraordinary sessions, vs.
  `"PRIMER AÑO DE EJERCICIO PRIMER PERIODO ORDINARIO"` (all caps, word order
  swapped) for ~91% of ordinary-session pages. A case-sensitive match against
  `"Periodo"` silently misses the all-caps form.
- **No alignment/cohesion metrics yet** — unlike the Diputados pipeline,
  there is no `senado_materialize.py`. `fact_senador_vote` has the same shape needed to
  build the equivalent of `gaceta_deputy_alignment.parquet` /
  `gaceta_party_cohesion.parquet` when that's wanted.

## Optional semantic classification

The Senate classifier uses `description`, `vote_type`, date, and period
metadata. `prepare` is entirely local; network access occurs only with the
explicit `submit` and `retrieve` commands:

```bash
python3 aux_scripts/senado_votes/classify_senado_votes.py prepare
python3 aux_scripts/senado_votes/classify_senado_votes.py submit
python3 aux_scripts/senado_votes/classify_senado_votes.py retrieve BATCH_ID
# Preserve the raw model CSV and apply the documented audited corrections:
python3 aux_scripts/senado_votes/classify_senado_votes.py review
# Review classifications_reviewed.csv before applying it:
python3 aux_scripts/senado_votes/classify_senado_votes.py apply \
  data/senado_vote_classification/classifications_reviewed.csv
```

Applied rows are stored in `fact_senado_vote_classification`. The taxonomy is
parallel to the Cámara classifier, except that legislative origin distinguishes
`minuta_de_camara_de_diputados` from `dictamen_de_comisiones`. Missing
`vote_type` must lower stage certainty rather than trigger a guessed phase.
The Streamlit **Congreso · Clasificación de votos** section exposes these rows
through its Senado chamber switch, four taxonomy filters, review queue, topic
summary, and roll-call drill-down. Senator calendars can also be filtered by
topic and voting stage.

## Current roster overlay

The official Senate "en funciones" directory is collected independently from
roll calls by `aux_scripts/congress_rosters/crawl_congress_rosters.py` and
resolved by `ingestion/congress_roster_ingest.py`. Official Senate profile IDs
are preferred; registered titular/suplente names and audited seat-specific
overrides cover changed profile IDs. A directory below 128 members produces
an explicit vacant seat rather than restoring the elected officeholder.

The snapshot cutoff, source URL and content hash are stored in
`dim_congress_roster_snapshot`; the complete 128-seat state is stored in
`fact_congress_roster_seat`. No vote pages are fetched during this refresh.

## Refresh

```bash
# 1. Crawl (polite cache/backoff; --all-votes for the full legislature, default is last 10)
python3 aux_scripts/senado_votes/crawl_senado_votes.py --all-votes

# 2. Load into the warehouse + rebuild the identity bridge
python -m ingestion.senado_ingest --force

# 3. Rebuild the hemicycle cache so seat customdata picks up any new senador_seat_id
python3 aux_scripts/build_hemicycle_cache.py
```

## Consumers

- **Streamlit**: `ui/hemicycle.py` makes each Senado seat clickable; a click resolves through
  `dim_senadores` and calls `ui/senado.py`'s `render_senado("Senador", senador_seat_id=..., ...)`.
- **`web/`** (static Next.js export): reads `dim_senadores`, `dim_senado_vote`, `fact_senador_vote`
  directly from `election_data.db` — see `web/README.md`.
