# current affairs mx — web handoff

Public site for Mexican politics, in three sections: **Visualizaciones**
(interactive dashboards, currently one LXVI Legislature explorer per chamber),
**Artículos** (long-form data pieces published from Quarto) and **Datos** (the
warehouse dictionary). `/` is a static landing page into the three.

`current affairs mx` is a placeholder name pending a final choice; it appears
only in the UI and `app/site-chrome.tsx`, not in the Worker or package name.

The explorers connect the official 2024 seat integration *and* the current
official directory to nominal roll-call histories, letting readers move from
chamber composition → member → vote → party breakdown.

## Current product

- **Cámara** (`/visualizaciones/diputados`): 500 seats, 295 Gaceta roll calls.
- **Senado** (`/visualizaciones/senado`): 128 seats, 378 roll calls.
- **Perfiles legislativos** (`/visualizaciones/perfiles`): búsqueda conjunta de
  las personas del directorio actual de ambas cámaras, con resumen de voto,
  trayectoria nominal y filtro por tema.
- Each explorer opens on **composición actual** and can switch to the **2024
  electoral result**. See "Elected versus sitting" below — this distinction is
  the reason the section exists in its current form.
- Hover/focus a seat for identity, electoral result and recent activity.
- Select a seat for its complete voting history.
- Select a vote for totals, official source and party-level aggregation.
- Party is encoded by color; seat origin is encoded by shape:
  - square: mayoría relativa (MR)
  - diamond: primera minoría (FM, Senate only)
  - circle: representación proporcional (RP)
- MR electoral results show official 2024 vote totals and percentages. Senate
  FM seats show the second-place result. RP seats show list placement because
  they do not have an individual district/state winning percentage.

## Elected versus sitting

The INE integration records who *won* each seat in 2024. It is not who is
voting today, and the gap is large enough to be misleading if ignored:

| | Cámara | Senado |
| --- | --- | --- |
| Seats whose occupant is not the elected titular | 45 | 25 |
| Seats whose parliamentary group ≠ election party | 24 | 13 |
| Licencia / vacante | 5 licencia | 1 vacante |
| Current occupants with no linked roll-call identity | 12 | 11 |

The site offers exactly two views and no date picker: **quién ocupa el escaño
hoy** and **quién lo ganó en 2024**. The roster cutoff is stated once, in the
methodology note. Resist adding an as-of selector here — the Streamlit has one
because it is an analysis tool; this is a reader's page, and a third temporal
axis made it read like a database console.

Both chambers work identically. The Senado is not a special case: it has its own
directory snapshot, 25 substituted seats and the legislature's one vacancy.

**Why not derive "current" from the last roll call?** It was considered and
rejected on evidence:

- The directory is *fresher*, not staler. Cutoffs at the time of writing:
  roll calls end 2026-05-28, the directory was observed 2026-08-08.
- The two agree. Party-by-party, the last Cámara roll call and the directory
  return identical benches once `MRN` is canonicalized to `MORENA`.
- Roll calls carry person and party but **no seat**. The hemicycle is seat-based,
  so votes alone cannot tell you which seat a new suplente occupies. The
  directory is what supplies that bridge.
- Roll calls cannot express *licencia* or *vacante*; an absent member and a
  member on leave are indistinguishable in them.

Vote-reported affiliation is still used — `fact_congress_party_membership`
models it — but as corroboration, not as the seat map.

Current occupancy comes from `fact_congress_roster_seat` at the latest
`dim_congress_roster_snapshot` — the same source the Streamlit "Composición
actual" view reads. Keep the two in agreement; they are two front ends over one
model, and `aux_scripts/build_hemicycle_cache.py` is the reference
implementation.

Consequences that are easy to get wrong:

- **Seat coordinates never move between views.** `seatCoordinates` sorts by
  `electedParty`, always. A seat is a constitutional object; if a member changes
  bench the hemicycle must recolor, not rearrange.
- **`licencia` and `vacante` override the displayed party** (to `LICENCIA` /
  `VACANTE`). The directory still prints a group for a member on leave, but that
  seat is not voting with the group and must not be counted into the bloc.
- **Histories are keyed by person, not by seat.** A seat can have had two
  occupants and each owns their own record. The client resolves the seat to an
  identity under the active view, then looks that person up.
- **`partyVotes` is independent of the view.** It comes from the chamber's own
  per-group aggregation at the time of the vote, which is the correct
  denominator for that roll call. Do not recompute it from current composition.
- Seats whose current occupant has no vote identity render normally and say so.
  They are never backfilled with the elected member's record — that would
  attribute votes to the wrong person.

## Hosting

