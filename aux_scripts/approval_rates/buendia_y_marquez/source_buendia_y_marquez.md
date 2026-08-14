# Source note — Buendía y Márquez

Source-specific companion to
[`approval_refresh_runbook.md`](../approval_refresh_runbook.md). Read both
before a refresh.

The warehouse preserves the Oraculus source spelling, `Buendia y Marquez`, as
the pollster key. It is part of the same `familia` as the earlier `Buendia y
Laredo` series, but the two remain distinct rows.

## Finding and fetching a wave

Buendía y Márquez publishes national reports as PDF slide decks on
`buendiaymarquez.org`, sometimes in partnership with *El Universal*. Search
for `site:buendiaymarquez.org aprobación presidencial Sheinbaum` and use the
direct PDF rather than an article recap.

```bash
/usr/bin/python3 aux_scripts/approval_rates/buendia_y_marquez/fetch_buendia_approval_pdf.py <PDF-URL>
```

The helper saves the original file in `data/clean_approval/pdfs/` and reads no
numbers.

## Headline approval chart

The eligible recurring question is:

> En términos generales, ¿usted aprueba o reprueba el trabajo que está haciendo
> Claudia Sheinbaum como Presidenta de la República? ¿Mucho o algo?

Transcribe `Aprueba mucho/algo` as `positivo` and `Reprueba mucho/algo` as
`negativo`. The `Ni aprueba ni reprueba` category and any unshown `NS/NC` form
`resto`; the house therefore uses `RESTO_LIMIT["Buendia y Marquez"] = 20`.

In the August 2025 report, the recurring chart is PDF page 5 (chart index
`04`). It covers Nov 2024, Jan, Feb, Apr, May and Aug 2025. All six points
match the Oraculus seed exactly, so the report corroborates existing data but
adds no month beyond the seed.

## Exclusions

Do not transcribe the approval-by-party or demographic breakdowns,
satisfaction-with-work scale, country-direction/evaluation questions, or the
open-ended questions. They are not the recurring national approval split.

## State as of 2026-08-14

- Chart verification loaded for Nov 2024 through Aug 2025: six seed rows,
  zero discrepancies.
- No post-September-2025 Buendía y Márquez approval report has been loaded.
- No eligible per-topic `desempeno` series has been identified.
