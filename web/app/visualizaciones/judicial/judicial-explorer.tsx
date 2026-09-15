"use client";

import { useEffect, useMemo, useState } from "react";

import { SiteFooter, SiteHeader } from "../../site-chrome";

type Position = [number, number];
type PolygonCoordinates = Position[][];
type MultiPolygonCoordinates = Position[][][];

type StateFeature = {
  type: "Feature";
  properties: { name: string; stateId: number };
  geometry:
    | { type: "Polygon"; coordinates: PolygonCoordinates }
    | { type: "MultiPolygon"; coordinates: MultiPolygonCoordinates };
};
type StateGeoJson = { type: "FeatureCollection"; features: StateFeature[] };

type StateTurnout = { stateId: number; name: string; turnoutPct: number | null; turnoutPct2024: number | null };
type TurnoutData = {
  electionId: string;
  label: string;
  nationalTurnoutPct: number;
  nationalTurnoutPct2024: number | null;
  states: StateTurnout[];
};

type Candidate = { name: string; genero: string; poderPostulante: string; votes: number; pct: number };
type AtLargeRace = { electionId: string; label: string; seats: number; termYears: number; winners: Candidate[] };
type RegionalRace = {
  electionId: string;
  label: string;
  seats: number;
  termYears: number;
  regions: { circunscripcion: number; winners: Candidate[] }[];
};
type Race = AtLargeRace | RegionalRace;
type ResultsData = { races: Race[] };

const WIDTH = 760;
const HEIGHT = 500;

// Light paper -> deep teal, interpolated in RGB. A continuous measure like
// turnout doesn't fit the categorical dot-legend used by the winner maps
// elsewhere on the site, so this gets its own gradient scale instead.
const SCALE_LOW: [number, number, number] = [233, 228, 216]; // --paper-deep
const SCALE_HIGH: [number, number, number] = [18, 43, 45]; // --navy

function formatNumber(value: number) {
  return new Intl.NumberFormat("es-MX").format(value);
}

function allPositions(feature: StateFeature): Position[] {
  return feature.geometry.type === "Polygon"
    ? feature.geometry.coordinates.flat()
    : feature.geometry.coordinates.flat(2);
}

function pathForFeature(
  feature: StateFeature,
  bounds: { minLon: number; maxLon: number; minLat: number; maxLat: number },
) {
  const project = ([lon, lat]: Position) => {
    const x = 12 + ((lon - bounds.minLon) / (bounds.maxLon - bounds.minLon)) * (WIDTH - 24);
    const y = 12 + ((bounds.maxLat - lat) / (bounds.maxLat - bounds.minLat)) * (HEIGHT - 24);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  };
  const polygonPath = (polygon: PolygonCoordinates) =>
    polygon.map((ring) => (ring.length ? `M${ring.map(project).join("L")}Z` : "")).join("");
  return feature.geometry.type === "Polygon"
    ? polygonPath(feature.geometry.coordinates)
    : feature.geometry.coordinates.map(polygonPath).join("");
}

function turnoutColor(pct: number | null, min: number, max: number) {
  if (pct === null || max === min) return "#d7d4cb";
  const t = Math.max(0, Math.min(1, (pct - min) / (max - min)));
  const [r1, g1, b1] = SCALE_LOW;
  const [r2, g2, b2] = SCALE_HIGH;
  const r = Math.round(r1 + (r2 - r1) * t);
  const g = Math.round(g1 + (g2 - g1) * t);
  const b = Math.round(b1 + (b2 - b1) * t);
  return `rgb(${r}, ${g}, ${b})`;
}