The app deploys to **Cloudflare Workers on your own account** via Wrangler.
It was originally scaffolded onto OpenAI Sites; that coupling has been
removed (see "Migration from Sites" below).

The old Sites preview at `brujula-legislativa-mx.efrenzagal.chatgpt.site`
still serves whatever was last deployed there. It is owner-only by Sites
access policy and is now independent of this source tree — retire it once the
Cloudflare deploy is verified.

## Architecture

| Path | Responsibility |
| --- | --- |
| `app/page.tsx` | Landing page. Static by design — no fetch, no loading state |
| `app/site-content.ts` | **All landing copy.** Edit prose here, not in `page.tsx` |
| `app/site-chrome.tsx` | Shared header/footer and the three-section nav |
| `app/visualizaciones/page.tsx` | Dashboard index, live counts from `visualizaciones.json` |
| `app/visualizaciones/dashboards.ts` | Dashboard registry; add a card here when you add a route |
| `app/visualizaciones/explorer.tsx` | The chamber explorer. One component, `chamber` prop |
| `app/visualizaciones/{diputados,senado}/page.tsx` | Thin routes over `explorer.tsx` |
| `app/visualizaciones/perfiles/` | Current-member search and person-level vote profiles across both chambers |
| `app/articulos/page.tsx` | Artículos index, driven by `public/data/articles.json` |
| `app/datos/page.tsx` | Datos: warehouse dictionary, master-detail over every table |
| `app/globals.css` | Complete visual system and responsive layout |
| `app/layout.tsx` | Metadata and social preview configuration |
| `scripts/export_gaceta_web.py` | Materializes the static web snapshots from SQLite + INE CSV |
| `scripts/build_article_pages.py` | Publishes rendered Quarto articles with site chrome |
| `public/articulos/*.html` | Published articles, served as static files |
| `public/data/legislature-66.json` | Cámara seats, votes, histories and party totals |
| `public/data/senate-66.json` | Senate seats, votes, histories and party totals |
| `public/data/visualizaciones.json` | Manifest-only digest so the index need not load 6.5 MB |
| `public/data/dictionary.json` | Table dictionaries, coverage matrices and column samples |
| `tests/rendered-html.test.mjs` | Build smoke test and core data invariants |
| `worker/index.ts` | Cloudflare Worker entry; routes image optimization, else delegates to the app router |
| `vite.config.ts` | vinext + Cloudflare plugin; declares the (empty) Worker binding config |

Each hemicycle explorer loads **only its own chamber's** JSON. The profile route
loads both because its primary interaction is a name search across chambers.
The old single hemicycle page pulled both (6.3 MB) to power a chamber tab;
splitting those routes still means the Senado hemicycle page costs 1.0 MB.

This is a static snapshot application. There is no live database, D1 or R2 at
runtime. The browser loads both JSON files and switches chamber datasets in
memory.

`next/link` cannot be used anywhere in this app. Under vinext the browser
resolves it from a pre-bundled dependency carrying its own React copy, so
rendering a `<Link>` throws "Invalid hook call" and hydration dies — while the
build and the SSR tests still pass. Navigate with plain `<a>`; the ESLint rule
that objects is disabled in `eslint.config.mjs` with that reason recorded.

## Data dictionary route

`/datos` documents the warehouse the rest of the site reads from: every
table's purpose, primary key, row grain, columns, domains and up to five real
sample values per column, plus the federal-election and roll-call coverage
matrices.

Its payload comes from the same builder as the standalone documentation viewer.
`documentation/table_dictionaries/build_viewer.py` writes both `viewer.html` and
`web/public/data/dictionary.json` on every run, which is what stops the two from
drifting. Pass `--no-json` to rebuild only the offline viewer.

Only the column *description* is bilingual — that is all the source CSVs carry.
Table purposes, notes and the "Values / Domain" column are English in
`overview.csv` and the per-table CSVs, so they read as English on a Spanish
page. Fixing that means adding Spanish columns upstream, not patching the route.

A table can be documented before it exists in `election_data.db`. Those are
flagged `inWarehouse: false`, labelled "pendiente" in the sidebar and explained
on the table page, because an empty Examples column with no explanation reads
as a bug. The build prints which tables are in that state.

## Articles

`/articulos` lists what is in `public/data/articles.json`; each entry links to a
static file under `public/articulos/`. Quarto owns the article — prose, code and
figures all come from the `.qmd`. `scripts/build_article_pages.py` never
re-executes it, it only wraps the finished render:

- injects the site header and footer so it reads as a page of this site,
- keeps the prose at a 760px measure while figures break out to `min(1560px,
  95vw)`, because the charts are interactive and worth exploring,
