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
| 2026-09-02 | Dec 2024 to "Sep 2026" (see label warning below) |

Both reports reproduce every overlapping point exactly. The points through
September 2025 also reconcile exactly with the Oraculus seed. Preserve that
overlap on future refreshes; it is the verification mechanism for newer
months.

## The month labels are not always the coverage month

**The `informe` decks label their newest point by publication month, not by
the month they polled.** The Segundo Informe deck (published 2026-09-02)
labels its last point `SEP 2026`, while its own methodology page states
fieldwork of **25–29 August 2026**. Under rule 2 of the main runbook that
point is `2026-08`, which is how it is stored.

The same deck also relabels two older points relative to the March and May
decks — same values, shifted one month:

| Point | Mar/May 2026 decks | Sep 2026 deck | Stored as |
|---|---|---|---|
| 79 / 18 | AGO 2025 | SEP 2025 | `2025-08` |
| 78 / 18 | SEP 2025 | OCT 2025 | `2025-09` |

Both stored months are `oraculus+grafica` — the Oraculus seed independently
agrees with the earlier decks — so the file is right and the newer deck is
labelling by publication. The 79/18 point is the *Primer* Informe wave
(published 2025-09-01, fielded late August 2025), which is the exact same
publication-vs-coverage offset one year earlier.

**Practical consequence:** do not transcribe the two informe-adjacent points
from an informe deck under that deck's own labels — they will collide with
correctly-dated rows. Take the newest point (re-dated to its fieldwork month)
and the unambiguous overlaps, and leave those two alone. The note in the
methodology page is the authority on coverage, not the axis label.

## Exclusions

Do not transcribe the demographic breakdowns of approval, spontaneous
"logro/error" questions, rankings of national problems, or country-direction
questions. None is the recurring national approval question.

## State as of 2026-09-05

- Headline rows loaded through **2026-08** (69 / 26), from the Segundo Informe
  deck published 2026-09-02 (fieldwork 25–29 August 2026).
- The eight unambiguous overlapping points on that deck all matched the file,
  which upgraded 2026-05 to `grafica+grafica`. 2026-08 is `grafica` until the
  next deck restates it.
- The El País write-up quotes 69 / 26 for this wave, matching the chart.
- No per-topic `desempeno` series has been identified for Enkoll.
