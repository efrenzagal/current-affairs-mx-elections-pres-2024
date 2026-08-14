# Source note — Demotecnia (De las Heras Demotecnia)

Source-specific companion to
[`approval_refresh_runbook.md`](../approval_refresh_runbook.md). The general
rules and the load step live there; this file covers only what is particular
to Demotecnia. Read both before a refresh.

Demotecnia already has 43 historical polls in `fact_approval_poll`, compiled
by Oraculus (frozen seed, through 2025-09). This is the continuation of that
series, not a new one — see "The house is already in the series" in the main
runbook.

---

## Finding a wave

No feed or tag page identified yet. Browse
<https://www.demotecnia.com.mx/encuestas/> (paginated) and look for national
report titles: `Encuesta Nacional <mes> <año>`, `Evaluación de Gobierno
Federal <mes> <año>`, `Estudio Nacional Evaluación de Gestión <mes> <año>`.
State/municipal reports (`Encuesta <estado> <mes> <año>`) are out of scope —
see "Subnational" in the main runbook.

## Fetching

No API found; each report is a WordPress page embedding a slide deck as
numbered JPGs. Pull the image URLs directly:

```bash
curl -s -A "Mozilla/5.0 ..." "<report URL>" | grep -oE '(src|data-src)="[^"]+\.(jpg|jpeg|png|webp)"'
```

Images are named `<DECK-TITLE>_page-00NN-1024x576.jpg`. The cover slide
(`page-0001`) is often not referenced in the page HTML at all — its absence is
normal, not a fetch failure. Download and read each `page-00NN` in order;
there is no prose to fall back on.

## Reading the charts — report type matters

**Demotecnia runs at least three differently-shaped national report
templates, and which questions appear depends entirely on which template a
given wave used.** Do not assume this month's deck has the same slide set as
last month's, even within the same nominal series.

### The headline question is not consistent across templates

| Template | Headline chart | Wording |
|---|---|---|
| `Estudio Nacional Evaluación de Gestión` (e.g. Sept 2025) | "¿Usted aprueba o desaprueba el trabajo de la presidenta...?" | Matches Oraculus's historical wording exactly — confirmed by reconciliation (2025-09: chart 74/15 vs Oraculus seed 74/15) |
| `Encuesta Nacional` (e.g. Jun 2026) | "¿Qué opinión tiene usted de la Presidenta...?" (muy buena/buena — muy mala/mala) | **Different question**, not aprueba/desaprueba |
| `Evaluación de Gobierno Federal` (e.g. Abr 2026) | Same "opinión" wording, sometimes as a trend chart | **Different question** |

Checked one wave of each of the three templates as of 2026-08; only the
`Evaluación de Gestión` type has shown the literal aprueba/desaprueba wording
so far, and only one instance of that template has been found. The other two
templates recur monthly and never carry it.

**Where the same month is shown under both wordings, the numbers differ
materially** — e.g. 2025-09: aprueba/desaprueba 74/15 vs opinión 71/17. These
are not interchangeable readings of the same question; do not average or
substitute one for the other.

### Working rule until a better source turns up

`aprobacion` rows for Demotecnia are transcribed from whichever headline
chart a wave actually has:

- If the wave has an aprueba/desaprueba chart, use it — it is directly
  comparable to the pre-2026 series.
- If it only has the "opinión" chart (true for every monthly wave seen so
  far in 2026), use that instead rather than leaving the month blank. This is
  a **known, accepted discontinuity**, not an oversight: the 2025-09 →
  2026-01 jump in the series reflects a change in what Demotecnia publishes,
  not a change in the president's standing. Do not smooth over it or treat
  the two waves as directly comparable in a chart without noting the break.
- Never load both wordings for the same `poll_month` — they collide on the
  `(poll_month, pollster, occurrence)` key and the loader will reject the
  disagreement as a conflict.

### Trend charts carry multiple months in one image

The "opinión" question sometimes appears as a multi-point trend line
(e.g. Estudio Nacional Sept 2025, slide 4: Jul 2023 → Sept 2025) rather than a
single reading. **Do not transcribe historical points from these trend
charts** if Oraculus already covers that month under the aprueba/desaprueba
wording — they are a different instrument and will conflict at load. Only
transcribe trend-chart points for months with no existing coverage.

## Topic questions (`desempeno`)

`Evaluación de Gobierno Federal` decks carry a per-topic table: "dígame si con
la Presidenta ... han mejorado o empeorado" — a 5-point scale (Ha
mejorado / Igual de bien / Igual de mal / Ha empeorado / No sabe). This is
Demotecnia's equivalent of the `desempeno` question. Store `positivo` = "Ha
mejorado", `negativo` = "Ha empeorado"; the two "Igual de" categories and "No
sabe" fold into `resto`, consistent with the wider neutral bucket already
documented for this house (`RESTO_LIMIT["Demotecnia"] = 20`).

Current `tema` → `tema_politica` mapping (in addition to the shared entries
in the main ingest):

| `tema` (Demotecnia wording) | `tema_politica` |
|---|---|
| Los derechos de las mujeres | `derechos_humanos_e_igualdad` |
| La atención a niños y jóvenes | `desarrollo_social_y_vivienda` |
| Los programas sociales | `desarrollo_social_y_vivienda` |
| El combate a la pobreza | `desarrollo_social_y_vivienda` |
| La construcción de obras e infraestructura | `infraestructura_y_transporte` |
| La educación pública | `educacion` |
| La protección al medio ambiente | `medio_ambiente` |
| La economía | `economia_e_industria` |
| Las relaciones internacionales | `relaciones_exteriores` |
| Los servicios de salud pública | `salud` |
| La seguridad | `justicia_y_seguridad` |
| El combate a la corrupción | `corrupcion` |

## Charts that must not be transcribed

Recurring Demotecnia charts that look evaluative but are not `aprobacion` or
`desempeno`:

- **0–10 "Calificación" average** — a numeric rating scale, not a two-way
  aprueba/desaprueba or bien/mal split. Different instrument entirely.
- **"¿Qué le diría a la Presidenta?"** — advice/sentiment question, not an
  evaluation.
- **"¿El país está mejor o peor?"** since the election — trajectory
  (right-track/wrong-track) question, not approval of the president.
- **Top virtue / attribute** — open-ended spontaneous-response question.
- **Mandate-revocation question** — a distinct referendum-style question.
- **"¿Está dando resultados?" (general, or per strategy)** — rates an
  *outcome*, not the government's handling of a topic. Same exclusion the
  main runbook already applies to El Financiero's "Relaciones bilaterales".
- **US-relations typology** (cooperación/confrontación/sumisión) —
  categorical, not a bien/mal handling scale.

## Format-change log

| Date | Change |
|---|---|
| 2026-08 | First reconciled: Sept 2025 wave's aprueba/desaprueba chart matches Oraculus's seed exactly (74/15) |
| ~2025-09 → 2026 | Monthly decks stop carrying the aprueba/desaprueba chart; "opinión buena/mala" is the only headline question found since |

## State as of 2026-08-14

- Rows loaded: `2025-09` (aprueba/desaprueba, corroborates Oraculus),
  `2026-01`, `2026-05`, `2026-06` (all "opinión" wording — accepted
  discontinuity, see above)
- `desempeno`: 12 temas, `2026-05` only, from one `Evaluación de Gobierno
  Federal` wave
- Not yet checked: whether any 2026 wave uses the `Evaluación de Gestión`
  template that carries the literal aprueba/desaprueba wording
