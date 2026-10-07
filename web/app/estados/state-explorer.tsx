"use client";

import { type CSSProperties, type PointerEvent, useEffect, useMemo, useRef, useState } from "react";

import { SITE_NAME, SiteFooter, SiteHeader } from "../site-chrome";


type MetricKey = "medianAge" | "lifeExpectancy" | "fertilityRate" | "infantMortality" | "agingIndex";
type SeriesKey =
  | "population" | "births" | "deaths" | "naturalIncrease" | "birthRate" | "deathRate"
  | "lifeExpectancy" | "lifeExpectancyMen" | "lifeExpectancyWomen" | "fertilityRate"
  | "adolescentFertility" | "infantMortality" | "medianAge" | "childDependency"
  | "olderDependency" | "dependency" | "agingIndex" | "votingAge";
type Values = (number | null)[];

type StateIndex = {
  schemaVersion: number;
  years: number[];
  source: { population: string };
  states: { code: string; name: string; stateId: number }[];
  metrics: Record<MetricKey, { label: string; unit: string; decimals: number }>;
  map: Record<MetricKey, Record<string, Values>>;
  national: Record<MetricKey, Values>;
};

type Geography = {
  schemaVersion: number;
  code: string;
  name: string;
  years: number[];
  series: Record<SeriesKey, Values>;
  pyramid: { bands: string[]; men: number[][]; women: number[][] };
};

type GroupId = "PRIMARY" | "SECONDARY" | "TERTIARY";

/** INEGI PIBE, millions of 2018 pesos. Sectors start at `sectorYears[0]`. */
type Economy = {
  schemaVersion: number;
  unit: string;
  source: string;
  years: number[];
  sectorYears: number[];
  groups: { id: GroupId; label: string }[];
  sectors: { id: string; label: string; name: string; group: GroupId }[];
  geographies: Record<string, {
    gdp: number[];
    valueAdded: number[];
    groups: Record<GroupId, number[]>;
    sectors: Record<string, number[]>;
  }>;
};

/** Encuesta Intercensal 2025 scorecard. State rows: [value, li, ls, cv, rank, best, worst]. */
type Eic = {
  schemaVersion: number;
  source: string;
  lowPrecisionCv: number;
  states: { code: string; name: string }[];
  categories: {
    id: string;
    label: string;
    indicators: { id: string; label: string; detail: string | null; unit: string; decimals: number; source: "inegi" | "propio" }[];
  }[];
  geographies: Record<string, Record<string, number[]>>;
};
type EicIndicator = Eic["categories"][number]["indicators"][number];

type Position = [number, number];
type StateFeature = {
  type: "Feature";
  properties: { name: string; stateId: number };
  geometry:
    | { type: "Polygon"; coordinates: Position[][] }
    | { type: "MultiPolygon"; coordinates: Position[][][] };
};
type StateGeoJson = { type: "FeatureCollection"; features: StateFeature[] };

type ChartSeries = { key: string; label: string; color: string; values: Values; reference?: boolean };

const NATIONAL = "00";
const PLAY_STEP_MS = 320;

// Validated with the dataviz palette checker against the --white surface:
// men/women and the two "life-cycle" hues pass every categorical check; the
// national reference gray is deliberately light (contrast relief: every chart
// labels it directly). Colour follows the entity across every chart: green is
// always the young end (births, children), violet the old end.
const COLORS = {
  men: "#2f6fae",
  women: "#d2603a",
  state: "#0a7d54",
  national: "#bdb8ac",
  young: "#178a62",
  old: "#6a4fb3",
};
// Single-hue sequential ramp for the choropleth, light → dark.
const MAP_RAMP = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"];
// Primary/secondary/tertiary activity, checked with the dataviz palette
// validator against --white. Olive, blue and ochre rather than the green,
// orange and blue of the R explorer, which this page already spends on
// births and on men/women.
const GROUP_COLORS: Record<GroupId, string> = { PRIMARY: "#6d8a1e", SECONDARY: "#0f72a8", TERTIARY: "#a86d12" };
const RANK_BAR = "#cfcabd";

const integer = new Intl.NumberFormat("es-MX", { maximumFractionDigits: 0 });

function decimal(value: number, digits: number) {
  return new Intl.NumberFormat("es-MX", { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(value);
}

function people(value: number) {
  if (value >= 1_000_000) return `${decimal(value / 1_000_000, 1)} millones`;
  return integer.format(value);
}

/** Millions of pesos as words: "25.4 billones", "474.7 mil millones". */
function pesos(millions: number) {
  if (millions >= 1_000_000) return `${decimal(millions / 1_000_000, 1)} billones`;
  if (millions >= 1000) return `${decimal(millions / 1000, millions >= 100_000 ? 0 : 1)} mil millones`;
  return `${decimal(millions, millions >= 100 ? 0 : 1)} millones`;
}

/** Charts print raw tick numbers, so scale large series to thousands of millions first. */
function pesoScale(values: number[]) {
  return Math.max(...values) >= 10_000
    ? { divisor: 1000, label: "Miles de millones de pesos de 2018" }
    : { divisor: 1, label: "Millones de pesos de 2018" };
}

function indexed(values: number[]): Values {
  const base = values[0];
  return values.map((value) => base > 0 ? value / base * 100 : null);
}

function signed(value: number, digits = 1) {
  return `${value >= 0 ? "+" : ""}${decimal(value, digits)}%`;
}

function niceScale(min: number, max: number, count = 4) {
  const span = max - min || Math.abs(max) || 1;
  const rough = span / count;
  const magnitude = 10 ** Math.floor(Math.log10(rough));
  const normalized = rough / magnitude;
  const step = (normalized < 1.5 ? 1 : normalized < 3 ? 2 : normalized < 7 ? 5 : 10) * magnitude;
  const low = Math.floor(min / step) * step;
  const high = Math.ceil(max / step) * step;
  const ticks: number[] = [];
  for (let tick = low; tick <= high + step / 2; tick += step) ticks.push(Math.round(tick * 1e6) / 1e6);
  return { low, high, ticks };
}

/** Decade labels plus the final year, unless it would collide with the last decade. */
function axisYears(years: number[], plotWidth: number) {
  const decades = years.filter((year) => year % 10 === 0);
  const last = years.at(-1)!;
  const gap = ((last - (decades.at(-1) ?? years[0])) / Math.max(1, years.length - 1)) * plotWidth;
  return decades.at(-1) === last || gap < 34 ? decades : [...decades, last];
}

function present(values: Values): number[] {
  return values.filter((value): value is number => value !== null);
}

/** Width of an element, tracked through resizes; 0 until the first measurement. */
function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.floor(entry.contentRect.width)));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return [ref, width] as const;
}

function seededParams() {
  if (typeof window === "undefined") return { code: NATIONAL, year: null as number | null };
  const params = new URLSearchParams(window.location.search);
  const code = params.get("estado");
  const year = Number(params.get("anio"));
  return {
    code: code && /^\d{2}$/.test(code) && Number(code) <= 32 ? code : NATIONAL,
    year: Number.isInteger(year) && year > 0 ? year : null,
  };
}

function Legend({ items }: { items: { label: string; color: string; outline?: boolean }[] }) {
  return (
    <div className="estado-legend">
      {items.map((item) => (
        <span key={item.label}>
          <i className={item.outline ? "outline" : ""} style={item.outline ? { borderColor: item.color } : { background: item.color }} />
          {item.label}
        </span>
      ))}
    </div>
  );
}

