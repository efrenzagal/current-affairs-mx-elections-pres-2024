"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { SiteFooter, SiteHeader } from "../../site-chrome";

type Point = {
  president: string;
  month: number;
  date: string;
  approve: number;
  disapprove: number;
  residual: number;
  pollster: string;
  sourceUrl: string;
};

type President = { key: string; label: string; color: string };

type ApprovalData = {
  schemaVersion: number;
  sourceThrough: string;
  presidents: President[];
  pollsters: string[];
  points: Point[];
};

const ALL_POLLSTERS = "Todos";
const PRESIDENT_FULL_NAMES: Record<string, string> = {
  EZPL: "Ernesto Zedillo",
  VFQ: "Vicente Fox",
  FCH: "Felipe Calderón",
  EPN: "Enrique Peña Nieto",
  AMLO: "Andrés Manuel López Obrador",
  Sheinbaum: "Claudia Sheinbaum",
};

function presidentName(president: President) {
  return PRESIDENT_FULL_NAMES[president.key] ?? president.label;
}

// A full sexenio, so a still-serving president's line reads against the
// term it is partway through rather than against whatever month it reaches
// on the day the site happens to be viewed.
const TERM_MONTHS = 72;
const WIDTH = 860;
const HEIGHT = 440;
const MARGIN = { top: 16, right: 20, bottom: 34, left: 38 };
const PLOT_WIDTH = WIDTH - MARGIN.left - MARGIN.right;
const PLOT_HEIGHT = HEIGHT - MARGIN.top - MARGIN.bottom;

function xForMonth(month: number) {
  return MARGIN.left + (month / TERM_MONTHS) * PLOT_WIDTH;
}

function yForApproval(approve: number) {
  return MARGIN.top + (1 - approve / 100) * PLOT_HEIGHT;
}

