"""Publish rendered Quarto articles into the website.

Quarto owns the article: prose, code and figures all come from the `.qmd`, and
this script never re-executes or rewrites that content. It only wraps the
finished render so it stops looking like a standalone document and starts
looking like a page of the site:

  * the site header and footer are injected around Quarto's own body,
  * the prose keeps a readable measure while the figures break out much wider
    than Quarto's default, because these are interactive Plotly charts and the
    whole point is to explore them,
  * every figure gets a control to expand it to the full viewport.

Re-render the `.qmd` with Quarto first, then run this to republish.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web"
OUT_DIR = WEB / "public" / "articulos"
INDEX_PATH = WEB / "public" / "data" / "articles.json"


@dataclass(frozen=True)
class Article:
    """One published article and the metadata the index page needs."""

    slug: str
    source: Path
    title: str
    subtitle: str
    author: str
    published: str
    summary: str
    topics: tuple[str, ...]
    kind: str = "quarto"  # "quarto" (rendered notebook, owns its own <head>/<body>)
    # or "prose" (a hand-authored body-only HTML fragment this script wraps into
    # a full page — used for articles whose figures are placeholders today).


ARTICLES: tuple[Article, ...] = (
    Article(
        slug="espectro-politico",
        source=ROOT / "articles" / "article_brujula_politica" / "quarto" / "brujula_politica.html",
        title="El Espectro Político en México",
        subtitle="La geometría de las elecciones presidenciales",
        author="Efrén Zagal",
        published="2026-08-05",
        summary=(
            "Un political compass no basta para un país con 18 partidos, coaliciones "
            "cambiantes y candidaturas independientes. Reduciendo seis ciclos "
            "presidenciales a sus componentes principales, aparece una geometría del "
            "voto mexicano que dos ejes perpendiculares no alcanzan a describir."
        ),
        topics=("Elecciones presidenciales", "1994–2024", "Reducción de dimensiones"),
    ),
    Article(
        slug="brujula-legislativa",
        source=ROOT / "articles" / "article_brujula_legislativa" / "brujula_legislativa.html",
        title="Brújula Legislativa",
        subtitle="Transparencia legislador por legislador en San Lázaro y el Senado",
        author="Efrén Zagal",
        published="2026-09-17",
        summary=(
            "500 diputados y 128 senadores votan con más frecuencia de la que se "
            "cubre. Cruzando la Gaceta Parlamentaria, el Senado y los resultados del "
            "INE, la Brújula Legislativa arma el historial de voto de cada "
            "legislador, escaño por escaño."
        ),
        topics=("Cámara de Diputados", "Senado", "Transparencia legislativa"),
        kind="prose",
    ),
)


# The site header/footer markup mirrors app/site-chrome.tsx. Kept as a literal
# rather than rendered from React because these pages are plain static files
# that Quarto produced and the app never hydrates.
CHROME_CSS = """
:root{--ink:#152321;--muted:#64706c;--paper:#f4f1e9;--paper-deep:#e9e4d8;--white:#fffdf8;--navy:#122b2d;--line:rgba(21,35,33,.15);--serif:Georgia,"Times New Roman",serif;--sans:Inter,ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
.ca-header{align-items:center;background:var(--navy);color:#f7f2e7;display:grid;font-family:var(--sans);grid-template-columns:1fr auto 1fr;min-height:84px;padding:0 4vw;position:sticky;top:0;z-index:1000}
.ca-brand{align-items:center;color:#f7f2e7;display:flex;font-family:var(--serif);font-size:17px;font-weight:700;line-height:.9;text-decoration:none;width:fit-content}
.ca-mark{align-items:center;border:1px solid rgba(255,255,255,.5);border-radius:50%;display:flex;font-family:var(--sans);font-size:10px;height:36px;justify-content:center;letter-spacing:-.03em;margin-right:10px;width:36px}
.ca-header nav{display:flex;gap:30px}
.ca-nav-item{position:relative}
.ca-nav-item>summary{cursor:pointer;list-style:none}
.ca-nav-item>summary::-webkit-details-marker{display:none}
.ca-nav-item.has-menu:after{content:"";height:22px;left:-18px;position:absolute;top:100%;width:calc(100% + 36px)}
.ca-header .ca-nav-trigger{border-bottom:2px solid transparent;color:rgba(255,255,255,.72);font-size:12px;font-weight:650;letter-spacing:.08em;padding-bottom:3px;text-decoration:none;text-transform:uppercase}
.ca-header .ca-nav-trigger:hover{color:#fff}
.ca-header .ca-nav-trigger.active{border-bottom-color:#79b897;color:#fff}
.ca-chevron{display:inline-block;font-size:13px;margin-left:5px;transition:transform .16s ease}
.ca-nav-menu{background:var(--white);border:1px solid var(--line);border-radius:4px;box-shadow:0 18px 48px rgba(4,17,17,.2);color:var(--ink);left:50%;min-width:350px;opacity:0;padding:7px;pointer-events:none;position:absolute;top:calc(100% + 18px);transform:translate(-50%,-5px);transition:opacity .15s ease,transform .15s ease,visibility .15s;visibility:hidden;z-index:1100}
.ca-nav-item:hover .ca-nav-menu,.ca-nav-item:focus-within .ca-nav-menu,.ca-nav-item[open] .ca-nav-menu{opacity:1;pointer-events:auto;transform:translate(-50%,0);visibility:visible}
.ca-nav-item:hover .ca-chevron,.ca-nav-item:focus-within .ca-chevron,.ca-nav-item[open] .ca-chevron{transform:rotate(180deg)}
.ca-nav-menu:before{background:var(--white);border-left:1px solid var(--line);border-top:1px solid var(--line);content:"";height:10px;left:50%;position:absolute;top:-6px;transform:translateX(-50%) rotate(45deg);width:10px}
.ca-header .ca-nav-menu a{border:0;color:var(--ink);display:block;letter-spacing:0;padding:10px 12px;text-decoration:none;text-transform:none}
.ca-header .ca-nav-menu a:hover,.ca-header .ca-nav-menu a:focus-visible{background:#f0ede5;border-radius:2px;outline:none}
.ca-header .ca-nav-menu a.ca-menu-overview{align-items:center;background:var(--navy);color:var(--white);display:flex;font-size:10px;font-weight:750;justify-content:space-between;letter-spacing:.08em;margin-bottom:4px;text-transform:uppercase}
.ca-header .ca-nav-menu a.ca-menu-overview:hover,.ca-header .ca-nav-menu a.ca-menu-overview:focus-visible{background:#1c3c3d}
.ca-menu-list{display:grid;gap:1px}
.ca-menu-list strong{display:block;font-family:var(--serif);font-size:15px;font-weight:600;line-height:1.18}
.ca-menu-list span{color:var(--muted);display:block;font-size:8px;line-height:1.35;margin-top:3px}
.ca-status{font-size:12px;justify-self:end;letter-spacing:.04em}
.ca-status i{background:#79b897;border-radius:50%;display:inline-block;height:7px;margin-right:7px;width:7px}
.ca-footer{align-items:center;border-top:1px solid var(--line);color:var(--muted);display:grid;font-family:var(--sans);font-size:10px;grid-template-columns:1fr auto 1fr;padding:28px 4vw}
.ca-footer .ca-brand{color:var(--ink);font-size:14px}
.ca-footer .ca-mark{border-color:var(--line)}
.ca-footer>span{justify-self:end}
.ca-back{display:block;font-family:var(--sans);font-size:11px;font-weight:650;letter-spacing:.08em;margin:0 auto;max-width:760px;padding:26px 20px 0;text-transform:uppercase}
@media(max-width:1100px){.ca-header{gap:10px 20px;grid-template-columns:1fr auto;min-height:0;padding-block:12px}.ca-header nav{display:flex;gap:20px;grid-column:1/-1;grid-row:2;justify-content:center}.ca-header .ca-nav-trigger{font-size:10px}.ca-nav-item{position:static}.ca-nav-item.has-menu:after,.ca-nav-menu:before{display:none}.ca-nav-menu{left:4vw;min-width:0;right:4vw;top:calc(100% + 1px);transform:translateY(-5px)}.ca-nav-item:hover .ca-nav-menu,.ca-nav-item:focus-within .ca-nav-menu,.ca-nav-item[open] .ca-nav-menu{transform:translateY(0)}}
@media(hover:none) and (max-width:1100px){.ca-nav-item:not([open]) .ca-nav-menu{opacity:0;pointer-events:none;transform:translateY(-5px);visibility:hidden}}
@media(max-width:700px){.ca-header{padding:10px 18px 12px}.ca-header nav{gap:14px;justify-content:space-between}.ca-footer{gap:15px;grid-template-columns:1fr}.ca-footer p,.ca-footer>span{justify-self:start}}
"""

# Quarto centres a narrow article column and drops figures inside it. For an
# interactive piece that is backwards: the prose wants a readable measure, the
# charts want the whole screen.
FIGURE_CSS = """
body{background:var(--paper)!important}
/* Quarto's page-columns grid pins <main> into a ~800px track. The figures need
   the whole viewport, so the grid is dropped and the measure re-applied below. */
#quarto-content{display:block!important;gap:0!important;max-width:none!important;padding:0!important}
main.content{background:var(--white);margin:0 auto!important;max-width:none!important;padding:0!important;width:100%!important}

/* Quarto wraps every `##` in <section class="level2">. Those must span the full
   width or they trap the figures; the readable measure belongs on the prose
   elements themselves, not on their container. */
main.content section,main.content>div{max-width:none!important;padding:0!important;width:auto!important}
main.content p,main.content h2,main.content h3,main.content h4,main.content ul,
main.content ol,main.content blockquote,main.content pre,main.content details,
main.content .footnotes,main.content hr{
  margin-left:auto!important;margin-right:auto!important;max-width:760px!important;
  padding-left:20px;padding-right:20px}
main.content .footnotes p,main.content .footnotes li,main.content blockquote p{
  padding-left:0;padding-right:0}
#title-block-header{max-width:none!important;padding:64px 20px 30px!important}
#title-block-header>*{margin-left:auto;margin-right:auto;max-width:760px}
#title-block-header .title{font-family:var(--serif);font-size:clamp(40px,5.5vw,68px);font-weight:400;letter-spacing:-.045em;line-height:1.02}
#title-block-header .subtitle{color:var(--muted);font-family:var(--serif);font-size:21px;font-style:italic}

/* Figures break out of the prose column. */
main.content div.cell{max-width:none!important;padding:0!important}
main.content .cell-output-display{margin:44px auto!important;max-width:min(1560px,95vw)!important;width:100%!important}
main.content figure.quarto-float{margin:0!important;width:100%}
.ca-figure{background:var(--white);border:1px solid var(--line);border-radius:4px;position:relative}
/* Quarto nests the plot inside wrappers that carry the figure's original fixed
   height (440px) and clip with overflow:auto. Left alone they cut the enlarged
   chart off above its own axis and legend, so the whole chain has to follow the
   plot instead of constraining it. */
main.content .ca-figure,
main.content .ca-figure figure.quarto-float,
main.content .ca-figure figure.quarto-float div{
  height:auto!important;max-height:none!important;overflow:visible!important}
/* Must out-specify the height:auto rule above, which also matches this div. */
main.content .ca-figure figure.quarto-float div.plotly-graph-div{
  height:clamp(520px,72vh,860px)!important;max-height:none!important;width:100%!important}
/* The two ternary charts lock x/y to an equal scale, so their triangle is sized
   by whichever axis is tighter. In a 1560x770 frame that is the height, and the
   shape ends up marooned in white space with its labels oversized around it.
   Cap those figures near the width the triangle can actually fill; every other
   chart keeps the full-width breakout. Tagged from the plot's own scaleanchor
   in FIGURE_JS, so a new locked-aspect figure picks this up on its own. */
main.content .cell-output-display.ca-aspect-locked:not(.ca-full){
  max-width:min(980px,92vw)!important}
/* A triangle wants a nearly square frame, not the short wide band the shared
   clamp gives it. Must out-specify the .plotly-graph-div height rule above. */
main.content .ca-figure.ca-aspect-locked:not(.ca-full) figure.quarto-float div.plotly-graph-div{
  height:clamp(660px,78vh,900px)!important}
.ca-figure figcaption,.ca-figure .quarto-float-caption{color:var(--muted);font-family:var(--sans)!important;font-size:11px!important;letter-spacing:.04em;padding:0 16px 14px;text-align:left!important}
.ca-expand{background:var(--white);border:1px solid var(--line);border-radius:3px;color:var(--muted);cursor:pointer;font-family:var(--sans);font-size:10px;font-weight:700;letter-spacing:.08em;padding:6px 11px;position:absolute;right:12px;text-transform:uppercase;top:12px;z-index:5}
.ca-expand:hover{background:var(--navy);border-color:var(--navy);color:#f7f2e7}
main.content .ca-figure.ca-full{background:var(--white);border-radius:0;bottom:0;left:0;margin:0!important;max-width:none!important;position:fixed;right:0;top:0;width:100vw!important;z-index:9999}
/* Matches the depth of the clamp rule above (which is otherwise more specific
   and wins even against !important), so expanding actually gets full height. */
main.content .ca-figure.ca-full figure.quarto-float div.plotly-graph-div{
  height:calc(100vh - 62px)!important}
body.ca-locked{overflow:hidden}
@media(max-width:700px){
  main.content>*{padding-left:16px;padding-right:16px}
  main.content .cell-output-display{margin:30px auto!important;max-width:100%!important}
  .ca-figure .plotly-graph-div{height:min(74vh,560px)!important}
}
"""

# Typography and placeholder-figure styling for hand-authored ("prose") articles.
# These pieces don't come out of Quarto, so they carry no prose styling or
# column measure of their own — this is the whole visual system for them,
# built from the same tokens CHROME_CSS already defines, and deliberately
# mirrors the structure FIGURE_CSS gives the Quarto article: a full-width
# white reading surface floating on the page's cream background, with the
# 760px measure applied per text element rather than on a boxed container —
# so a wide figure can still break out to its own, wider measure.
# The link colour (#18bc9c) and card-vs-page contrast are lifted straight
# from the Quarto/Flatly theme espectro-politico.html renders with, sampled
# from its own output, so the two articles read as one visual system.
PROSE_CSS = """
/* This page is a standalone document (see publish_prose), not wrapped by the
   Next.js app, so it never inherits globals.css's `body{margin:0}` reset —
   without this, the browser's default 8px body margin exposes a thin band of
   the page's default (unstyled) background all the way around it. Invisible
   in light mode, but browsers auto-darken that unstyled band in dark mode,
   which reads as a stray black border on every edge. */
html,body{margin:0;padding:0}
html{background:var(--paper)}
body{background:var(--paper)!important}
.ca-article{background:var(--white);color:var(--ink);font-family:var(--serif);padding:64px 0 80px}
.ca-article-header,.ca-article>p,.ca-article>h2,.ca-article>ul,.ca-article>ol{
  margin-left:auto;margin-right:auto;max-width:760px;padding-left:20px;padding-right:20px}
.ca-article-header{margin-bottom:8px}
/* Small-caps meta labels stay sans (matching the site's nav/eyebrow
   convention) even though the base article font below is now serif — only
   the title and reading copy should read as Georgia/Times New Roman. */
.ca-eyebrow{color:var(--muted);font-family:var(--sans);font-size:11px;font-weight:650;letter-spacing:.1em;margin:0 0 14px;text-transform:uppercase}
.ca-title{font-family:var(--serif);font-size:clamp(36px,5.5vw,60px);font-weight:400;letter-spacing:-.03em;line-height:1.04;margin:0 0 16px}
.ca-subtitle{color:var(--muted);font-family:var(--serif);font-size:20px;font-style:italic;margin:0 0 18px}
.ca-byline-label{color:var(--muted);font-family:var(--sans);font-size:11px;font-weight:650;letter-spacing:.1em;margin:0 0 6px;text-transform:uppercase}
.ca-byline-name{color:var(--ink);font-family:var(--sans);font-size:14px;margin:0 0 48px}
.ca-article h2{border-bottom:1px solid var(--line);font-family:var(--serif);font-size:28px;font-weight:400;letter-spacing:-.01em;margin:52px auto 18px;padding-bottom:12px}
.ca-article p{font-size:18px;line-height:1.7;margin:0 auto 20px}
.ca-article a{color:#18bc9c;text-decoration:underline;text-decoration-color:rgba(24,188,156,.35)}
.ca-article a:hover{text-decoration-color:currentColor}
/* CHROME_CSS's .ca-back (shared with the Quarto article) sets no colour of
   its own — the Quarto page reads teal only because Bootstrap/Flatly's own
   theme applies a page-wide `a{color:#18bc9c}` that happens to cover it too.
   This page has no such global rule, so without this it falls back to the
   browser's default (often visited-purple) link colour instead. */
.ca-back{color:#18bc9c}
.ca-viz-placeholder{background:var(--paper-deep);border:1.5px dashed var(--line);border-radius:6px;margin:36px auto 44px;max-width:min(900px,92vw);padding:48px 28px;text-align:center}
.ca-viz-kind{color:var(--muted);font-family:var(--sans);font-size:10px;font-weight:700;letter-spacing:.1em;margin:0 0 10px;text-transform:uppercase}
.ca-viz-title{color:var(--ink);font-family:var(--serif);font-size:20px;margin:0 0 8px}
.ca-viz-note{color:var(--muted);font-family:var(--sans);font-size:13px;line-height:1.5;margin:0 auto;max-width:480px}
.ca-embed{margin:36px auto 44px;max-width:min(980px,92vw)}
.ca-embed iframe{background:transparent;border:0;display:block;width:100%}
/* Extra room for embeds whose content itself is a 2-up grid (or otherwise
   wants more breathing room than 980px) — mirrors .ca-embed-row's ceiling. */
.ca-embed.ca-embed-wide{max-width:min(1400px,94vw)}
/* auto-fit rather than a hard 2-column split: below ~1480px of available
   width (a modest, non-maximized browser window), a forced 2-up split would
   squeeze each hemicycle under 720px — both where its legend starts
   truncating and where the app's own 700px breakpoint quietly drops the
   metrics row — so this collapses to one column instead of letting either
   happen. */
.ca-embed-row{display:grid;gap:20px;grid-template-columns:repeat(auto-fit,minmax(min(720px,100%),1fr));margin:36px auto 44px;max-width:min(1600px,94vw)}
/* Capped independently of the grid track: below the 2-up breakpoint a lone
   track can be much wider than 720px, and a hemicycle stretched that wide
   needs real extra height (taller arc, legend down to one row) that a single
   fixed iframe height can't also give the 2-up case without leaving it half
   empty. Pinning the iframe's own width keeps one height number honest
   either way — and keeps it above the app's 700px breakpoint either way. */
.ca-embed-row iframe{background:transparent;border:0;display:block;margin:0 auto;max-width:760px;width:100%}

/* Closing "explore the rest of the tool" row — three square link cards
   instead of inline text links, so the article ends on something more
   clickable than a sentence. Same 760px measure as the prose above it. */
.ca-explore-label{color:var(--muted);font-family:var(--sans);font-size:11px;font-weight:650;letter-spacing:.1em;margin:44px auto -6px;text-transform:uppercase}
.ca-explore{display:grid;gap:14px;grid-template-columns:repeat(3,1fr);margin:20px auto 48px}
.ca-explore-label,.ca-explore{max-width:760px;padding-left:20px;padding-right:20px}
.ca-explore-card{aspect-ratio:1;background:var(--white);border:1px solid var(--line);border-radius:6px;color:var(--ink);display:flex;flex-direction:column;font-family:var(--sans);justify-content:space-between;padding:20px;text-decoration:none;transition:background .15s ease,border-color .15s ease,color .15s ease,transform .15s ease}
/* Beats `.ca-article a`'s teal link color (0-1-1 specificity) with two
   classes (0-2-0) rather than !important. */
.ca-article .ca-explore-card{color:var(--ink);text-decoration:none}
.ca-explore-card-eyebrow{color:var(--muted);font-size:9px;font-weight:700;letter-spacing:.09em;text-transform:uppercase}
.ca-explore-card-title{font-family:var(--serif);font-size:19px;line-height:1.15}
.ca-explore-card-arrow{align-self:flex-end;font-size:17px}
.ca-article .ca-explore-card:hover,.ca-article .ca-explore-card:focus-visible{background:var(--navy);border-color:var(--navy);color:#f7f2e7;outline:0;transform:translateY(-2px)}
.ca-explore-card:hover .ca-explore-card-eyebrow,.ca-explore-card:focus-visible .ca-explore-card-eyebrow{color:#8dbca5}

@media(max-width:700px){
  .ca-article{padding:40px 0 60px}
  .ca-embed-row{grid-template-columns:1fr}
  .ca-article-header,.ca-article>p,.ca-article>h2,.ca-article>ul,.ca-article>ol{padding-left:18px;padding-right:18px}
  .ca-explore-label,.ca-explore{padding-left:18px;padding-right:18px}
  .ca-explore{gap:10px;grid-template-columns:1fr}
  .ca-explore-card{aspect-ratio:auto;padding:18px}
}
"""

# Plotly sizes itself once, at its original container width. Widening the
# container afterwards does nothing until it is told to measure again.
FIGURE_JS = """
(function(){
  // Plotly font sizes and margins are absolute pixels, fixed when the .qmd is
  // rendered. The article authors them against a ~920px column (see
  // chart_style.py), but this page stretches the same figure to 1560px and
  // wider still in fullscreen — at which point the type is proportionally
  // smaller than it was authored to be, which is the exact problem the article's
  // type scale exists to solve. Nothing in Plotly scales type with the
  // container, so scale it here from the width the figure actually gets.
  var CA_BASE_WIDTH = 920;
  var CA_MAX_SCALE = 1.75;

  // How hard each role follows the width. Scaling everything by the same factor
  // preserves the authored ratios, which sounds right and is not: a headline and
  // a tick label do not want to grow at the same rate. Held together, a legend
  // sized for a 920px column arrives at ~29px on a 1560px one and competes with
  // the title. So the headline takes the full factor and everything supporting
  // it takes a fraction, widening the hierarchy exactly as the canvas grows.
  var CA_ROLE_EXPONENT = {
    title: 1,
    subtitle: 0.55,
    support: 0.35,   // ticks, legend, annotations, in-plot labels
  };

  // Every font-bearing object in one figure, collected once with the role that
  // decides its growth. Marker sizes are deliberately excluded: the state
  // bubbles encode vote volume through sizeref/area, so touching their size
  // would misstate the data.
  function fontTargets(div){
    var layout = div.layout || {}, data = div.data || [], out = [];
    function add(obj, exp){
      if(obj && typeof obj.size === 'number') out.push({obj:obj, exp:exp});
    }
    var SUPPORT = CA_ROLE_EXPONENT.support;
    if(layout.title){
      add(layout.title.font, CA_ROLE_EXPONENT.title);
      if(layout.title.subtitle) add(layout.title.subtitle.font, CA_ROLE_EXPONENT.subtitle);
    }
    add(layout.font, SUPPORT);
    ['xaxis','yaxis'].forEach(function(key){
      if(layout[key]) add(layout[key].tickfont, SUPPORT);
    });
    if(layout.legend) add(layout.legend.font, SUPPORT);
    if(layout.hoverlabel) add(layout.hoverlabel.font, SUPPORT);
    (layout.annotations||[]).forEach(function(ann){ add(ann.font, SUPPORT); });
    data.forEach(function(trace){ add(trace.textfont, SUPPORT); });
    return out;
  }

  // Margins scale with the type they have to clear — held back, because a
  // margin grown by the full factor eats the plot area on a wide screen.
  function marginTargets(div){
    var margin = (div.layout||{}).margin, out = [];
    if(!margin) return out;
    ['l','r','t','b'].forEach(function(side){
      if(typeof margin[side] === 'number') out.push({obj:margin, key:side});
    });
    return out;
  }

  function scaleTypography(div){
    var width = div.clientWidth;
    if(!width) return false;
    var scale = Math.max(1, Math.min(width / CA_BASE_WIDTH, CA_MAX_SCALE));
    if(!div.__caType){
      div.__caType = {
        fonts: fontTargets(div).map(function(f){
          return {obj:f.obj, key:'size', base:f.obj.size, exp:f.exp};
        }),
        margins: marginTargets(div).map(function(m){ return {obj:m.obj, key:m.key, base:m.obj[m.key]}; })
      };
    }
    var changed = false;
    function apply(entry, factor){
      var next = Math.round(entry.base * factor * 10) / 10;
      if(entry.obj[entry.key] !== next){ entry.obj[entry.key] = next; changed = true; }
    }
    div.__caType.fonts.forEach(function(e){ apply(e, Math.pow(scale, e.exp)); });
    // Square root keeps the margins ahead of the type without crowding the plot.
    div.__caType.margins.forEach(function(e){ apply(e, Math.sqrt(scale)); });
    return changed;
  }

  // A figure whose y axis is pinned to x (the ternaries) cannot use the full
  // breakout width — see the .ca-aspect-locked rule. Read it off the plot
  // rather than hard-coding which figures those are.
  function tagAspectLocked(div){
    var cell = div.closest ? div.closest('.cell-output-display') : null;
    if(!cell || cell.__caAspectTagged || !div.layout) return;
    var yaxis = div.layout.yaxis;
    if(yaxis && yaxis.scaleanchor) cell.classList.add('ca-aspect-locked');
    cell.__caAspectTagged = true;
  }

  // Re-apply the ranges a locked-aspect figure was authored with. Plotly solves
  // the x/y scale constraint once, against the box it first saw, then writes the
  // solved ranges back over layout.xaxis.range — so a later Plots.resize only
  // rescales that first solution instead of redoing it, and the triangle stays
  // at roughly a third of the frame however the container is sized. Feeding the
  // authored ranges back makes it solve again at the current size.
  // chart_style.locked_aspect_meta() puts them in layout.meta, the one place
  // plotly leaves alone.
  function restoreLockedRanges(div){
    var meta = (div.layout||{}).meta;
    if(!meta || !meta.ca_xrange || !meta.ca_yrange) return;
    window.Plotly.relayout(div, {
      'xaxis.range': meta.ca_xrange.slice(),
      'yaxis.range': meta.ca_yrange.slice()
    });
  }

  // One figure, brought back in step with its container. The steps are chained
  // rather than merely called in order because each returns a promise, and
  // restoring the ranges before the resize has settled just gets overwritten by
  // the resize's own solve.
  function refresh(div){
    if(!window.Plotly) return;
    try{
      // Only redraw when a size actually moved, so the ResizeObserver below
      // cannot feed itself: the scale is a pure function of the width, so it
      // reaches a fixed point after one pass.
      var step = scaleTypography(div)
        ? window.Plotly.redraw(div)
        : Promise.resolve();
      step
        .then(function(){ return window.Plotly.Plots.resize(div); })
        // Last, so it solves against the final box. Changing the ranges does not
        // change the container, so this cannot retrigger the observer.
        .then(function(){ restoreLockedRanges(div); })
        .catch(function(){});
    }catch(e){}
  }

  function resizeAll(){
    if(!window.Plotly) return;
    document.querySelectorAll('.plotly-graph-div').forEach(function(div){
      tagAspectLocked(div);
      refresh(div);
    });
  }
  function ready(fn){
    if(document.readyState!=='loading') fn();
    else document.addEventListener('DOMContentLoaded',fn);
  }
  ready(function(){
    document.querySelectorAll('main.content .cell-output-display').forEach(function(cell){
      if(!cell.querySelector('.plotly-graph-div')) return;
      cell.classList.add('ca-figure');
      var button=document.createElement('button');
      button.type='button';
      button.className='ca-expand';
      button.textContent='Ampliar';
      button.setAttribute('aria-expanded','false');
      button.addEventListener('click',function(){
        var full=cell.classList.toggle('ca-full');
        document.body.classList.toggle('ca-locked',full);
        button.textContent=full?'Cerrar':'Ampliar';
        button.setAttribute('aria-expanded',String(full));
        if(!full) cell.scrollIntoView({block:'center'});
        setTimeout(resizeAll,60);
      });
      cell.appendChild(button);
    });
    // Plotly measures once, and the container keeps changing after that: fonts
    // land, the sticky header settles, the figure is expanded. A timer would be
    // a guess, so observe the boxes themselves and re-measure whenever one moves.
    if(window.ResizeObserver){
      var observer=new ResizeObserver(function(entries){
        entries.forEach(function(entry){
          var div=entry.target.querySelector('.plotly-graph-div');
          // Same path as resizeAll: a bare Plots.resize here would stretch the
          // figure while leaving the type at its authored size.
          if(div) refresh(div);
        });
      });
      document.querySelectorAll('.ca-figure').forEach(function(cell){ observer.observe(cell); });
    }
    setTimeout(resizeAll,80);
    window.addEventListener('resize',function(){ clearTimeout(window.__caResize); window.__caResize=setTimeout(resizeAll,150); });
    document.addEventListener('keydown',function(event){
      if(event.key!=='Escape') return;
      var open=document.querySelector('.ca-figure.ca-full');
      if(open) open.querySelector('.ca-expand').click();
    });
  });
})();
"""


def chrome_header(active: str = "articulos") -> str:
    # Mirrors SECTIONS in web/app/site-chrome.tsx. Keep the two in step: this
    # markup is what makes a published Quarto file read as a page of the site.
    sections = (
        ("visualizaciones", "/visualizaciones", "Visualizaciones"),
        ("articulos", "/articulos", "Artículos"),
        ("datos", "/datos", "Datos"),
    )
    visualizations = (
        ("/visualizaciones/trayectoria", "Geografía electoral", "Elecciones"),
        ("/visualizaciones/votaciones", "Buscador de votaciones", "Congreso"),
        ("/visualizaciones/diputados", "Cámara de Diputados", "Congreso"),
        ("/visualizaciones/senado", "Senado de la República", "Congreso"),
    )
    menus = {
        "visualizaciones": (
            "Todas las visualizaciones",
            visualizations,
        ),
        "articulos": (
            "Todos los artículos",
            tuple(
                (
                    f"/articulos/{article.slug}.html",
                    article.title,
                    article.subtitle,
                )
                for article in ARTICLES
            ),
        ),
    }
    links = []
    for key, href, label in sections:
        menu = menus.get(key)
        active_class = " active" if key == active else ""
        if menu is None:
            links.append(
                f'<div class="ca-nav-item"><a class="ca-nav-trigger{active_class}" '
                f'href="{href}">{label}</a></div>'
            )
            continue
        overview, items = menu
        item_links = "".join(
            f'<a href="{item_href}"><strong>{item_label}</strong><span>{meta}</span></a>'
            for item_href, item_label, meta in items
        )
        links.append(
            f'<details class="ca-nav-item has-menu" name="site-navigation">'
            f'<summary class="ca-nav-trigger{active_class}">'
            f'{label}<span class="ca-chevron" aria-hidden="true">⌄</span></summary>'
            f'<div class="ca-nav-menu" aria-label="Opciones de {label}">'
            f'<a class="ca-menu-overview" href="{href}">{overview}<span>→</span></a>'
            f'<div class="ca-menu-list">{item_links}</div></div></details>'
        )
    return (
        '<header class="ca-header">'
        '<a class="ca-brand" href="/"><span class="ca-mark">ca</span>'
        "<span>current affairs<br>mx</span></a>"
        f"<nav>{''.join(links)}</nav>"
        '<div class="ca-status"><i></i>Artículo</div>'
        "</header>"
        '<a class="ca-back" href="/articulos">← Todos los artículos</a>'
    )


def chrome_footer() -> str:
    return (
        '<footer class="ca-footer">'
        '<div class="ca-brand"><span class="ca-mark">ca</span><span>current affairs mx</span></div>'
        "<p>Una lectura pública de la política mexicana.</p>"
        "<span>Artículos</span>"
        "</footer>"
    )


SCRIPT_RE = re.compile(r"<script\b[^>]*>.*?</script>", re.S | re.I)
# Quarto's own markup for rendered math. MathJax's source text is full of TeX
# delimiters, so the presence of the library says nothing; this does.
MATH_RE = re.compile(r'class="math (?:inline|display)"|<mjx-container', re.I)


def slim(html: str) -> tuple[str, dict[str, int]]:
    """Drop payload that `embed-resources` duplicates or includes for nothing.

    Quarto inlines the whole Plotly bundle once per figure — four identical
    4.8 MB copies in this article — and ships MathJax whether or not a single
    formula was written. Only the first Plotly copy defines `window.Plotly`;
    the rest are dead weight the reader still has to download.
    """
    has_math = bool(MATH_RE.search(html))
    stats = {
        "plotly_removed": 0,
        "mathjax_removed": 0,
        "cdn_removed": 0,
        "bytes_before": len(html),
    }
    seen_plotly = False

    def replace(match: re.Match[str]) -> str:
        nonlocal seen_plotly
        block = match.group(0)
        # Quarto emits `import "https://cdn.plot.ly/plotly-3.6.0.min"` — a URL
        # missing its .js that 403s on every load. Plotly is already inlined, so
        # this only costs the reader a failed third-party request.
        if len(block) < 400 and "cdn.plot.ly" in block:
            stats["cdn_removed"] += 1
            return ""
        if "plotly.js v" in block[:400]:
            if seen_plotly:
                stats["plotly_removed"] += 1
                return ""
            seen_plotly = True
            return block
        if not has_math and "/MathJax.js" in block[:400]:
            stats["mathjax_removed"] += 1
            return ""
        return block

    html = SCRIPT_RE.sub(replace, html)
    stats["bytes_after"] = len(html)
    return html, stats


def wrap(html: str, extra_css: str = FIGURE_CSS, script: str = "") -> str:
    """Inject site chrome and page-specific styling/behaviour around a body."""
    head_close = html.lower().rfind("</head>")
    if head_close == -1:
        raise ValueError("rendered article has no </head>; is this a Quarto html file?")
    injection = f"<style>{CHROME_CSS}{extra_css}</style>"
    html = html[:head_close] + injection + html[head_close:]

    body_open = re.search(r"<body[^>]*>", html, re.I)
    if not body_open:
        raise ValueError("rendered article has no <body>")
    html = html[: body_open.end()] + chrome_header() + html[body_open.end() :]

    body_close = html.lower().rfind("</body>")
    if body_close == -1:
        raise ValueError("rendered article has no </body>")
    tail = chrome_footer() + (f"<script>{script}</script>" if script else "")
    return html[:body_close] + tail + html[body_close:]


def publish(article: Article) -> tuple[Path, dict[str, int]]:
    if article.kind == "prose":
        return publish_prose(article)
    return publish_quarto(article)


def publish_quarto(article: Article) -> tuple[Path, dict[str, int]]:
    if not article.source.exists():
        raise FileNotFoundError(
            f"{article.source} is missing. Render it with Quarto first:\n"
            f"  quarto render {article.source.with_suffix('.qmd')}"
        )
    html = article.source.read_text(encoding="utf-8")
    if "plotly-graph-div" not in html:
        raise ValueError(
            f"{article.source} contains no Plotly figures. Quarto probably rendered "
            "without executing the notebook; re-render before publishing."
        )
    slimmed, stats = slim(html)
    wrapped = wrap(slimmed, extra_css=FIGURE_CSS, script=FIGURE_JS)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    destination = OUT_DIR / f"{article.slug}.html"
    destination.write_text(wrapped, encoding="utf-8")

    if wrapped.count('class="plotly-graph-div"') and "window.Plotly = Plotly" not in wrapped:
        raise RuntimeError(
            f"{destination} has Plotly figures but no Plotly bundle left; "
            "the de-duplication in slim() removed too much."
        )
    return destination, stats


def publish_prose(article: Article) -> tuple[Path, dict[str, int]]:
    """Wrap a hand-authored, body-only HTML fragment into a full site page.

    Unlike a Quarto render, the fragment has no <head>/<body> of its own —
    just the article markup (see PROSE_CSS for the classes it expects), so
    this builds a minimal document around it before handing off to wrap().
    """
    if not article.source.exists():
        raise FileNotFoundError(f"{article.source} is missing.")
    fragment = article.source.read_text(encoding="utf-8")
    document = (
        "<!doctype html><html lang=\"es\"><head><meta charset=\"utf-8\">"
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{article.title} · current affairs mx</title></head>"
        f"<body>{fragment}</body></html>"
    )
    wrapped = wrap(document, extra_css=PROSE_CSS)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    destination = OUT_DIR / f"{article.slug}.html"
    destination.write_text(wrapped, encoding="utf-8")
    return destination, {
        "plotly_removed": 0,
        "mathjax_removed": 0,
        "cdn_removed": 0,
        "bytes_before": len(fragment),
        "bytes_after": len(wrapped),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slug", help="Publish only this article.")
    args = parser.parse_args()

    selected = [a for a in ARTICLES if not args.slug or a.slug == args.slug]
    if not selected:
        raise SystemExit(f"No article with slug {args.slug!r}")

    for article in selected:
        destination, stats = publish(article)
        before = stats["bytes_before"] / 1_048_576
        after = destination.stat().st_size / 1_048_576
        print(
            f"Wrote {destination} ({after:.1f} MB, was {before:.1f} MB) — dropped "
            f"{stats['plotly_removed']} duplicate Plotly bundle(s), "
            f"{stats['mathjax_removed']} unused MathJax copy(ies) and "
            f"{stats['cdn_removed']} failing CDN import(s)"
        )

    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(
        json.dumps(
            [
                {
                    "slug": a.slug,
                    "href": f"/articulos/{a.slug}.html",
                    "title": a.title,
                    "subtitle": a.subtitle,
                    "author": a.author,
                    "published": a.published,
                    "summary": a.summary,
                    "topics": list(a.topics),
                }
                for a in ARTICLES
            ],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Wrote {INDEX_PATH} with {len(ARTICLES)} article(s)")


if __name__ == "__main__":
    main()
