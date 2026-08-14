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

The charts include every LXVI vote record published and downloaded from the
official source, mapped to a constitutional seat. Seats omitted by the source
remain **“Sin registro”**; they are not imputed as absent or assigned a
fabricated vote.

## Current product

- **Cámara** (`/visualizaciones/diputados`): 500 seats, 295 Gaceta roll calls.
- **Senado** (`/visualizaciones/senado`): 128 seats, 378 roll calls.
- **Geografía electoral** (`/visualizaciones/trayectoria`): mapa seleccionable
  de las 32 entidades y resultados nacionales/estatales para Presidencia,
  Senado y Diputaciones, con trayectoria y desglose por boleta/coalición y partido.
- **Buscador de votaciones** (`/visualizaciones/votaciones`): las 673 votaciones
  nominales de ambas cámaras, buscables por texto y filtrables por las cuatro
  ejes de clasificación, con el desglose por grupo parlamentario en cuadros.
- Each explorer opens on **composición actual** and can switch to the **2024
  electoral result**. See "Elected versus sitting" below — this distinction is
  the reason the section exists in its current form.
- Hover/focus a seat for identity, electoral result and recent activity.
- Select a seat for its complete voting history, filterable by policy topic.
- Or search by name from the person panel. The search spans three identities:
  the sitting occupant, the titular who won the seat in 2024, and members with a
  roll-call record and no seat at all. Picking one of the first two moves the
  view tab to wherever that seat is lit; picking the last darkens the whole
  hemicycle, because no curul is theirs. When a name has no match the panel
  offers the same search in the other chamber via `?q=`.
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
- **Raw histories are keyed by canonical person; seat histories are composed
  client-side.** A seat can have had several occupants and each vote remains
  attributed to the person who cast it. Clicking a seat combines those records
  by default, with titular/suplencia filters; searching a name still opens only
  that person's record. `personAliases` preserves audited raw-id merges and
  `seatMembers` records each person's role and official relationship source.
- **`partyVotes` is independent of the view.** It comes from the chamber's own
  per-group aggregation at the time of the vote, which is the correct
  denominator for that roll call. Do not recompute it from current composition.
- Seats whose current occupant has no vote identity render normally and say so.
  They are never backfilled with the elected member's record — that would
  attribute votes to the wrong person.
- **The view tab never moves on its own.** It relabels all 500 seats, so flipping
  it as a side effect of picking one name makes the whole hemicycle lurch. When
  the active tab cannot show the selected identity, the chamber dims and that
  person's seat of origin is marked hollow instead (`.seat-origin-mark`) — a
  weaker claim than the solid ring of a live selection, and deliberately styled
  so the two cannot be confused. Crossing to the other tab is offered as a link.
- **A search hit keeps its own identity.** Picking the titular of a seat that has
  changed hands must keep showing the titular, not whoever replaced them. Only
  hits the active view already places collapse into a plain seat selection; a
  former member never does, because resolving them through their seat would
  silently swap their record for the current occupant's.

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
| `app/visualizaciones/parties.ts` | The one party palette and bench order. Never re-declare these |
| `app/visualizaciones/votes.ts` | The one vote vocabulary: choice colours, title cleanup, Spanish labels for every classification code. Never re-declare these |
| `app/visualizaciones/explorer.tsx` | The chamber explorer: hemicycle, name search over sitting/elected/former members, and the person panel. One component, `chamber` prop |
| `app/visualizaciones/{diputados,senado}/page.tsx` | Thin routes over `explorer.tsx` |
| `app/visualizaciones/votaciones/` | Vote search across both chambers, with the party square grid |
| `app/articulos/page.tsx` | Artículos index, driven by `public/data/articles.json` |
| `app/datos/page.tsx` | Datos: warehouse dictionary, master-detail over every table |
| `app/globals.css` | Complete visual system and responsive layout |
| `app/layout.tsx` | Metadata and social preview configuration |
| `scripts/export_gaceta_web.py` | Materializes the static web snapshots from SQLite + INE CSV |
| `scripts/build_article_pages.py` | Publishes rendered Quarto articles with site chrome |
| `public/articulos/*.html` | Published articles, served as static files |
| `public/data/legislature-66.json` | Cámara seats, votes, histories and party totals |
| `public/data/senate-66.json` | Senate seats, votes, histories and party totals |
| `public/data/votes-66.json` | Both chambers' roll calls and party breakdowns, no seats. 0.8 MB, ~62 KB gzipped |
| `public/data/vote-ballots-66.json` | Names for the individual squares, mirroring `partyVotes`. 0.8 MB, ~64 KB gzipped |
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

