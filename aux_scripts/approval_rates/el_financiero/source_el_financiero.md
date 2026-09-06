# Source note — El Financiero ("Encuesta EF")

Source-specific companion to
[`approval_refresh_runbook.md`](../approval_refresh_runbook.md). The general
rules and the load step live there; this file covers only what is particular
to El Financiero. Read both before a refresh.

El Financiero is the highest-volume house on file and the only one currently
wired up. Monthly national telephone poll, published in the first days of the
following month.

---

## Finding a wave

**Site-scoped web search is the most dependable route:**

```
Encuesta EF aprobación Sheinbaum <month> <year>    (site: elfinanciero.com.mx)
```

Other channels, all partial:

- <https://www.elfinanciero.com.mx/encuestas-ef/> — dedicated section, appeared
  by 2026-08. Looks cleaner than the tags; not yet confirmed to index reliably.
- <https://www.elfinanciero.com.mx/tags/aprobacion-presidencial/> — incomplete.
- <https://www.elfinanciero.com.mx/tags/encuesta-el-financiero/> — incomplete
  and mixed-topic; also carries World Cup and state-election polling.

**The URL path is not stable.** Waves through 2026-06 sat under `/nacional/`;
the 2026-07 wave was published under `/encuestas-ef/`. Do not filter discovery
on the path.

**Publication has drifted later.** Historically the first day or two of the
month; the 2026-06 wave landed on the 6th. Not worth checking before ~the 3rd.

## Fetching

```bash
/usr/bin/python3 aux_scripts/approval_rates/el_financiero/fetch_ef_approval_article.py <URL>
```

Pulls the Arc Publishing `Fusion.globalContent` JSON and writes:

- `data/clean_approval/text/<slug>.txt` — prose plus the methodology note
- `data/clean_approval/charts/<slug>__NN.<ext>` — every body chart

The section change to `/encuestas-ef/` did **not** move them off Arc; the
fetcher works unchanged on both paths. It reads no numbers, by design.

## Reading the charts

**Chart `__00` is the headline approval chart.** That has held so far. Nothing
else about the layout has.

### The window is no longer cumulative

Through the 2026-05 wave, `__00` redrew the entire series back to Oct 2024, so
a single image reconstructed everything. **That stopped.** Recent windows:

| Wave | `__00` window | Months shown |
|---|---|---|
| ≤ 2026-05 | Oct 2024 → current | cumulative |
| 2026-06 | Jan → Jun 2026 | 6 |
| 2026-07 | Apr → Jul 2026 | 4 |
| 2026-08 | Aug 2025 → Aug 2026 | 13 |

The 2026-08 wave **widened the window back out** to 13 months. The shrinkage
was therefore not a one-way trend, and the window has to be read off each wave
rather than predicted from the last one.

Verification now rests on **consecutive waves overlapping each other** rather
than on one cumulative chart. The 2026-06 and 2026-07 waves overlap by three
months, and the loader cross-checks that overlap automatically
(`grafica+grafica`). Watch the window: if it ever shrinks to a single month,
the cross-check disappears entirely and that needs flagging, not working
around.

### `__00` can be a composite

In the 2026-07 wave, `__00` is one image holding both the approval line chart
*and* an unrelated World Cup donut. Transcribe the approval panel; ignore the
rest.

### Topic panels rotate

There is no fixed panel order, and **not every topic appears in every wave.**
Read the panel titles rather than trusting the index:

| Wave | Chart | Contents |
|---|---|---|
| 2026-06 | `__01` | Crimen organizado, Apoyos sociales |
| | `__02` | *Principal problema del país* — do not transcribe |
| | `__03` | Relación con Trump |
| | `__04` | *Relaciones bilaterales* — do not transcribe |
| | `__05` | Revisiones al T-MEC |
| 2026-07 | `__01` | Economía, Corrupción, Seguridad pública |
| | `__02` | Crimen organizado, Apoyos sociales |
| | `__03` | Revisiones al T-MEC |
| | `__04` | Relación con Trump |
| 2026-08 | `__01` | *Principal problema del país* — do not transcribe |
| | `__02` | Relación con Trump |
| | `__03` | Revisiones al T-MEC |
| | `__04` | *Visas / Listas* — one-off, do not transcribe |

The 2026-08 wave carries **no panel at all** for Economía, Corrupción,
Seguridad pública, Crimen organizado or Apoyos sociales, so those five temas
are simply absent for that month.

