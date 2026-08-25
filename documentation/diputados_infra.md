# Cámara de Diputados — roll-call vote infrastructure

End-to-end pipeline that scrapes Gaceta Parlamentaria roll-call votes,
bridges them to official INE seat identities, and serves them through
Streamlit and the `web/` static export. Covers legislatures 58–66
(2000–2026), 5,245 votes, 5,169 distinct deputy names, 2.6M individual
vote records.

## Pipeline

```text
gaceta.diputados.gob.mx (HTML pages)
  -> camara_de_diputados/votos/crawl_gaceta_metadata.py   (fetch + cache raw HTML)
  -> camara_de_diputados/votos/parse_gaceta_vote_batch.py (parse cached HTML -> parquet)
  -> camara_de_diputados/votos/ingest.py                          (parquet -> election_data.db)
  -> camara_de_diputados/escanos/ingest.py                       (INE seats -> dim_diputados bridge)
  -> camara_de_diputados/votos/classify_gaceta_votes.py    (optional: LLM topic/stage labels)
  -> camara_de_diputados/votos/materialize.py                      (db -> Streamlit-ready parquet)
  -> ui/gaceta.py                                         (Streamlit rendering)
```

## File map

| Path | Responsibility |
| --- | --- |
| `camara_de_diputados/votos/crawl_gaceta_metadata.py` | Polite fetch/cache/backoff crawler. Caches every page under `data/raw_gaceta_votes/`; cache hits never re-hit the server. Vote-summary page fetching is opt-in and capped. |
| `camara_de_diputados/votos/parse_gaceta_vote.py` | Parses a single cached vote page into summary counts and deputy-level rows. Pure parsing helpers, reused by the batch script. |
| `camara_de_diputados/votos/parse_gaceta_vote_batch.py` | Walks cached pages, applies `parse_gaceta_vote.py`, writes per-legislature parquet under `data/gaceta_votes/clean/by_legislature/`. |
| `camara_de_diputados/votos/ingest.py` | Loads the per-legislature parquet into `election_data.db`, deduplicates deputies across legislatures, runs hard/soft QA (referential integrity, duplicate keys, unexpected vote-choice values, summary/detail reconciliation). |
| `camara_de_diputados/escanos/ingest.py` | Builds `dim_diputados`: matches all 500 official 2024 INE seats to `dim_gaceta_deputy` identities. |
| `camara_de_diputados/votos/classify_gaceta_votes.py` | Legislatura 66 classification via OpenAI Batch API: `prepare` (local, no network) → `submit` → `retrieve BATCH_ID` → `review` → `apply classifications_reviewed.csv`. Writes `fact_gaceta_vote_classification`. |
| `camara_de_diputados/votos/materialize.py` | Computes alignment/cohesion/correlation metrics from the warehouse and writes Streamlit-ready parquet to `data/materialized/`. |
| `ui/gaceta.py` | Deputy voting-calendar view and LLM-classification explorer. Entry point: `render_gaceta()`. |

## Warehouse tables

| Table | Grain | Rows | Notes |
| --- | --- | --- | --- |
| `dim_gaceta_vote` | one roll-call vote page | 5,245 | URL, legislature, chamber, title, vote date, gaceta number/date, status text. |
| `dim_gaceta_deputy` | one normalized deputy name | 5,169 | `deputy_id` is deterministic from the normalized name; union across all legislatures. |
| `dim_diputados` | one official 2024 seat | 500 | Bridges INE seat/candidate identity to `dim_gaceta_deputy`. See "Identity bridge" below. |
| `fact_gaceta_vote_summary` | `(gaceta_vote_id, vote_choice, party_key)` | 291,252 | Summary matrix as shown on the Gaceta page, including `Total` rows/columns. |
| `fact_gaceta_deputy_vote` | `(gaceta_vote_id, deputy_id)` | 2,603,711 | Individual deputy vote choices. `party_key` is recorded per-fact because affiliation is time-specific. |
| `fact_gaceta_vote_classification` | one row per vote | 5,245 | `origen`, `etapa_votacion`, `tipo_instrumento`, `tema_politica`, `requiere_revision`, `evidencia`, local `review_status`/`review_notes`, plus model/prompt/timestamp provenance. The current workflow updates Legislatura 66 only. |
| `dim_gaceta_iniciativa` | one initiative | 6,720 (legislatura 66) | Proposer name/party (when a named legislator), committee referral, and `vote_url` joining to `dim_gaceta_vote.source_url` when the initiative reached a floor vote. See "Initiative proposers" below. |

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
- `AUDITED_GACETA_NAME_OVERRIDES` in `camara_de_diputados/escanos/ingest.py`: manually
  verified aliases for cases the conservative matcher can't prove (source
  typos, unusual abbreviations). Every entry is comment-annotated with why.
- Validation is strict: `materialize_dim_diputados` refuses to commit unless
  all 500 seats resolve, IDs are unique, no seat maps to more than one
  Gaceta identity, and every approximate match clears the score threshold.

Rebuild after refreshing the INE integration file or the Gaceta warehouse:

```bash
python -m camara_de_diputados.escanos.ingest
```

## Current roster overlay

The election bridge remains immutable historical context. Current occupants
and parliamentary groups come from the official LXVI SITL group directories:

```bash
python3 camara_de_diputados/composicion/crawl_diputados_roster.py --refresh
python3 camara_de_senadores/composicion/crawl_senadores_roster.py --refresh
python3 -m camara_de_diputados.composicion.ingest
python3 -m camara_de_senadores.composicion.ingest
python3 aux_scripts/build_hemicycle_cache.py
```

