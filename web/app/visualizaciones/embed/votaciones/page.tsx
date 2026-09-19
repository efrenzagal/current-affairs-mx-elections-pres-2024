"use client";

import { useLayoutEffect, useState } from "react";

import VoteExplorer from "../../votaciones/vote-explorer";

/**
 * The real vote search/browse explorer (see app/visualizaciones/votaciones),
 * bare — no duplicate site header/footer, no intro hero, meant to be iframed
 * into a static article. Everything else (search box, facet filters, sort,
 * opening a vote's own detail view) is the genuine component used by
 * /visualizaciones/votaciones, not a rebuild.
 *
 * VoteExplorer already reads its filters and deep-linked selection straight
 * off `window.location.search` (`camara`, `tema`, `orden`, `q`, `v`, …), so
 * the iframe's own query string doubles as the deep link — no translation
 * layer needed for those. `bare` is the one flag this page owns: it strips
 * the hero and the whole search/filter/list column, leaving just the
 * deep-linked vote's own detail (title, tags, tally, thresholds, per-party
 * grid) — for an embed built around one specific roll call rather than the
 * browser around it.
 *
 * `interactive={false}` on the browse view: the article wants the search
 * box, filters and sort as a self-contained "explore the archive" widget,
 * not a click-through into the dark detail panel (that's what the separate
 * `bare=1` embed above is for, deep-linked to one specific vote). Harmless
 * in `bare` mode too — the list it would apply to is already hidden there.
 *
 * `pageSize={7}`, and the advanced filter fieldsets (topic, stage, origin,
 * instrument, result, margin) stay behind the "Más filtros" toggle even at
 * desktop width — see the `.embed-votaciones .vote-filter-more`/
 * `.vote-filter-advanced` rules in globals.css, which just apply the same
 * collapsed treatment VoteExplorer already uses under its own 700px
 * breakpoint. Both trim what's the tallest, least essential part of this
 * component for an article embed — a wall of category chips, and the long
 * tail of "mostrar más" pages — without touching the full standalone page.
 *
 * Starts `false` (matching what the server renders, since it has no
 * `window`) and flips in a `useLayoutEffect`, not a `useEffect`: React runs
 * every layout effect in the tree, child-before-parent, before any passive
 * effect anywhere in that same commit gets to run. VoteExplorer mirrors its
 * own filter state back into the URL via `replaceState` in a plain
 * `useEffect` on mount, which rewrites `window.location.search` down to
 * just the params it knows about — dropping `bare`. Reading the flag here
 * with `useLayoutEffect` wins that race (it's in the earlier phase
 * entirely), where a plain `useEffect` would sometimes lose it depending on
 * scheduling. Starting from `false` on both sides keeps hydration itself
 * mismatch-free; this only changes the class in a normal update right after.
 *
 * Example: /visualizaciones/embed/votaciones?v=diputados%3AGACETA_L66_TABLA2OR1_76&bare=1
 */
export default function VotacionesEmbedPage() {
  const [bare, setBare] = useState(false);

  useLayoutEffect(() => {
    setBare(new URLSearchParams(window.location.search).get("bare") === "1");
  }, []);

  const classes = ["embed-votaciones"];
  if (bare) classes.push("votaciones-bare");

  return (
    <div className={classes.join(" ")}>
      <VoteExplorer interactive={false} pageSize={7} />
    </div>
  );
}
