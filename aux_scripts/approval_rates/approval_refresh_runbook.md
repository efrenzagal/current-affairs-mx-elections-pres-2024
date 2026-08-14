# Refreshing presidential approval data

The original datasource came from Oraculus MX but they stopped updating after September 2025. I want to have the updated data and being able to easily refresh when possible. 
Approval data is **not** scraped on a schedule. It is refreshed on request,
because the only reliable numbers live inside chart images and reading those is
a judgment step, not a parsing step. This document is the general procedure and
applies to every polling house.

Per-source detail — where waves are published, how to fetch them, what the
charts look like and how that has changed — lives in a source note in that
house's own subfolder here, alongside its fetcher. Read the source note *and*
this document before a refresh.

| Source | Note | Status |
|---|---|---|
| El Financiero | [`el_financiero/source_el_financiero.md`](el_financiero/source_el_financiero.md) | Wired up |
| Demotecnia | [`demotecnia/source_demotecnia.md`](demotecnia/source_demotecnia.md) | Wired up (headline question wording changed partway through — see note) |
| Oraculus | — | Dead, frozen seed (Feb 1995 – Sep 2025) |
| Enkoll | [`enkoll/source_enkoll.md`](enkoll/source_enkoll.md) | Wired up |
| Buendía y Márquez | [`buendia_y_marquez/source_buendia_y_marquez.md`](buendia_y_marquez/source_buendia_y_marquez.md) | Wired up |
| Covarrubias | [`covarrubias/source_covarrubias.md`](covarrubias/source_covarrubias.md) | Wired up |

To trigger a refresh, say something like *"a new Encuesta EF is up, refresh the
approval data"* and point at the article.

---

## Why this is not automated

Automation was attempted and deliberately abandoned. The evidence, gathered
against El Financiero but general in character:

| Channel | Reliability | Verdict |
|---|---|---|
| Chart images | 12/12 and 13/13 against independent sources | **Authoritative** |
| Article prose | Failed on 4 of 10 articles; 1 unresolvable month | Context only |
| Tag-page discovery | Skips most of 2025 entirely | Unusable alone |
| Image captions | Inconsistent or empty | Cannot filter on |

Prose wording changes between waves and editors apply tags inconsistently. A
scraper built on either would fail silently and quietly poison the series — the
worst possible failure mode for a data set whose whole purpose is being
trustworthy. Reading the chart takes about a minute and has never been wrong.

## The five rules

These hold regardless of source, and every one of them exists because the
alternative produces data that looks fine and is wrong.

1. **Read printed data labels only.** Never estimate a value from where a point
   sits against the axis. If a point carries no printed number, that cell is
   missing — record it as missing and move on.
2. **`poll_month` is the month the poll *covers*,** not the month it was
   published. A wave published 2026-08-03 covering July is `2026-07`.
3. **Never edit a source to force agreement.** When the loader reports a
   conflict, re-read the chart. The check exists precisely because a misread
   digit is invisible once it lands in the warehouse.
4. **Transcribe overlaps deliberately.** Re-reading months already on file is
   encouraged: overlap is what converts a single reading into a verified one.
5. **Only transcribe the question the table is for.** Publications mix in
   one-off and differently-shaped questions. Charts that are not the headline
   approval question or a government-performance evaluation do not belong in
   these tables — see "What not to transcribe" below.

## The refresh procedure

### 1. Find and fetch the wave

Follow the source note for discovery and for the fetch command. A fetcher
writes prose plus every body chart into `data/clean_approval/`, and reads no
numbers — that is intentional.

### 2. Read the charts

Open each image and transcribe the printed labels. **Do not assume the chart
layout is the same as last time.** Panel order, the window each chart covers,
and which topics appear at all can change between waves without notice. Check
what each chart actually is before transcribing it.

### 3. Record what you read

Append to `chart_transcriptions.csv`:

```csv
pollster,president,serie,poll_month,tema,positivo,negativo,source_url,chart_index
El Financiero,Sheinbaum,aprobacion,2026-06,,68,32,https://...,00
El Financiero,Sheinbaum,desempeno,2026-06,Economía,46,40,https://...,01
```