function TurnoutMap({
  geojson,
  turnout,
  selectedState,
  hoveredState,
  onSelect,
  onHover,
}: {
  geojson: StateGeoJson;
  turnout: TurnoutData;
  selectedState: number | null;
  hoveredState: number | null;
  onSelect: (stateId: number) => void;
  onHover: (stateId: number | null) => void;
}) {
  const bounds = useMemo(() => {
    const positions = geojson.features.flatMap(allPositions);
    return {
      minLon: Math.min(...positions.map(([lon]) => lon)),
      maxLon: Math.max(...positions.map(([lon]) => lon)),
      minLat: Math.min(...positions.map(([, lat]) => lat)),
      maxLat: Math.max(...positions.map(([, lat]) => lat)),
    };
  }, [geojson]);

  const byState = new Map(turnout.states.map((state) => [state.stateId, state]));
  const values = turnout.states
    .map((state) => state.turnoutPct)
    .filter((value): value is number => value !== null);
  const min = Math.min(...values);
  const max = Math.max(...values);

  return (
    <div className="electoral-map-wrap">
      <svg
        className="electoral-map"
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        role="group"
        aria-label="Participación por estado en la elección judicial 2025"
      >
        {geojson.features.map((feature) => {
          const stateId = feature.properties.stateId;
          const state = byState.get(stateId);
          const active = stateId === selectedState;
          const hovered = stateId === hoveredState;
          return (
            <path
              key={stateId}
              d={pathForFeature(feature, bounds)}
              fill={turnoutColor(state?.turnoutPct ?? null, min, max)}
              className={`electoral-state${active ? " selected" : ""}${hovered ? " hovered" : ""}`}
              role="button"
              tabIndex={0}
              aria-label={`${feature.properties.name}, participación ${state?.turnoutPct ?? "—"}%`}
              onClick={() => onSelect(stateId)}
              onFocus={() => onHover(stateId)}
              onBlur={() => onHover(null)}
              onMouseEnter={() => onHover(stateId)}
              onMouseLeave={() => onHover(null)}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  onSelect(stateId);
                }
              }}
            />
          );
        })}
      </svg>
      <div className="electoral-map-legend">
        <span className="turnout-scale">
          <i
            className="turnout-scale-swatch"
            style={{ background: `linear-gradient(90deg, ${turnoutColor(min, min, max)}, ${turnoutColor(max, min, max)})` }}
          />
          {min.toFixed(1)}% – {max.toFixed(1)}% participación
        </span>
      </div>
    </div>
  );
}

function CandidateRow({ candidate, rank }: { candidate: Candidate; rank: number }) {
  return (
    <li className="judicial-candidate-row">
      <span className="judicial-candidate-rank">{rank}</span>
      <div className="judicial-candidate-info">
        <span className="judicial-candidate-name">{candidate.name}</span>
        <span className="judicial-candidate-meta">
          {candidate.genero === "M" ? "Mujer" : "Hombre"} · Postuló: {candidate.poderPostulante}
        </span>
        <div className="vote-bar">
          <span style={{ width: `${candidate.pct}%`, background: "var(--navy)" }} />
        </div>
      </div>
      <div className="judicial-candidate-votes">
        <strong>{formatNumber(candidate.votes)}</strong>
        <small>{candidate.pct.toFixed(1)}%</small>
      </div>
    </li>
  );
}

function isRegionalRace(race: Race): race is RegionalRace {
  return "regions" in race;
}

function RaceCard({ race }: { race: Race }) {
  return (
    <article className="judicial-race-card">
      <header>
        <h3>{race.label}</h3>
        <p>
          {race.seats} {race.seats === 1 ? "escaño" : "escaños"} · {race.termYears} años
        </p>
      </header>
      {isRegionalRace(race) ? (
        race.regions.map((region) => (
          <div key={region.circunscripcion} className="judicial-region">
            <h4>{region.circunscripcion}ª circunscripción</h4>
            <ol>
              {region.winners.map((candidate, index) => (
                <CandidateRow key={candidate.name} candidate={candidate} rank={index + 1} />
              ))}
            </ol>
          </div>
        ))
      ) : (
        <ol>
          {race.winners.map((candidate, index) => (
            <CandidateRow key={candidate.name} candidate={candidate} rank={index + 1} />
          ))}
        </ol>
      )}
    </article>
  );
}

