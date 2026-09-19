"use client";

import { useEffect, useState } from "react";

import type { Chamber } from "../../explorer";
import { LegislatorCard } from "../../legislator-card";

/**
 * A bare embed of one or more `LegislatorCard`s, meant to be iframed into a
 * static article page rather than browsed directly — no site header/footer,
 * just the card(s). `?seats=` takes a comma-separated list of seat ids so the
 * same route covers both a single profile and a side-by-side comparison.
 *
 * Example: /visualizaciones/embed/legislador?chamber=senado&seats=SEN_A,SEN_B
 */
export default function LegisladorEmbedPage() {
  const [seats, setSeats] = useState<string[] | null>(null);
  const [chamber, setChamber] = useState<Chamber>("senado");

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const raw = params.get("seats") ?? params.get("seat") ?? "";
    setSeats(raw.split(",").map((id) => id.trim()).filter(Boolean));
    setChamber(params.get("chamber") === "diputados" ? "diputados" : "senado");
  }, []);

  if (seats === null) return null;
  if (!seats.length) {
    return <p className="seat-flag">Falta el parámetro `seats` en la URL del embed.</p>;
  }

  return (
    <div className={`embed-legislator-grid${seats.length > 1 ? " embed-legislator-grid-multi" : ""}`}>
      {seats.map((seatId) => (
        <LegislatorCard key={seatId} chamber={chamber} seatId={seatId} />
      ))}
    </div>
  );
}
