# Source note — Enkoll

Source-specific companion to
[`approval_refresh_runbook.md`](../approval_refresh_runbook.md). Read both
before a refresh.

Enkoll is a continuing house: Oraculus supplies the frozen seed through
September 2025. Its national reports are PDF slide decks published on the
Enkoll WordPress uploads host.

## Finding and fetching a wave

Search Enkoll for `"APROBACIÓN PRESIDENCIAL" "Claudia Sheinbaum"`, then use
the direct PDF link. The report date in the filename/title is the field date;
the chart's month label is the value for `poll_month`.

```bash
/usr/bin/python3 aux_scripts/approval_rates/enkoll/fetch_enkoll_approval_pdf.py <PDF-URL>
```

The helper writes the original PDF into `data/clean_approval/pdfs/`. It reads
no values. Open the report and transcribe printed labels only.

## Headline approval chart

The eligible question is:

> En general, ¿usted “aprueba” o “desaprueba” el trabajo de Claudia Sheinbaum
> como presidenta de México?

The current split is printed on the opening approval slide. The recurring
time-series chart is the fifth PDF page (chart index `04` in
`chart_transcriptions.csv`); it includes `Aprueba`, `Desaprueba`, and `No
sabe`. Transcribe the first two values. The residual is the printed `No sabe /
No respondió` share; `RESTO_LIMIT["Enkoll"]` is 8.

The reports checked so far confirm that the trend is cumulative, not just a
single wave:

| Report | Trend window |
|---|---|
| 2026-03-04 | Dec 2024 to Mar 2026 |
| 2026-05-27 | Dec 2024 to May 2026 |

Both reports reproduce every overlapping point exactly. The points through
September 2025 also reconcile exactly with the Oraculus seed. Preserve that
overlap on future refreshes; it is the verification mechanism for newer
months.

## Exclusions

Do not transcribe the demographic breakdowns of approval, spontaneous
"logro/error" questions, rankings of national problems, or country-direction
questions. None is the recurring national approval question.

## State as of 2026-08-14

- Headline rows loaded through **2026-05** (68 / 27).
- 2025-12 and 2026-03 are corroborated by both reports; 2026-05 is currently
  a single-chart reading.
- No per-topic `desempeno` series has been identified for Enkoll.
