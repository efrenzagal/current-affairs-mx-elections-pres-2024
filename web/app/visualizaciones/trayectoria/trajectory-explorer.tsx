"use client";

import { useEffect, useMemo, useState } from "react";

import { SITE_NAME, SiteFooter, SiteHeader } from "../../site-chrome";


type BlocKey = "A" | "B" | "C";
type ContestKey = "PRE" | "SEN" | "DIP";
type Position = [number, number];
type PolygonCoordinates = Position[][];
type MultiPolygonCoordinates = Position[][][];

type ElectionResult = {
  totalVotes: number;
  nominalList: number;
  nullVotes: number;
  turnout: number | null;
  candidacies: { key: string; label: string; color?: string; votes: number }[];
  parties: { key: string; label?: string; color?: string; votes: number }[];
};

type Geography = {
  name: string;
  trajectory: {
    year: number;
    left: number;
    right: number;
    other: number;
    category: string;
    votes: number;
  }[];
  elections: Record<string, ElectionResult>;
};

type ContestData = {
  years: number[];
  cycles: Record<string, Record<BlocKey, { label: string; color: string }>>;
  maps: Record<string, { stateId: number; winner: BlocKey; winnerLabel: string; winnerPct: number }[]>;
  geographies: Record<string, Geography>;
};

type TrajectoryData = {
  schemaVersion: number;
  states: { id: number; name: string }[];
  contests: Record<ContestKey, ContestData>;
};

type StateFeature = {
  type: "Feature";
  properties: { name: string; stateId: number };
  geometry:
    | { type: "Polygon"; coordinates: PolygonCoordinates }
    | { type: "MultiPolygon"; coordinates: MultiPolygonCoordinates };
};

type StateGeoJson = { type: "FeatureCollection"; features: StateFeature[] };

const WIDTH = 760;
const HEIGHT = 500;
const PARTY_COLORS: Record<string, string> = {
  MORENA: "#8e2533", PAN: "#1769aa", PRI: "#b64048", PRD: "#d2aa18",
  PT: "#bd2734", PVEM: "#4d8d54", MC: "#e88626", PANAL: "#49a8af",
  "A. CAM.": "#1769aa", "A. MEX.": "#d2aa18", PBT: "#d2aa18", APM: "#b64048",
};
const TRAJECTORY_COLORS: Record<string, string> = {
  "Base Izquierda": "#8B0000",
  "Base Derecha": "#1E90FF",
  "Base Otros": "#006847",
  "Plural Izquierda": "#B85C5C",
  "Plural Derecha": "#5CA3D9",
  "Plural Otros": "#4CA37A",
  "Contenciosa Izquierda-Otros": "#CC7A00",
  "Contenciosa Izquierda-Derecha": "#7B2D8B",
  "Contenciosa Otros-Derecha": "#1F8A8A",
  "Empate": "#8b8f8c",
};
const CONTEST_META: Record<ContestKey, {
  tab: string;
  status: string;
  electionLabel: string;
  mapTitle: string;
  primaryResultTitle: string;
  trajectoryLabel: string;
}> = {
  PRE: {
    tab: "Presidencia",
    status: "Elecciones presidenciales",
    electionLabel: "Elección presidencial",
    mapTitle: "Ganador por estado",
    primaryResultTitle: "Por candidatura / coalición",
    trajectoryLabel: "presidencial",
  },
  SEN: {
    tab: "Senado",
    status: "Elecciones al Senado",
    electionLabel: "Elección de senadurías",
    mapTitle: "Bloque con más votos por estado",
    primaryResultTitle: "Voto registrado en boleta",
    trajectoryLabel: "del Senado",
  },
  DIP: {
    tab: "Diputaciones",
    status: "Elecciones de diputaciones",
    electionLabel: "Elección de diputaciones federales",
    mapTitle: "Bloque con más votos por estado",
    primaryResultTitle: "Voto registrado en boleta",
    trajectoryLabel: "de diputaciones",
  },
};