`dim_congress_roster_snapshot` records the observation cutoff and source hash;
`fact_congress_roster_seat` resolves the 500 directory profiles to stable INE
seat IDs. MR seats use state+district; RP seats use audited titular/suplente
identity matching. Directory `LICENCIA` markers are preserved. This workflow
does not fetch or rebuild any roll-call votes.

Two temporal facts are rebuilt from those append-only snapshots:

- `fact_congress_seat_occupancy` collapses unchanged snapshots into non-overlapping
  occupant/status intervals per stable seat.
- `fact_congress_party_membership` keeps official-directory and vote-reported
  affiliation episodes as separate source series. It never overwrites the
  immutable INE `election_party`.

`data/diputados_roster_reconciliation.csv` (and its
`data/senadores_roster_reconciliation.csv` counterpart — one per chamber,
since each roster run now writes its own) compares the latest official directory with
the electoral origin and the latest roll-call episode. A `LICENCIA` seat remains
attached to its published profile for provenance, but the current hemicycle
shows it as `LICENCIA` rather than counting it as an active party seat until an
official acting substitute is resolved.

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

## Initiative proposers

`camara_de_diputados/iniciativas/crawl_gaceta_iniciativas.py` walks the
separate `/Gaceta/Iniciativas/` page tree (not `/Gaceta/Votaciones/`), which
lists who proposed each initiative rather than how it was voted:

```bash
python3 camara_de_diputados/iniciativas/crawl_gaceta_iniciativas.py --legislature 66
python3 -m camara_de_diputados.iniciativas.ingest --force
```

`--legislature` defaults to the current (highest) legislature on the index.
Only the target legislature's period pages are force-refreshed on each run —
closed legislatures are immutable — mirroring the fix already applied to the
votes crawler for the same "don't cache a growing list forever" bug.

Each entry is regex-classified into `proposer_type`: `legislador` (named
deputy/senator + parliamentary group), `ejecutivo` (Ejecutivo federal), or
`minuta` (sent over by the other chamber); anything that doesn't match a
known template gets `proposer_type='otro'` and `needs_review=1`, with the
raw text always preserved regardless. About 4–5% of rows land in
`needs_review`, mostly multi-signatory joint initiatives. When the
initiative reached a floor vote, `vote_url` resolves exactly against
`dim_gaceta_vote.source_url` — verified 1:1 on the current data.

## Known quirks

The charts include every LXVI vote record published and downloaded from the
official source, mapped to a constitutional seat. Seats omitted by the source
remain **“Sin registro”**; they are not imputed as absent or assigned a
fabricated vote.

- **Vote-choice vocabulary drifts by legislature era**: `Favor`/`Contra` vs.
  `A favor`/`En contra`, plus `Quórum *` (present but not voting) and
  `Abstención`/`Abstencion`. `camara_de_diputados/votos/ingest.py`'s
  `KNOWN_VOTE_CHOICES` and `camara_de_diputados/votos/materialize.py`'s
  `ACTIVE_CHOICES`/`ABSENCE_CHOICES` both need
  updating together if a new legislature introduces a new label.
- **Source date typos**: `crawl_gaceta_metadata.py`'s
  `KNOWN_SOURCE_DATE_TYPOS` holds case-by-case verified corrections where
  Gaceta's own source text has an internally inconsistent date (verified
  against weekday text and vote title, never applied speculatively).
- **Model self-confidence is deliberately excluded.** Reliability comes from
  literal source hints, related-roll-call consistency, deterministic local
  checks, `review_status`, textual evidence, and a human-audited sample.
  `submit` refuses stale requests or a manifest whose row count no longer
  matches the complete target legislature. `apply` refuses unresolved
  `needs_review` rows unless the operator explicitly retains them with
  `--allow-needs-review`.

## Refresh

```bash
# 1. Crawl (polite cache/backoff; safe to interrupt and resume)
python3 camara_de_diputados/votos/crawl_gaceta_metadata.py --fetch-vote-pages

# 2. Parse cached pages into parquet
python3 camara_de_diputados/votos/parse_gaceta_vote_batch.py

# 3. Load into the warehouse + rebuild the identity bridge
python3 camara_de_diputados/votos/ingest.py
python -m camara_de_diputados.escanos.ingest

# 4. (optional) LLM classification
python3 camara_de_diputados/votos/classify_gaceta_votes.py prepare
python3 camara_de_diputados/votos/classify_gaceta_votes.py submit
python3 camara_de_diputados/votos/classify_gaceta_votes.py retrieve BATCH_ID
python3 camara_de_diputados/votos/classify_gaceta_votes.py review
# Resolve needs_review rows, then:
python3 camara_de_diputados/votos/classify_gaceta_votes.py apply \
  data/gaceta_vote_classification/classifications_reviewed.csv

# 5. Rebuild Streamlit-ready parquet
python3 camara_de_diputados/votos/materialize.py --force
```

## Consumers

- **Streamlit** (`ui/gaceta.py`): deputy voting calendar (`render_gaceta("Diputado", ...)`, driven from
  the `ui/hemicycle.py` seat click via `dim_diputados`) and the LLM-classification explorer
  (`render_gaceta("Clasificación")`).
- **`web/`** (static Next.js export): `camara_de_diputados/escanos/seat_members.py` first resolves
  seat occupancy, person aliases and seat/vote conflicts into the `fact_legislature_66_*` tables.
  `web/scripts/export_gaceta_web.py` then reads those alongside `dim_diputados`, `dim_gaceta_vote`,
  `fact_gaceta_deputy_vote`, `fact_gaceta_vote_summary`, and classifications from
  `election_data.db` — see `web/README.md`. The exporter shapes; it no longer derives.
