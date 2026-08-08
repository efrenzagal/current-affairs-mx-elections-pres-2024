# Brújula Legislativa — web handoff

Interactive LXVI Legislature explorer for Mexico's Cámara de Diputados and
Senado. It connects the official 2024 seat integration to nominal roll-call
histories and lets readers move from chamber composition → member → vote →
party breakdown.

## Current product

- **Cámara:** 500 seats, 295 Gaceta roll calls, 500 linked seat histories.
- **Senado:** 128 seats, 378 roll calls, 128 linked seat histories.
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
| `app/page.tsx` | Single-page client UI, chamber tabs and all interactions |
| `app/globals.css` | Complete visual system and responsive layout |
| `app/layout.tsx` | Metadata and social preview configuration |
| `scripts/export_gaceta_web.py` | Materializes the static web snapshots from SQLite + INE CSV |
| `public/data/legislature-66.json` | Cámara seats, votes, histories and party totals |
| `public/data/senate-66.json` | Senate seats, votes, histories and party totals |
| `tests/rendered-html.test.mjs` | Build smoke test and core data invariants |
| `worker/index.ts` | Cloudflare Worker entry; routes image optimization, else delegates to the app router |
| `vite.config.ts` | vinext + Cloudflare plugin; declares the (empty) Worker binding config |

This is a static snapshot application. There is no live database, D1 or R2 at
runtime. The browser loads both JSON files and switches chamber datasets in
memory.

## Sources and joins

The exporter reads the repository-root `election_data.db`:

- Cámara: `dim_diputados`, `dim_gaceta_vote`,
  `fact_gaceta_deputy_vote`, `fact_gaceta_vote_summary`, and vote
  classifications.
- Senado: `dim_senadores`, `dim_senado_vote`, `fact_senador_vote`.
- Electoral results and list placement:
  `data/electoral_data_raw/raw_2024/PRESIDENCIA_2024/CSV/INTEGRACION_CARGOS_PEF_2024.csv`.

Seat-to-legislator bridges are already persisted in `dim_diputados` and
`dim_senadores`. Join histories through those IDs; do not fuzzy-match names in
the web layer.

The two chambers use the same exported shape:

```text
manifest   counts, legislature and source cutoff
seats      identity, party, seat type, geography/list, 2024 result
votes      metadata, source URL and chamber-wide totals
histories  seat ID -> ordered [vote ID, choice] pairs
partyVotes vote ID -> party -> choice -> count
```

Vote IDs are chamber-local. Never merge Cámara and Senate votes into one
namespace or aggregation.

## Refresh and validate

From the repository root:

```bash
python3 web/scripts/export_gaceta_web.py
cd web
npm test
```

`npm test` performs a production build and checks the important invariants.
Expected current counts are 500/295 for Cámara and 128/378 for Senado. All 628
official seats must remain linked. The 300 Cámara MR seats and 96 Senate MR/FM
seats must have electoral results; RP results must remain null.

Do not hand-edit generated JSON. Change the exporter or upstream warehouse,
regenerate both snapshots, then test.

## Interaction invariants

- Changing chamber resets the member, vote, search and party filter to that
  chamber's state.
- Histories are newest-first.
- A selected history row drives the lower vote-detail section.
- Party bars come from `partyVotes`, not from seat composition.
- Cámara and Senate terminology, source copy and electoral-seat descriptions
  must change with the active tab.
- Hover interactions must also work through keyboard focus; seats are buttons.
- Preserve the compact mobile layout and reduced-motion behavior.

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