- adds an **Ampliar** control that takes any figure to the full viewport,
- re-measures every Plotly chart through a `ResizeObserver`, since Plotly sizes
  itself once and never notices the container growing.

It also strips what `embed-resources` duplicates. Quarto inlines the entire
4.8 MB Plotly bundle **once per figure**, and ships MathJax whether or not the
article contains a formula. For the current article that was 3 redundant Plotly
copies, 4 unused MathJax copies and a `cdn.plot.ly` import whose URL 404s:
**20.4 MB → 6.3 MB, and 6.2 MB → 1.9 MB gzipped.** The test asserts exactly one
Plotly bundle survives, so a regression here cannot ship quietly.

To add an article: render the `.qmd` with Quarto, add an `Article(...)` entry to
`ARTICLES` in the script, then rerun it.

Quarto's own layout fights this. `#quarto-content` is a `page-columns` grid that
pins `<main>` to ~800px, `section.level2` wraps every `##`, and the figure
wrappers carry the original fixed height with `overflow:auto`. All three are
overridden; if a future Quarto version changes those class names, the figures
will silently shrink back.

## Sources and joins

The exporter reads the repository-root `election_data.db`:

- Cámara: `dim_diputados`, `dim_gaceta_vote`,
  `fact_gaceta_deputy_vote`, `fact_gaceta_vote_summary`, and vote
  classifications.
- Senado: `dim_senadores`, `dim_senado_vote`, `fact_senador_vote`.
- Current occupancy, both chambers: `fact_congress_roster_seat` at the latest
  `dim_congress_roster_snapshot`.
- Electoral results and list placement:
  `data/electoral_data_raw/raw_2024/PRESIDENCIA_2024/CSV/INTEGRACION_CARGOS_PEF_2024.csv`.

Seat-to-legislator bridges are already persisted in `dim_diputados`,
`dim_senadores` and the roster tables. Join histories through those IDs; do not
fuzzy-match names in the web layer.

The two chambers use the same exported shape (`schemaVersion: 2`):

```text
manifest   counts, legislature, source cutoff, roster cutoff, occupancy stats
seats      seat geography/list, 2024 result,
           elected{PersonId,Name,Party,NameRole} — INE, who won it
           current{PersonId,Name,Party,Status}  — directory, who holds it
votes      metadata, source URL and chamber-wide totals
histories  person ID -> ordered [vote ID, choice] pairs
partyVotes vote ID -> party -> choice -> count
```

`currentStatus` is one of `en_funciones`, `licencia`, `vacante` or
`sin_directorio` (our own fallback when a seat is missing from the snapshot —
the seat keeps its elected occupant rather than disappearing).

Vote IDs are chamber-local. Never merge Cámara and Senate votes into one
namespace or aggregation. Person IDs are likewise chamber-local: Cámara uses the
Gaceta deputy hash, Senado the numeric `senador_id` as text.

Party keys pass through `canonical_party` from
`ingestion/congress_roster_ingest.py` on **both** sides — seat benches and the
per-vote aggregation. The Gaceta writes `MRN` for MORENA and INE writes
`CAND_INDEPENDIENTE` for an independent, so without this the hemicycle legend
and the vote breakdown label the same bench differently on the same page. Where
two source keys collapse into one, the counts are summed, never overwritten; the
test asserts every vote's party rows still sum to its chamber-wide totals.

## Refresh and validate

From the repository root:

```bash
python3 web/scripts/export_gaceta_web.py
python3 documentation/table_dictionaries/build_viewer.py
python3 web/scripts/build_article_pages.py
cd web
npm test
```

`npm test` performs a production build and checks the important invariants.
Expected current counts are 500/295 for Cámara and 128/378 for Senado. All 628
official seats must remain linked. The 300 Cámara MR seats and 96 Senate MR/FM
seats must have electoral results; RP results must remain null. Every identity a
seat can resolve to, under either view, must have an entry in `histories`, and
`substitutedSeats` must stay above zero — a zero there means the roster overlay
silently stopped applying.

Refresh the roster before the exporter if the directory has moved:

```bash
python3 ingestion/congress_roster_ingest.py
```

Do not hand-edit generated JSON. Change the exporter or upstream warehouse,
regenerate both snapshots, then test.

## Interaction invariants

- Switching view (actual/electoral) resets the open vote, the party filter and
  the color mode; they all belong to identities the previous view resolved.
- Seats keep their coordinates across views. Only color and text change.
- Histories are newest-first.
- A selected history row drives the lower vote-detail section.
- Party bars come from `partyVotes`, not from seat composition.
- Cámara and Senate terminology, source copy and electoral-seat descriptions
  come from the `CHAMBERS` map in `explorer.tsx`, keyed by the route's chamber.
