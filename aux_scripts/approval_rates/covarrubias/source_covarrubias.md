# Source note — Covarrubias y Asociados

Source-specific companion to
[`approval_refresh_runbook.md`](../approval_refresh_runbook.md). Read both
before a refresh.

The warehouse uses the existing Oraculus spelling, `Covarrubias y Asoc`, as
the pollster key. Covarrubias publishes through Pulso / its own channels as
PDF slide decks.

## Finding and fetching a wave

Look for a direct national PDF, then fetch it with:

```bash
/usr/bin/python3 aux_scripts/approval_rates/covarrubias/fetch_covarrubias_approval_pdf.py <PDF-URL>
```

The helper writes the original PDF to `data/clean_approval/pdfs/` and does not
read values. The September 2025 report was supplied as a local attachment;
its original Pulso URL returned 404 when checked on 2026-08-14. Keep the URL
in the transcription for provenance and locate a live replacement before the
next web refresh.

## Headline approval chart

The eligible question is:

> Por lo que ha visto hasta ahora, ¿usted aprueba o desaprueba el trabajo que
> ha hecho la Presidenta Claudia Sheinbaum?

The September 2025 deck's second PDF page (chart index `01`) prints `Aprueba`
as 72% and `Desaprueba` as 16%. It also prints `Ni aprueba, ni desaprueba`
(10%) and `No sabe` (2%); those categories form `resto`, so
`RESTO_LIMIT["Covarrubias y Asoc"] = 20`.

The fieldwork was 4–9 September 2025, so its `poll_month` is `2025-09`, not
the month the report was obtained. The existing frozen seed has no
Covarrubias row for that month; this is a new, one-chart observation.

## Exclusions

Do not transcribe the opinion, expectation, country-direction, results,
poverty, programme, or personal-attributes questions. None is the recurring
national approval split.

## State as of 2026-08-14

- Headline approval loaded through **2025-09** (72 / 16).
- The September 2025 row is `grafica`: no second source or seed overlap yet.
- No eligible per-topic `desempeno` series has been identified.