function LineChart({
  title, note, years, series, year, onYear, format, zero = false, height = 230,
}: {
  title: string;
  note: string;
  years: number[];
  series: ChartSeries[];
  year: number;
  onYear: (year: number) => void;
  format: (value: number) => string;
  zero?: boolean;
  height?: number;
}) {
  const [wrapRef, width] = useWidth<HTMLDivElement>();
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);
  const margin = { top: 14, right: 104, bottom: 28, left: 46 };
  const plotWidth = Math.max(0, width - margin.left - margin.right);
  const plotHeight = height - margin.top - margin.bottom;
  const all = series.flatMap((item) => present(item.values));
  const scale = niceScale(zero ? Math.min(0, ...all) : Math.min(...all), Math.max(...all));
  const x = (index: number) => margin.left + (years.length > 1 ? index / (years.length - 1) : 0) * plotWidth;
  const y = (value: number) => margin.top + plotHeight - ((value - scale.low) / (scale.high - scale.low || 1)) * plotHeight;
  const selectedIndex = years.indexOf(year);
  const activeIndex = hoverIndex ?? selectedIndex;
  const decades = axisYears(years, plotWidth);
  const tickDigits = scale.ticks.every(Number.isInteger) ? 0 : 1;

  // Direct labels at the line ends, nudged apart so two converging series
  // never print on top of each other.
  const ends = series
    .map((item) => {
      const last = item.values.at(-1);
      return last === null || last === undefined ? null : { key: item.key, label: item.label, y: y(last) };
    })
    .filter((item): item is NonNullable<typeof item> => item !== null)
    .sort((a, b) => a.y - b.y);
  for (let i = 1; i < ends.length; i += 1) ends[i].y = Math.max(ends[i].y, ends[i - 1].y + 14);

  const indexAt = (clientX: number, element: Element) => {
    const box = element.getBoundingClientRect();
    const ratio = (clientX - box.left - margin.left) / (plotWidth || 1);
    return Math.max(0, Math.min(years.length - 1, Math.round(ratio * (years.length - 1))));
  };

  return (
    <figure className="estado-chart">
      <figcaption>
        <h3>{title}</h3>
        <p>{note}</p>
      </figcaption>
      {series.length > 1 && <Legend items={series.map((item) => ({ label: item.label, color: item.color }))} />}
      <div className="estado-chart-plot" ref={wrapRef}>
        {width > 0 && (
          <svg
            width={width}
            height={height}
            role="img"
            aria-label={`${title}. ${series.map((item) => {
              const value = item.values[selectedIndex];
              return `${item.label}: ${value === null || value === undefined ? "sin dato" : format(value)}`;
            }).join("; ")} en ${year}.`}
            tabIndex={0}
            onKeyDown={(event) => {
              if (event.key === "ArrowLeft" && selectedIndex > 0) onYear(years[selectedIndex - 1]);
              if (event.key === "ArrowRight" && selectedIndex < years.length - 1) onYear(years[selectedIndex + 1]);
            }}
          >
            {scale.ticks.map((tick) => (
              <g key={tick}>
                <line className="estado-grid" x1={margin.left} x2={margin.left + plotWidth} y1={y(tick)} y2={y(tick)} />
                <text className="estado-tick" x={margin.left - 8} y={y(tick)} dy="0.32em" textAnchor="end">{decimal(tick, tickDigits)}</text>
              </g>
            ))}
            {decades.map((decade) => {
              const index = years.indexOf(decade);
              return <text key={decade} className="estado-tick" x={x(index)} y={height - 8} textAnchor="middle">{decade}</text>;
            })}
            {selectedIndex >= 0 && (
              <line className="estado-year-rule" x1={x(selectedIndex)} x2={x(selectedIndex)} y1={margin.top} y2={margin.top + plotHeight} />
            )}
            {hoverIndex !== null && hoverIndex !== selectedIndex && (
              <line className="estado-hover-rule" x1={x(hoverIndex)} x2={x(hoverIndex)} y1={margin.top} y2={margin.top + plotHeight} />
            )}
            {series.map((item) => {
              const path = item.values
                .map((value, index) => value === null ? null : `${x(index).toFixed(1)},${y(value).toFixed(1)}`)
                .reduce<string>((d, point, index, points) => {
                  if (point === null) return d;
                  return `${d}${index === 0 || points[index - 1] === null ? "M" : "L"}${point}`;
                }, "");
              return (
                <path
                  key={item.key}
                  d={path}
                  fill="none"
                  stroke={item.color}
                  strokeWidth={item.reference ? 1.75 : 2}
                  strokeLinejoin="round"
                  strokeLinecap="round"
                />
              );
            })}
            {activeIndex >= 0 && series.map((item) => {
              const value = item.values[activeIndex];
              return value === null || value === undefined ? null : (
                <circle key={item.key} className="estado-point" cx={x(activeIndex)} cy={y(value)} r={4} fill={item.color} />
              );
            })}
            {ends.map((end) => (
              <text key={end.key} className="estado-end-label" x={margin.left + plotWidth + 10} y={end.y} dy="0.32em">{end.label}</text>
            ))}
            <rect
              className="estado-hit"
              x={margin.left}
              y={margin.top}
              width={plotWidth}
              height={plotHeight}
              onMouseMove={(event) => setHoverIndex(indexAt(event.clientX, event.currentTarget.ownerSVGElement ?? event.currentTarget))}
              onMouseLeave={() => setHoverIndex(null)}
              onClick={(event) => onYear(years[indexAt(event.clientX, event.currentTarget.ownerSVGElement ?? event.currentTarget)])}
            />
          </svg>
        )}
        {hoverIndex !== null && width > 0 && (
          <div
            className="estado-tooltip"
            style={{
              left: x(hoverIndex) > width / 2 ? undefined : x(hoverIndex) + 12,
              right: x(hoverIndex) > width / 2 ? width - x(hoverIndex) + 12 : undefined,
            }}
          >
            <strong>{years[hoverIndex]}</strong>
            {series.map((item) => {
              const value = item.values[hoverIndex];
              return (
                <span key={item.key}>
                  <i style={{ background: item.color }} />
                  {item.label}
                  <b>{value === null || value === undefined ? "—" : format(value)}</b>
                </span>
              );
            })}
            <small>Clic para fijar el año</small>
          </div>
        )}
      </div>
    </figure>
  );
}

function Sparkline({ values, index }: { values: Values; index: number }) {
  const width = 120;
  const height = 34;
  const data = present(values);
  const min = Math.min(...data);
  const max = Math.max(...data);
  const x = (i: number) => 3 + (i / Math.max(1, values.length - 1)) * (width - 6);
  const y = (value: number) => height - 4 - ((value - min) / (max - min || 1)) * (height - 8);
  // Restart the line after a gap (e.g. a growth rate has no first year).
  const path = values
    .map((value, i) => value === null ? "" : `${i === 0 || values[i - 1] === null ? "M" : "L"}${x(i).toFixed(1)},${y(value).toFixed(1)}`)
    .join("");
  const current = values[index];
  return (
    <svg className="estado-sparkline" viewBox={`0 0 ${width} ${height}`} aria-hidden="true">
      <path d={path} fill="none" stroke={COLORS.state} strokeWidth={1.5} strokeLinejoin="round" />
      {current !== null && current !== undefined && <circle cx={x(index)} cy={y(current)} r={3} fill={COLORS.state} />}
    </svg>
  );
}

function StatTiles({ geography, yearIndex }: { geography: Geography; yearIndex: number }) {
  const { series, years } = geography;
  const tiles: { label: string; key: SeriesKey; format: (value: number) => string; detail?: string }[] = [
    { label: "Población", key: "population", format: people },
    { label: "En edad de votar", key: "votingAge", format: people, detail: "18 años y más" },
    { label: "Edad mediana", key: "medianAge", format: (value) => `${integer.format(value)} años` },
    { label: "Esperanza de vida", key: "lifeExpectancy", format: (value) => `${decimal(value, 1)} años` },
    { label: "Hijos por mujer", key: "fertilityRate", format: (value) => decimal(value, 2) },
  ];
  return (
    <dl className="estado-tiles">
      {tiles.map((tile) => {
        const value = series[tile.key][yearIndex];
        const first = series[tile.key][0];
        return (
          <div key={tile.key}>
            <dt>{tile.label}</dt>
            <dd>{value === null ? "—" : tile.format(value)}</dd>
            <small>
              {tile.detail ? `${tile.detail} · ` : ""}
              {first === null ? "" : `${years[0]}: ${tile.format(first)}`}
            </small>
            <Sparkline values={series[tile.key]} index={yearIndex} />
          </div>
        );
      })}
    </dl>
  );
}