- Hover interactions must also work through keyboard focus; seats are buttons.
- Preserve the compact mobile layout and reduced-motion behavior.

## Editing copy

| What | Where |
| --- | --- |
| Landing: hero, buttons, three section cards, method note | `app/site-content.ts` |
| Header brand, nav labels, footer tagline | `app/site-chrome.tsx` |
| Browser title, meta description, social preview | `app/layout.tsx` |
| Dashboard cards (title, summary, area, topics) | `app/visualizaciones/dashboards.ts` |
| Explorer chamber wording (member nouns, source names) | `CHAMBERS` in `app/visualizaciones/explorer.tsx` |

The landing copy is written about the **method**, never the subject matter.
The Congress is what the site covers today; "official sources, every figure
traceable, every table documented" is what stays true when elections, climate,
mobility or public finances are added. Anything only true of the hemicycle
belongs on `/visualizaciones` or inside an explorer, not on `/`.

Two things on the front page are derived, not written, so they cannot go stale:
the featured dashboard links and the list of covered subject areas both come
from `DASHBOARDS`.

## Adding a dashboard

1. Add a route under `app/visualizaciones/<slug>/page.tsx`.
2. Add its entry to `DASHBOARDS` in `app/visualizaciones/dashboards.ts`,
   including `area`. A new `area` value makes the subject appear on the landing
   page and turns on per-area headings in the section index automatically — no
   edit to `/` required.
3. If it needs headline numbers on the index, add them to the digest written by
   `export_summary()` in `scripts/export_gaceta_web.py` rather than fetching the
   full payload from the index page. A dashboard with no digest entry degrades
   gracefully to its static `subtitle` and shows no stats block.

## Local development

Requires Node.js `>=22.13.0` **installed as a normal toolchain** (nodejs.org
installer, Homebrew, nvm or similar).

```bash
cd web
npm install
npm run dev
```

A note learned the hard way: a Node binary bundled inside another signed macOS
app (for example `ChatGPT.app/Contents/Resources/cua_node/bin/node`) cannot
build this project. macOS refuses to load Rolldown's native `.node` addon into
a hardened process signed by a different Team ID, and the build dies with a
misleading "Cannot find native binding" error. Pure-JS tooling (`tsc`,
`eslint`) still runs under it; `vinext build` does not.

## Deploying to Cloudflare Workers

```bash
npx wrangler login          # once, against your own Cloudflare account
npm run preview             # build + run the real Worker locally
npm run deploy              # build + publish
```

Both scripts build first, then point Wrangler at the generated
`dist/server/wrangler.json`. That file is regenerated on every build by the
Cloudflare Vite plugin — edit `vite.config.ts`, never `dist/`.

The Worker name comes from `package.json`'s `name`, so the default hostname is
`brujula-legislativa-web.<your-subdomain>.workers.dev`. Attach a custom domain
from the Cloudflare dashboard.

The app declares **no bindings** — no D1, R2, KV or queues. `dist/client/`
ships as static assets and the Worker only server-renders the shell. The two
JSON snapshots total roughly 6.5 MB, so let Cloudflare's edge cache do the
work and avoid cache-busting them on unrelated deploys.

Preserve `public/og.png`; do not regenerate it for ordinary UI or data updates.

## Migration from Sites

This project was scaffolded from a Sites/vinext starter. Removed, because
nothing imported them and they only existed to satisfy that platform:

- `app/chatgpt-auth.ts` — ChatGPT identity-header helpers, never imported.
- `db/`, `drizzle/`, `drizzle.config.ts`, `examples/` and the `drizzle-orm` /
  `drizzle-kit` dependencies — an empty D1 scaffold for a database this app
  does not have.
- `build/sites-vite-plugin.ts` — packaged Sites metadata into `dist/.openai/`.
- `vite.config.ts`'s import of `.openai/hosting.json`, which derived D1/R2
  bindings that were both `null`.
- The `DB: D1Database` field on the Worker `Env`.

Removing these also cleared every outstanding `tsc` error; the project now
typechecks clean.

`.openai/hosting.json` is deliberately **kept but no longer imported**. It is
inert for a Cloudflare deploy and is the only remaining record of the Sites
project ID, which is worth having until the migration is confirmed. Delete it
once you are sure you will not go back.

## Known constraints

- Data updates require rerunning the exporter and redeploying; the site does
  not query the warehouse directly.
- A seat is linked to the persisted officeholder identity used by the
  warehouse. Short histories can be legitimate when a substitute entered
  during the legislature.
- The website currently covers only Legislature 66 and the 2024 composition.
- A Cloudflare Workers deploy is public by default. There is no login gate and
  no application authentication, so treat publishing as a deliberate choice.