function median(values: number[]) {
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

function monthlyTrend(points: Point[]) {
  const byMonth = new Map<number, number[]>();
  for (const point of points) {
    const bucket = byMonth.get(point.month);
    if (bucket) bucket.push(point.approve);
    else byMonth.set(point.month, [point.approve]);
  }
  return [...byMonth.entries()]
    .map(([month, values]) => ({ month, approve: median(values) }))
    .sort((a, b) => a.month - b.month);
}

// Two houses reporting the same whole-number approval in the same month land on
// identical coordinates and paint over each other -- 73 such pairs across the
// series, so a hidden point is the norm rather than an edge case. Fan a month's
// points out horizontally instead.
//
// The spread is deliberately bounded: one month is only PLOT_WIDTH / TERM_MONTHS
// (~11px) wide, so a dot pushed much further would read as belonging to the
// neighbouring month -- trading an invisible point for a misdated one. Months
// carrying up to nine polls therefore stay partly stacked by design; the offset
// separates the common cases and never lies about the date.
//
// Ordering is by pollster name, not input order, so a house keeps the same slot
// from month to month and the fan stays still when the data reloads.
const MONTH_WIDTH = PLOT_WIDTH / TERM_MONTHS;
const MAX_MONTH_SPREAD = MONTH_WIDTH * 0.7;
const PREFERRED_DOT_STEP = 4;

function offsetsWithinMonth(points: Point[]) {
  const byMonth = new Map<number, Point[]>();
  for (const point of points) {
    const bucket = byMonth.get(point.month);
    if (bucket) bucket.push(point);
    else byMonth.set(point.month, [point]);
  }

  const offsets = new Map<Point, number>();
  for (const bucket of byMonth.values()) {
    if (bucket.length === 1) continue;
    const ordered = [...bucket].sort((a, b) => a.pollster.localeCompare(b.pollster, "es"));
    const step = Math.min(PREFERRED_DOT_STEP, MAX_MONTH_SPREAD / (ordered.length - 1));
    const first = (-step * (ordered.length - 1)) / 2;
    ordered.forEach((point, index) => offsets.set(point, first + index * step));
  }
  return offsets;
}

function linePath(trend: { month: number; approve: number }[]) {
  return trend
    .map((point, index) => `${index === 0 ? "M" : "L"}${xForMonth(point.month).toFixed(1)},${yForApproval(point.approve).toFixed(1)}`)
    .join("");
}

function formatDate(date: string) {
  const [year, month] = date.split("-").map(Number);
  return new Intl.DateTimeFormat("es-MX", { month: "long", year: "numeric" }).format(new Date(year, month - 1, 1));
}

type Hover = { x: number; y: number; point: Point };

export default function ApprovalExplorer() {
  const [data, setData] = useState<ApprovalData | null>(null);
  const [error, setError] = useState(false);
  const [highlight, setHighlight] = useState<string | null>(null);
  const [pollster, setPollster] = useState(ALL_POLLSTERS);
  const [hover, setHover] = useState<Hover | null>(null);
  const chartWrapRef = useRef<HTMLDivElement | null>(null);

  function showHover(event: React.MouseEvent, point: Point) {
    const rect = chartWrapRef.current?.getBoundingClientRect();
    if (!rect) return;
    setHover({ x: event.clientX - rect.left, y: event.clientY - rect.top, point });
  }

  // Stale coordinates would point at a dot that no longer exists once the
  // highlighted president or pollster filter changes the point set.
  function selectHighlight(key: string) {
    setHighlight(key);
    setHover(null);
  }

  function selectPollster(house: string) {
    setPollster(house);
    setHover(null);
  }

  useEffect(() => {
    fetch("/data/approval.json")
      .then((response) => (response.ok ? response.json() : Promise.reject()))
      .then((payload: ApprovalData) => {
        setData(payload);
        setHighlight(payload.presidents.at(-1)?.key ?? null);
      })
      .catch(() => setError(true));
  }, []);

  const filteredPoints = useMemo(() => {
    if (!data) return [];
    return pollster === ALL_POLLSTERS
      ? data.points
      : data.points.filter((point) => point.pollster === pollster);
  }, [data, pollster]);

  const trendsByPresident = useMemo(() => {
    const byPresident = new Map<string, Point[]>();
    for (const point of filteredPoints) {
      const bucket = byPresident.get(point.president);
      if (bucket) bucket.push(point);
      else byPresident.set(point.president, [point]);
    }
    return new Map([...byPresident.entries()].map(([key, points]) => [key, monthlyTrend(points)]));
  }, [filteredPoints]);

  // Only needed once a pollster filter narrows the highlighted line, so the
  // dimmer "todas las encuestadoras" median can sit behind it as a house-effect
  // reference; skip the grouping otherwise.
  const overallTrendForHighlight = useMemo(() => {
    if (!data || pollster === ALL_POLLSTERS) return null;
    const points = data.points.filter((point) => point.president === highlight);
    const trend = monthlyTrend(points);
    return trend.length >= 2 ? trend : null;
  }, [data, highlight, pollster]);

  const highlightPoints = useMemo(
    () => (highlight ? filteredPoints.filter((point) => point.president === highlight) : []),
    [filteredPoints, highlight],
  );

  // Keyed on the point objects themselves, which survive both filters by
  // reference, so the lookup below stays an identity hit.
  const dotOffsets = useMemo(() => offsetsWithinMonth(highlightPoints), [highlightPoints]);

  const tableRows = useMemo(
    () =>
      [...highlightPoints].sort(
        (a, b) => b.date.localeCompare(a.date) || a.pollster.localeCompare(b.pollster, "es"),
      ),
    [highlightPoints],
  );

  const summary = useMemo(() => {
    if (highlightPoints.length === 0) return null;
    const dates = [...highlightPoints].map((point) => point.date).sort();
    return {
      count: highlightPoints.length,
      pollsters: [...new Set(highlightPoints.map((point) => point.pollster))].sort((a, b) => a.localeCompare(b, "es")),
      first: dates[0],
      last: dates[dates.length - 1],
    };
  }, [highlightPoints]);

  if (error) return <main className="state-screen"><h1>No pudimos cargar la aprobación presidencial.</h1></main>;
  if (!data || !highlight) {
    return (
      <main className="state-screen loading-state" aria-live="polite">
        <span className="loading-mark" />
        <p className="eyebrow">Visualizaciones</p>
        <h1>Preparando la aprobación…</h1>
      </main>
    );
  }

  const highlightPresident = data.presidents.find((president) => president.key === highlight)!;
  const yearTicks = Array.from({ length: TERM_MONTHS / 12 + 1 }, (_, index) => index * 12);

  return (
    <main>
      <SiteHeader active="visualizaciones" status="Aprobación presidencial" />

      <section className="electoral-hero">
        <div>
          <p className="eyebrow"><a href="/visualizaciones">Visualizaciones</a> · Presidencia</p>
          <h1>Aprobación presidencial.</h1>
        </div>
        <p className="hero-copy">
          Compara la aprobación de cada sexenio en el mismo punto de su mandato, no en la misma
          fecha del calendario. Elige a quién destacar y filtra por casa encuestadora.
        </p>
      </section>

      <section className="electoral-explorer">
        <div className="approval-controls">
          <div className="approval-control-group">
            <span>Presidente</span>
            <div className="party-filter approval-president-filter" role="group" aria-label="Presidente a destacar">
              {data.presidents.map((president) => (
                <button
                  key={president.key}
                  type="button"
                  className={highlight === president.key ? "active" : ""}
                  style={highlight === president.key ? { background: president.color, borderColor: president.color } : undefined}
                  onClick={() => selectHighlight(president.key)}
                >
                  {presidentName(president)}
                </button>
              ))}
            </div>
          </div>
        </div>

        <section className="electoral-panel approval-chart-panel">
          <header>
            <div>
              <p className="eyebrow">Meses en funciones · 0–{TERM_MONTHS}</p>
              <h2>{presidentName(highlightPresident)}</h2>
            </div>
            <p>
              Cada punto es una encuesta. La línea gruesa es la mediana mensual de{" "}
              <strong>{presidentName(highlightPresident)}</strong>; las líneas tenues son el mismo cálculo
              para el resto de los sexenios, en el mismo mes de su mandato.
              {pollster !== ALL_POLLSTERS && (
                <>
                  {" "}La línea punteada es la mediana de <strong>todas</strong> las encuestadoras, para
                  comparar el efecto casa de <strong>{pollster}</strong>.
                </>
              )}
            </p>
          </header>
          <div className="approval-line-legend" aria-hidden="true">
            <span className="approval-line-legend-item">
              <i className="approval-line-swatch approval-line-swatch-highlight" style={{ background: highlightPresident.color }} />
              {presidentName(highlightPresident)}
            </span>
            <span className="approval-line-legend-item">
              <i className="approval-line-swatch approval-line-swatch-ghost" />
              Otros sexenios
            </span>
            {overallTrendForHighlight && (
              <span className="approval-line-legend-item">
                <i className="approval-line-swatch approval-line-swatch-median" style={{ borderColor: highlightPresident.color }} />
                Mediana de todas las encuestadoras
              </span>
            )}
          </div>
          <div className="approval-chart-wrap" ref={chartWrapRef}>
            <svg
              className="approval-chart"
              viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
              role="img"
              aria-label={`Aprobación de ${presidentName(highlightPresident)} por mes de mandato, con el resto de los sexenios de referencia`}
            >
              {[0, 20, 40, 60, 80, 100].map((tick) => (
                <g key={tick}>
                  <line
                    x1={MARGIN.left}
                    x2={WIDTH - MARGIN.right}
                    y1={yForApproval(tick)}
                    y2={yForApproval(tick)}
                    className="approval-gridline"
                  />
                  <text x={MARGIN.left - 8} y={yForApproval(tick) + 3} className="approval-axis-label" textAnchor="end">
                    {tick}%
                  </text>
                </g>
              ))}
              {yearTicks.map((month) => (
                <text
                  key={month}
                  x={xForMonth(month)}
                  y={HEIGHT - MARGIN.bottom + 18}
                  className="approval-axis-label"
                  textAnchor={month === 0 ? "start" : month === TERM_MONTHS ? "end" : "middle"}
                >
                  {month === 0 ? "Toma de posesión" : `Año ${month / 12}`}
                </text>
              ))}

              {data.presidents
                .filter((president) => president.key !== highlight)
                .map((president) => {
                  const trend = trendsByPresident.get(president.key) ?? [];
                  if (trend.length < 2) return null;
                  return (
                    <path key={president.key} d={linePath(trend)} className="approval-line-ghost" style={{ stroke: president.color }} />
                  );
                })}

              {overallTrendForHighlight && (
                <path
                  d={linePath(overallTrendForHighlight)}
                  className="approval-line-median"
                  style={{ stroke: highlightPresident.color }}
                  aria-label={`Mediana de todas las encuestadoras para ${presidentName(highlightPresident)}`}
                />
              )}

              {highlightPoints.map((point, index) => (
                <circle
                  key={`${point.date}-${point.pollster}-${index}`}
                  cx={xForMonth(point.month) + (dotOffsets.get(point) ?? 0)}
                  cy={yForApproval(point.approve)}
                  r={hover?.point === point ? 5 : 3}
                  className="approval-dot"
                  style={{ fill: highlightPresident.color }}
                  aria-label={`${formatDate(point.date)} · ${point.pollster} · ${point.approve}% aprobación`}
                  onMouseEnter={(event) => showHover(event, point)}
                  onMouseMove={(event) => showHover(event, point)}
                  onMouseLeave={() => setHover(null)}
                />
              ))}
              {(() => {
                const trend = trendsByPresident.get(highlight) ?? [];
                return trend.length >= 2 ? (
                  <path d={linePath(trend)} className="approval-line-highlight" style={{ stroke: highlightPresident.color }} />
                ) : null;
              })()}
            </svg>
            {hover && (
              <div className="approval-tooltip" style={{ left: hover.x, top: hover.y }}>
                <strong>{hover.point.approve}% aprobación</strong>
                <span>{formatDate(hover.point.date)}</span>
                <span>{hover.point.pollster}</span>
              </div>
            )}
            <div className="approval-legend" aria-label="Sexenios de referencia">
              {data.presidents.map((president) => (
                <button
                  key={president.key}
                  type="button"
                  className={`approval-legend-item ${president.key === highlight ? "active" : ""}`}
                  onClick={() => selectHighlight(president.key)}
                >
                  <i style={{ background: president.color }} />
                  {presidentName(president)}
                </button>
              ))}
            </div>
          </div>
          <div className="approval-control-group approval-pollster-filter">
            <span>Encuestadora</span>
            <div className="party-filter" role="group" aria-label="Casa encuestadora">
              <button type="button" className={pollster === ALL_POLLSTERS ? "active" : ""} onClick={() => selectPollster(ALL_POLLSTERS)}>
                {ALL_POLLSTERS}
              </button>
              {data.pollsters.map((house) => (
                <button key={house} type="button" className={pollster === house ? "active" : ""} onClick={() => selectPollster(house)}>
                  {house}
                </button>
              ))}
            </div>
          </div>
        </section>

        <section className="electoral-panel approval-summary-panel">
          <header className="approval-summary-header">
            <p className="eyebrow">Resumen de la selección</p>
            <h2>{presidentName(highlightPresident)}{pollster !== ALL_POLLSTERS ? ` · ${pollster}` : ""}</h2>
          </header>
          {summary ? (
            <>
              <dl className="electoral-metrics">
                <div><dt>Encuestas</dt><dd>{summary.count}</dd></div>
                <div><dt>Primera encuesta</dt><dd>{formatDate(summary.first)}</dd></div>
                <div><dt>Última encuesta</dt><dd>{formatDate(summary.last)}</dd></div>
              </dl>
              <div className="approval-summary-pollsters">
                <span className="approval-summary-label">
                  Encuestadoras ({summary.pollsters.length})
                </span>
                <div className="article-topics">
                  {summary.pollsters.map((house) => (
                    <span key={house}>{house}</span>
                  ))}
                </div>
              </div>
            </>
          ) : (
            <p className="approval-summary-empty">Sin encuestas para esta selección.</p>
          )}
        </section>
      </section>

      <section className="approval-table-section">
        <header className="approval-summary-header">
          <p className="eyebrow">Encuestas y fuentes</p>
          <h2>{presidentName(highlightPresident)}{pollster !== ALL_POLLSTERS ? ` · ${pollster}` : ""}</h2>
        </header>
        {tableRows.length > 0 ? (
          <div className="dict-table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Encuestadora</th>
                  <th>Fecha</th>
                  <th>Aprueba</th>
                  <th>Desaprueba</th>
                  <th>Otro / NS</th>
                  <th>Fuente</th>
                </tr>
              </thead>
              <tbody>
                {tableRows.map((point, index) => (
                  <tr key={`${point.date}-${point.pollster}-${index}`}>
                    <td>{point.pollster}</td>
                    <td>{formatDate(point.date)}</td>
                    <td>{point.approve}%</td>
                    <td>{point.disapprove}%</td>
                    <td>{point.residual}%</td>
                    <td>
                      <a href={point.sourceUrl} target="_blank" rel="noopener noreferrer">
                        Ver fuente
                      </a>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="approval-summary-empty">Sin encuestas para esta selección.</p>
        )}
      </section>

      <section className="method-note">
        <p className="eyebrow">Corte y metodología</p>
        <div className="method-body">
          <p>
            Encuestas de aprobación presidencial de {data.presidents.length} sexenios, corte al{" "}
            <strong>{data.sourceThrough}</strong>. El eje horizontal es el número de meses desde la
            toma de posesión de cada presidente, no la fecha calendario, para poder comparar
            sexenios en el mismo punto de su mandato. Cada sexenio se traza con la mediana mensual
            de las encuestas disponibles; el filtro de encuestadora recalcula esa mediana solo con
            las encuestas de la casa elegida.
          </p>
        </div>
      </section>

      <SiteFooter note="Aprobación presidencial" />
    </main>
  );
}