- `pollster` and `president` are **required on every row** and are never
  inferred. Deriving the president from the month would be right for a house
  that only rates the sitting one and silently wrong for a wave that rates a
  predecessor — Demotecnia publishes exactly that comparison. A blank in
  either column aborts the load naming the line.
- `serie` is `aprobacion` (headline → `fact_approval_poll`) or `desempeno`
  (per-topic → `fact_approval_topic`).
- `tema` stays empty for `aprobacion`. Otherwise it must have an entry in
  `TEMA_POLITICA` in the ingest, which groups each house's wording under a
  shared slug so two houses asking about the same issue stay comparable. A
  tema with no entry aborts the load — add the mapping rather than inventing a
  near-duplicate of a topic already on file.
- `occurrence` is optional, defaulting to 1. Only needed if a house runs more
  than one wave in a month.

### What not to transcribe

Only two question shapes belong in these tables:

- **`aprobacion`** — "¿aprueba o desaprueba el trabajo...?"
- **`desempeno`** — "¿cómo calificaría la manera en que el gobierno está
  tratando...?", on the four-point scale.

Anything else is a different question and must be left out, however tempting
the numbers look. A question that ranks problems, rates an outcome rather than
the government's handling of it, or appears once around a news event is not
this series. If a recurring question genuinely deserves storage, give it its
own table rather than bending `fact_approval_topic` around it.

### 4. Load

```bash
/usr/bin/python3 ingestion/approval_ingest.py
```

The loader runs two independent cross-checks and treats a failure of either as
fatal:

- **Article vs article** — where two source documents describe the same cell,
  the readings must match.
- **Chart vs spreadsheet** — where the Oraculus seed and a transcription both
  describe a month, they must match.

A disagreement aborts the load naming the cell:

```
ERROR:   2025-09 El Financiero: spreadsheet 73/27 vs chart 63/27
```

Rows record which checks backed them in `extraction`, so confidence is always
visible in SQL:

| `extraction` | Meaning |
|---|---|
| `oraculus` | Spreadsheet seed only |
| `oraculus+grafica` | Spreadsheet and a chart agree |
| `grafica+grafica` | Two articles agree |
| `grafica` | **One chart, unverified** |

The load is idempotent, and asserts that every input row survives so a
primary-key collision can never silently drop observations.

### 5. Sanity-check

```sql
SELECT poll_month, aprueba, desaprueba, resto, extraction
FROM fact_approval_poll p JOIN dim_approval_pollster d USING(pollster_id)
WHERE d.pollster_name = 'El Financiero'
ORDER BY poll_month DESC LIMIT 12;
```

`resto` is `100 - aprueba - desaprueba` — **everything a house does not report
as explicitly positive or negative.** What that contains is a property of the
instrument, not a defect, and it differs sharply between houses:

| House | mean `resto` | What it absorbs |
|---|---|---|
| El Financiero | 1.9 | "No sabe" only |
| Demotecnia | 10.6 | an explicit neutral category, plus "No sabe" |
| BGC Telefonica | 10.3 | same |

So there is no single plausible range. The loader carries a per-house bound in
`RESTO_LIMIT`, and a new house needs an entry before its first load — the
default of 25 is deliberately loose and will not catch a misread digit at a
house that suppresses only don't-knows.

The check applies to chart-read rows, where an outlier is a re-readable
mistake. Oraculus seed rows are reported separately and not as a warning: they
were compiled years ago from sources that no longer exist, so a wide residual
there is a fact to know, not a task.

## Schema notes

Four tables, created by `ingestion/approval_ingest.py`:

- `dim_approval_pollster` — one row per house, with `pollster_type`
  (Casa encuestadora / Medio / Gobierno) and a `familia` label grouping split
  identities (BGC ×3, Ipsos ×2, Buendía under both partner names).
- `dim_approval_source` — one row per source document, spreadsheet or article,
  carrying the methodology note.
- `fact_approval_poll` — headline approval. Keyed on
  `(poll_month, pollster_id, occurrence)`. **`occurrence` matters**: Parametría
  ran up to four waves in a single month, and keying without it silently
  discarded 33 real observations.
- `fact_approval_topic` — issue evaluations, keyed on
  `(poll_month, pollster_id, tema)`. `tema` holds the house's own wording;
  `tema_politica` groups those under a shared slug. The slug vocabulary is
  borrowed from the gaceta classifier's `tema_politica` so the two sides can be
  joined ad hoc — but nothing here depends on that code, and `corrupcion` is
  local to approval. Group by `tema_politica` to compare houses, by `tema` to
  read one house faithfully.