## Vote explorer

`/visualizaciones/votaciones` is the vote-first route: the hemicycle explorers
start from a seat and reach a vote, this one starts from the vote. It reads its
own `votes-66.json` and neither seats nor histories are in it — those are 90% of
the hemicycle payloads and none of what a vote search needs. Reusing
`legislature-66.json` here would have cost a reader 5.9 MB to use 0.4 MB.

**The grid is sized by counts and only named by ballots.** `partyVotes` is the
chamber's own tally for that roll call and is what draws every square; hovering
one shows who it is, from `vote-ballots-66.json`. That file mirrors the shape of
`partyVotes` — party, then choice, then an ordered list of people — so the
client just zips them. Keep it that way. Rebuilding the grid *from* ballots is
the tempting simplification and it is wrong:

- The two sources disagree on six LXVI Camara roll calls. The per-deputy table
  files an independent under `SP` where the official summary says `IND` — same
  person, same choice, different bench label. Sized from ballots, those votes
  would render a party block the official tally does not contain.
- The aggregation is the chamber's count *at the time of the vote*, so it stays
  right for a bench that has since changed. Never recompute it from current
  composition.

The exporter clamps each name list to its official count and reports coverage;
it currently names 187,374 of 187,380 squares, the six exceptions being exactly
that `SP`/`IND` mismatch. A square with no matching name renders normally and
says only its party and choice — it is never moved to another bench to find one.
The test fails if coverage drops below 99.9%, which is what a broken join looks
like.

Names come from the seat tables where the person is linked, so a legislator
reads the same here as in the hemicycle, and from the chamber's own spelling
otherwise — roll calls reach interim substitutes the seat tables never held.
Only the Senado's `Apellido, Nombre` form is flipped, because only it has the
comma to split on; guessing where a deputy's surnames end would rename people.

**Model confidence is deliberately not published.** The Senado classification
carries a `confianza` score and the export drops it. A number a reader cannot
act on reads as precision the label does not have, and "sin revisión" already
says the useful part. The test asserts no `confidence` key reaches the client.

**Vote IDs stay namespaced.** `partyVotes` is keyed `"<chamber>:<id>"`. The two
chambers' IDs are independent — the Camara's are Gaceta slugs, the Senado's are
small integers — and `5115` is a real Senado vote and a plausible future Camara
one. This payload is the only place the two namespaces meet; the test asserts
every key carries its chamber.

**The two chambers do not classify identically**, and the exporter reconciles
them rather than the component:

- `dictamen_de_comisiones` (Senado) and `dictamen_de_comision` (Camara) are one
  concept spelled twice, and are collapsed by `canonical_code` — the same
  problem `canonical_party` solves for benches. Uncollapsed they render as two
  filter chips meaning the same thing on a page that lists both chambers.
- `minuta_del_senado` and `minuta_de_camara_de_diputados` look like the same
  case and are **not**: each names the chamber the bill arrived from. They stay
  distinct.
- Review provenance is asymmetric. The Camara ran the deterministic review pass
  and stores an outcome (`rule_checked`, `audited`); the Senado never ran it and
  stores none. `reviewKey` folds that into one vocabulary a reader can filter
  on, reporting the Senado as "solo modelo, sin revisión". A Senado label must
  never render as "verificada" — that is asserted.

**Quorum and the majority thresholds are Camara-only.** They are derived, not
stored, and the exporter imports `add_vote_thresholds` from
`ingestion/gaceta_materialize.py` rather than restating the arithmetic, so
"mayoría calificada" means one thing on both front ends. They are not exported
for the Senado because the formula keys off `total` meaning the whole chamber,
which is what the Gaceta tally reports; a Senado tally's total is the number of
senators recorded in that roll call, so the same formula would compute a quorum
floor against a denominator that already excludes the absent.

**Every classification code needs a written Spanish label** in `votes.ts`. The
codes are unaccented `snake_case` at the source, so no mechanical transform can
produce "Organización y régimen del Congreso". The test parses the label maps
out of `votes.ts` and fails the build on any code the payload uses and the maps
do not cover — a future classification pass cannot quietly ship raw snake_case
onto the page.

