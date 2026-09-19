"use client";

import { useEffect, useState } from "react";

import Explorer, { type Chamber } from "../../explorer";

/**
 * The real Explorer, deep-linked straight to one roll call's decision view
 * (see the `?vote=` initial state in explorer.tsx) and bare — no duplicate
 * site header/footer, no seat browser above it, just the vote-detail section
 * (result, quorum, per-party breakdown, decision hemicycle). Meant to be
 * iframed into a static article.
 *
 * Example: /visualizaciones/embed/votacion?chamber=senado&vote=SEN_...
 */
export default function VotacionEmbedPage() {
  const [chamber, setChamber] = useState<Chamber | null>(null);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    setChamber(params.get("chamber") === "diputados" ? "diputados" : "senado");
  }, []);

  if (!chamber) return null;

  return (
    <div className="embed-votacion">
      <Explorer chamber={chamber} />
    </div>
  );
}