`fact_approval_topic` uses `bien`/`mal`, **not** `aprueba`/`desaprueba`. It is a
different question on a four-point scale with the middle category suppressed,
and the columns are named apart specifically so the two series can never be
joined by accident. Do not "harmonize" these names.

## Adding a new source

The transcription and load steps are source-agnostic — `pollster` is just a
column, and cross-source agreement is checked automatically once two documents
cover the same month. Wiring up a new house:

1. **A subfolder here** named after the house (e.g. `enkoll/`), holding its
   fetcher and source note together — see the existing houses for the
   pattern.
2. **A fetcher** in that subfolder that puts prose and chart images into
   `data/clean_approval/`. If the house publishes through a partner outlet
   (Enkoll via *El País*, Buendía via *El Universal*), the fetcher targets the
   outlet.
3. **A `RESTO_LIMIT` entry** in the ingest. Work out what the house's `resto`
   absorbs — read the chart's own footnote — before the first load, not after.
4. **`TEMA_POLITICA` entries** for any topics it publishes, mapping its wording
   onto the shared slugs. Add a slug if nothing fits rather than forcing a bad
   match.
5. **A source note** in that subfolder, following the El Financiero one, and a
   row in the table at the top.
6. `classify_pollster()` and `POLLSTER_FAMILY` already carry the known house
   names, including every house Oraculus compiled. Check there before adding a
   new spelling — the point of `familia` is that split identities stay
   distinct rows with a shared label.

### The house is already in the series

Every house named in the table at the top **already has rows** in
`fact_approval_poll`, compiled by Oraculus. Demotecnia goes back to 2001-02
with 43 polls. So a new house is almost never a new series — it is the
continuation of one that stops in 2025.

That makes the first load its own check: transcribe a wave Oraculus already
covers, and the reconciler will tell you whether your reading of that house's
chart matches what Oraculus recorded. If it does not, the disagreement is
usually about *which categories the house folds into positive and negative* —
resolve that before loading anything new, because it silently changes every
row you go on to add.

### Subnational and one-off questions

Some houses publish approval broken out by state or city, and most publish
one-off questions around news events. Neither belongs in these tables as they
stand: every row here is a national figure for a recurring question, and a
subnational row mixed in would corrupt every average silently.

Leave them out. If an analysis genuinely needs them later, that is a schema
change — the warehouse rebuilds from source with `--force`, so there is no
migration debt in deferring it.

## When a source format changes

Expect this. General failure modes:

**The fetcher stops finding content.** The publisher moved off its CMS. Chart
images will still be in the page HTML; rewrite the fetch around the new
structure. Transcription and ingest are unaffected — they never touch HTML.

**The chart windows shrink or shift.** Verification depends on overlap between
documents. Note the new window in the source note and check that consecutive
waves still overlap by at least one month; if they stop overlapping, the series
loses its cross-check and that fact needs recording, not working around.

**Charts become interactive embeds** (Datawrapper, Flourish, etc.). This would
be *good news* — those services expose a `.csv` endpoint, and transcription
could be replaced with a real fetch. Worth checking for periodically.

**Numbers stop being printed.** Stop. Do not estimate from pixel positions.
Find another publication of the same wave, or record the month as missing.

## State as of 2026-08-14

- `fact_approval_poll`: 995 rows, 1995-02 → 2026-07, 21 houses
- `fact_approval_topic`: 74 rows, 19 temas, 2025-01 → 2026-07 (El Financiero and Demotecnia)
- Last El Financiero wave loaded: **2026-07** (67 / 33)
- Last Enkoll wave loaded: **2026-05** (68 / 27)
- Last Covarrubias wave loaded: **2025-09** (72 / 16)
- 26 months `oraculus+grafica`, 8 months `grafica+grafica`, 9 months `grafica`
- All listed sources are wired up.

The warehouse is fully derived: `--force` drops the four tables and rebuilds
them from the two spreadsheets and `chart_transcriptions.csv`. Those three
files plus this ingest are the whole source of truth, so schema changes cost a
rerun rather than a migration.
