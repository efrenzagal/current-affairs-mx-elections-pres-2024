# Legislative vote classification review sync

This folder defines the manual-review entry and exit points around
`election_data.db`. Google Sheets is the editable review surface; SQLite remains
the application warehouse; generated JSON remains the website's runtime data.

```text
                         manual review boundary
election_data.db  ->  Parquet cache  ->  Google Sheets
       ^                                      |
       |                                      |
       +--- validated diff + transaction -----+
       |
       +-> web/scripts/export_gaceta_web.py -> web/public/data/*.json
```

The Sheet is authoritative only for deliberate manual changes to classification
fields. It is not authoritative for official roll-call totals, source metadata,
or OpenAI provenance. Those columns are exported for context but ignored by the
importer.

## Dependencies

Install once in R:

```r
install.packages(c("DBI", "RSQLite", "googlesheets4", "arrow"))
```

Run RStudio from the repository or one of its subdirectories so the scripts can
find `election_data.db`. Google authentication is interactive and does not need
a project key.

## Local private configuration

The account hint and Google Sheet identifier live in:

```text
config/legislative_vote_review.env
```

That file is explicitly gitignored. A safe tracked template is available at
`config/legislative_vote_review.env.example`. On a new checkout, create the
private file with:

```bash
cp config/legislative_vote_review.env.example \
  config/legislative_vote_review.env
```

Then edit only the copied file:

```dotenv
LEGISLATIVE_REVIEW_GOOGLE_EMAIL=name@example.com
LEGISLATIVE_REVIEW_SHEET_ID=your_google_sheet_id_or_url
```

Neither value grants access by itself, but excluding them avoids publishing
private identifiers. Never place a password, service-account JSON, API key, or
OAuth token in this file. `googlesheets4` performs OAuth interactively and keeps
its token in its credential cache outside this repository.

## Exit point: SQLite to Google Sheets

Open `export_legislative_votes_to_google_sheets.R`, review its configuration,
and click **Source**. Important settings are:

- `SPREADSHEET` and `GOOGLE_EMAIL`: loaded automatically from the private env file.
- `LEGISLATURES`: `NULL` exports all available terms; `c(66)` exports the term
  currently used by the website.
- `ONLY_CLASSIFIED`: when `TRUE`, omit roll calls without an applied classification.
- `WRITE_PARQUET_CACHE`: when `TRUE`, retain the exact outbound extract locally.

The Sheet receives `Cobertura`, `Diputados`, and `Senado` tabs. Before upload,
the script writes:

```text
data/legislative_vote_review_cache/exports/<timestamp-pid>/
  cobertura.parquet
  diputados.parquet
  senado.parquet
```

These files are reproducibility caches, not another source of truth. `data/` is
gitignored, so copy important snapshots to managed storage if they must survive
machine loss.

## Editable columns

For both chambers, manual review may change:

- `origen`
- `etapa_votacion`
- `tipo_instrumento`
- `tema_politica`
- `requiere_revision`
- `evidencia`

For Cámara de Diputados it may also change `review_status` and `review_notes`.
A Cámara row with a manual classification change must set
`review_status = "audited"` and contain a non-empty explanation in
`review_notes`.

Do not use this workflow to correct official vote totals or source metadata.
Fix those upstream in the scraper/ingestion pipeline. The importer also
preserves `modelo_openai`, `prompt_version`, `clasificado_en`, and the Senate
model-confidence field because they describe the original model run.

## Entry point: Google Sheets to SQLite

Open and source `apply_google_sheet_classification_revisions.R`. Sourcing only
defines functions; it performs no authentication or database operation.

Preview the diff first:

```r
revision_preview <- apply_google_sheet_revisions()
View(revision_preview)
```

You can also override the configured Sheet for one call:

```r
revision_preview <- apply_google_sheet_revisions(
  spreadsheet = "GOOGLE_SHEET_URL_OR_ID",
  google_email = "name@gmail.com"
)
```

The preview validates tab names, required columns, unique and known vote IDs,
boolean review flags, and every taxonomy code. It returns only changed rows,
with `_antes` and `_despues` columns. SQLite remains untouched.

After inspecting the preview, apply deliberately:

```r
applied <- apply_google_sheet_revisions(apply = TRUE)
```

Immediately before the transaction, the script writes:

```text
data/legislative_vote_review_cache/imports/<timestamp-pid>/
  sheet_diputados.parquet
  sheet_senado.parquet
  database_diputados_before.parquet
  database_senado_before.parquet
  changes_before_after.parquet
```

It also writes a compact CSV diff under
`data/legislative_vote_manual_revisions/`. Both chamber updates occur in one
SQLite transaction: either all validated changes commit or none do. Missing
Sheet rows do not delete database rows.

The Parquet files are a safety snapshot, not an automatic restore mechanism.
If a rollback is needed, inspect `changes_before_after.parquet` and deliberately
restore the `_antes` values through a reviewed Sheet edit or a purpose-built
transaction.

## Refresh the current website

The website does not query Google Sheets or SQLite at runtime. After an import,
regenerate its static JSON from the repository root:

```bash
python3 web/scripts/export_gaceta_web.py
cd web
npm test
```

For a local visual review:

```bash
npm run dev
```

Publish only when the generated diff and tests look correct:

```bash
npm run deploy
```

The current exporter selects legislature 66 only. Revisions to Cámara terms
58–65 remain in SQLite but will not appear on the current site until the web
exporter and interface support historical terms.

The generated website files include `legislature-66.json`, `senate-66.json`,
`votes-66.json`, `vote-ballots-66.json`, and `visualizaciones.json` under
`web/public/data/`. Never hand-edit them.
