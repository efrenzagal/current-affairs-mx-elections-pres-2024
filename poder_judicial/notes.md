# Poder Judicial — exploration notes

Status: **shelved — case-vote transcripts are too much to scale for now (see
"Scale check & prior art" below). The 2025 judicial-election data (item 1
below) is unexplored and may be the more tractable next step.**

## Motivation

The project already covers the legislative power (Cámara de Diputados,
Senado — rosters, votes, iniciativas) and the executive power (electoral
data + presidential approval). The judiciary is the missing third branch.

Two distinct kinds of "judicial vote" exist, and they are not the same
problem:

1. **2025 judicial elections** — Mexico's first popular election of judges,
   including SCJN ministers. This is an actual election, published by INE,
   and would fit the existing crawl/materialize pattern used for
   presidential/legislative electoral data. Not yet investigated in detail.
2. **SCJN case rulings (Pleno voting records)** — how individual ministers
   voted on specific cases. This is the one discussed below.

## SCJN case-vote records

Source: official "Versiones Taquigráficas" (session transcripts).

- Index: https://www.scjn.gob.mx/pleno/secretaria-general-de-acuerdos/versiones-taquigraficas
- Consultation tool: https://www2.scjn.gob.mx/ConsultaPleno/Index.html?sitio=versiones-taquigraficas
- Example transcript: https://www.scjn.gob.mx/sites/default/files/versiones-taquigraficas/documento/2026-08-14/13%20agosto%202026%20-%20Versi%C3%B3n%20definitiva.pdf

### What we found by pulling one session (Aug 13, 2026)

- PDFs are native text (not scanned), so extraction is cheap: 121 pages →
  ~160k characters / ~24k words, extracted locally in ~1s with `pypdf`
  (no OCR, no API cost).
- But the content is a **verbatim deliberation transcript**, not a
  structured roll-call table like the Gaceta vote data. Problems found:
  - Cases are often resolved in batches ("cuenta conjunta") — a minister
    votes with the whole batch except one case called out by list position
    ("salvo el número 9"), which has to be resolved back to an actual case
    number mentioned pages earlier.
  - Most outcomes are unanimous and **no individual votes are stated at
    all** — the Secretario just says "existe unanimidad" and every minister
    present is implicitly assumed to agree.
  - Non-unanimous outcomes sometimes name dissenters explicitly in prose
    ("Son: de la Ministra Sara Irene Herrerías Guerra, del Ministro..."),
    with the majority inferred by elimination against the sitting roster —
    not stated directly.
  - A single case can have sub-votes (procedencia vs. fondo) with different
    outcomes, and ministers sometimes correct/clarify their vote mid-session.
- Conclusion: not easily parseable with a regex/rules parser. Would need an
  LLM-assisted extraction pass per session, with a schema that tracks *how*
  each vote was attributed (explicit vs. inferred), not just the vote value
  itself — otherwise downstream analysis can't tell a stated vote from an
  assumption.

### Draft schema (discussed, not built)

Follows the project's existing `dim_`/`fact_` SQLite convention (same shape
as `camara_de_diputados/votos/ingest.py`).

- `dim_scjn_session` — one row per transcribed session (date, body, source URL/hash)
- `dim_scjn_minister` — roster, with `transcript_name` to match free text, term dates
- `fact_scjn_session_roster` — who was sitting for a given session (impedimentos change this per case)
- `dim_scjn_case` — one row per case (asunto), case type/number/topic/ponente
- `fact_scjn_vote_motion` — the votable unit: case × motion type (proyecto/procedencia/fondo/impedimento), batch_id for cuenta conjunta groups, outcome, vote counts
- `fact_scjn_minister_vote` — individual attribution, with `attribution_method` ("explicit" | "inferred_unanimous" | "inferred_by_elimination") and `source_excerpt` for audit

Key design point: `attribution_method` is load-bearing. Most rows will be
`inferred_unanimous`, not `explicit` — the schema needs to be honest about
that distinction rather than flattening everything into a plain vote value.

### Toy example — amparo en revisión 133/2026 (pp. 22-25 of the Aug 13 session)

Populated with only what's actually recoverable from that one passage,
gaps included, to stress-test the schema.

**dim_scjn_session**

| session_id | session_date | body | session_type |
|---|---|---|---|
| SCJN_PLENO_2026-08-13 | 2026-08-13 | Pleno | ordinaria |

**dim_scjn_case**