function Pyramid({
  geography, yearIndex, compare,
}: {
  geography: Geography;
  yearIndex: number;
  compare: { label: string; men: number[]; women: number[] } | null;
}) {
  const [focusBand, setFocusBand] = useState<number | null>(null);
  const shares = (men: number[], women: number[]) => {
    const total = men.reduce((sum, value) => sum + value, 0) + women.reduce((sum, value) => sum + value, 0);
    return { men: men.map((value) => value / total * 100), women: women.map((value) => value / total * 100) };
  };
  // One fixed scale per geography across all fifty years, so playing the
  // years shows the shape change instead of the axis re-fitting each frame.
  const maxShare = useMemo(() => {
    let max = 0;
    geography.pyramid.men.forEach((men, index) => {
      const share = shares(men, geography.pyramid.women[index]);
      max = Math.max(max, ...share.men, ...share.women);
    });
    if (compare) {
      const share = shares(compare.men, compare.women);
      max = Math.max(max, ...share.men, ...share.women);
    }
    return Math.ceil(max);
  }, [geography, compare]);

  const men = geography.pyramid.men[yearIndex];
  const women = geography.pyramid.women[yearIndex];
  const current = shares(men, women);
  const reference = compare ? shares(compare.men, compare.women) : null;
  const bands = geography.pyramid.bands;
  const order = bands.map((_, index) => index).reverse();
  const menTotal = men.reduce((sum, value) => sum + value, 0);
  const womenTotal = women.reduce((sum, value) => sum + value, 0);
  const width = (share: number) => `${(share / maxShare) * 100}%`;
  const readout = focusBand === null ? null : {
    band: bands[focusBand],
    men: men[focusBand],
    women: women[focusBand],
    menShare: current.men[focusBand],
    womenShare: current.women[focusBand],
  };

  return (
    <div className="estado-pyramid">
      <Legend
        items={[
          { label: "Hombres", color: COLORS.men },
          { label: "Mujeres", color: COLORS.women },
          ...(compare ? [{ label: compare.label, color: "#152321", outline: true }] : []),
        ]}
      />
      <div className="estado-pyramid-head" aria-hidden="true">
        <span>Hombres · {people(menTotal)}</span>
        <span>Edad</span>
        <span>Mujeres · {people(womenTotal)}</span>
      </div>
      <div className="estado-pyramid-rows" role="list" aria-label={`Pirámide de población de ${geography.name}, ${geography.years[yearIndex]}`}>
        {order.map((index) => (
          <div
            key={bands[index]}
            role="listitem"
            tabIndex={0}
            className={`estado-pyramid-row${focusBand === index ? " active" : ""}`}
            aria-label={`${bands[index]} años: hombres ${decimal(current.men[index], 1)}%, mujeres ${decimal(current.women[index], 1)}%`}
            onMouseEnter={() => setFocusBand(index)}
            onMouseLeave={() => setFocusBand(null)}
            onFocus={() => setFocusBand(index)}
            onBlur={() => setFocusBand(null)}
          >
            <div className="estado-pyramid-side men">
              <i className="bar" style={{ width: width(current.men[index]), background: COLORS.men }} />
              {reference && <i className="ghost" style={{ width: width(reference.men[index]) }} />}
            </div>
            <span className="estado-pyramid-band">{bands[index]}</span>
            <div className="estado-pyramid-side women">
              <i className="bar" style={{ width: width(current.women[index]), background: COLORS.women }} />
              {reference && <i className="ghost" style={{ width: width(reference.women[index]) }} />}
            </div>
          </div>
        ))}
      </div>
      <div className="estado-pyramid-axis" aria-hidden="true">
        <div className="men"><span>{maxShare}%</span><span>{decimal(maxShare / 2, maxShare % 2 ? 1 : 0)}%</span><span>0</span></div>
        <span />
        <div className="women"><span>0</span><span>{decimal(maxShare / 2, maxShare % 2 ? 1 : 0)}%</span><span>{maxShare}%</span></div>
      </div>
      <p className="estado-pyramid-readout" aria-live="polite">
        {readout ? (
          <>
            <strong>{readout.band} años</strong>
            <span>Hombres {integer.format(readout.men)} ({decimal(readout.menShare, 1)}%)</span>
            <span>Mujeres {integer.format(readout.women)} ({decimal(readout.womenShare, 1)}%)</span>
            {reference && focusBand !== null && (
              <span className="muted">{compare?.label}: {decimal(reference.men[focusBand], 1)}% · {decimal(reference.women[focusBand], 1)}%</span>
            )}
          </>
        ) : (
          <span className="muted">
            {integer.format(Math.round(menTotal / womenTotal * 100))} hombres por cada 100 mujeres. Pasa el cursor sobre un grupo de edad para ver las cifras.
          </span>
        )}
      </p>
    </div>
  );
}

function allPositions(feature: StateFeature): Position[] {
  return feature.geometry.type === "Polygon" ? feature.geometry.coordinates.flat() : feature.geometry.coordinates.flat(2);
}

function StateMap({
  geojson, index, metric, yearIndex, selected, onSelect,
}: {
  geojson: StateGeoJson;
  index: StateIndex;
  metric: MetricKey;
  yearIndex: number;
  selected: string;
  onSelect: (code: string) => void;
}) {
  const [hovered, setHovered] = useState<string | null>(null);
  const mapWidth = 760;
  const mapHeight = 500;
  const project = useMemo(() => {
    const positions = geojson.features.flatMap(allPositions);
    const lons = positions.map(([lon]) => lon);
    const lats = positions.map(([, lat]) => lat);
    const bounds = { minLon: Math.min(...lons), maxLon: Math.max(...lons), minLat: Math.min(...lats), maxLat: Math.max(...lats) };
    return ([lon, lat]: Position) => {
      const px = 12 + ((lon - bounds.minLon) / (bounds.maxLon - bounds.minLon)) * (mapWidth - 24);
      const py = 12 + ((bounds.maxLat - lat) / (bounds.maxLat - bounds.minLat)) * (mapHeight - 24);
      return `${px.toFixed(1)},${py.toFixed(1)}`;
    };
  }, [geojson]);
  const paths = useMemo(() => new Map(geojson.features.map((feature) => {
    const polygon = (rings: Position[][]) => rings.map((ring) => `M${ring.map(project).join("L")}Z`).join("");
    const d = feature.geometry.type === "Polygon"
      ? polygon(feature.geometry.coordinates)
      : feature.geometry.coordinates.map(polygon).join("");
    return [String(feature.properties.stateId).padStart(2, "0"), d] as const;
  })), [geojson, project]);

  // The colour domain spans every state and every year, so the map darkens
  // or lightens as the years play instead of re-ranking each frame.
  const values = index.map[metric];
  const domain = useMemo(() => {
    const all = Object.values(values).flatMap(present);
    return { min: Math.min(...all), max: Math.max(...all) };
  }, [values]);
  const meta = index.metrics[metric];
  const classOf = (value: number) => Math.min(
    MAP_RAMP.length - 1,
    Math.floor(((value - domain.min) / (domain.max - domain.min || 1)) * MAP_RAMP.length),
  );
  const focus = hovered ?? (selected === NATIONAL ? null : selected);
  const focusValue = focus ? values[focus]?.[yearIndex] : index.national[metric][yearIndex];
  const focusName = focus ? index.states.find((state) => state.code === focus)?.name : "Nacional";
  const codes = [...paths.keys()].sort((a, b) => Number(a === selected) - Number(b === selected));

  return (
    <div className="estado-map-wrap">
      <p className="estado-map-readout">
        <strong>{focusName}</strong>
        <span>{meta.label}: {focusValue === null || focusValue === undefined ? "—" : decimal(focusValue, meta.decimals)} {meta.unit}</span>
      </p>
      <svg className="estado-map" viewBox={`0 0 ${mapWidth} ${mapHeight}`} role="group" aria-label={`Mapa de ${meta.label.toLowerCase()} por estado`}>
        {codes.map((code) => {
          const value = values[code]?.[yearIndex];
          const name = index.states.find((state) => state.code === code)?.name ?? code;
          return (
            <path
              key={code}
              d={paths.get(code)}
              fill={value === null || value === undefined ? "#d7d4cb" : MAP_RAMP[classOf(value)]}
              className={`estado-map-state${code === selected ? " selected" : ""}${code === hovered ? " hovered" : ""}`}
              role="button"
              tabIndex={0}
              aria-label={`${name}: ${value === null || value === undefined ? "sin dato" : `${decimal(value, meta.decimals)} ${meta.unit}`}`}
              onClick={() => onSelect(code)}
              onMouseEnter={() => setHovered(code)}
              onMouseLeave={() => setHovered(null)}
              onFocus={() => setHovered(code)}
              onBlur={() => setHovered(null)}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  onSelect(code);
                }
              }}
            />
          );
        })}
      </svg>
      <div className="estado-map-scale" aria-label={`Escala de ${meta.label.toLowerCase()}`}>
        <span>{decimal(domain.min, meta.decimals)}</span>
        <div>{MAP_RAMP.map((color) => <i key={color} style={{ background: color }} />)}</div>
        <span>{decimal(domain.max, meta.decimals)} {meta.unit}</span>
      </div>
    </div>
  );
}