Two Camara votes have no `gaceta_date` and therefore no daily-issue link; the
panel omits it. Streamlit's *Iniciativa (PDF)* button has no equivalent here on
purpose — it fuzzy-matches the dictamen against a live fetch of that day's
Gaceta index, which a static site cannot do at render time.

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

The two chambers use the same exported shape (`schemaVersion: 6`):

```text
manifest   counts, legislature, source cutoff, roster cutoff, occupancy stats
seats      seat geography/list, 2024 result,
           titularName/substituteName — stable INE seat labels
           elected{PersonId,Name,Party,NameRole} — roll-call identity bridge
           current{PersonId,Name,Party,Status}  — directory, who holds it
formerMembers  voting identities absent from both seat snapshots, with their
               latest vote-reported party, plus seatId/seatRole (see below)
votes      metadata, source URL and chamber-wide totals
histories  person ID -> ordered [vote ID, choice] pairs
partyVotes vote ID -> party -> choice -> count
```

`currentStatus` is one of `en_funciones`, `licencia`, `vacante` or
`sin_directorio` (our own fallback when a seat is missing from the snapshot —
the seat keeps its elected occupant rather than disappearing).

### Placing former members

77 voting identities (35 Cámara, 42 Senado) appear in no seat snapshot. They are
overwhelmingly suplentes who covered a licencia: Bonilla Herrera cast 310 of 378
Senate roll calls. Roll-call rows carry no geography — `dim_gaceta_deputy` is an
id and a name — so the *only* route from one of these records back to a place in
the chamber is `ine_substitute_name`, the suplente the INE registered per seat.

`link_former_members` in `export_gaceta_web.py` does that match and places 74 of
77. It is name matching, which the web layer is forbidden to do — that is exactly
why it lives in the export, resolved once against the warehouse and shipped as an
id. Comparison is on accent-stripped, order-independent tokens, with a lone
initial allowed to stand for a given name; a candidate counts only when it is the
single seat in the chamber that matches, so an ambiguous name stays unlinked.

`seatRole` splits the result, and the two halves render very differently:

- `suplencia_concluida` (69) — served, and the titular has since returned. Every
  one of these lands on a seat where `currentPersonId == electedPersonId`, which
  is the strongest evidence the match is real rather than coincidental.
- `en_funciones` (5) — still in the seat. The roll call files them under a second
  identity that the directory never linked, so the same human holds both a seat
  entry and a `formerMembers` entry, each with its own history. Neither record is
  merged into the other: the site shows the one you asked for.
- `null` (3) — no seat could be named. The chamber goes fully dark for these, and
  the copy says the linkage failed rather than asserting they never held a seat.

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

`votes-66.json` must carry all 673 roll calls, every one resolving to a
namespaced party breakdown whose four choice totals equal the chamber tally —
the squares are drawn by expanding those counts, so a breakdown that does not
sum renders a grid of the wrong size. Every classification code it uses must
have a Spanish label in `votes.ts`. In `vote-ballots-66.json` no party/choice
name list may be longer than the count it decorates, and coverage must stay
above 99.9%.

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

On the vote explorer specifically:

- The whole reading state — query, chamber, four facets, review, result, margin,
  sort and the open vote — lives in the query string, seeded at first render so
  a shared link never paints the unfiltered archive first. `replaceState`, not
  `pushState`: ticking four chips must not cost four presses of Back.
- Facet options are derived from the votes the other filters already allow, with
  counts, so a chip that would return nothing is never offered.
- The list renders 10 rows and grows on request. Rendering all 673 made the page
  unusable to scroll, and the detail panel sits below it. The page counter
  resets by comparing a signature of the filter state during render, not from an
  effect, so narrowing a filter always returns you to the top of the results.
- Square tooltips are a custom element, not the native `title`. `title` forces a
  `help` cursor, waits about a second, and cannot be styled. One `mouseover`
  listener sits on the grid container and reads the square's data attributes;
  `PartyGrids` is memoized so moving the pointer never re-renders 500 squares,
  and a reading that lands on the gap between squares is held rather than
  cleared, or the label strobes on its way across a bench.
- A selection the chamber switch made unreachable (`permiso` is Senado-only) is
  **ignored, not deleted**. It has no chip to un-tick, so leaving it live would
  empty the list for no visible reason; discarding it would lose the reader's
  filter when they switch back.

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