Economía, Corrupción and Seguridad pública are simply **absent** from the
2026-06 article. A missing topic is normal and needs no note; the next wave's
overlap usually fills it.

## Charts that must not be transcribed

Recurring EF charts that are *not* the `desempeno` question, despite looking
similar:

- **"Principal problema del país"** — ranks problems (Inseguridad / Economía /
  Corrupción) as three competing series. Not an evaluation, and the series do
  not pair into `bien`/`mal`.
- **"Relaciones bilaterales"** — "¿Cómo calificaría las relaciones entre México
  y Estados Unidos?" rates the *state of the relationship*, not the
  government's handling of it. Distinct from "Relación con Trump", which does
  ask about handling and does belong.
- **One-off event questions** — e.g. the 2026-07 World Cup donut. Single
  occurrence, no time series.

## Topic naming

`tema` must match what is already in `fact_approval_topic`. EF varies its panel
titles:

| Printed | Store as |
|---|---|
| "El crimen organizado" (2026-07) | `Crimen organizado` |

Every tema also needs a `TEMA_POLITICA` entry in the ingest, or the load
aborts. Current mapping:

| `tema` (EF wording) | `tema_politica` |
|---|---|
| Economía | `economia_e_industria` |
| Corrupción | `corrupcion` |
| Seguridad pública | `justicia_y_seguridad` |
| Crimen organizado | `justicia_y_seguridad` |
| Apoyos sociales | `desarrollo_social_y_vivienda` |
| Relación con Trump | `relaciones_exteriores` |
| Revisiones al T-MEC | `relaciones_exteriores` |

The mapping is many-to-one by design, so `tema_politica` groups but cannot
round-trip back to a house label. Aggregate from `tema` when you need EF's own
breakdown.

`Revisiones al T-MEC` is new as of the 2026-04 wave — "¿cómo calificaría la
manera en que el gobierno está llevando las negociaciones del tratado comercial
con Estados Unidos y Canadá?". It is a genuine `desempeno` question.

## Known gaps

- **`Revisiones al T-MEC`, 2026-07** — *recovered.* The 2026-07 chart drew the
  "Muy bien/Bien" point with no printed label, so it was left out at the time;
  the 2026-08 chart restates it as **51**, and it is now loaded. This is the
  overlap rule paying for itself.
- **Five temas for 2026-08** — Economía, Corrupción, Seguridad pública,
  Crimen organizado and Apoyos sociales have no chart in the 2026-08 article.
  Both articles quote figures for them in prose, and **the two disagree**
  (see below), so nothing was transcribed. Recoverable if the 2026-09 wave
  restates them.

### The prose of two same-day articles contradicted itself

EF published a second piece on 2026-09-01, *"¿Cuál es el 'fuerte' de
Sheinbaum?"*, carrying **no charts at all** (the fetcher reports `Charts (0)`,
which is a correct result, not a fetch failure). Its prose says seguridad and
crimen organizado are "11 y 13 por ciento, respectivamente"; the main article
says "seguridad, crimen organizado y corrupción ... 13, 11 y 14". The two are
transposed, and nothing on either page resolves which is which.

This is the clearest instance yet of the rule that prose is context only. Had
either sentence been trusted, two topic cells would have gone in wrong with no
way to notice. Both were left out.

## Format-change log

| Date | Change |
|---|---|
| 2026-08 | Back under `/nacional/`; the path is confirmed unstable in both directions |
| 2026-08 | `__00` window widens back out to 13 months |
| 2026-08 | Five recurring topic panels absent; a chartless companion article contradicts the main one in prose |
| 2026-07 | `__00` composited with an unrelated donut chart |
| 2026-07 | Published under `/encuestas-ef/` instead of `/nacional/` |
| 2026-07 | `/encuestas-ef/` section page appears |
| 2026-06 | **`__00` stops being cumulative** — window drops to 6, then 4 months |
| 2026-06 | Publication drifts to the 6th of the month |
| 2026-04 | New tema `Revisiones al T-MEC` |

## State as of 2026-09-05

- Last wave loaded: **2026-08** (68 / 32), from the article published
  2026-09-01
- Headline series continuous from 2024-10
- 2026-01 → 2026-07 are `grafica+grafica`; 2026-08 is `grafica` until the next
  wave restates it
- The 2026-08 chart re-read all 12 prior months and every one matched what was
  already on file