function formatNumber(value: number) {
  return new Intl.NumberFormat("es-MX", { notation: value >= 1_000_000 ? "compact" : "standard", maximumFractionDigits: 1 }).format(value);
}

function partyColor(key: string) {
  if (PARTY_COLORS[key]) return PARTY_COLORS[key];
  let hash = 0;
  for (const char of key) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return `hsl(${hash % 360} 42% 43%)`;
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
  const polygonPath = (polygon: PolygonCoordinates) => polygon
    .map((ring) => ring.length ? `M${ring.map(project).join("L")}Z` : "")
    .join("");
  return feature.geometry.type === "Polygon"
    ? polygonPath(feature.geometry.coordinates)
    : feature.geometry.coordinates.map(polygonPath).join("");
}

function MexicoMap({
  geojson, data, year, selectedState, hoveredState, contestLabel, onSelect, onHover,
}: {
  geojson: StateGeoJson;
  data: ContestData;
  year: number;
  selectedState: number | null;
  hoveredState: number | null;
  contestLabel: string;
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
  const winners = new Map(data.maps[String(year)].map((result) => [result.stateId, result]));
  const cycles = data.cycles[String(year)];
  // The ternary's third corner is an analytical residual, not a party. For
  // the current electoral map, show the party that actually led each state.
  const usePartyWinners = year === 2024;
  const partyWinners = new Map(geojson.features.flatMap((feature) => {
    const parties = data.geographies[String(feature.properties.stateId)]?.elections[String(year)]?.parties ?? [];
    const total = parties.reduce((sum, party) => sum + party.votes, 0);
    const winner = parties.reduce<(typeof parties)[number] | null>(
      (leading, party) => !leading || party.votes > leading.votes ? party : leading,
      null,
    );
    return winner ? [[feature.properties.stateId, {
      key: winner.key,
      label: winner.label ?? winner.key.replaceAll("_", " + "),
      color: partyColor(winner.key),
      pct: total ? winner.votes / total * 100 : 0,
    }] as const] : [];
  }));
  const legend = usePartyWinners
    ? [...new Map([...partyWinners.values()].map((winner) => [winner.key, winner])).values()]
    : (Object.entries(cycles) as [BlocKey, { label: string; color: string }][]).map(([key, cycle]) => ({ key, ...cycle }));

  return (
    <div className="electoral-map-wrap">
      <svg className="electoral-map" viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="group" aria-label={`Mapa de ${contestLabel} en México, ${year}`}>
        {geojson.features.map((feature) => {
          const stateId = feature.properties.stateId;
          const result = winners.get(stateId);
          const partyWinner = partyWinners.get(stateId);
          const active = stateId === selectedState;
          const hovered = stateId === hoveredState;
          return (
            <path
              key={stateId}
              d={pathForFeature(feature, bounds)}
              fill={usePartyWinners ? partyWinner?.color ?? "#d7d4cb" : result ? cycles[result.winner].color : "#d7d4cb"}
              className={`electoral-state${active ? " selected" : ""}${hovered ? " hovered" : ""}`}
              role="button"
              tabIndex={0}
              aria-label={`${feature.properties.name}${usePartyWinners && partyWinner ? `, ganó ${partyWinner.label} con ${partyWinner.pct.toFixed(1)}%` : result ? `, lideró ${result.winnerLabel} con ${result.winnerPct}%` : ""}`}
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
        {legend.map((item) => (
          <span key={item.key}><i style={{ background: item.color }} />{item.label}</span>
        ))}
      </div>
    </div>
  );
}

function TrajectoryTriangle({ geography, year, contestLabel, onYear }: { geography: Geography; year: number; contestLabel: string; onYear: (year: number) => void }) {
  // 404-unit base × √3/2 = 349.9-unit height: keep the ternary genuinely
  // equilateral so equal changes along L/R/O occupy equal visual distances.
  const triangleHeight = 404 * Math.sqrt(3) / 2;
  const baseY = 390;
  const apexY = baseY - triangleHeight;
  const ternaryPoint = (left: number, right: number, other: number) => ({
    x: 48 + (right / 100) * 404 + (other / 100) * 202,
    y: baseY - (other / 100) * triangleHeight,
  });
  const points = geography.trajectory.map((point) => ({
    ...point,
    ...ternaryPoint(point.left, point.right, point.other),
  }));
  const selected = points.find((point) => point.year === year);
  const centroid = ternaryPoint(100 / 3, 100 / 3, 100 / 3);
  const tieRadius = 404 * .08;
  const gridLines = [100 / 3, 200 / 3].flatMap((fraction) => [
    [ternaryPoint(100 - fraction, 0, fraction), ternaryPoint(0, 100 - fraction, fraction)],
    [ternaryPoint(100 - fraction, fraction, 0), ternaryPoint(0, fraction, 100 - fraction)],
    [ternaryPoint(fraction, 100 - fraction, 0), ternaryPoint(fraction, 0, 100 - fraction)],
  ]);
  const categories = [...new Set(points.map((point) => point.category))];

  return (
    <div className="trajectory-triangle-wrap">
      <svg className="trajectory-triangle" viewBox="0 0 500 430" role="img" aria-label={`Trayectoria ${contestLabel} de ${geography.name}`}>
        <defs>
          <marker id="trajectory-arrow" markerWidth="7" markerHeight="7" refX="5.5" refY="3.5" orient="auto" markerUnits="strokeWidth">
            <path d="M0 0 L7 3.5 L0 7 Z" />
          </marker>
        </defs>
        <path d={`M48 ${baseY} L452 ${baseY} L250 ${apexY} Z`} className="triangle-outline" />
        {gridLines.map(([start, end], index) => (
          <line key={`grid-${index}`} x1={start.x} y1={start.y} x2={end.x} y2={end.y} className="triangle-grid-line" />
        ))}
        <path
          d={`M149 ${baseY - triangleHeight / 2} L351 ${baseY - triangleHeight / 2} L250 ${baseY} Z`}
          className="triangle-middle"
        />
        <circle cx={centroid.x} cy={centroid.y} r={tieRadius} className="triangle-tie-zone" />
        <g className="triangle-centroid" aria-hidden="true">
          <line x1={centroid.x - 6} y1={centroid.y} x2={centroid.x + 6} y2={centroid.y} />
          <line x1={centroid.x} y1={centroid.y - 6} x2={centroid.x} y2={centroid.y + 6} />
        </g>
        <text className="vertex-left" x="35" y="418">Izquierda</text>
        <text className="vertex-right" x="465" y="418" textAnchor="end">Derecha</text>
        <text className="vertex-other" x="250" y="24" textAnchor="middle">Otros</text>
        {points.slice(1).map((point, index) => (
          <line
            key={`${point.year}-line`}
            x1={points[index].x}
            y1={points[index].y}
            x2={point.x}
            y2={point.y}
            className="trajectory-link"
            markerEnd="url(#trajectory-arrow)"
          />
        ))}
        {points.map((point) => (
          <g
            key={point.year}
            className={`trajectory-point${point.year === year ? " selected" : ""}`}
            role="button"
            tabIndex={0}
            aria-label={`${point.year}: izquierda ${point.left}%, derecha ${point.right}%, otros ${point.other}%`}
            onClick={() => onYear(point.year)}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") onYear(point.year);
            }}
          >
            <circle
              cx={point.x}
              cy={point.y}
              r={point.year === year ? 9 : 6}
              style={{ fill: TRAJECTORY_COLORS[point.category] ?? "#64706c" }}
            />
            <text x={point.x} y={point.y - 13} textAnchor="middle">{point.year}</text>
          </g>
        ))}
      </svg>
      {selected && (
        <div className="trajectory-readout">
          <strong>{year}</strong>
          <span>Izquierda {selected.left}%</span>
          <span>Derecha {selected.right}%</span>
          <span>Otros {selected.other}%</span>
          <small>{selected.category}</small>
        </div>
      )}
      <div className="trajectory-category-key" aria-label="Clasificaciones presentes en la trayectoria">
        {categories.map((category) => (
          <span key={category}><i style={{ background: TRAJECTORY_COLORS[category] ?? "#64706c" }} />{category}</span>
        ))}
      </div>
    </div>
  );
}

function ResultBars({
  title, rows,
}: {
  title: string;
  rows: { key: string; label: string; color?: string; votes: number }[];
}) {
  const total = rows.reduce((sum, row) => sum + row.votes, 0);
  return (
    <section className="electoral-bars">
      <p className="eyebrow">{title}</p>
      <div className="electoral-bar-list">
        {rows.map((row) => {
          const pct = total ? row.votes / total * 100 : 0;
          return (
            <div className="electoral-bar-row" key={row.key}>
              <div><strong>{row.label}</strong><span>{formatNumber(row.votes)} · {pct.toFixed(1)}%</span></div>
              <div className="electoral-bar-track"><i style={{ width: `${pct}%`, background: row.color ?? partyColor(row.key) }} /></div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

export default function TrajectoryExplorer() {
  const [data, setData] = useState<TrajectoryData | null>(null);
  const [geojson, setGeojson] = useState<StateGeoJson | null>(null);
  const [error, setError] = useState(false);
  const [contestKey, setContestKey] = useState<ContestKey>("PRE");
  const [year, setYear] = useState(2024);
  const [selectedState, setSelectedState] = useState<number | null>(null);
  const [hoveredState, setHoveredState] = useState<number | null>(null);

  useEffect(() => {
    Promise.all([
      fetch("/data/electoral-trajectory.json").then((response) => response.ok ? response.json() : Promise.reject()),
      fetch("/data/electoral-states.geojson").then((response) => response.ok ? response.json() : Promise.reject()),
    ])
      .then(([trajectory, states]) => {
        setData(trajectory as TrajectoryData);
        setGeojson(states as StateGeoJson);
      })
      .catch(() => setError(true));
  }, []);

  if (error) return <main className="state-screen"><h1>No pudimos cargar la trayectoria electoral.</h1></main>;
  if (!data || !geojson) return <main className="state-screen loading-state"><span className="loading-mark" /><p className="eyebrow">{SITE_NAME}</p><h1>Preparando el mapa…</h1></main>;

  const contest = data.contests[contestKey];
  const meta = CONTEST_META[contestKey];
  const geography = contest.geographies[selectedState === null ? "national" : String(selectedState)];
  const election = geography.elections[String(year)];
  const mapFocusId = hoveredState ?? selectedState;
  const mapFocus = mapFocusId === null ? null : contest.maps[String(year)].find((item) => item.stateId === mapFocusId);
  const mapFocusName = mapFocusId === null ? null : data.states.find((state) => state.id === mapFocusId)?.name;
  const partyRows = election.parties.map((party) => ({
    ...party,
    label: party.label ?? party.key.replaceAll("_", " + "),
    color: partyColor(party.key),
  }));

  return (
    <main>
      <SiteHeader active="visualizaciones" status={meta.status} />
      <section className="electoral-hero">
        <div>
          <p className="eyebrow"><a href="/visualizaciones">Visualizaciones</a> · Elecciones</p>
          <h1>Geografía electoral.</h1>
        </div>
        <p className="hero-copy">
          Compara la trayectoria del voto presidencial, del Senado y de las diputaciones federales.
          Selecciona una entidad y un ciclo para actualizar el mapa y los resultados.
        </p>
      </section>

      <section className="electoral-explorer">
        <div className="electoral-contest-control">
          <span>Tipo de elección</span>
          <div role="group" aria-label="Tipo de elección">
            {(Object.keys(CONTEST_META) as ContestKey[]).map((key) => (
              <button
                key={key}
                type="button"
                className={contestKey === key ? "active" : ""}
                onClick={() => {
                  setContestKey(key);
                  setYear(data.contests[key].years.at(-1) ?? 2024);
                  setHoveredState(null);
                }}
              >
                {CONTEST_META[key].tab}
              </button>
            ))}
          </div>
        </div>
        <div className="electoral-cycle-control">
          <span>Ciclo electoral</span>
          <div role="group" aria-label="Ciclo electoral">
            {contest.years.map((cycle) => (
              <button key={cycle} type="button" className={year === cycle ? "active" : ""} onClick={() => setYear(cycle)}>{cycle}</button>
            ))}
          </div>
          <button type="button" className="national-reset" disabled={selectedState === null} onClick={() => setSelectedState(null)}>Ver nacional</button>
        </div>

        <div className="electoral-master-detail">
          <section className="electoral-panel electoral-map-panel">
            <header>
              <div><p className="eyebrow">{meta.mapTitle} · {year}</p><h2>Selecciona una entidad.</h2></div>
              <p>{mapFocusName && mapFocus ? <><strong>{mapFocusName}</strong><br />{mapFocus.winnerLabel}<br />{mapFocus.winnerPct}%</> : "Pasa el cursor o usa el teclado para explorar el mapa."}</p>
            </header>
            <MexicoMap geojson={geojson} data={contest} year={year} selectedState={selectedState} hoveredState={hoveredState} contestLabel={meta.trajectoryLabel} onSelect={setSelectedState} onHover={setHoveredState} />
          </section>

          <section className="electoral-panel trajectory-panel">
            <header>
              <div><p className="eyebrow">Trayectoria · {contest.years[0]}–{contest.years.at(-1)}</p><h2>{geography.name}</h2></div>
              <a className="trajectory-methodology-link" href="/articulos/espectro-politico.html">
                Metodología →
              </a>
            </header>
            <p className="trajectory-definition">“Otros” reúne el voto no asignado a las tradiciones PRD/Morena o PAN; no representa una ideología única.</p>
            <TrajectoryTriangle geography={geography} year={year} contestLabel={meta.trajectoryLabel} onYear={setYear} />
          </section>
        </div>

        <section className="electoral-cycle-detail">
          <header className="electoral-detail-heading">
            <div><p className="eyebrow">{geography.name} · {meta.electionLabel}</p><h2>{year}</h2></div>
            <p>El ciclo seleccionado actualiza simultáneamente el mapa, el punto destacado de la trayectoria y los resultados.</p>
          </header>
          <dl className="electoral-metrics">
            <div><dt>Votos emitidos</dt><dd>{formatNumber(election.totalVotes)}</dd><small>{election.nominalList ? `de ${formatNumber(election.nominalList)} en lista nominal` : "Lista nominal no disponible"}</small></div>
            <div><dt>Participación</dt><dd>{election.turnout === null ? "—" : `${election.turnout}%`}</dd><small>Sobre lista nominal reportada</small></div>
            <div><dt>Votos nulos</dt><dd>{formatNumber(election.nullVotes)}</dd><small>{election.totalVotes ? `${(election.nullVotes / election.totalVotes * 100).toFixed(1)}% de los emitidos` : "—"}</small></div>
          </dl>
          <div className="electoral-results-grid">
            <ResultBars title={meta.primaryResultTitle} rows={election.candidacies} />
            <ResultBars title="Por partido" rows={partyRows} />
          </div>
        </section>

        <details className="electoral-method">
          <summary>Cómo se construye la trayectoria</summary>
          <p>
            El triángulo compara tres componentes estables entre ciclos: la tradición PRD/Morena,
            la tradición PAN y un componente residual de otros partidos. Las coaliciones mixtas se
            distribuyen entre sus integrantes cuando la fuente permite observar el voto por partido.
            Los resultados inferiores conservan la configuración particular de cada elección.
          </p>
        </details>
      </section>
      <SiteFooter note={`Trayectoria ${meta.trajectoryLabel} · ${contest.years[0]}–${contest.years.at(-1)}`} />
    </main>
  );
}