function DataTable({ geography }: { geography: Geography }) {
  const columns: { label: string; key: SeriesKey; format: (value: number) => string }[] = [
    { label: "Población", key: "population", format: (value) => integer.format(value) },
    { label: "Nacimientos", key: "births", format: (value) => integer.format(value) },
    { label: "Defunciones", key: "deaths", format: (value) => integer.format(value) },
    { label: "Edad mediana", key: "medianAge", format: (value) => integer.format(value) },
    { label: "Esperanza de vida", key: "lifeExpectancy", format: (value) => decimal(value, 1) },
    { label: "Hijos por mujer", key: "fertilityRate", format: (value) => decimal(value, 2) },
    { label: "Mortalidad infantil", key: "infantMortality", format: (value) => decimal(value, 1) },
  ];
  return (
    <details className="estado-table">
      <summary>Ver los datos anuales en tabla</summary>
      <div>
        <table>
          <thead>
            <tr><th>Año</th>{columns.map((column) => <th key={column.key}>{column.label}</th>)}</tr>
          </thead>
          <tbody>
            {geography.years.map((year, index) => (
              <tr key={year}>
                <th>{year}</th>
                {columns.map((column) => {
                  const value = geography.series[column.key][index];
                  return <td key={column.key}>{value === null ? "—" : column.format(value)}</td>;
                })}
              </tr>
            )).reverse()}
          </tbody>
        </table>
      </div>
    </details>
  );
}

/** 100% stacked area: each layer's share of the whole, year by year. */
function StackedShare({
  title, note, years, layers, year, onYear, height = 230,
}: {
  title: string;
  note: string;
  years: number[];
  layers: { key: string; label: string; color: string; values: number[] }[];
  year: number;
  onYear: (year: number) => void;
  height?: number;
}) {
  const [wrapRef, width] = useWidth<HTMLDivElement>();
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);
  const margin = { top: 14, right: 104, bottom: 28, left: 46 };
  const plotWidth = Math.max(0, width - margin.left - margin.right);
  const plotHeight = height - margin.top - margin.bottom;
  const x = (index: number) => margin.left + (years.length > 1 ? index / (years.length - 1) : 0) * plotWidth;
  const y = (share: number) => margin.top + plotHeight - (share / 100) * plotHeight;
  const selectedIndex = years.indexOf(year);
  const decades = axisYears(years, plotWidth);

  // Stack the last layer on the baseline, so the legend (first layer on top)
  // reads in the same order as the bands.
  const stacked = useMemo(() => {
    const floor = years.map(() => 0);
    return [...layers].reverse().map((layer) => {
      const lower = [...floor];
      layer.values.forEach((value, index) => { floor[index] += value; });
      return { ...layer, lower, upper: [...floor] };
    }).reverse();
  }, [layers, years]);

  const ends = stacked
    .map((layer) => ({ key: layer.key, label: layer.label, y: y((layer.lower.at(-1)! + layer.upper.at(-1)!) / 2) }))
    .sort((a, b) => a.y - b.y);
  for (let i = 1; i < ends.length; i += 1) ends[i].y = Math.max(ends[i].y, ends[i - 1].y + 14);

  const indexAt = (clientX: number, element: Element) => {
    const box = element.getBoundingClientRect();
    const ratio = (clientX - box.left - margin.left) / (plotWidth || 1);
    return Math.max(0, Math.min(years.length - 1, Math.round(ratio * (years.length - 1))));
  };

  return (
    <figure className="estado-chart">
      <figcaption>
        <h3>{title}</h3>
        <p>{note}</p>
      </figcaption>
      <Legend items={layers.map((layer) => ({ label: layer.label, color: layer.color }))} />
      <div className="estado-chart-plot" ref={wrapRef}>
        {width > 0 && (
          <svg
            width={width}
            height={height}
            role="img"
            aria-label={`${title}. ${layers.map((layer) => `${layer.label}: ${decimal(layer.values[selectedIndex] ?? 0, 1)}%`).join("; ")} en ${year}.`}
            tabIndex={0}
            onKeyDown={(event) => {
              if (event.key === "ArrowLeft" && selectedIndex > 0) onYear(years[selectedIndex - 1]);
              if (event.key === "ArrowRight" && selectedIndex < years.length - 1) onYear(years[selectedIndex + 1]);
            }}
          >
            {stacked.map((layer) => {
              const top = layer.upper.map((value, index) => `${x(index).toFixed(1)},${y(value).toFixed(1)}`);
              const bottom = layer.lower.map((value, index) => `${x(index).toFixed(1)},${y(value).toFixed(1)}`).reverse();
              return <path key={layer.key} d={`M${top.join("L")}L${bottom.join("L")}Z`} fill={layer.color} stroke="var(--white)" strokeWidth={1.5} strokeLinejoin="round" />;
            })}
            {[0, 25, 50, 75, 100].map((tick) => (
              <text key={tick} className="estado-tick" x={margin.left - 8} y={y(tick)} dy="0.32em" textAnchor="end">{tick}%</text>
            ))}
            {decades.map((decade) => (
              <text key={decade} className="estado-tick" x={x(years.indexOf(decade))} y={height - 8} textAnchor="middle">{decade}</text>
            ))}
            {selectedIndex >= 0 && (
              <line className="estado-year-rule strong" x1={x(selectedIndex)} x2={x(selectedIndex)} y1={margin.top} y2={margin.top + plotHeight} />
            )}
            {hoverIndex !== null && hoverIndex !== selectedIndex && (
              <line className="estado-year-rule strong" x1={x(hoverIndex)} x2={x(hoverIndex)} y1={margin.top} y2={margin.top + plotHeight} strokeDasharray="3 3" />
            )}
            {ends.map((end) => (
              <text key={end.key} className="estado-end-label" x={margin.left + plotWidth + 10} y={end.y} dy="0.32em">{end.label}</text>
            ))}
            <rect
              className="estado-hit"
              x={margin.left}
              y={margin.top}
              width={plotWidth}
              height={plotHeight}
              onMouseMove={(event) => setHoverIndex(indexAt(event.clientX, event.currentTarget.ownerSVGElement ?? event.currentTarget))}
              onMouseLeave={() => setHoverIndex(null)}
              onClick={(event) => onYear(years[indexAt(event.clientX, event.currentTarget.ownerSVGElement ?? event.currentTarget)])}
            />
          </svg>
        )}
        {hoverIndex !== null && width > 0 && (
          <div
            className="estado-tooltip"
            style={{
              left: x(hoverIndex) > width / 2 ? undefined : x(hoverIndex) + 12,
              right: x(hoverIndex) > width / 2 ? width - x(hoverIndex) + 12 : undefined,
            }}
          >
            <strong>{years[hoverIndex]}</strong>
            {layers.map((layer) => (
              <span key={layer.key}>
                <i style={{ background: layer.color }} />
                {layer.label}
                <b>{decimal(layer.values[hoverIndex], 1)}%</b>
              </span>
            ))}
            <small>Clic para fijar el año</small>
          </div>
        )}
      </div>
    </figure>
  );
}

/** Rank of `code` among the 32 states for each value list, 1 = largest. */
function rankOf(code: string, values: Record<string, number>) {
  return Object.entries(values)
    .filter(([key]) => key !== NATIONAL)
    .filter(([, value]) => value > values[code]).length + 1;
}

function EconomyTiles({ economy, code, year }: { economy: Economy; code: string; year: number }) {
  const { years, geographies } = economy;
  const index = years.indexOf(year);
  const gdp = geographies[code].gdp;
  const national = geographies[NATIONAL].gdp;
  const growth: Values = gdp.map((value, i) => i === 0 ? null : (value / gdp[i - 1] - 1) * 100);
  const byYear = years.map((_, i) => Object.fromEntries(Object.entries(geographies).map(([key, geo]) => [key, geo.gdp[i]])));

  const tiles: { label: string; value: string; detail: string; spark: Values }[] = [
    { label: `PIB ${year}`, value: pesos(gdp[index]), detail: "de pesos de 2018", spark: gdp },
    {
      label: "Variación anual",
      value: growth[index] === null ? "—" : signed(growth[index]!),
      detail: index > 0 ? `real, respecto a ${years[index - 1]}` : "primer año de la serie",
      spark: growth,
    },
    {
      label: `Crecimiento desde ${years[0]}`,
      value: signed((gdp[index] / gdp[0] - 1) * 100, 0),
      detail: `real, ${years[0]}–${year}`,
      spark: indexed(gdp),
    },
  ];
  if (code !== NATIONAL) {
    const ranks = byYear.map((values) => rankOf(code, values));
    const shares = gdp.map((value, i) => value / national[i] * 100);
    tiles.push(
      // Negated so the sparkline rises when the state climbs the ranking.
      { label: "Lugar nacional", value: `${ranks[index]}.º`, detail: "de 32 entidades, por PIB", spark: ranks.map((rank) => -rank) },
      { label: "Parte del PIB nacional", value: `${decimal(shares[index], 1)}%`, detail: "del PIB del país", spark: shares },
    );
  }
  return (
    <dl className="estado-tiles flat" style={{ "--tiles": tiles.length } as CSSProperties}>
      {tiles.map((tile) => (
        <div key={tile.label}>
          <dt>{tile.label}</dt>
          <dd>{tile.value}</dd>
          <small>{tile.detail}</small>
          <Sparkline values={tile.spark} index={index} />
        </div>
      ))}
    </dl>
  );
}

