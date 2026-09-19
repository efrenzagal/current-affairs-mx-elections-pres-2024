"use client";

import { useEffect, useState } from "react";

import Explorer, { type Chamber } from "../../explorer";

/**
 * The real hemicycle Explorer, bare — no duplicate site header/footer, meant
 * to be iframed into a static article. Everything else (hover tooltips,
 * party/state filters, search, seat click-through) is the genuine component
 * used by /visualizaciones/diputados and /senado, not a rebuild.
 *
 * `?bare=1` strips it further, down to just the hemicycle chart and its
 * hover preview — no tabs, filters, search box or profile panel — and
 * disables the seat click-through (see `interactive` on Explorer), for a
 * purely illustrative embed.
 *
 * `?singleView=1` hides the "Quién ocupa el escaño hoy" / "Quién lo ganó en
 * 2024" toggle, for an embed built around one specific view (the "actual"
 * one, already the default) where showing the other tab would be a red
 * herring nobody's meant to click.
 *
 * Example: /visualizaciones/embed/hemiciclo?chamber=senado&bare=1
 */
export default function HemicicloEmbedPage() {
  const [chamber, setChamber] = useState<Chamber | null>(null);
  const [bare, setBare] = useState(false);
  const [singleView, setSingleView] = useState(false);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    setChamber(params.get("chamber") === "diputados" ? "diputados" : "senado");
    setBare(params.get("bare") === "1");
    setSingleView(params.get("singleView") === "1");
  }, []);

  if (!chamber) return null;

  const classes = ["embed-hemiciclo"];
  if (bare) classes.push("hemiciclo-bare");
  if (singleView) classes.push("hemiciclo-single-view");

  return (
    <div className={classes.join(" ")}>
      <Explorer chamber={chamber} interactive={!bare} />
    </div>
  );
}
