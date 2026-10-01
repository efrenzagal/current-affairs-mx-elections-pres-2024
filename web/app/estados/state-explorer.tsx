"use client";

import { useEffect, useMemo, useRef, useState } from "react";

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
  source: { population: string; remittances: string };
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
  remittances: {
    periods: string[];
    region: string | null;
    quarterly: number[];
    topYear: number;
    topMunicipios: { code: string; name: string; grade: string; usdMillions: number }[];
  };
};

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

const integer = new Intl.NumberFormat("es-MX", { maximumFractionDigits: 0 });

function decimal(value: number, digits: number) {
  return new Intl.NumberFormat("es-MX", { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(value);
}

function people(value: number) {
  if (value >= 1_000_000) return `${decimal(value / 1_000_000, 1)} millones`;
  return integer.format(value);
}

function usd(millions: number) {
  if (millions >= 1000) return `${decimal(millions / 1000, 1)} mil millones de dólares`;
  return `${decimal(millions, millions >= 100 ? 0 : 1)} millones de dólares`;
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
  const decades = years.filter((item, index) => item % 10 === 0 || index === years.length - 1);
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
  const path = values
    .map((value, i) => value === null ? "" : `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(value).toFixed(1)}`)
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

function RemittanceBars({
  geography, national, stateNames,
}: {
  geography: Geography;
  national: Geography;
  stateNames: Map<string, string>;
}) {
  const [wrapRef, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const { periods, quarterly } = geography.remittances;
  const height = 220;
  const margin = { top: 12, right: 8, bottom: 28, left: 52 };
  const plotWidth = Math.max(0, width - margin.left - margin.right);
  const plotHeight = height - margin.top - margin.bottom;
  const scale = niceScale(0, Math.max(...quarterly));
  const slot = plotWidth / periods.length;
  const barWidth = Math.max(2, slot - 2);
  const y = (value: number) => margin.top + plotHeight - (value / (scale.high || 1)) * plotHeight;

  const years = [...new Set(periods.map((period) => Number(period.slice(0, 4))))];
  const yearTotal = (values: number[], year: number) =>
    values.reduce((sum, value, index) => periods[index].startsWith(String(year)) ? sum + value : sum, 0);
  const first = years[0];
  const last = years.at(-1) ?? first;
  const latest = yearTotal(quarterly, last);
  const earliest = yearTotal(quarterly, first);
  const share = latest / yearTotal(national.remittances.quarterly, last) * 100;
  const isNational = geography.code === NATIONAL;
  const topMax = Math.max(...geography.remittances.topMunicipios.map((item) => item.usdMillions));

  return (
    <div className="estado-remittances">
      <dl className="estado-remittance-stats">
        <div><dt>Remesas en {last}</dt><dd>{usd(latest)}</dd></div>
        <div><dt>Cambio desde {first}</dt><dd>{latest >= earliest ? "+" : ""}{decimal((latest / earliest - 1) * 100, 0)}%</dd><small>en dólares corrientes</small></div>
        {isNational
          ? <div><dt>Trimestres</dt><dd>{periods.length}</dd><small>{periods[0].replace("-T", " T")} a {periods.at(-1)?.replace("-T", " T")}</small></div>
          : <div><dt>Parte del total nacional</dt><dd>{decimal(share, 1)}%</dd><small>Región migratoria: {geography.remittances.region}</small></div>}
      </dl>
      <div className="estado-remittance-grid">
        <figure className="estado-chart">
          <figcaption>
            <h3>Remesas por trimestre</h3>
            <p>Millones de dólares corrientes recibidos cada trimestre.</p>
          </figcaption>
          <div className="estado-chart-plot" ref={wrapRef}>
            {width > 0 && (
              <svg width={width} height={height} role="img" aria-label={`Remesas trimestrales de ${geography.name}, ${periods[0]} a ${periods.at(-1)}`}>
                {scale.ticks.map((tick) => (
                  <g key={tick}>
                    <line className="estado-grid" x1={margin.left} x2={margin.left + plotWidth} y1={y(tick)} y2={y(tick)} />
                    <text className="estado-tick" x={margin.left - 8} y={y(tick)} dy="0.32em" textAnchor="end">{integer.format(tick)}</text>
                  </g>
                ))}
                {quarterly.map((value, index) => {
                  const x = margin.left + index * slot + 1;
                  const top = y(value);
                  const barHeight = Math.max(0, margin.top + plotHeight - top);
                  const radius = Math.min(3, barWidth / 2, barHeight);
                  return (
                    <g key={periods[index]}>
                      <path
                        d={`M${x},${top + barHeight}V${top + radius}Q${x},${top} ${x + radius},${top}H${x + barWidth - radius}Q${x + barWidth},${top} ${x + barWidth},${top + radius}V${top + barHeight}Z`}
                        fill={COLORS.state}
                        opacity={hover === null || hover === index ? 1 : 0.45}
                      />
                      <rect
                        className="estado-hit"
                        x={margin.left + index * slot}
                        y={margin.top}
                        width={slot}
                        height={plotHeight}
                        onMouseEnter={() => setHover(index)}
                        onMouseLeave={() => setHover(null)}
                      />
                    </g>
                  );
                })}
                {years.filter((year) => (year - first) % 2 === 0).map((year) => {
                  const index = periods.indexOf(`${year}-T1`);
                  return <text key={year} className="estado-tick" x={margin.left + (index + 2) * slot} y={height - 8} textAnchor="middle">{year}</text>;
                })}
              </svg>
            )}
            {hover !== null && width > 0 && (
              <div
                className="estado-tooltip"
                style={{
                  left: hover > periods.length / 2 ? undefined : margin.left + (hover + 1) * slot + 8,
                  right: hover > periods.length / 2 ? width - margin.left - hover * slot + 8 : undefined,
                }}
              >
                <strong>{periods[hover].replace("-T", " · trimestre ")}</strong>
                <span><i style={{ background: COLORS.state }} />Remesas<b>{decimal(quarterly[hover], 1)} mdd</b></span>
              </div>
            )}
          </div>
        </figure>
        <section className="estado-top-list">
          <h3>Municipios que más reciben</h3>
          <p>Remesas en {geography.remittances.topYear}, millones de dólares.</p>
          <ol>
            {geography.remittances.topMunicipios.map((item) => (
              <li key={item.code}>
                <div>
                  <strong>{item.name}</strong>
                  <span>{isNational ? `${stateNames.get(item.code.slice(0, 2)) ?? ""} · ` : ""}{decimal(item.usdMillions, 1)}</span>
                </div>
                <i style={{ width: `${(item.usdMillions / topMax) * 100}%` }} />
              </li>
            ))}
          </ol>
        </section>
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
      <SiteHeader active="estados" status="Población por entidad" />
      <section className="electoral-hero estado-hero">
        <div>
          <p className="eyebrow">Conoce tu estado · Población</p>
          <h1>Conoce tu estado.</h1>
        </div>
        <p className="hero-copy">
          Cómo ha cambiado la población de cada entidad desde 1970: su pirámide de edades, cuántos
          nacen y mueren, cuánto se vive y cuántas remesas recibe. Elige un estado en el mapa o en la
          lista y recorre los años.
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

        <section className="estado-section">
          <header className="estado-section-heading">
            <div><p className="eyebrow">{shown.name} · {years[0]}–{lastYear}</p><h2>La transición demográfica</h2></div>
            <p>Menos nacimientos, vidas más largas y una población que envejece. Haz clic en cualquier gráfica para fijar el año.</p>
          </header>
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

        <section className="estado-section">
          <header className="estado-section-heading">
            <div><p className="eyebrow">{shown.name} · Remesas</p><h2>El dinero que llega de fuera</h2></div>
            <p>Remesas familiares registradas por trimestre. Son montos observados, no estimaciones, y llegan hasta 2024.</p>
          </header>
          <RemittanceBars geography={shown} national={national} stateNames={stateNames} />
        </section>

        <details className="electoral-method">
          <summary>De dónde vienen estos datos</summary>
          <p>
            Población, nacimientos, defunciones, esperanza de vida y fecundidad: {index.source.population}.
            Solo se muestran los años {years[0]}–{lastYear}; a partir de 2020 CONAPO publica proyecciones, que
            aquí no se usan. La pirámide agrupa la población a mitad de año en grupos quinquenales y la
            pirámide nacional es la suma de las 32 entidades.
          </p>
          <p>
            Remesas: {index.source.remittances}. Son dólares corrientes, sin ajustar por inflación. Las
            remesas que no se pueden asignar a un municipio cuentan para el total del estado pero no aparecen
            en la lista de municipios.
          </p>
        </details>
      </section>
      <SiteFooter note={`Conoce tu estado · CONAPO ${years[0]}–${lastYear}`} />
    </main>
  );
}