| case_id | case_type | case_number | topic_summary | ponente_id |
|---|---|---|---|---|
| AR_133_2026 | amparo en revisión | 133/2026 | Interés jurídico de persona moral impugnando disposiciones de la LFT/LSS sobre trabajadores del campo | (not stated in excerpt) |

**First pass at this table was wrong** — it invented a standalone
"procedencia" motion with its own confirmed 5-4 tally. Re-reading the
passage: Espinosa Betanzo characterizes procedencia alone as a 4-4 tie, and
the Presidente never confirms a separate procedencia number — he reframes
it as "votamos el total del proyecto," gets 5-4 there, and treats that as
subsuming ("convalidando") the procedencia question rather than resolving
it as its own vote. There is one confirmed motion, not two. Presenting two
clean rows made the transcript's live, unresolved floor disagreement look
settled when it wasn't — the exact failure mode this schema is supposed to
prevent, reproduced by hand.

**fact_scjn_vote_motion**

| motion_id | motion_type | outcome | favor | contra | notes |
|---|---|---|---|---|---|
| AR_133_2026_proyecto | proyecto (procedencia + fondo, not voted separately) | mayoría | 5 | 4 | Espinosa Betanzo disputed procedencia alone as a 4-4 tie on the record; Presidente resolved by treating the combined vote as subsuming procedencia rather than confirming a separate tally |

**fact_scjn_minister_vote**

| minister_id | vote | vote_detail | attribution_method | voto_particular |
|---|---|---|---|---|
| herrerias_guerra | contra | — | explicit | 1 |
| espinosa_betanzo | contra | — | explicit | 0 |
| figueroa_mejia | contra | — | explicit | 0 |
| aguilar_ortiz | contra | — | explicit | 1 |
| ortiz_ahlf | favor | contra on procedencia specifically, favor on fondo — does not collapse to one value | explicit | 0 |
| (unresolved × 4) | favor | — | inferred_by_elimination | 0 |

Added a `vote_detail` column: Ortiz Ahlf's position doesn't fit a single
`vote` cell (against on procedencia, for on fondo, in what the room
ultimately treated as one motion). Forcing that into one value is the same
kind of over-tidying as the invented second motion — a real minister
position, silently smoothed into something cleaner than the record
supports.

Takeaway, revised: this passage names 5 of 9 sitting ministers explicitly.
The other 4 "favor" votes exist only by arithmetic (5 + 4 = 9, 4 contra
named), not by name — not recoverable without the session's opening roll
call. And even the "explicit" rows above required resolving pronouns and
disputed characterizations across several pages of live back-and-forth to
attach them to the right case — that resolution work is itself a form of
inference the current schema doesn't capture (it only flags
inferred-vs-explicit at the vote level, not at the reference-resolution
level). First attempt at this toy example undercounted how much of that
was happening — which is itself evidence for "not easily parseable,"
including by hand.

### Scale check & prior art (2026-08-16)

SCJN publishes roughly one "versión taquigráfica" per weekday session, so
even a couple of years of coverage means hundreds of PDFs — hundreds of LLM
extraction runs, each needing the kind of careful disambiguation that still
produced an error in a single hand-checked example above. **Decision: too
much for now, shelving.**

Checked for prior art / shortcuts before shelving:

- SCJN's own [Votos de Ministros](https://www.scjn.gob.mx/pleno/secretaria-general-de-acuerdos/votos-de-ministros)
  page is a searchable index, but only of *voto particular* / *voto
  aclaratorio* documents (formal written dissents/clarifications) —
  filterable by case type only, no minister/date/outcome filter, no
  CSV/API. Doesn't replace transcript parsing; at most a narrow
  supplementary source for cases where a minister filed a separate opinion.
- No dedicated published dataset or tool found for Mexico's SCJN
  minister-level voting records.
- Prior art exists for other supreme courts (e.g. a SCOTUS judgment-
  prediction benchmark, multi-sourced NLP datasets for the US court),
  built by dedicated research teams — confirms this is a legitimate
  research-scale problem elsewhere, not something typically solo-built.

## Open questions / next steps

- Seed `dim_scjn_minister` by hand (small, stable roster) rather than
  deriving it from transcript mentions.
- Look into the 2025 judicial-election INE data before deciding whether to
  prioritize that (structured, cheap) over the case-vote transcripts
  (unstructured, LLM-dependent).
- Prototype the LLM extraction on one session and see how clean/consistent
  the output actually is before committing to the schema above.
- Scope: is the goal per-case voting records, or something coarser (e.g.
  ideological leaning per minister over time, analogous to the
  `article_brujula_politica` piece)? That would change how much precision
  the extraction actually needs.
