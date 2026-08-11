"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { SITE_NAME, SiteFooter, SiteHeader } from "../site-chrome";
import { PARTY_COLORS, partyRank } from "./parties";
import {
  CHOICE_COLORS,
  cleanTitle,
  label,
  shortDate,
  shortTitle,
  stageLabel,
  topicLabel,
  voteLabel,
} from "./votes";

export type Chamber = "diputados" | "senado";

/** Occupancy state published by the official directory, plus our own fallback. */
type SeatStatus = "en_funciones" | "licencia" | "vacante" | "sin_directorio";

type Seat = {
  id: string;
  seatType: "MR" | "FM" | "RP";
  stateId: number | null;
  state: string | null;
  district: number | null;
  districtSeat: string | null;
  circunscripcion: number | null;
  listNumber: number | null;
  electionActor: string | null;
  winningVotes: number | null;
  winningPct: number | null;
  /** Who INE recorded as winning this seat in 2024. */
  electedPersonId: string | null;
  electedName: string;
  electedParty: string;
  electedNameRole: "titular" | "suplente";
  /** Who the official directory shows holding it at the roster cutoff. */
  currentPersonId: string | null;
  currentName: string | null;
  currentParty: string;
  currentStatus: SeatStatus;
};

type Vote = {
  id: string;
  date: string;
  title: string;
  status: string | null;
  sourceUrl: string;
  favor: number;
  contra: number;
  abstention: number;
  absent: number;
  presentNoVote: number;
  total: number;
  stage: string | null;
  topic: string | null;
};

type SiteData = {
  manifest: {
    schemaVersion: number;
    legislature: number;
    chamber: Chamber;
    sourceThrough: string;
    seatCount: number;
    voteCount: number;
    linkedSeats: number;
    roster: { observedAt: string; sourceUrl: string };
    substitutedSeats: number;
    partyChangedSeats: number;
    onLeaveSeats: number;
    vacantSeats: number;
    currentLinkedSeats: number;
  };
  seats: Seat[];
  votes: Vote[];
  /** Keyed by person, not by seat: a seat can have had more than one occupant. */
  histories: Record<string, [string, string][]>;
  partyVotes: Record<string, Record<string, Record<string, number>>>;
};

/** Which identity the hemicycle names in each seat. */
type View = "actual" | "electoral";

const NO_HISTORY: [string, string][] = [];

const CHAMBERS: Record<Chamber, {
  dataUrl: string;
  name: string;
  short: string;
  member: string;
  memberSearch: string;
  pin: string;
  sourceName: string;
  isSenate: boolean;
}> = {
  diputados: {
    dataUrl: "/data/legislature-66.json",
    name: "Cámara de Diputados",
    short: "Cámara de Diputados",
    member: "diputación",
    memberSearch: "Buscar diputada o diputado",
    pin: "una diputación",
    sourceName: "la Gaceta Parlamentaria de la Cámara de Diputados",
    isSenate: false,
  },
  senado: {
    dataUrl: "/data/senate-66.json",
    name: "Senado de la República",
    short: "Senado",
    member: "senaduría",
    memberSearch: "Buscar senadora o senador",
    pin: "una senaduría",
    sourceName: "el Senado de la República",
    isSenate: true,
  },
};

function statusLabel(status: SeatStatus) {
  if (status === "licencia") return "Con licencia";
  if (status === "vacante") return "Vacante";
  if (status === "sin_directorio") return "Sin registro en el directorio";
  return "En funciones";
}

function electionActorLabel(actor: string | null) {
  return actor?.replaceAll("_", " · ") ?? "";
}

function seatTypeLabel(seatType: Seat["seatType"]) {
  if (seatType === "MR") return "mayoría relativa";
  if (seatType === "FM") return "primera minoría";
  return "representación proporcional";
}

/**
 * The identity a seat carries under the active view.
 *
 * `licencia` and `vacante` deliberately override the party: the directory still
 * prints a group for a member on leave, but the seat is not voting with that
 * group, and colouring it as if it were would overstate the bloc.
 */
type Occupant = {
  name: string;
  party: string;
  personId: string | null;
  status: SeatStatus;
  substituted: boolean;
  partyChanged: boolean;
};

