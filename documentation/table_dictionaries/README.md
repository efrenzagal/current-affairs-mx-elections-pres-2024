# Table Dictionaries

This folder documents the ingested election-data warehouse and the raw source
files that feed it.

Start with `overview.csv`. It describes each normalized table, its primary key,
row grain, purpose, and important joins.

## Election Coverage

`dim_election` currently holds 18 contests across 8 federal cycles, all backed
by populated `fact_casilla_vote` rows (not just metadata):

| Year | President (PRE) | Deputies MR (DIP) | Senate MR (SEN) |
|---|---|---|---|
| 1994 | ✅ | — | — |
| 2000 | ✅ | ✅ | ✅ |
| 2006 | ✅ | ✅ | ✅ |
| 2012 | ✅ | ✅ | ✅ |
| 2015 | — | ✅ | — |
| 2018 | ✅ | ✅ | ✅ |
| 2021 | — | ✅ | — |
| 2024 | ✅ | ✅ | ✅ |

Notes:
- Only *mayoría relativa* (first-past-the-post district) seats are ingested for
  deputies and senate. *Representación proporcional* (RP) raw source files
  exist under `data/electoral_data_raw/raw_2024/DIPUTACIONES_FED_RP_2024` and
  `SENADURIAS_RP_2024`, but there is no RP ingestion path yet — RP is not in
  the warehouse for any cycle.
- `dim_candidatos` is populated for 2015, 2021, and 2024 only; 1994/2000/2006/
  2012/2018 did not have candidate catalogs available for ingestion.
- The clean parquet layer (`data/electoral_data_clean/clean_*`) mirrors the
  db exactly for all 8 cycles — nothing is loaded into one and missing from
  the other.

## Normalized Warehouse Tables

### Electoral Results

These files describe the SQLite/parquet schema populated by `ingestion/electoral_ingest.py`:

- `dim_election.csv` — one row per election contest (year, office, type)
- `dim_geography.csv` — geography keys scoped per election cycle (estado,
  municipio, sección, distrito federal, circunscripción). Primary key is
  `(geo_id, election_id)` because section boundaries and district assignments
  can differ across cycles.
- `dim_casilla.csv` — one polling station or acta per election. Carries
  `lista_nominal` (registered voters) and casilla type.
- `dim_party.csv` — normalized party/coalition vote options. `party_key` can
  represent a single party, coalition, or source-era label. Coalition membership
  is recorded in the `members` column when known.
- `dim_candidatos.csv` — candidate catalog rows where available. Present for
  2015 and 2021 cycles; absent for 1994/2000/2006.
- `fact_casilla_vote.csv` — long-format vote totals: one row per
  `(election_id, casilla_id, party_key)`.

### Legislative Roll-Call Votes (Gaceta Parlamentaria)

These files describe tables populated by scraping the Cámara de Diputados
Gaceta Parlamentaria. Data is collected separately from the electoral pipeline
using `aux_scripts/gaceta_votes/`.

- `dim_gaceta_vote.csv` — one row per Gaceta roll-call vote page: URL,
  legislature, chamber, vote title, and detail endpoint.
- `dim_gaceta_deputy.csv` — normalized deputy names observed across roll-call
  detail lists. `deputy_id` is derived deterministically from the normalized
  name.
- `fact_gaceta_vote_summary.csv` — summary vote matrix by choice
  (A favor / En contra / Abstención) and parliamentary group for each roll-call.
  Includes Total rows/columns as reported by Gaceta.
- `fact_gaceta_deputy_vote.csv` — individual deputy vote records:
  one row per `(gaceta_vote_id, deputy_id)`. `party_key` is recorded on the
  fact because parliamentary affiliation is time-specific.

## Raw Source References

Files prefixed with a year describe representative raw source files as
received from INE, before normalization. Use these to understand source quirks
when editing a cycle-specific converter in `ingestion/*_to_arrow_*.py`.

- `[1994] 1994_PRE_CAS_94.csv` — fixed-width `.DAT` source layout for the 1994 presidential election
- `[2000] 2000_DAT_DISTRITALES_CAS.csv` — district-level DAT format used in 2000
- `[2006] 2006_ESTADISTICAS_TXT_CAS.csv` — TXT/pipe-delimited format used in 2006
- `[2015] DIPUTADOS_CAS.csv` — CSV format introduced for 2015 diputados
- `[2021] DIPUTACIONES_CAS.csv` — CSV format for 2021 diputaciones
- `[2024] 2024_SEE_PRE_NAL_CAS.csv` — CSV format used in 2024 presidential

`raw_cycle_examples.csv` summarizes the source layout by cycle, including
delimiters, header locations, example rows, and important quirks discovered
during ingestion.