function EconomyStructure({ economy, code, year, onYear }: { economy: Economy; code: string; year: number; onYear: (year: number) => void }) {
  const [mode, setMode] = useState<"share" | "value">("share");
  const geo = economy.geographies[code];
  const national = economy.geographies[NATIONAL];
  const scale = pesoScale(geo.valueAdded);
  const isNational = code === NATIONAL;
  return (
    <>
      <div className="estado-chart-cell">
        <div className="estado-chips inline" role="group" aria-label="Medida de la estructura económica">
          <button type="button" className={mode === "share" ? "active" : ""} onClick={() => setMode("share")}>Participación</button>
          <button type="button" className={mode === "value" ? "active" : ""} onClick={() => setMode("value")}>Valor real</button>
        </div>
        {mode === "share" ? (
          <StackedShare
            title="Estructura económica"
            note="Parte del valor agregado que genera cada gran actividad."
            years={economy.years}
            layers={economy.groups.map((group) => ({
              key: group.id,
              label: group.label,
              color: GROUP_COLORS[group.id],
              values: geo.groups[group.id].map((value, i) => value / geo.valueAdded[i] * 100),
            }))}
            year={year}
            onYear={onYear}
          />
        ) : (
          <LineChart
            title="Estructura económica"
            note={`Valor agregado por gran actividad. ${scale.label}.`}
            years={economy.years}
            series={economy.groups.map((group) => ({
              key: group.id,
              label: group.label,
              color: GROUP_COLORS[group.id],
              values: geo.groups[group.id].map((value) => value / scale.divisor),
            }))}
            year={year}
            onYear={onYear}
            format={(value) => pesos(value * scale.divisor)}
            zero
          />
        )}
      </div>
      <LineChart
        title="Crecimiento del PIB"
        note={`PIB real, ${economy.years[0]} = 100. Una línea en 200 indica que la economía duplicó su tamaño.`}
        years={economy.years}
        series={[
          { key: "gdp", label: isNational ? "Nacional" : "Estado", color: COLORS.state, values: indexed(geo.gdp) },
          ...(isNational ? [] : [{ key: "national-gdp", label: "Nacional", color: COLORS.national, values: indexed(national.gdp), reference: true }]),
        ]}
        year={year}
        onYear={onYear}
        format={(value) => decimal(value, 0)}
      />
    </>
  );
}

