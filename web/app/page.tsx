"use client";

import { useEffect, useMemo, useRef, useState } from "react";

type Seat = {
  id: string;
  deputyId?: string;
  senatorId?: number;
  name: string;
  party: string;
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
  nameRole: "titular" | "suplente";
};

type Chamber = "diputados" | "senado";

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
    sourceThrough: string;
    seatCount: number;
    voteCount: number;
    linkedSeats: number;
  };
  seats: Seat[];
  votes: Vote[];
  histories: Record<string, [string, string][]>;
  partyVotes: Record<string, Record<string, Record<string, number>>>;
};

const PARTY_COLORS: Record<string, string> = {
  PT: "#c7323f",
  MORENA: "#8e2533",
  MRN: "#8e2533",
  PVEM: "#3b8b62",
  MC: "#e97935",
  PRI: "#d55d75",
  PAN: "#2d69a4",
  PRD: "#e5ad31",
  IND: "#7c7f82",
  CAND_INDEPENDIENTE: "#7c7f82",
  SG: "#7c7f82",
};

const CHOICE_COLORS: Record<string, string> = {
  Favor: "#267a53",
  Contra: "#bb3d48",
  "Abstención": "#d4a72c",
  Abstencion: "#d4a72c",
  Ausente: "#9b9a94",
  "Quórum *": "#537a8f",
};

const PARTY_ORDER = ["PT", "MORENA", "MRN", "PVEM", "MC", "PRI", "PAN", "PRD", "IND", "CAND_INDEPENDIENTE", "SG"];

function partyRank(party: string) {
  const rank = PARTY_ORDER.indexOf(party);
  return rank === -1 ? PARTY_ORDER.length : rank;
}

function cleanTitle(title: string) {
  return title.replace(/\s*<p>.*$/i, "").trim();
}

/** Vote titles run long; native tooltips need a readable summary, not the full dictamen. */
function shortTitle(title: string, limit = 90) {
  const clean = cleanTitle(title);
  return clean.length > limit ? `${clean.slice(0, limit).trimEnd()}…` : clean;
}