export default function JudicialExplorer() {
  const [turnout, setTurnout] = useState<TurnoutData | null>(null);
  const [results, setResults] = useState<ResultsData | null>(null);
  const [geojson, setGeojson] = useState<StateGeoJson | null>(null);
  const [error, setError] = useState(false);
  const [selectedState, setSelectedState] = useState<number | null>(null);
  const [hoveredState, setHoveredState] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      fetch("/data/judicial-turnout.json").then((response) => (response.ok ? response.json() : Promise.reject())),
      fetch("/data/judicial-results.json").then((response) => (response.ok ? response.json() : Promise.reject())),
      fetch("/data/electoral-states.geojson").then((response) => (response.ok ? response.json() : Promise.reject())),
    ])
      .then(([turnoutData, resultsData, geo]) => {
        if (cancelled) return;
        setTurnout(turnoutData);
        setResults(resultsData);
        setGeojson(geo);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const displayStateId = hoveredState ?? selectedState;
  const stateDetail = turnout?.states.find((state) => state.stateId === displayStateId) ?? null;

  if (error) {
    return (
      <main>
        <SiteHeader active="visualizaciones" status="Visualizaciones" />
        <section className="method-note">
          <p className="eyebrow">Error</p>
          <p>No se pudieron cargar los datos de la elección judicial.</p>
        </section>
        <SiteFooter note="Visualizaciones" />
      </main>
    );
  }

  return (
    <main>
      <SiteHeader active="visualizaciones" status="Visualizaciones" />

      <section className="articles-hero">
        <div>
          <p className="eyebrow">Elecciones</p>
          <h1>Elección judicial 2025.</h1>
        </div>
        <p className="hero-copy">
          La primera elección popular del Poder Judicial de la Federación en México, el 1 de junio de 2025.
          Participación nacional: {turnout ? `${turnout.nationalTurnoutPct}%` : "—"}
          {turnout?.nationalTurnoutPct2024 != null
            ? ` frente a ${turnout.nationalTurnoutPct2024}% en la elección presidencial de 2024.`
            : "."}
        </p>
      </section>

      <section className="judicial-map-section">
        <div className="electoral-panel electoral-map-panel">
          <header>
            <div>
              <p className="eyebrow">Participación por estado</p>
              <h2>¿Dónde votó más gente?</h2>
            </div>
            {stateDetail && (
              <p>
                <strong>{stateDetail.name}</strong>
                <br />
                Judicial 2025: {stateDetail.turnoutPct ?? "—"}%
                <br />
                Presidencial 2024: {stateDetail.turnoutPct2024 ?? "—"}%
              </p>
            )}
          </header>
          {geojson && turnout ? (
            <TurnoutMap
              geojson={geojson}
              turnout={turnout}
              selectedState={selectedState}
              hoveredState={hoveredState}
              onSelect={(stateId) => setSelectedState(stateId === selectedState ? null : stateId)}
              onHover={setHoveredState}
            />
          ) : (
            <p style={{ padding: 24 }}>Cargando mapa…</p>
          )}
        </div>
      </section>

      <section className="dashboard-list">
        <h2 className="dashboard-area">Ganadoras y ganadores</h2>
        <p className="judicial-scope-note">
          Solo se muestran las cuatro carreras de representación nacional o regional. Magistraturas de
          circuito y juzgados de distrito (850 cargos en total) se eligen por distrito judicial y no se
          incluyen todavía: el número de escaños por distrito no está en el almacén de datos.
        </p>
        <div className="judicial-races-grid">
          {results?.races.map((race) => (
            <RaceCard key={race.electionId} race={race} />
          ))}
        </div>
      </section>

      <SiteFooter note="Visualizaciones" />
    </main>
  );
}