function StateRanking({
  economy, code, year, stateNames, onSelect,
}: {
  economy: Economy;
  code: string;
  year: number;
  stateNames: Map<string, string>;
  onSelect: (code: string) => void;
}) {
  const [activity, setActivity] = useState<string>("gdp");
  const [hovered, setHovered] = useState<string | null>(null);
  const group = economy.groups.find((item) => item.id === activity);
  const sector = economy.sectors.find((item) => item.id === activity);
  // Sectors start later than the aggregates; rank the first sector year instead.
  const rankYear = sector ? Math.max(year, economy.sectorYears[0]) : year;
  const valueOf = (key: string) => {
    const geo = economy.geographies[key];
    if (sector) return geo.sectors[sector.id][economy.sectorYears.indexOf(rankYear)];
    const index = economy.years.indexOf(rankYear);
    return group ? geo.groups[group.id][index] : geo.gdp[index];
  };
  const total = valueOf(NATIONAL);
  const rows = Object.keys(economy.geographies)
    .filter((key) => key !== NATIONAL)
    .map((key) => ({ code: key, value: valueOf(key) }))
    .sort((a, b) => b.value - a.value);
  const max = rows[0]?.value || 1;
  const label = sector ? sector.label : group ? `Actividades ${group.label.toLowerCase()}` : "PIB total";
  const focus = hovered ?? (code === NATIONAL ? null : code);
  const focusRow = rows.findIndex((row) => row.code === focus);

  return (
    <section className="estado-chart estado-rank">
      <h3>Lugar entre los estados</h3>
      <p>
        {label}, {rankYear}.{sector && year < economy.sectorYears[0] ? ` Los sectores empiezan en ${economy.sectorYears[0]}.` : ""}
        {" "}Haz clic en un estado para abrir su perfil.
      </p>
      <div className="estado-chips flush" role="group" aria-label="Actividad del ranking">
        <button type="button" className={activity === "gdp" ? "active" : ""} onClick={() => setActivity("gdp")}>PIB total</button>
        {economy.groups.map((item) => (
          <button key={item.id} type="button" className={activity === item.id ? "active" : ""} onClick={() => setActivity(item.id)}>{item.label}</button>
        ))}
        <select
          className={sector ? "active" : ""}
          value={sector ? sector.id : ""}
          aria-label="Sector del ranking"
          onChange={(event) => setActivity(event.target.value || "gdp")}
        >
          <option value="">Un sector…</option>
          {economy.groups.map((item) => (
            <optgroup key={item.id} label={item.label}>
              {economy.sectors.filter((s) => s.group === item.id).map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
            </optgroup>
          ))}
        </select>
      </div>
      <p className="estado-rank-readout" aria-live="polite">
        {focusRow >= 0 ? (
          <>
            <strong>{stateNames.get(rows[focusRow].code)}</strong>
            <span>{focusRow + 1}.º de 32</span>
            <span>{pesos(rows[focusRow].value)} de pesos</span>
            <span>{decimal(rows[focusRow].value / total * 100, 1)}% del total nacional</span>
          </>
        ) : (
          <span className="muted">Pasa el cursor sobre un estado para ver su lugar y su valor.</span>
        )}
      </p>
      <ol className="estado-rank-list" onMouseLeave={() => setHovered(null)}>
        {rows.map((row, index) => (
          <li key={row.code}>
            <button
              type="button"
              className={`${row.code === code ? "selected" : ""}${row.code === hovered ? " hovered" : ""}`}
              aria-label={`${index + 1}. ${stateNames.get(row.code)}: ${pesos(row.value)} de pesos`}
              onClick={() => onSelect(row.code)}
              onMouseEnter={() => setHovered(row.code)}
              onFocus={() => setHovered(row.code)}
              onBlur={() => setHovered(null)}
            >
              <span>{index + 1}</span>
              <span>{stateNames.get(row.code)}</span>
              <span className="track">
                <i style={{ width: `${row.value / max * 100}%`, background: row.code === code ? COLORS.state : RANK_BAR }} />
              </span>
            </button>
          </li>
        ))}
      </ol>
    </section>
  );
}

function SectorBreakdown({
  economy, code, year, onYear,
}: {
  economy: Economy;
  code: string;
  year: number;
  onYear: (year: number) => void;
}) {
  const [group, setGroup] = useState<GroupId | "all">("all");
  const [picked, setPicked] = useState<string | null>(null);
  const [mode, setMode] = useState<"index" | "value">("index");
  const sectorYears = economy.sectorYears;
  const shownYear = Math.max(year, sectorYears[0]);
  const si = sectorYears.indexOf(shownYear);
  const yi = economy.years.indexOf(shownYear);
  const geo = economy.geographies[code];
  const national = economy.geographies[NATIONAL];
  const isNational = code === NATIONAL;
  const rows = economy.sectors
    .filter((sector) => group === "all" || sector.group === group)
    .map((sector) => ({
      ...sector,
      share: geo.sectors[sector.id][si] / geo.valueAdded[yi] * 100,
      nationalShare: national.sectors[sector.id][si] / national.valueAdded[yi] * 100,
    }))
    .sort((a, b) => b.share - a.share);
  const max = Math.max(...rows.flatMap((row) => isNational ? [row.share] : [row.share, row.nationalShare]), 1);
  const active = rows.find((row) => row.id === picked) ?? rows[0];
  const values = geo.sectors[active.id];
  // An index needs a nonzero base year; a sector absent in 2003 falls back to value.
  const canIndex = values[0] > 0 && national.sectors[active.id][0] > 0;
  const showIndex = mode === "index" && canIndex;
  const scale = pesoScale(values);

  return (
    <section className="estado-chart estado-sectors">
      <h3>Detalle por sector</h3>
      <p>
        Parte del valor agregado del estado en {shownYear}{year < sectorYears[0] ? ` (los sectores empiezan en ${sectorYears[0]})` : ""}.
        {isNational ? "" : " La marca indica el promedio nacional."} Elige un sector para ver su trayectoria.
      </p>
      <div className="estado-chips flush" role="group" aria-label="Gran actividad">
        <button type="button" className={group === "all" ? "active" : ""} onClick={() => setGroup("all")}>Todos</button>
        {economy.groups.map((item) => (
          <button key={item.id} type="button" className={group === item.id ? "active" : ""} onClick={() => setGroup(item.id)}>{item.label}</button>
        ))}
      </div>
      <ol className="estado-sector-list">
        {rows.map((row) => (
          <li key={row.id}>
            <button
              type="button"
              className={row.id === active.id ? "selected" : ""}
              title={row.name}
              aria-pressed={row.id === active.id}
              aria-label={`${row.label}: ${decimal(row.share, 1)}% del valor agregado${isNational ? "" : `; nacional ${decimal(row.nationalShare, 1)}%`}`}
              onClick={() => setPicked(row.id)}
            >
              <span>{row.label}</span>
              <span className="track">
                <i style={{ width: `${row.share / max * 100}%`, background: GROUP_COLORS[row.group] }} />
                {!isNational && <b style={{ left: `${row.nationalShare / max * 100}%` }} />}
              </span>
              <span>{decimal(row.share, 1)}%</span>
            </button>
          </li>
        ))}
      </ol>
      <div className="estado-sector-chart">
        <div className="estado-chips flush" role="group" aria-label="Escala del sector">
          <button type="button" className={showIndex ? "active" : ""} disabled={!canIndex} onClick={() => setMode("index")}>Índice</button>
          <button type="button" className={!showIndex ? "active" : ""} onClick={() => setMode("value")}>Valor real</button>
        </div>
        <LineChart
          title={active.label}
          note={showIndex
            ? `Valor agregado real, ${sectorYears[0]} = 100${isNational ? "" : ", comparado con el mismo sector en todo el país"}.`
            : `${scale.label}.${canIndex ? "" : ` Sin actividad registrada en ${sectorYears[0]}, así que no hay índice.`}`}
          years={sectorYears}
          series={showIndex
            ? [
              { key: active.id, label: isNational ? "Nacional" : "Estado", color: GROUP_COLORS[active.group], values: indexed(values) },
              ...(isNational ? [] : [{ key: `national-${active.id}`, label: "Nacional", color: COLORS.national, values: indexed(national.sectors[active.id]), reference: true }]),
            ]
            : [{ key: active.id, label: isNational ? "Nacional" : "Estado", color: GROUP_COLORS[active.group], values: values.map((value) => value / scale.divisor) }]}
          year={shownYear}
          onYear={onYear}
          format={(value) => showIndex ? decimal(value, 0) : pesos(value * scale.divisor)}
          zero={!showIndex}
          height={200}
        />
      </div>
    </section>
  );
}

function EconomyTable({ economy, code }: { economy: Economy; code: string }) {
  const geo = economy.geographies[code];
  return (
    <details className="estado-table">
      <summary>Ver los datos del PIB en tabla</summary>
      <div>
        <table>
          <thead>
            <tr>
              <th>Año</th>
              <th>PIB (millones de pesos de 2018)</th>
              <th>Variación anual</th>
              {economy.groups.map((group) => <th key={group.id}>{group.label}</th>)}
            </tr>
          </thead>
          <tbody>
            {economy.years.map((year, i) => (
              <tr key={year}>
                <th>{year}</th>
                <td>{integer.format(geo.gdp[i])}</td>
                <td>{i === 0 ? "—" : signed((geo.gdp[i] / geo.gdp[i - 1] - 1) * 100)}</td>
                {economy.groups.map((group) => (
                  <td key={group.id}>{decimal(geo.groups[group.id][i] / geo.valueAdded[i] * 100, 1)}%</td>
                ))}
              </tr>
            )).reverse()}
          </tbody>
        </table>
      </div>
    </details>
  );
}

function eicValue(indicator: EicIndicator, value: number) {
  const text = decimal(value, indicator.decimals);
  if (indicator.unit === "%") return `${text}%`;
  if (indicator.unit === "pesos") return `$${integer.format(value)}`;
  return indicator.unit ? `${text} ${indicator.unit}` : text;
}

/** All 32 states on one axis: the selected one large and green, the nation as a tick. */
function StateStrip({
  eic, indicator, code, stateNames, onSelect,
}: {
  eic: Eic;
  indicator: EicIndicator;
  code: string;
  stateNames: Map<string, string>;
  onSelect: (code: string) => void;
}) {
  const [hover, setHover] = useState<{ code: string; left: number; top: number } | null>(null);
  const width = 168;
  const height = 24;
  const rows = Object.entries(eic.geographies)
    .filter(([key]) => key !== NATIONAL)
    .map(([key, values]) => ({ code: key, value: values[indicator.id][0], rank: values[indicator.id][4] }));
  const min = Math.min(...rows.map((row) => row.value));
  const max = Math.max(...rows.map((row) => row.value));
  const x = (value: number) => 6 + ((value - min) / (max - min || 1)) * (width - 12);
  const national = eic.geographies[NATIONAL][indicator.id][0];
  // Selected and hovered states last, so they paint over their neighbours.
  const order = (row: { code: string }) => Number(row.code === code) + 2 * Number(row.code === hover?.code);
  rows.sort((a, b) => order(a) - order(b));
  const hovered = hover ? rows.find((row) => row.code === hover.code) : undefined;

  // Dots are 3px and often overlap, so the pointer picks the nearest one along
  // the axis rather than needing to land on a circle.
  const pick = (event: PointerEvent<SVGSVGElement>) => {
    const box = event.currentTarget.getBoundingClientRect();
    const px = ((event.clientX - box.left) / box.width) * width;
    const nearest = rows.reduce((best, row) => Math.abs(x(row.value) - px) < Math.abs(x(best.value) - px) ? row : best);
    setHover({ code: nearest.code, left: box.left + (x(nearest.value) / width) * box.width, top: box.top });
  };

  return (
    <>
      <svg className="estado-strip" width={width} height={height + 12} viewBox={`0 0 ${width} ${height + 12}`} role="img"
        aria-label={`De ${eicValue(indicator, min)} a ${eicValue(indicator, max)} entre los 32 estados`}
        onPointerMove={pick}
        onPointerLeave={() => setHover(null)}
        onClick={() => hover && onSelect(hover.code)}>
        <line className="estado-grid" x1={6} x2={width - 6} y1={height / 2} y2={height / 2} />
        <line className="estado-strip-national" x1={x(national)} x2={x(national)} y1={3} y2={height - 3} />
        {rows.map((row) => (
          <circle
            key={row.code}
            cx={x(row.value)}
            cy={height / 2}
            r={row.code === code || row.code === hover?.code ? 5 : 3}
            fill={row.code === code ? COLORS.state : row.code === hover?.code ? "var(--ink)" : RANK_BAR}
            stroke={row.code === code || row.code === hover?.code ? "var(--white)" : "none"}
            strokeWidth={2}
          />
        ))}
        <text className="estado-tick" x={2} y={height + 10}>{eicValue(indicator, min)}</text>
        <text className="estado-tick" x={width - 2} y={height + 10} textAnchor="end">{eicValue(indicator, max)}</text>
      </svg>
      {hover && hovered && (
        <div className="estado-tooltip estado-strip-tooltip" style={{ left: hover.left, top: hover.top }}>
          <strong>{stateNames.get(hovered.code)}</strong>
          <span>Lugar {hovered.rank} de 32<b>{eicValue(indicator, hovered.value)}</b></span>
        </div>
      )}
    </>
  );
}

function EicSection({
  eic, code, name, stateNames, onSelect,
}: {
  eic: Eic;
  code: string;
  name: string;
  stateNames: Map<string, string>;
  onSelect: (code: string) => void;
}) {
  // One category at a time: all 46 indicators at once would push the rest of the page far down.
  const [category, setCategory] = useState<string>(eic.categories[0].id);
  const isNational = code === NATIONAL;
  const values = eic.geographies[code];
  const national = eic.geographies[NATIONAL];
  const shown = eic.categories.filter((item) => category === "all" || item.id === category);
  const lowPrecision = eic.categories.some((item) => item.indicators.some((indicator) => values[indicator.id][3] > eic.lowPrecisionCv));
  const hasOwn = shown.some((item) => item.indicators.some((indicator) => indicator.source === "propio"));

  return (
    <section className="estado-section">
      <header className="estado-section-heading">
        <div><p className="eyebrow">{name} · Encuesta Intercensal 2025</p><h2>Radiografía 2025</h2></div>
        <p>
          {isNational
            ? "Cómo vive el país hoy, con el rango que va del estado más bajo al más alto."
            : "Cómo se compara con los otros 31 estados. El lugar 1 es el valor más alto, sea bueno o malo; entre paréntesis, los lugares que podría ocupar dado el margen de error."}
        </p>
      </header>
      <div className="estado-chips eic-chips" role="group" aria-label="Categoría">
        {eic.categories.map((item) => (
          <button key={item.id} type="button" className={category === item.id ? "active" : ""} onClick={() => setCategory(item.id)}>{item.label}</button>
        ))}
        <button type="button" className={category === "all" ? "active" : ""} onClick={() => setCategory("all")}>Todas</button>
      </div>
      <div className="estado-eic-wrap">
        <table className="estado-eic">
          <thead>
            <tr>
              <th scope="col">Indicador</th>
              {!isNational && <th scope="col">{name}</th>}
              <th scope="col">Nacional</th>
              {!isNational && <th scope="col">Lugar <small>de 32</small></th>}
              <th scope="col" className="estado-eic-strip">Los 32 estados</th>
            </tr>
          </thead>
          {shown.map((item) => (
            <tbody key={item.id}>
              <tr className="estado-eic-category"><th scope="rowgroup" colSpan={isNational ? 3 : 5}>{item.label}</th></tr>
              {item.indicators.map((indicator) => {
                const [value, li, ls, cv, rank, best, worst] = values[indicator.id];
                const imprecise = cv > eic.lowPrecisionCv;
                return (
                  <tr key={indicator.id}>
                    <th scope="row">
                      {indicator.label}
                      {indicator.detail && <small>{indicator.detail}</small>}
                    </th>
                    {!isNational && (
                      <td className="estado-eic-value" title={`Intervalo al 90%: ${eicValue(indicator, li)} a ${eicValue(indicator, ls)}`}>
                        {eicValue(indicator, value)}
                        {imprecise && <sup title={`Estimación poco precisa (coeficiente de variación ${decimal(cv, 0)})`}>†</sup>}
                      </td>
                    )}
                    <td className={isNational ? "estado-eic-value" : "estado-eic-national"} title={`Intervalo al 90%: ${eicValue(indicator, national[indicator.id][1])} a ${eicValue(indicator, national[indicator.id][2])}`}>
                      {eicValue(indicator, national[indicator.id][0])}
                    </td>
                    {!isNational && (
                      <td className="estado-eic-rank">
                        {rank}.º
                        {best !== worst && <small>({best}–{worst})</small>}
                      </td>
                    )}
                    <td className="estado-eic-strip"><StateStrip eic={eic} indicator={indicator} code={code} stateNames={stateNames} onSelect={onSelect} /></td>
                  </tr>
                );
              })}
            </tbody>
          ))}
        </table>
      </div>
      <p className="estado-eic-note">
        Pasa el cursor sobre una cifra para ver su intervalo de confianza al 90%, o sobre un punto para ver el estado y su lugar; haz clic para abrirlo.
        {lowPrecision && " † Estimación poco precisa: coeficiente de variación mayor a 30."}
        {hasOwn && " El ingreso por trabajo es un cálculo propio con los microdatos, con el método de INEGI; INEGI no lo publica."}
      </p>
    </section>
  );
}

function EconomySection({
  economy, code, name, stateNames, onSelect,
}: {
  economy: Economy;
  code: string;
  name: string;
  stateNames: Map<string, string>;
  onSelect: (code: string) => void;
}) {
  // Its own year: GDP runs 1980–2024, the population slider only to 2019.
  const lastYear = economy.years.at(-1)!;
  const [year, setYear] = useState(lastYear);
  return (
    <section className="estado-section">
      <header className="estado-section-heading">
        <div><p className="eyebrow">{name} · PIB {economy.years[0]}–{lastYear}</p><h2>La economía</h2></div>
        <div className="estado-section-aside">
          <p>Cuánto produce la entidad y en qué, a precios de 2018 para quitar el efecto de la inflación. Haz clic en cualquier gráfica para fijar el año.</p>
          <label className="estado-inline-select">
            <span>Año del PIB</span>
            <select value={year} onChange={(event) => setYear(Number(event.target.value))}>
              {[...economy.years].reverse().map((item) => <option key={item} value={item}>{item}</option>)}
            </select>
          </label>
        </div>
      </header>
      <EconomyTiles economy={economy} code={code} year={year} />
      <div className="estado-chart-grid">
        <EconomyStructure economy={economy} code={code} year={year} onYear={setYear} />
      </div>
      <div className="estado-chart-grid estado-economy-grid">
        <StateRanking economy={economy} code={code} year={year} stateNames={stateNames} onSelect={onSelect} />
        <SectorBreakdown economy={economy} code={code} year={year} onYear={setYear} />
      </div>
      <EconomyTable economy={economy} code={code} />
    </section>
  );
}

export default function StateExplorer() {
  const [seed] = useState(seededParams);
  const [index, setIndex] = useState<StateIndex | null>(null);
  const [geojson, setGeojson] = useState<StateGeoJson | null>(null);
  const [geographies, setGeographies] = useState<Record<string, Geography>>({});
  const [error, setError] = useState(false);
  const [code, setCode] = useState(seed.code);
  const [year, setYear] = useState<number | null>(seed.year);
  const [playing, setPlaying] = useState(false);
  const [metric, setMetric] = useState<MetricKey>("medianAge");
  const [compareMode, setCompareMode] = useState<"auto" | "first" | "none">("auto");
  const [economy, setEconomy] = useState<Economy | null>(null);
  const [eic, setEic] = useState<Eic | null>(null);

  useEffect(() => {
    Promise.all([
      fetch("/data/estados/index.json").then((response) => response.ok ? response.json() : Promise.reject()),
      fetch("/data/estados/00.json").then((response) => response.ok ? response.json() : Promise.reject()),
      fetch("/data/electoral-states.geojson").then((response) => response.ok ? response.json() : Promise.reject()),
    ])
      .then(([stateIndex, national, states]: [StateIndex, Geography, StateGeoJson]) => {
        setIndex(stateIndex);
        setGeographies((current) => ({ ...current, [NATIONAL]: national }));
        setGeojson(states);
      })
      .catch(() => setError(true));
  }, []);

  // The economy file holds every state (the ranking needs them all), so it
  // loads once, after the population data has painted.
  useEffect(() => {
    if (!index) return;
    fetch("/data/estados/economia.json")
      .then((response) => response.ok ? response.json() : Promise.reject())
      .then((data: Economy) => setEconomy(data))
      .catch(() => setError(true));
    fetch("/data/estados/eic2025.json")
      .then((response) => response.ok ? response.json() : Promise.reject())
      .then((data: Eic) => setEic(data))
      .catch(() => setError(true));
  }, [index]);

  useEffect(() => {
    if (code === NATIONAL || geographies[code]) return;
    let cancelled = false;
    fetch(`/data/estados/${code}.json`)
      .then((response) => response.ok ? response.json() : Promise.reject())
      .then((geography: Geography) => {
        if (!cancelled) setGeographies((current) => ({ ...current, [code]: geography }));
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });
    return () => { cancelled = true; };
  }, [code, geographies]);

  const years = index?.years ?? [];
  const lastYear = years.at(-1) ?? 0;
  const activeYear = year !== null && years.includes(year) ? year : lastYear;

  useEffect(() => {
    if (!playing) return;
    const timer = window.setTimeout(() => {
      if (activeYear >= lastYear) setPlaying(false);
      else setYear(activeYear + 1);
    }, PLAY_STEP_MS);
    return () => window.clearTimeout(timer);
  }, [playing, activeYear, lastYear]);

  // The reading state lives in the query string so a state and year can be
  // shared. replaceState, so playing fifty years does not cost fifty Backs.
  useEffect(() => {
    if (!index) return;
    const params = new URLSearchParams();
    if (code !== NATIONAL) params.set("estado", code);
    if (activeYear !== lastYear) params.set("anio", String(activeYear));
    const search = params.toString();
    window.history.replaceState(null, "", `${window.location.pathname}${search ? `?${search}` : ""}`);
  }, [index, code, activeYear, lastYear]);

  if (error) return <main className="state-screen"><h1>No pudimos cargar los datos del estado.</h1></main>;
  const national = geographies[NATIONAL];
  if (!index || !national || !geojson) {
    return <main className="state-screen loading-state"><span className="loading-mark" /><p className="eyebrow">{SITE_NAME}</p><h1>Preparando los estados…</h1></main>;
  }

  const stateNames = new Map(index.states.map((state) => [state.code, state.name]));
  const geography = geographies[code] ?? null;
  const shown = geography ?? national;
  const pending = geography === null;
  const yearIndex = years.indexOf(activeYear);
  const isNational = shown.code === NATIONAL;
  const compareFirst = compareMode === "first" || (compareMode === "auto" && isNational);
  const compare = compareMode === "none"
    ? null
    : compareFirst
      ? { label: `${shown.name} ${years[0]}`, men: shown.pyramid.men[0], women: shown.pyramid.women[0] }
      : { label: `Nacional ${activeYear}`, men: national.pyramid.men[yearIndex], women: national.pyramid.women[yearIndex] };
  const reference = (key: SeriesKey, label = "Nacional"): ChartSeries[] =>
    isNational ? [] : [{ key: `national-${key}`, label, color: COLORS.national, values: national.series[key], reference: true }];
  const selectCode = (next: string) => {
    setCode(next);
    setPlaying(false);
  };

  return (
    <main>
      <SiteHeader active="visualizaciones" status="Población y economía por entidad" />
      <section className="electoral-hero estado-hero">
        <div>
          <p className="eyebrow">Conoce tu estado · Población y economía</p>
          <h1>Conoce tu estado.</h1>
        </div>
        <p className="hero-copy">
          Cómo vive hoy cada entidad, qué produce su economía y cómo ha cambiado su población desde
          1970. Elige un estado y empieza por lo más reciente.
        </p>
      </section>

      <section className={`estado-explorer${pending ? " pending" : ""}`}>
        <div className="estado-controls">
          <label className="estado-select">
            <span>Entidad</span>
            <select value={code} onChange={(event) => selectCode(event.target.value)}>
              {index.states.map((state) => (
                <option key={state.code} value={state.code}>{state.code === NATIONAL ? "Nacional · República Mexicana" : state.name}</option>
              ))}
            </select>
          </label>
        </div>

        {eic ? (
          <EicSection eic={eic} code={shown.code} name={shown.name} stateNames={stateNames} onSelect={selectCode} />
        ) : (
          <section className="estado-section estado-section-loading"><p className="eyebrow">Cargando la Encuesta Intercensal…</p></section>
        )}

        {economy ? (
          <EconomySection economy={economy} code={shown.code} name={shown.name} stateNames={stateNames} onSelect={selectCode} />
        ) : (
          <section className="estado-section estado-section-loading"><p className="eyebrow">Cargando el PIB…</p></section>
        )}

        <section className="estado-section estado-history">
          <header className="estado-section-heading">
            <div><p className="eyebrow">{shown.name} · {years[0]}–{lastYear}</p><h2>Medio siglo de población</h2></div>
            <div className="estado-section-aside">
              <p>Menos nacimientos, vidas más largas y una población que envejece. Recorre los años o haz clic en cualquier gráfica para fijar uno.</p>
              <div className="estado-year">
                <span>Año</span>
                <button
                  type="button"
                  className="estado-play"
                  aria-label={playing ? "Pausar" : "Reproducir los años"}
                  onClick={() => {
                    if (playing) {
                      setPlaying(false);
                    } else {
                      if (activeYear >= lastYear) setYear(years[0]);
                      setPlaying(true);
                    }
                  }}
                >
                  {playing ? "❚❚" : "▶"}
                </button>
                <input
                  type="range"
                  min={years[0]}
                  max={lastYear}
                  step={1}
                  value={activeYear}
                  aria-label="Año"
                  onChange={(event) => {
                    setPlaying(false);
                    setYear(Number(event.target.value));
                  }}
                />
                <strong>{activeYear}</strong>
              </div>
            </div>
          </header>

          <div className="estado-master-detail">
            <section className="electoral-panel estado-map-panel">
              <header>
                <div><p className="eyebrow">Mapa · {activeYear}</p><h2>Selecciona una entidad.</h2></div>
                <button type="button" className="estado-national" disabled={code === NATIONAL} onClick={() => selectCode(NATIONAL)}>Ver nacional</button>
              </header>
              <div className="estado-chips" role="group" aria-label="Indicador del mapa">
                {(Object.keys(index.metrics) as MetricKey[]).map((key) => (
                  <button key={key} type="button" className={metric === key ? "active" : ""} onClick={() => setMetric(key)}>
                    {index.metrics[key].label}
                  </button>
                ))}
              </div>
              <StateMap geojson={geojson} index={index} metric={metric} yearIndex={yearIndex} selected={code} onSelect={selectCode} />
            </section>

            <section className="electoral-panel estado-pyramid-panel">
              <header>
                <div><p className="eyebrow">Pirámide de población · {activeYear}</p><h2>{shown.name}</h2></div>
                <div className="estado-chips compact" role="group" aria-label="Comparar la pirámide con">
                  <span>Comparar con</span>
                  {!isNational && (
                    <button type="button" className={!compareFirst && compareMode !== "none" ? "active" : ""} onClick={() => setCompareMode("auto")}>Nacional</button>
                  )}
                  <button type="button" className={compareFirst ? "active" : ""} onClick={() => setCompareMode("first")}>{years[0]}</button>
                  <button type="button" className={compareMode === "none" ? "active" : ""} onClick={() => setCompareMode("none")}>Nada</button>
                </div>
              </header>
              <Pyramid geography={shown} yearIndex={yearIndex} compare={compare} />
            </section>
          </div>

          <StatTiles geography={shown} yearIndex={yearIndex} />

          <div className="estado-chart-grid">
            <LineChart
              title="Nacimientos y defunciones"
              note="Por cada mil habitantes al año."
              years={years}
              series={[
                { key: "birthRate", label: "Nacimientos", color: COLORS.young, values: shown.series.birthRate },
                { key: "deathRate", label: "Defunciones", color: COLORS.old, values: shown.series.deathRate },
              ]}
              year={activeYear}
              onYear={setYear}
              format={(value) => decimal(value, value >= 10 ? 0 : 1)}
              zero
            />
            <LineChart
              title="Esperanza de vida al nacer"
              note="Años que viviría en promedio una persona nacida ese año."
              years={years}
              series={[
                { key: "lifeExpectancyWomen", label: "Mujeres", color: COLORS.women, values: shown.series.lifeExpectancyWomen },
                { key: "lifeExpectancyMen", label: "Hombres", color: COLORS.men, values: shown.series.lifeExpectancyMen },
                ...reference("lifeExpectancy", "Nacional, total"),
              ]}
              year={activeYear}
              onYear={setYear}
              format={(value) => decimal(value, 0)}
            />
            <LineChart
              title="Hijos por mujer"
              note="Tasa global de fecundidad. El reemplazo generacional ronda 2.1."
              years={years}
              series={[
                { key: "fertilityRate", label: isNational ? "Nacional" : shown.name, color: COLORS.state, values: shown.series.fertilityRate },
                ...reference("fertilityRate"),
              ]}
              year={activeYear}
              onYear={setYear}
              format={(value) => decimal(value, 1)}
              zero
            />
            <LineChart
              title="Dependencia por edad"
              note="Menores de 15 y mayores de 64 por cada 100 personas de 15 a 64 años."
              years={years}
              series={[
                { key: "childDependency", label: "Menores de 15", color: COLORS.young, values: shown.series.childDependency },
                { key: "olderDependency", label: "65 y más", color: COLORS.old, values: shown.series.olderDependency },
              ]}
              year={activeYear}
              onYear={setYear}
              format={(value) => decimal(value, 0)}
              zero
            />
          </div>
          <DataTable geography={shown} />
        </section>

        <details className="electoral-method">
          <summary>De dónde vienen estos datos</summary>
          {eic && (
            <p>
              Radiografía 2025: {eic.source}. Son estimaciones por muestreo: cada cifra tiene un intervalo de
              confianza al 90%, y el lugar entre paréntesis cuenta solo los estados cuyo intervalo queda por
              completo arriba o abajo. Los porcentajes se calculan sobre la población o los hogares indicados
              bajo cada nombre. El ingreso mensual por trabajo no lo publica INEGI: es el promedio de la
              población ocupada que declaró ingreso, calculado con los microdatos y el método de INEGI.
            </p>
          )}
          {economy && (
            <p>
              Producción: {economy.source}. Valores en millones de pesos a precios de 2018, que suman
              exactamente entre niveles: sectores, grandes actividades, valor agregado y PIB, y los 32 estados
              al total nacional. El PIB incluye los impuestos netos sobre los productos; la estructura por
              actividad usa el valor agregado, que no los incluye. Los 20 sectores existen desde 2003; antes
              solo hay grandes actividades. INEGI marca 2023 y 2024 como cifras revisadas.
            </p>
          )}
          <p>
            Población, nacimientos, defunciones, esperanza de vida y fecundidad: {index.source.population}.
            Solo se muestran los años {years[0]}–{lastYear}; a partir de 2020 CONAPO publica proyecciones, que
            aquí no se usan. La pirámide agrupa la población a mitad de año en grupos quinquenales y la
            pirámide nacional es la suma de las 32 entidades.
          </p>
        </details>
      </section>
      <SiteFooter note="Conoce tu estado · INEGI y CONAPO" />
    </main>
  );
}
