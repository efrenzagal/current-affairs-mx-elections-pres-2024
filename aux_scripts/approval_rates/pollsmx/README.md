# PollsMX approval refresh

Run from the repository root (Python standard library only):

```bash
python3 aux_scripts/approval_rates/pollsmx/refresh_pollsmx.py --export-web
```

This downloads every president listed by the public API, archives a timestamped
JSON response under `data/clean_approval/pollsmx/`, loads `election_data.db`, and
writes `web/public/data/approval-pollsmx.json` plus the compact
`web/public/data/approval-pollsmx-weekly.json` used by the approval dashboard.
Rerun the same command for updates.
The API supplies the full current history: a new model run may revise earlier
dates, so refreshes load the entire run rather than just appending recent dates.
Identical runs are skipped; previous runs remain queryable. Validation happens
before loading and all selected presidents load in one transaction. Older saved
snapshots cannot roll the current tables backward; replay them into a separate DB.

For a quicker refresh of Claudia Sheinbaum only:

```bash
python3 aux_scripts/approval_rates/pollsmx/refresh_pollsmx.py --president 00 --export-web
```

API keys: `005` Salinas, `004` Zedillo, `003` Fox, `002` Calderón,
`001` Peña Nieto, `006` López Obrador, `00` Sheinbaum. Keys remain strings to
preserve leading zeroes. Future unknown keys retain their provider names, with
local president code and inauguration date left empty until mapped.

Download without loading, or replay a saved response offline:

```bash
python3 aux_scripts/approval_rates/pollsmx/refresh_pollsmx.py --download-only
python3 aux_scripts/approval_rates/pollsmx/refresh_pollsmx.py --from-file data/clean_approval/pollsmx/snapshot-TIMESTAMP.json --db /tmp/pollsmx-review.db --export-web --web-output /tmp/approval-pollsmx.json
```

Tables:

- `dim_approval_pollsmx_president`: provider key, name, local code, inauguration,
  current run, last refresh time.
- `dim_approval_pollsmx_run`: model run ID, upstream version, API URL, retrieval
  time, archived response location, and response hash.
- `fact_approval_pollsmx_point`: metric, date, estimate, bounds, source ID and
  original series/point order, keyed by run and those orders.
- `view_approval_pollsmx_current`: only the latest observed run per president.

`line` points are model estimates; `arearange` points carry the provider's
uncertainty bounds (the API does not specify a confidence level); `scatter`
points are the observations shown by the provider. The latter carry opaque
`id_fuente` values, without pollster names, sample sizes or verified original
report URLs. They may have been transformed by PollsMX; do not treat them as
unmodified original poll figures or deduplicate observations by date/value.
Neither model estimates nor these observations are merged into the existing
Oraculus/chart-transcription tables. Source responses preserve extra fields.

```sql
SELECT president, observation_date, estimate, external_run_id
FROM view_approval_pollsmx_current
WHERE metric = 'aprobacion' AND series_type = 'line'
ORDER BY observation_date DESC;
```

The full web export is a separate versioned JSON dataset containing presidents,
current-run provenance and points. The approval dashboard reads `approval.json`
for surveys and `approval-pollsmx-weekly.json` for an optional aggregate overlay
for the selected president. Weekly points are the latest available daily approval
estimate in each Monday–Sunday week, including the latest partial week. They
remain visible when filtering polling houses and do not count toward survey totals.
Export again
without fetching via `python3 web/scripts/export_approval_pollsmx.py`. Website
publication uses the existing deployment workflow.
The refresh command does not deploy the website.

Endpoints used by the page:

- https://prep.polls.mx/api/v1/polls/scopes?source=approval
- https://prep.polls.mx/api/v1/polls/chart?source=approval&president=00

Requests are sequential with a one-second gap and bounded retries for transient
errors. Endpoint/schema changes fail explicitly instead of silently skipping data.