function occupantOf(seat: Seat, view: View): Occupant {
  if (view === "electoral") {
    return {
      name: seat.electedName,
      party: seat.electedParty,
      personId: seat.electedPersonId,
      status: "en_funciones",
      substituted: false,
      partyChanged: false,
    };
  }
  const party =
    seat.currentStatus === "licencia"
      ? "LICENCIA"
      : seat.currentStatus === "vacante"
        ? "VACANTE"
        : seat.currentParty;
  return {
    name: seat.currentName ?? "Escaño vacante",
    party,
    personId: seat.currentPersonId,
    status: seat.currentStatus,
    substituted: seat.currentPersonId !== seat.electedPersonId,
    partyChanged:
      seat.currentStatus === "en_funciones" && seat.currentParty !== seat.electedParty,
  };
}

/**
 * Seat coordinates always come from the *electoral* party and name, never the
 * current occupant. A seat is a constitutional object: it must not slide across
 * the hemicycle when a member changes bench, or switching views would look like
 * the chamber rearranged itself.
 */
function seatCoordinates(seats: Seat[]) {
  const ordered = [...seats].sort(
    (a, b) =>
      partyRank(a.electedParty) - partyRank(b.electedParty) ||
      a.electedName.localeCompare(b.electedName, "es"),
  );
  const isSenate = ordered.length <= 130;
  const ringCounts = isSenate
    ? [14, 17, 20, 23, 26, 28]
    : Array.from({ length: 16 }, (_, index) => 16 + index * 2);
  ringCounts[ringCounts.length - 1] += ordered.length - ringCounts.reduce((sum, count) => sum + count, 0);

  let cursor = 0;
  return ringCounts.flatMap((count, ring) => {
    const radius = isSenate ? 40 + ring * 10 : 24 + ring * 4.55;
    return Array.from({ length: count }, (_, index) => {
      const seat = ordered[cursor++];
      const angle = Math.PI - (index / Math.max(count - 1, 1)) * Math.PI;
      return {
        ...seat,
        x: 50 + Math.cos(angle) * radius * 0.53,
        y: 96 - Math.sin(angle) * radius,
      };
    });
  });
}