function label(value: string | null) {
  if (!value) return "Sin clasificar";
  return value
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function shortDate(date: string) {
  return new Intl.DateTimeFormat("es-MX", {
    day: "numeric",
    month: "short",
    year: "numeric",
  }).format(new Date(`${date}T12:00:00`));
}

function voteLabel(choice: string) {
  if (choice === "Favor") return "A favor";
  if (choice === "Contra") return "En contra";
  if (choice === "Quórum *") return "Presente, sin voto";
  return choice;
}

function electionActorLabel(actor: string | null) {
  return actor?.replaceAll("_", " · ") ?? "";
}

function seatTypeLabel(seatType: Seat["seatType"]) {
  if (seatType === "MR") return "mayoría relativa";
  if (seatType === "FM") return "primera minoría";
  return "representación proporcional";
}

function seatCoordinates(seats: Seat[]) {
  const ordered = [...seats].sort(
    (a, b) => partyRank(a.party) - partyRank(b.party) || a.name.localeCompare(b.name, "es"),
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

export default function Home() {
  const [datasets, setDatasets] = useState<Record<Chamber, SiteData> | null>(null);
  const [chamber, setChamber] = useState<Chamber>("diputados");
  const [error, setError] = useState(false);
  const [selectedSeatId, setSelectedSeatId] = useState<string | null>(null);
  const [selectedVoteId, setSelectedVoteId] = useState<string | null>(null);
  const [hoveredSeatId, setHoveredSeatId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [partyFilter, setPartyFilter] = useState("Todos");
  const [colorMode, setColorMode] = useState<"party" | "vote">("party");
  const pendingScroll = useRef(false);
  const data = datasets?.[chamber] ?? null;
  const isSenate = chamber === "senado";

  useEffect(() => {
    Promise.all([
      fetch("/data/legislature-66.json"),
      fetch("/data/senate-66.json"),
    ])
      .then(async ([deputiesResponse, senateResponse]) => {
        if (!deputiesResponse.ok || !senateResponse.ok) throw new Error("Data request failed");
        return {
          diputados: await deputiesResponse.json() as SiteData,
          senado: await senateResponse.json() as SiteData,
        };
      })
      .then((payloads) => {
        const payload = payloads.diputados;
        const preferred =
          payload.seats.find((seat) => seat.name.includes("Sanchez Cordero")) ?? payload.seats[0];
        setDatasets(payloads);
        setSelectedSeatId(preferred.id);
        // No vote is preselected: the vote detail only appears once the reader
        // opens one, from the history list or the calendar.
      })
      .catch(() => setError(true));
  }, []);

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
  const selectedSeat = data?.seats.find((seat) => seat.id === selectedSeatId) ?? null;
  const previewSeat = data?.seats.find((seat) => seat.id === hoveredSeatId) ?? selectedSeat;
  const selectedHistory = useMemo(
    () => (selectedSeat && data ? data.histories[selectedSeat.id] ?? [] : []),
    [selectedSeat, data],
  );
  const previewHistory = previewSeat && data ? data.histories[previewSeat.id] ?? [] : [];
  const selectedVote = selectedVoteId ? votesById.get(selectedVoteId) ?? null : null;

  const parties = useMemo(() => {
    if (!data) return [];
    return [...new Set(data.seats.map((seat) => seat.party))].sort(
      (a, b) => partyRank(a) - partyRank(b),
    );
  }, [data]);

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
      const entry = data.histories[seat.id]?.find(([voteId]) => voteId === selectedVoteId);
      if (entry) map.set(seat.id, entry[1]);
    }
    return map;
  }, [data, selectedVoteId]);

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
    const partyMatches = partyFilter === "Todos" || seat.party === partyFilter;
    const textMatches = !queryNormalized || seat.name.toLocaleLowerCase("es").includes(queryNormalized);
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
    // to wait for the render that creates it (see the effect below).
    pendingScroll.current = true;
    setSelectedVoteId(voteId);
    setColorMode("vote");
  }

  function selectChamber(nextChamber: Chamber) {
    if (nextChamber === chamber) return;
    const nextData = datasets?.[nextChamber];
    if (!nextData) return;
    const nextSeat = nextData.seats[0];
    setChamber(nextChamber);
    setSelectedSeatId(nextSeat.id);
    setSelectedVoteId(null);
    setHoveredSeatId(null);
    setPartyFilter("Todos");
    setQuery("");
    setColorMode("party");
  }

  if (error) {
    return (
      <main className="state-screen">
        <p className="eyebrow">Brújula Legislativa</p>
        <h1>No pudimos cargar el corte de votaciones.</h1>
        <p>Actualiza la página para intentarlo de nuevo.</p>
      </main>
    );
  }

  if (!data || !selectedSeat) {
    return (
      <main className="state-screen loading-state" aria-live="polite">
        <span className="loading-mark" />
        <p className="eyebrow">Brújula Legislativa</p>
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

  return (
    <main>
      <header className="site-header">
        <a className="brand" href="#inicio" aria-label="Brújula Legislativa, inicio">
          <span className="brand-mark">BL</span>
          <span>
            Brújula
            <br />
            Legislativa
          </span>
        </a>
        <nav aria-label="Navegación principal">
          <a href="#pleno">El pleno</a>
          <a href="#historial">Integrantes</a>
          {selectedVote && <a href="#votacion">Votaciones</a>}
          <a href="#metodologia">Metodología</a>
        </nav>
        <div className="header-status">
          <span /> LXVI Legislatura
        </div>
      </header>

      <section className="hero" id="inicio">
        <div>
          <p className="eyebrow">{isSenate ? "Senado de la República" : "Cámara de Diputados"} · México</p>
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
          <div><strong>{data.manifest.linkedSeats}</strong><span>historias enlazadas</span></div>
        </div>
      </section>

      <section className="explorer" id="pleno">
        <div className="chamber-tabs" role="tablist" aria-label="Seleccionar cámara legislativa">
          <button
            type="button"
            role="tab"
            aria-selected={!isSenate}
            className={!isSenate ? "active" : ""}
            onClick={() => selectChamber("diputados")}
          >
            Cámara de Diputados <span>500</span>
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={isSenate}
            className={isSenate ? "active" : ""}
            onClick={() => selectChamber("senado")}
          >
            Senado de la República <span>128</span>
          </button>
        </div>
        <div className="section-heading">
          <div>
            <p className="eyebrow">Explorador interactivo</p>
            <h2>El pleno, escaño por escaño</h2>
          </div>
          <p>Pasa el cursor para una lectura rápida. Haz clic para fijar {isSenate ? "una senaduría" : "una diputación"}.</p>
        </div>

        <div className="explorer-grid">
          <div className="chamber-card">
            <div className="toolbar">
              <label className="search-field">
                <span aria-hidden="true">⌕</span>
                <input
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder={isSenate ? "Buscar senadora o senador" : "Buscar diputada o diputado"}
                  aria-label={isSenate ? "Buscar senadora o senador" : "Buscar diputada o diputado"}
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
                const choice = showingVoteColors ? choiceBySeat.get(seat.id) : undefined;
                const unrecorded = showingVoteColors && !choice;
                return (
                  <button
                    key={seat.id}
                    type="button"
                    aria-label={
                      showingVoteColors
                        ? `${seat.name}, ${seat.party}, ${choice ? voteLabel(choice) : "sin registro en esta votación"}`
                        : `${seat.name}, ${seat.party}, ${seatTypeLabel(seat.seatType)}`
                    }
                    className={`seat-dot seat-${seat.seatType.toLowerCase()} ${
                      selectedSeatId === seat.id ? "selected" : ""
                    } ${
                      isSeatVisible(seat) ? "" : "muted"
                    } ${unrecorded ? "no-record" : ""}`}
                    style={{
                      left: `${seat.x}%`,
                      top: `${seat.y}%`,
                      backgroundColor: unrecorded
                        ? "transparent"
                        : showingVoteColors
                          ? CHOICE_COLORS[choice as string] ?? "#8b8b86"
                          : PARTY_COLORS[seat.party] ?? "#74736e",
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

            {previewSeat && (
              <div className="hover-reader" aria-live="polite">
                <div className="party-badge" style={{ background: PARTY_COLORS[previewSeat.party] }}>
                  {previewSeat.party}
                </div>
                <div className="hover-identity">
                  <strong>{previewSeat.name}</strong>
                  <span>
                    {previewSeat.seatType} · {previewSeat.state ?? `Circunscripción ${previewSeat.circunscripcion}`}
                  </span>
                  {previewSeat.seatType !== "RP" && previewSeat.winningPct !== null && (
                    <span className="election-result-preview">
                      {previewSeat.seatType === "FM" ? "Primera minoría en 2024 con " : "Ganó en 2024 con "}
                      {previewSeat.winningPct.toLocaleString("es-MX", {
                        minimumFractionDigits: 1,
                        maximumFractionDigits: 2,
                      })}% · {previewSeat.winningVotes?.toLocaleString("es-MX")} votos
                    </span>
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
                    {party} {data.seats.filter((seat) => seat.party === party).length}
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
                <span>{selectedSeat.name} · {selectedHistory.length} registros</span>
              </div>
              {calendarYears.length === 0 && (
                <p className="calendar-empty">Este escaño todavía no tiene votaciones registradas.</p>
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
                <span className="large-party" style={{ color: PARTY_COLORS[selectedSeat.party] }}>
                  {selectedSeat.party}
                </span>
                <h2>{selectedSeat.name}</h2>
                <p>
                  {!isSenate && selectedSeat.seatType === "MR"
                    ? `${selectedSeat.state} · Distrito ${selectedSeat.district}${selectedSeat.districtSeat ? ` · ${selectedSeat.districtSeat}` : ""}`
                    : !isSenate
                      ? `Representación proporcional · Circunscripción ${selectedSeat.circunscripcion} · Lista ${selectedSeat.listNumber}`
                      : selectedSeat.seatType === "RP"
                        ? `Representación proporcional · Lista nacional · Posición ${selectedSeat.listNumber}`
                        : `${seatTypeLabel(selectedSeat.seatType)} · ${selectedSeat.state}`}
                  {selectedSeat.nameRole === "suplente" ? " · Suplencia en funciones" : ""}
                </p>
              </div>
              <span className="seat-number">{coords.findIndex((seat) => seat.id === selectedSeat.id) + 1}</span>
            </div>

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
                      <small>{shortDate(vote.date)} · {label(vote.topic)}</small>
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
              <p className="vote-stage">{selectedVote.stage}</p>
            )}
          </div>
          <a href={selectedVote.sourceUrl} target="_blank" rel="noreferrer">
            Ver fuente oficial <span aria-hidden="true">↗</span>
          </a>
        </div>

        <div className="vote-tags">
          <span>{isSenate ? label(selectedVote.status) : label(selectedVote.stage)}</span>
          <span>{label(selectedVote.topic)}</span>
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
        <p>
          Votaciones nominales publicadas por {isSenate
            ? "el Senado de la República"
            : "la Gaceta Parlamentaria de la Cámara de Diputados"}.
          Corte al <strong>{shortDate(data.manifest.sourceThrough)}</strong>. Los temas y etapas son
          clasificaciones analíticas; los registros individuales conservan su vínculo con la fuente oficial.
        </p>
      </section>

      <footer>
        <div className="brand footer-brand"><span className="brand-mark">BL</span><span>Brújula Legislativa</span></div>
        <p>Una lectura pública del Congreso mexicano.</p>
        <span>Datos · {isSenate ? "Senado" : "Cámara de Diputados"}</span>
      </footer>
    </main>
  );
}