export default function Explorer({ chamber }: { chamber: Chamber }) {
  const config = CHAMBERS[chamber];
  const isSenate = config.isSenate;

  const [data, setData] = useState<SiteData | null>(null);
  const [error, setError] = useState(false);
  const [view, setView] = useState<View>("actual");
  const [selectedSeatId, setSelectedSeatId] = useState<string | null>(null);
  const [selectedVoteId, setSelectedVoteId] = useState<string | null>(null);
  const [hoveredSeatId, setHoveredSeatId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [partyFilter, setPartyFilter] = useState("Todos");
  const [colorMode, setColorMode] = useState<"party" | "vote">("party");
  const pendingScroll = useRef(false);

  useEffect(() => {
    let cancelled = false;
    fetch(config.dataUrl)
      .then((response) => {
        if (!response.ok) throw new Error(String(response.status));
        return response.json();
      })
      .then((payload: SiteData) => {
        if (cancelled) return;
        setData(payload);
        setSelectedSeatId(payload.seats[0]?.id ?? null);
        // No vote is preselected: the vote detail only appears once the reader
        // opens one, from the history list or the calendar.
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });
    return () => {
      cancelled = true;
    };
  }, [config.dataUrl]);

  // Runs after the vote-detail section has been mounted by the render that
  // `openVote` triggered, which is the only point where it can be scrolled to.
  useEffect(() => {
    if (!pendingScroll.current || !selectedVoteId) return;
    pendingScroll.current = false;
    // When the hemicycle is already on screen it recolors in place, and that is
    // the answer to "who voted how" — scrolling past it would hide the result.
    // Only jump to the breakdown when the hemicycle is not visible anyway,
    // which is the usual case on the single-column mobile layout.
    const rect = document.getElementById("hemiciclo")?.getBoundingClientRect();
    if (rect && rect.bottom > 0 && rect.top < window.innerHeight) return;
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    document.getElementById("votacion")?.scrollIntoView({
      behavior: reduceMotion ? "auto" : "smooth",
      block: "start",
    });
  }, [selectedVoteId]);

  const votesById = useMemo(
    () => new Map(data?.votes.map((vote) => [vote.id, vote]) ?? []),
    [data],
  );
  const coords = useMemo(() => (data ? seatCoordinates(data.seats) : []), [data]);
  const occupants = useMemo(() => {
    const map = new Map<string, Occupant>();
    for (const seat of data?.seats ?? []) map.set(seat.id, occupantOf(seat, view));
    return map;
  }, [data, view]);

  const selectedSeat = data?.seats.find((seat) => seat.id === selectedSeatId) ?? null;
  const selectedOccupant = selectedSeat ? occupants.get(selectedSeat.id)! : null;
  const previewSeat = data?.seats.find((seat) => seat.id === hoveredSeatId) ?? selectedSeat;
  const previewOccupant = previewSeat ? occupants.get(previewSeat.id)! : null;

  // A shared empty array keeps the identity stable across renders, so the
  // memos downstream of a history do not recompute for every unlinked seat.
  const historyOf = (occupant: Occupant | null) => {
    const personId = occupant?.personId;
    if (!personId || !data) return NO_HISTORY;
    return data.histories[personId] ?? NO_HISTORY;
  };
  const selectedHistory = historyOf(selectedOccupant);
  const previewHistory = historyOf(previewOccupant);
  const selectedVote = selectedVoteId ? votesById.get(selectedVoteId) ?? null : null;

  const parties = useMemo(() => {
    if (!data) return [];
    return [...new Set([...occupants.values()].map((occupant) => occupant.party))].sort(
      (a, b) => partyRank(a) - partyRank(b),
    );
  }, [data, occupants]);

  const partyCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const occupant of occupants.values()) {
      counts.set(occupant.party, (counts.get(occupant.party) ?? 0) + 1);
    }
    return counts;
  }, [occupants]);

  // Calendar strip for the *selected* seat, not the hovered one: these squares
  // are click targets, so they must not shift under the cursor on hover.
  const calendarYears = useMemo(() => {
    const byYear = new Map<string, { voteId: string; choice: string; date: string; title: string }[]>();
    for (const [voteId, choice] of selectedHistory) {
      const vote = votesById.get(voteId);
      if (!vote) continue;
      const year = vote.date.slice(0, 4);
      const bucket = byYear.get(year);
      const entry = { voteId, choice, date: vote.date, title: vote.title };
      if (bucket) bucket.push(entry);
      else byYear.set(year, [entry]);
    }
    return [...byYear.entries()]
      .map(([year, entries]) => ({
        year,
        entries: entries.sort(
          (a, b) => a.date.localeCompare(b.date) || a.voteId.localeCompare(b.voteId),
        ),
      }))
      .sort((a, b) => b.year.localeCompare(a.year));
  }, [selectedHistory, votesById]);

  // How every seat voted on the open roll call. Seats absent from the record
  // are deliberately left out rather than defaulted to "Ausente": the source
  // does not distinguish "did not vote" from "not in the chamber that day",
  // and for the Senado that gap is large enough to matter.
  const choiceBySeat = useMemo(() => {
    const map = new Map<string, string>();
    if (!data || !selectedVoteId) return map;
    for (const seat of data.seats) {
      const personId = occupants.get(seat.id)?.personId;
      if (!personId) continue;
      const entry = data.histories[personId]?.find(([voteId]) => voteId === selectedVoteId);
      if (entry) map.set(seat.id, entry[1]);
    }
    return map;
  }, [data, selectedVoteId, occupants]);

  const choiceTotals = useMemo(() => {
    const totals = new Map<string, number>();
    for (const choice of choiceBySeat.values()) {
      totals.set(choice, (totals.get(choice) ?? 0) + 1);
    }
    return totals;
  }, [choiceBySeat]);

  const showingVoteColors = colorMode === "vote" && Boolean(selectedVoteId);
  const unrecordedSeats = data ? data.seats.length - choiceBySeat.size : 0;

  const queryNormalized = query.trim().toLocaleLowerCase("es");
  const isSeatVisible = (seat: Seat) => {
    const occupant = occupants.get(seat.id);
    if (!occupant) return true;
    const partyMatches = partyFilter === "Todos" || occupant.party === partyFilter;
    const textMatches =
      !queryNormalized || occupant.name.toLocaleLowerCase("es").includes(queryNormalized);
    return partyMatches && textMatches;
  };

  function selectSeat(seat: Seat) {
    setSelectedSeatId(seat.id);
    // Clear the open vote: it belongs to the previous member's history.
    setSelectedVoteId(null);
    setColorMode("party");
  }

  function openVote(voteId: string) {
    // The detail section is unmounted until a vote is chosen, so the scroll has
    // to wait for the render that creates it (see the effect above).
    pendingScroll.current = true;
    setSelectedVoteId(voteId);
    setColorMode("vote");
  }

  function selectView(nextView: View) {
    if (nextView === view) return;
    setView(nextView);
    // The open vote and the party filter both belong to the identities the
    // previous view resolved; neither survives the switch coherently.
    setSelectedVoteId(null);
    setPartyFilter("Todos");
    setColorMode("party");
  }

  if (error) {
    return (
      <main className="state-screen">
        <p className="eyebrow">{SITE_NAME}</p>
        <h1>No pudimos cargar el corte de votaciones.</h1>
        <p>Actualiza la página para intentarlo de nuevo.</p>
      </main>
    );
  }

  if (!data || !selectedSeat || !selectedOccupant) {
    return (
      <main className="state-screen loading-state" aria-live="polite">
        <span className="loading-mark" />
        <p className="eyebrow">{SITE_NAME}</p>
        <h1>Preparando el pleno…</h1>
      </main>
    );
  }

  const activeVotes = selectedHistory.filter(([, choice]) =>
    ["Favor", "Contra", "Abstención", "Abstencion"].includes(choice),
  );
  const attendance = selectedHistory.length
    ? 1 - selectedHistory.filter(([, choice]) => choice === "Ausente").length / selectedHistory.length
    : 0;
  const favorRate = activeVotes.length
    ? activeVotes.filter(([, choice]) => choice === "Favor").length / activeVotes.length
    : 0;
  const previewActive = previewHistory.filter(([, choice]) => choice !== "Ausente");
  const partyRows = Object.entries((selectedVote && data.partyVotes[selectedVote.id]) ?? {})
    .map(([party, counts]) => ({
      party,
      favor: counts.Favor ?? 0,
      contra: counts.Contra ?? 0,
      abstention: counts["Abstención"] ?? counts.Abstencion ?? 0,
      absent: counts.Ausente ?? 0,
    }))
    .sort((a, b) => partyRank(a.party) - partyRank(b.party));
  const rosterCutoff = data.manifest.roster.observedAt.slice(0, 10);
  const isCurrentView = view === "actual";

  return (
    <main>
      <SiteHeader active="visualizaciones" status="LXVI Legislatura" />

      <section className="hero" id="inicio">
        <div>
          <p className="eyebrow">
            <a href="/visualizaciones">Visualizaciones</a> · {config.name}
          </p>
          <h1>Así vota el pleno.</h1>
        </div>
        <p className="hero-copy">
          Explora las {data.manifest.voteCount} votaciones nominales de la LXVI Legislatura.
          Selecciona un escaño, recorre su historial y abre cada decisión para entender cómo
          votaron los grupos parlamentarios.
        </p>
        <div className="hero-stats" aria-label="Resumen del conjunto de datos">
          <div><strong>{data.manifest.seatCount}</strong><span>escaños</span></div>
          <div><strong>{data.manifest.voteCount}</strong><span>votaciones</span></div>
          <div><strong>{data.manifest.substitutedSeats}</strong><span>escaños con relevo</span></div>
        </div>
      </section>

      <section className="explorer" id="pleno">
        {/* Exactly two answers, never a date picker. "Quién está hoy" and "quién
            fue electo" are the only two questions a reader has; exposing the
            roster snapshot as a third axis made the page feel like a database. */}
        <div className="view-tabs" role="tablist" aria-label="Identidad mostrada en cada escaño">
          <button
            type="button"
            role="tab"
            aria-selected={isCurrentView}
            className={isCurrentView ? "active" : ""}
            onClick={() => selectView("actual")}
          >
            Quién ocupa el escaño hoy
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={!isCurrentView}
            className={!isCurrentView ? "active" : ""}
            onClick={() => selectView("electoral")}
          >
            Quién lo ganó en 2024
          </button>
        </div>

        <p className="view-note">
          {isCurrentView ? (
            <>
              <strong>{data.manifest.substitutedSeats}</strong> de {data.manifest.seatCount} escaños
              los ocupa alguien distinto de quien ganó la elección, y{" "}
              <strong>{data.manifest.partyChangedSeats}</strong> están hoy en un grupo parlamentario
              distinto del partido que los postuló. Licencias y vacantes aparecen como estados
              propios y no se suman a ningún grupo.
            </>
          ) : (
            <>
              La integración electoral de 2024 según el INE. Responde quién <em>ganó</em> cada
              escaño, no quién lo está votando: {data.manifest.substitutedSeats} de estos nombres
              ya no están en el pleno.
            </>
          )}
        </p>

        <div className="section-heading">
          <div>
            <p className="eyebrow">Explorador interactivo</p>
            <h2>El pleno, escaño por escaño</h2>
          </div>
          <p>Pasa el cursor para una lectura rápida. Haz clic para fijar {config.pin}.</p>
        </div>

        <div className="explorer-grid">
          <div className="chamber-card">
            <div className="toolbar">
              <label className="search-field">
                <span aria-hidden="true">⌕</span>
                <input
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder={config.memberSearch}
                  aria-label={config.memberSearch}
                />
              </label>
              <div className="party-filter" aria-label="Filtrar por partido">
                {["Todos", ...parties].map((party) => (
                  <button
                    key={party}
                    className={partyFilter === party ? "active" : ""}
                    onClick={() => setPartyFilter(party)}
                    type="button"
                  >
                    {party}
                  </button>
                ))}
              </div>
            </div>

            {selectedVote && (
              <div className="hemicycle-mode">
                <span className="mode-label">Colorear escaños por</span>
                <div className="mode-switch" role="group" aria-label="Criterio de color del hemiciclo">
                  <button
                    type="button"
                    className={showingVoteColors ? "" : "active"}
                    aria-pressed={!showingVoteColors}
                    onClick={() => setColorMode("party")}
                  >
                    Partido
                  </button>
                  <button
                    type="button"
                    className={showingVoteColors ? "active" : ""}
                    aria-pressed={showingVoteColors}
                    onClick={() => setColorMode("vote")}
                  >
                    Sentido del voto
                  </button>
                </div>
                <span className="mode-context">{shortDate(selectedVote.date)}</span>
              </div>
            )}

            <div
              id="hemiciclo"
              className={`hemicycle ${isSenate ? "senate-hemicycle" : ""}`}
              role="group"
              aria-label={
                showingVoteColors
                  ? `Hemiciclo de ${data.manifest.seatCount} escaños, coloreado por sentido del voto`
                  : `Hemiciclo de ${data.manifest.seatCount} escaños`
              }
            >
              <div className="dais">
                <span>Mesa Directiva</span>
              </div>
              {coords.map((seat) => {
                const occupant = occupants.get(seat.id)!;
                const choice = showingVoteColors ? choiceBySeat.get(seat.id) : undefined;
                const unrecorded = showingVoteColors && !choice;
                return (
                  <button
                    key={seat.id}
                    type="button"
                    aria-label={
                      showingVoteColors
                        ? `${occupant.name}, ${occupant.party}, ${choice ? voteLabel(choice) : "sin registro en esta votación"}`
                        : `${occupant.name}, ${occupant.party}, ${seatTypeLabel(seat.seatType)}`
                    }
                    className={`seat-dot seat-${seat.seatType.toLowerCase()} ${
                      selectedSeatId === seat.id ? "selected" : ""
                    } ${
                      isSeatVisible(seat) ? "" : "muted"
                    } ${unrecorded ? "no-record" : ""} ${
                      occupant.status === "vacante" ? "seat-vacant" : ""
                    }`}
                    style={{
                      left: `${seat.x}%`,
                      top: `${seat.y}%`,
                      backgroundColor: unrecorded
                        ? "transparent"
                        : showingVoteColors
                          ? CHOICE_COLORS[choice as string] ?? "#8b8b86"
                          : PARTY_COLORS[occupant.party] ?? "#74736e",
                    }}
                    onMouseEnter={() => setHoveredSeatId(seat.id)}
                    onMouseLeave={() => setHoveredSeatId(null)}
                    onFocus={() => setHoveredSeatId(seat.id)}
                    onBlur={() => setHoveredSeatId(null)}
                    onClick={() => selectSeat(seat)}
                  />
                );
              })}
            </div>

            {previewSeat && previewOccupant && (
              <div className="hover-reader" aria-live="polite">
                <div className="party-badge" style={{ background: PARTY_COLORS[previewOccupant.party] }}>
                  {previewOccupant.party}
                </div>
                <div className="hover-identity">
                  <strong>{previewOccupant.name}</strong>
                  <span>
                    {previewSeat.seatType} · {previewSeat.state ?? `Circunscripción ${previewSeat.circunscripcion}`}
                  </span>
                  {previewOccupant.substituted ? (
                    <span className="election-result-preview">
                      Ocupa el escaño de {previewSeat.electedName}
                    </span>
                  ) : (
                    previewSeat.seatType !== "RP" && previewSeat.winningPct !== null && (
                      <span className="election-result-preview">
                        {previewSeat.seatType === "FM" ? "Primera minoría en 2024 con " : "Ganó en 2024 con "}
                        {previewSeat.winningPct.toLocaleString("es-MX", {
                          minimumFractionDigits: 1,
                          maximumFractionDigits: 2,
                        })}% · {previewSeat.winningVotes?.toLocaleString("es-MX")} votos
                      </span>
                    )
                  )}
                </div>
                <span className="hover-count">{previewActive.length} participaciones</span>
              </div>
            )}

            {showingVoteColors ? (
              <div className="legend legend-vote">
                {["Favor", "Contra", "Abstención", "Ausente", "Quórum *"]
                  .filter((choice) => (choiceTotals.get(choice) ?? 0) > 0)
                  .map((choice) => (
                    <span key={choice}>
                      <i style={{ background: CHOICE_COLORS[choice] }} />
                      {voteLabel(choice)} <strong>{choiceTotals.get(choice)}</strong>
                    </span>
                  ))}
                {unrecordedSeats > 0 && (
                  <span>
                    <i className="key-hollow" />
                    Sin registro <strong>{unrecordedSeats}</strong>
                  </span>
                )}
              </div>
            ) : (
              <div className="legend">
                {parties.map((party) => (
                  <span key={party}>
                    <i style={{ background: PARTY_COLORS[party] ?? "#74736e" }} />
                    {party} {partyCounts.get(party) ?? 0}
                  </span>
                ))}
              </div>
            )}
            <div className="seat-type-key" aria-label="Tipo de elección del escaño">
              <span><i className="key-square" /> Mayoría relativa · {data.seats.filter((seat) => seat.seatType === "MR").length}</span>
              {isSenate && <span><i className="key-diamond" /> Primera minoría · 32</span>}
              <span><i className="key-circle" /> Representación proporcional · {data.seats.filter((seat) => seat.seatType === "RP").length}</span>
            </div>

            <div className="vote-calendar">
              <div className="calendar-heading">
                <span>Calendario de votaciones</span>
                <span>{selectedOccupant.name} · {selectedHistory.length} registros</span>
              </div>
              {calendarYears.length === 0 && (
                <p className="calendar-empty">
                  {selectedOccupant.personId
                    ? "Este escaño todavía no tiene votaciones registradas."
                    : "Esta persona aún no tiene votaciones nominales enlazadas en la base local."}
                </p>
              )}
              {calendarYears.map(({ year, entries }) => (
                <div className="calendar-year" key={year}>
                  <span className="calendar-year-label">{year}</span>
                  <div className="calendar-track" role="group" aria-label={`Votaciones de ${year}`}>
                    {entries.map(({ voteId, choice, date, title }) => (
                      <button
                        type="button"
                        key={voteId}
                        className={`calendar-cell ${selectedVoteId === voteId ? "selected" : ""}`}
                        style={{ background: CHOICE_COLORS[choice] ?? "#8b8b86" }}
                        title={`${shortDate(date)} · ${voteLabel(choice)} · ${shortTitle(title)}`}
                        aria-label={`${shortDate(date)}, ${voteLabel(choice)}, ${shortTitle(title)}`}
                        aria-pressed={selectedVoteId === voteId}
                        onClick={() => openVote(voteId)}
                      />
                    ))}
                  </div>
                </div>
              ))}
              <div className="calendar-key">
                <span><i style={{ background: CHOICE_COLORS.Favor }} /> Favor</span>
                <span><i style={{ background: CHOICE_COLORS.Contra }} /> Contra</span>
                <span><i style={{ background: CHOICE_COLORS["Abstención"] }} /> Abst.</span>
                <span><i style={{ background: CHOICE_COLORS.Ausente }} /> Ausente</span>
                {!isSenate && <span><i style={{ background: CHOICE_COLORS["Quórum *"] }} /> Presente, sin voto</span>}
                <span className="calendar-hint">Cada rectángulo es una votación, en orden cronológico.</span>
              </div>
            </div>
          </div>

          <aside className="deputy-panel" id="historial">
            <div className="deputy-heading">
              <div>
                <span className="large-party" style={{ color: PARTY_COLORS[selectedOccupant.party] }}>
                  {selectedOccupant.party}
                </span>
                <h2>{selectedOccupant.name}</h2>
                <p>
                  {!isSenate && selectedSeat.seatType === "MR"
                    ? `${selectedSeat.state} · Distrito ${selectedSeat.district}${selectedSeat.districtSeat ? ` · ${selectedSeat.districtSeat}` : ""}`
                    : !isSenate
                      ? `Representación proporcional · Circunscripción ${selectedSeat.circunscripcion} · Lista ${selectedSeat.listNumber}`
                      : selectedSeat.seatType === "RP"
                        ? `Representación proporcional · Lista nacional · Posición ${selectedSeat.listNumber}`
                        : `${seatTypeLabel(selectedSeat.seatType)} · ${selectedSeat.state}`}
                  {isCurrentView ? ` · ${statusLabel(selectedOccupant.status)}` : ""}
                </p>
              </div>
              <span className="seat-number">{coords.findIndex((seat) => seat.id === selectedSeat.id) + 1}</span>
            </div>

            {isCurrentView && (selectedOccupant.substituted || selectedOccupant.partyChanged) && (
              <p className="seat-origin">
                {selectedOccupant.substituted && (
                  <>
                    Escaño ganado en 2024 por <strong>{selectedSeat.electedName}</strong> ({selectedSeat.electedParty}).{" "}
                  </>
                )}
                {selectedOccupant.partyChanged && (
                  <>
                    Electo por <strong>{selectedSeat.electedParty}</strong>; el directorio lo registra
                    hoy en <strong>{selectedSeat.currentParty}</strong>.
                  </>
                )}
              </p>
            )}

            {selectedOccupant.status === "licencia" && (
              <p className="seat-flag">
                El directorio marca a esta persona con licencia. El escaño no se atribuye a un grupo
                activo hasta identificar una suplencia en una fuente oficial.
              </p>
            )}
            {selectedOccupant.status === "vacante" && (
              <p className="seat-flag">Este escaño figura vacante en el directorio oficial consultado.</p>
            )}
            {isCurrentView && !selectedOccupant.personId && selectedOccupant.status !== "vacante" && (
              <p className="seat-flag">
                Esta persona aún no tiene votaciones nominales enlazadas en la base local. Las
                cifras de abajo quedan en cero hasta que aparezca en una votación descargada.
              </p>
            )}

            <div className="deputy-metrics">
              <div><strong>{selectedHistory.length}</strong><span>registros</span></div>
              <div><strong>{attendance.toLocaleString("es-MX", { style: "percent", maximumFractionDigits: 0 })}</strong><span>asistencia</span></div>
              <div><strong>{favorRate.toLocaleString("es-MX", { style: "percent", maximumFractionDigits: 0 })}</strong><span>voto a favor</span></div>
              <div className="election-metric">
                {selectedSeat.seatType !== "RP" && selectedSeat.winningPct !== null ? (
                  <>
                    <strong>{selectedSeat.winningPct.toLocaleString("es-MX", {
                      minimumFractionDigits: 1,
                      maximumFractionDigits: 2,
                    })}%</strong>
                    <span>{selectedSeat.winningVotes?.toLocaleString("es-MX")} votos · {selectedSeat.seatType === "FM" ? "primera minoría" : "elección 2024"}</span>
                    {selectedSeat.electionActor && (
                      <small>{electionActorLabel(selectedSeat.electionActor)}</small>
                    )}
                  </>
                ) : (
                  <>
                    <strong>Lista {selectedSeat.listNumber}</strong>
                    <span>asignación RP · 2024</span>
                  </>
                )}
              </div>
            </div>

            <div className="history-label">
              <span>Historial de votación</span>
              <span>Más reciente primero</span>
            </div>
            <div className="vote-history">
              {selectedHistory.map(([voteId, choice]) => {
                const vote = votesById.get(voteId);
                if (!vote) return null;
                return (
                  <button
                    type="button"
                    key={voteId}
                    className={selectedVoteId === voteId ? "selected" : ""}
                    onClick={() => openVote(voteId)}
                  >
                    <span className="choice-dot" style={{ background: CHOICE_COLORS[choice] ?? "#8b8b86" }} />
                    <span className="history-copy">
                      <small>{shortDate(vote.date)} · {topicLabel(vote.topic)}</small>
                      <strong>{cleanTitle(vote.title)}</strong>
                    </span>
                    <span className="choice-label">{voteLabel(choice)}</span>
                    <span className="arrow" aria-hidden="true">↗</span>
                  </button>
                );
              })}
            </div>
          </aside>
        </div>
      </section>

      {selectedVote && (
      <section className="vote-detail" id="votacion">
        <div className="vote-title-block">
          <div>
            <p className="eyebrow">Votación seleccionada · {shortDate(selectedVote.date)}</p>
            <h2>{cleanTitle(selectedVote.title)}</h2>
            {isSenate && selectedVote.stage && (
              <p className="vote-stage">{stageLabel(selectedVote.stage)}</p>
            )}
          </div>
          <a href={selectedVote.sourceUrl} target="_blank" rel="noreferrer">
            Ver fuente oficial <span aria-hidden="true">↗</span>
          </a>
        </div>

        <div className="vote-tags">
          <span>{isSenate ? label(selectedVote.status) : stageLabel(selectedVote.stage)}</span>
          <span>{topicLabel(selectedVote.topic)}</span>
          <span>{selectedVote.id}</span>
        </div>

        <div className="result-grid">
          <div className="result-summary">
            <p className="panel-kicker">Resultado del pleno</p>
            <div className="result-number">
              <strong>{selectedVote.favor}</strong>
              <span>votos a favor</span>
            </div>
            <div className="overall-bar" aria-label="Distribución total del voto">
              <span style={{ width: `${(selectedVote.favor / selectedVote.total) * 100}%`, background: CHOICE_COLORS.Favor }} />
              <span style={{ width: `${(selectedVote.contra / selectedVote.total) * 100}%`, background: CHOICE_COLORS.Contra }} />
              <span style={{ width: `${(selectedVote.abstention / selectedVote.total) * 100}%`, background: CHOICE_COLORS["Abstención"] }} />
              <span style={{ width: `${(selectedVote.absent / selectedVote.total) * 100}%`, background: CHOICE_COLORS.Ausente }} />
            </div>
            <dl>
              <div><dt>En contra</dt><dd>{selectedVote.contra}</dd></div>
              <div><dt>Abstenciones</dt><dd>{selectedVote.abstention}</dd></div>
              <div><dt>Ausencias</dt><dd>{selectedVote.absent}</dd></div>
              <div><dt>Padrón</dt><dd>{selectedVote.total}</dd></div>
            </dl>
          </div>

          <div className="party-results">
            <div className="party-results-heading">
              <div>
                <p className="panel-kicker">Voto por grupo parlamentario</p>
                <h3>Composición de la decisión</h3>
              </div>
              <div className="choice-key">
                <span><i style={{ background: CHOICE_COLORS.Favor }} /> Favor</span>
                <span><i style={{ background: CHOICE_COLORS.Contra }} /> Contra</span>
                <span><i style={{ background: CHOICE_COLORS["Abstención"] }} /> Abst.</span>
                <span><i style={{ background: CHOICE_COLORS.Ausente }} /> Ausente</span>
              </div>
            </div>
            <div className="party-table">
              {partyRows.map((row) => {
                const total = row.favor + row.contra + row.abstention + row.absent;
                return (
                  <div className="party-row" key={row.party}>
                    <strong style={{ color: PARTY_COLORS[row.party] ?? "#333" }}>{row.party}</strong>
                    <div className="party-stacked-bar" aria-label={`${row.party}: ${row.favor} a favor, ${row.contra} en contra`}>
                      <span style={{ width: `${(row.favor / total) * 100}%`, background: CHOICE_COLORS.Favor }} />
                      <span style={{ width: `${(row.contra / total) * 100}%`, background: CHOICE_COLORS.Contra }} />
                      <span style={{ width: `${(row.abstention / total) * 100}%`, background: CHOICE_COLORS["Abstención"] }} />
                      <span style={{ width: `${(row.absent / total) * 100}%`, background: CHOICE_COLORS.Ausente }} />
                    </div>
                    <span className="party-total">{row.favor} / {row.contra}</span>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      </section>
      )}

      <section className="method-note" id="metodologia">
        <p className="eyebrow">Corte y metodología</p>
        {/* The section is a two-column grid: eyebrow, then one body cell.
            Extra paragraphs go inside the wrapper, never as grid siblings. */}
        <div className="method-body">
          <p>
            Votaciones nominales publicadas por {config.sourceName}. Corte al{" "}
            <strong>{shortDate(data.manifest.sourceThrough)}</strong>. La composición actual proviene
            del <a href={data.manifest.roster.sourceUrl} target="_blank" rel="noreferrer">directorio
            oficial</a> observado el <strong>{shortDate(rosterCutoff)}</strong>; el resultado
            electoral de 2024 proviene del INE. Los temas y etapas son clasificaciones analíticas;
            los registros individuales conservan su vínculo con la fuente oficial. Cada tabla del
            almacén que sostiene estas cifras está documentada en el{" "}
            <a href="/datos">diccionario de datos</a>.
          </p>
          <p>
            Un escaño puede haber tenido más de un ocupante. El historial que se muestra es el de la
            persona que el escaño resuelve en la vista activa, no el del escaño completo:{" "}
            <strong>{data.manifest.seatCount - data.manifest.currentLinkedSeats}</strong> ocupantes
            actuales todavía no aparecen en ninguna votación descargada.
          </p>
        </div>
      </section>

      <SiteFooter note={`Datos · ${config.short}`} />
    </main>
  );
}
