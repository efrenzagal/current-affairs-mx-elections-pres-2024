"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { SiteFooter, SiteHeader } from "../../site-chrome";
import PollAccuracy from "./poll-accuracy";
import {
  ALL_FIRMS,
  type AccuracyData,
  type Cycle,
  type Poll,
  type PollData,
  dateFromDay,
  dayNumber,
  formatDay,
  formatFieldwork,
  formatMonth,
  optionColor,
  pct,
  shortLabel,
  useWidth,
} from "./poll-data";

// The trend is a plain mean of each party's effective share over the polls
// whose fieldwork ended in the trailing window, stepped weekly. 90 days is
// wide enough that the sparse 2025 stretch (a poll a month) still averages
// more than one house; a party needs TREND_MIN_POLLS polls in the window or
// its line breaks rather than tracing a single poll.
const TREND_WINDOW_DAYS = 90;
const TREND_MIN_POLLS = 2;
const TREND_STEP_DAYS = 7;

// INE calendar for the 2026-2027 federal process.
const CALENDAR_2027 = [
  { date: "2027-01-04", label: "Precampañas", short: "Precamp." },
  { date: "2027-04-04", label: "Campañas", short: "Camp." },
];

type TrendPoint = { day: number; value: number; polls: number };

function rollingAverage(polls: Poll[], party: string): TrendPoint[][] {
  const points = polls.flatMap((poll) => {
    const option = poll.options.find(([label]) => label === party);
    return option && option[2] != null ? [{ day: dayNumber(poll.date), value: option[2] }] : [];
  });
  if (points.length === 0) return [];
  const days = points.map((point) => point.day);
  const first = Math.min(...days);
  const last = Math.max(...days);
  const grid: number[] = [];
  for (let day = first; day < last; day += TREND_STEP_DAYS) grid.push(day);
  grid.push(last);

  const segments: TrendPoint[][] = [];
  let current: TrendPoint[] = [];
  for (const day of grid) {
    const inWindow = points.filter((point) => point.day > day - TREND_WINDOW_DAYS && point.day <= day);
    if (inWindow.length >= TREND_MIN_POLLS) {
      const value = inWindow.reduce((sum, point) => sum + point.value, 0) / inWindow.length;
      current.push({ day, value, polls: inWindow.length });
    } else if (current.length) {
      segments.push(current);
      current = [];
    }
  }
  if (current.length) segments.push(current);
  return segments;
}

type Hover = { x: number; y: number; poll: Poll; party: string; raw: number | null; eff: number };

function Tracker({
  cycle,
  polls,
  firm,
  setFirm,
}: {
  cycle: Cycle;
  polls: Poll[];
  firm: string;
  setFirm: (firm: string) => void;
}) {
  const [hover, setHover] = useState<Hover | null>(null);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const parties = cycle.columns;

  // Drawn at the container's own width: a phone gets a shorter chart with
  // half as many month ticks and staggered calendar labels, not a shrunken one
  const WIDTH = Math.max(300, useWidth(wrapRef, 860));
  const narrow = WIDTH < 600;
  const HEIGHT = narrow ? 360 : Math.round(Math.min(520, Math.max(380, WIDTH * 0.42)));
  const MARGIN = { top: narrow ? 46 : 30, right: narrow ? 72 : 86, bottom: 34, left: 34 };
  const PLOT_WIDTH = WIDTH - MARGIN.left - MARGIN.right;
  const PLOT_HEIGHT = HEIGHT - MARGIN.top - MARGIN.bottom;

  const trends = useMemo(
    () => new Map(parties.map((party) => [party, rollingAverage(polls, party)])),
    [parties, polls],
  );

  const firms = useMemo(() => {
    const counts = new Map<string, number>();
    for (const poll of polls) counts.set(poll.firm, (counts.get(poll.firm) ?? 0) + 1);
    return [...counts.entries()].sort((a, b) => a[0].localeCompare(b[0], "es"));
  }, [polls]);

  const latestDay = Math.max(...polls.map((poll) => dayNumber(poll.date)));
  const latest = parties
    .map((party) => {
      const segment = trends.get(party)?.at(-1);
      const point = segment?.at(-1);
      return point && point.day === latestDay ? { party, ...point } : null;
    })
    .filter((row): row is { party: string } & TrendPoint => row !== null)
    .sort((a, b) => b.value - a.value);

  const startDay = Math.min(...polls.map((poll) => dayNumber(poll.date))) - 20;
  const endDay = dayNumber(cycle.date) + 12;
  const maxShare = Math.max(
    50,
    ...polls.flatMap((poll) => poll.options.map((option) => option[2] ?? 0)),
  );
  const yMax = Math.ceil(maxShare / 10) * 10;
  const x = (day: number) => MARGIN.left + ((day - startDay) / (endDay - startDay)) * PLOT_WIDTH;
  const y = (value: number) => MARGIN.top + (1 - value / yMax) * PLOT_HEIGHT;
  const yTicks = Array.from({ length: yMax / 10 + 1 }, (_, index) => index * 10);

  // A tick on the first of January, April, July and October (January and July
  // on a phone)
  const xTicks: string[] = [];
  {
    const step = narrow ? 6 : 3;
    const start = new Date(startDay * 86_400_000);
    let year = start.getUTCFullYear();
    let month = Math.ceil(start.getUTCMonth() / step) * step + 1;
    for (;;) {
      if (month > 12) { month -= 12; year += 1; }
      const date = `${year}-${String(month).padStart(2, "0")}-01`;
      if (dayNumber(date) > endDay) break;
      if (dayNumber(date) >= startDay) xTicks.push(date);
      month += step;
    }
  }

  // Calendar marks in date order; on a phone their labels alternate between
  // two rows, since the last nine months of the axis are only ~100px wide
  const marks = [
    { day: latestDay, label: narrow ? "Última" : "Última encuesta", latest: true },
    ...CALENDAR_2027.map((event) => ({ day: dayNumber(event.date), label: narrow ? event.short : event.label, latest: false })),
    { day: dayNumber(cycle.date), label: "Elección", latest: false },
  ].sort((a, b) => a.day - b.day);

  // End-of-line labels, nudged apart so small parties stacked near zero stay legible
  const endLabels = (() => {
    const rows = latest.map((row) => ({ party: row.party, value: row.value, at: y(row.value) }));
    rows.sort((a, b) => a.at - b.at);
    for (let index = 1; index < rows.length; index += 1) {
      rows[index].at = Math.max(rows[index].at, rows[index - 1].at + 12);
    }
    const overflow = rows.length ? rows[rows.length - 1].at - (MARGIN.top + PLOT_HEIGHT) : 0;
    if (overflow > 0) rows.forEach((row) => { row.at -= overflow; });
    return rows;
  })();

  function showHover(event: React.MouseEvent, value: Omit<Hover, "x" | "y">) {
    const rect = wrapRef.current?.getBoundingClientRect();
    if (!rect) return;
    setHover({ x: event.clientX - rect.left, y: event.clientY - rect.top, ...value });
  }

  const linePath = (segment: TrendPoint[]) =>
    segment.map((point, index) => `${index ? "L" : "M"}${x(point.day).toFixed(1)},${y(point.value).toFixed(1)}`).join("");

  const latestDate = dateFromDay(latestDay);

  return (
    <section className="electoral-panel polls-tracker-panel">
      <header>
        <div>
          <p className="eyebrow">Cámara de Diputados · elección del {formatDay(cycle.date)}</p>
          <h2>Intención de voto rumbo a 2027</h2>
        </div>
        <p>
          Cada punto es una encuesta, en <strong>preferencia efectiva</strong>: sin indecisos ni
          quien no responde. La línea es el promedio de las encuestas de los {TREND_WINDOW_DAYS} días
          previos.
        </p>
      </header>

      <div className="polls-latest" aria-label={`Promedio de ${TREND_WINDOW_DAYS} días al ${formatDay(latestDate)}`}>
        <span className="polls-latest-label">Promedio al {formatDay(latestDate)}</span>
        <div>
          {latest.map((row) => (
            <span key={row.party} className="polls-latest-item">
              <i style={{ background: optionColor(row.party) }} />
              {row.party}
              <strong>{row.value.toFixed(1)}%</strong>
            </span>
          ))}
        </div>
      </div>

      <div className="approval-chart-wrap" ref={wrapRef}>
        <svg
          className="approval-chart"
          viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
          role="img"
          aria-label="Intención de voto por partido para la elección de diputados federales de 2027, encuesta por encuesta, con su promedio móvil"
        >
          {yTicks.map((tick) => (
            <g key={tick}>
              <line x1={MARGIN.left} x2={WIDTH - MARGIN.right} y1={y(tick)} y2={y(tick)} className="approval-gridline" />
              <text x={MARGIN.left - 8} y={y(tick) + 3} className="approval-axis-label" textAnchor="end">{tick}%</text>
            </g>
          ))}
          {xTicks.map((date) => (
            <text key={date} x={x(dayNumber(date))} y={HEIGHT - MARGIN.bottom + 18} className="approval-axis-label" textAnchor="middle">
              {formatMonth(date)}
            </text>
          ))}

          {marks.map((mark, index) => {
            const labelY = MARGIN.top - 12 - (narrow && index % 2 ? 14 : 0);
            // On a phone the outer labels lean outward so the inner ones fit
            const anchor = !narrow ? "middle" : index === 0 ? "end" : index === marks.length - 1 ? "start" : "middle";
            return (
              <g key={mark.label} className={`polls-calendar-mark ${mark.latest ? "polls-latest-mark" : ""}`}>
                <line x1={x(mark.day)} x2={x(mark.day)} y1={labelY + 5} y2={MARGIN.top + PLOT_HEIGHT} />
                <text x={x(mark.day) + (anchor === "start" ? -4 : anchor === "end" ? 4 : 0)} y={labelY} textAnchor={anchor}>{mark.label}</text>
              </g>
            );
          })}

          {polls.map((poll) =>
            poll.options.map(([party, raw, eff]) => {
              if (eff == null) return null;
              const muted = firm !== ALL_FIRMS && poll.firm !== firm;
              const active = hover?.poll === poll && hover.party === party;
              return (
                <circle
                  key={`${poll.id}-${party}`}
                  cx={x(dayNumber(poll.date))}
                  cy={y(eff)}
                  r={active ? 6 : firm !== ALL_FIRMS && !muted ? 4.5 : 3.2}
                  className="polls-dot"
                  style={{ fill: optionColor(party), opacity: muted ? 0.1 : firm !== ALL_FIRMS ? 0.95 : 0.45 }}
                  aria-label={`${poll.pollster} · ${formatFieldwork(poll)} · ${party} ${eff.toFixed(1)}%`}
                  onMouseEnter={(event) => showHover(event, { poll, party, raw, eff })}
                  onMouseMove={(event) => showHover(event, { poll, party, raw, eff })}
                  onMouseLeave={() => setHover(null)}
                />
              );
            }),
          )}

          {parties.map((party) =>
            (trends.get(party) ?? []).map((segment, index) =>
              segment.length > 1 ? (
                <path key={`${party}-${index}`} d={linePath(segment)} className="approval-line-highlight polls-trend" style={{ stroke: optionColor(party) }} />
              ) : null,
            ),
          )}

          {endLabels.map((row) => (
            <text key={row.party} x={x(latestDay) + 8} y={row.at + 3.5} className="polls-end-label" style={{ fill: optionColor(row.party) }}>
              {row.party} {row.value.toFixed(1)}
            </text>
          ))}
        </svg>
        {hover && (
          <div className="approval-tooltip" style={{ left: hover.x, top: hover.y }}>
            <strong>{hover.party} · {hover.eff.toFixed(1)}% efectiva</strong>
            <span>Publicada: {pct(hover.raw)}</span>
            <span>{hover.poll.pollster} · {formatFieldwork(hover.poll)}</span>
          </div>
        )}
      </div>

      <div className="approval-control-group approval-pollster-filter">
        <span>Encuestadora</span>
        <div className="party-filter" role="group" aria-label="Destacar una casa encuestadora">
          <button type="button" className={firm === ALL_FIRMS ? "active" : ""} onClick={() => setFirm(ALL_FIRMS)}>
            Todas
          </button>
          {firms.map(([name, count]) => (
            <button key={name} type="button" className={firm === name ? "active" : ""} onClick={() => setFirm(firm === name ? ALL_FIRMS : name)}>
              {name} <span className="polls-count">{count}</span>
            </button>
          ))}
        </div>
      </div>
    </section>
  );
}

function sourceHost(url: string) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return "fuente";
  }
}

// A presidential year mixes questions: by candidate, by coalition and, as a
// party-ID question, by party.
const LEVEL_LABELS: Record<string, string> = { candidato: "candidato", coalicion: "coalición", partido: "partido" };

const BASIS_NOTES: Record<string, string> = {
  bruta: "",
  efectiva: "La casa publicó la preferencia ya sin indecisos.",
  incompleta: "Las cifras publicadas no suman 100: faltan categorías.",
  inconsistente: "Las cifras no cuadran bajo ninguna lectura; fuera del análisis de precisión.",
};

function PollTable({
  data,
  cycleId,
  setCycleId,
  firm,
  setFirm,
}: {
  data: PollData;
  cycleId: string;
  setCycleId: (cycle: string) => void;
  firm: string;
  setFirm: (firm: string) => void;
}) {
  const cycle = data.cycles.find((candidate) => candidate.id === cycleId)!;
  const cyclePolls = useMemo(() => data.polls.filter((poll) => poll.cycle === cycleId), [data, cycleId]);
  const firms = useMemo(() => {
    const names = new Set(cyclePolls.map((poll) => poll.firm));
    if (firm !== ALL_FIRMS) names.add(firm);
    return [...names].sort((a, b) => a.localeCompare(b, "es"));
  }, [cyclePolls, firm]);
  const rows = firm === ALL_FIRMS ? cyclePolls : cyclePolls.filter((poll) => poll.firm === firm);
  const columns = cycle.columns;

  return (
    <section className="polls-table-section" id="tabla">
      <header className="polls-section-header">
        <div>
          <p className="eyebrow">Encuestas y fuentes</p>
          <h2>Cada encuesta, con su fuente</h2>
        </div>
        <p>
          En cada celda, la cifra grande es la preferencia efectiva y la pequeña la que publicó la casa.
          «Encuesta» abre el documento o la nota original; «Wikipedia», la revisión de la página de la
          que se tomó la fila.
        </p>
      </header>

      <div className="polls-table-controls">
        <label>
          <span>Elección</span>
          <select value={cycleId} onChange={(event) => setCycleId(event.target.value)}>
            {data.cycles.map((option) => (
              <option key={option.id} value={option.id}>{option.label}</option>
            ))}
          </select>
        </label>
        <label>
          <span>Encuestadora</span>
          <select value={firm} onChange={(event) => setFirm(event.target.value)}>
            <option value={ALL_FIRMS}>Todas</option>
            {firms.map((name) => (
              <option key={name} value={name}>{name}</option>
            ))}
          </select>
        </label>
        <p className="polls-table-count">
          {rows.length} {rows.length === 1 ? "encuesta" : "encuestas"}
          {firm === ALL_FIRMS ? ` · ${new Set(cyclePolls.map((poll) => poll.firm)).size} casas` : ""}
        </p>
      </div>

      {rows.length === 0 ? (
        <p className="approval-summary-empty">Sin encuestas de {firm} para {cycle.label}.</p>
      ) : (
        <div className="dict-table-wrap polls-table-wrap">
          <table className="polls-table">
            <thead>
              <tr>
                <th className="polls-sticky">Encuesta</th>
                <th className="num">Muestra</th>
                {columns.map((label) => (
                  <th key={label} className="num" title={label}>
                    <i className="polls-swatch" style={{ background: optionColor(label) }} />
                    {shortLabel(label)}
                  </th>
                ))}
                <th>Otras opciones</th>
                <th className="num">Indecisos</th>
                <th className="num">Ninguno</th>
                <th>Fuentes</th>
              </tr>
            </thead>
            <tbody>
              {cycle.results && (
                <tr className="polls-result-row">
                  <td className="polls-sticky">
                    <strong>Resultado oficial</strong>
                    <small>{formatDay(cycle.date)} · % de la votación válida</small>
                  </td>
                  <td />
                  {columns.map((label) => (
                    <td key={label} className="num"><strong>{pct(cycle.results?.[label])}</strong></td>
                  ))}
                  <td colSpan={4} />
                  <td />
                </tr>
              )}
              {rows.map((poll) => {
                const byLabel = new Map(poll.options.map((option) => [option[0], option]));
                const rest = poll.options.filter(([label]) => !columns.includes(label));
                return (
                  <tr key={poll.id} className={poll.basis === "inconsistente" ? "polls-row-flag" : undefined}>
                    <td className="polls-sticky">
                      <strong>{poll.pollster}</strong>
                      <small className="polls-date">{formatFieldwork(poll)}</small>
                      {poll.client && <small>para {poll.client}</small>}
                      {cycle.id.startsWith("PRE") && <small>pregunta por {LEVEL_LABELS[poll.level] ?? poll.level}</small>}
                    </td>
                    <td className="num">{poll.n ? poll.n.toLocaleString("es-MX") : "—"}</td>
                    {columns.map((label) => {
                      const option = byLabel.get(label);
                      return (
                        <td key={label} className="num">
                          {option && option[2] != null ? (
                            <>
                              <strong>{option[2].toFixed(1)}</strong>
                              <small>{pct(option[1])}</small>
                            </>
                          ) : "—"}
                        </td>
                      );
                    })}
                    <td className="polls-rest">
                      {rest.length ? rest.map(([label, , eff]) => `${shortLabel(label)} ${eff == null ? "—" : eff.toFixed(1)}`).join(" · ") : ""}
                      {poll.others != null && <small>Otros {pct(poll.others)}</small>}
                    </td>
                    <td className="num">{pct(poll.undecided)}</td>
                    <td className="num">{pct(poll.none)}</td>
                    <td className="polls-links">
                      {poll.url && (
                        <a href={poll.url} target="_blank" rel="noopener noreferrer" title={poll.url}>
                          Encuesta ↗<small>{sourceHost(poll.url)}</small>
                        </a>
                      )}
                      {poll.wiki && (
                        <a href={poll.wiki} target="_blank" rel="noopener noreferrer" title={poll.wiki}>
                          Wikipedia {poll.wiki.includes("//es.") ? "ES" : "EN"} ↗
                        </a>
                      )}
                      {BASIS_NOTES[poll.basis] && <small className="polls-basis" title={BASIS_NOTES[poll.basis]}>{poll.basis}</small>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export default function PollExplorer() {
  const [data, setData] = useState<PollData | null>(null);
  const [accuracy, setAccuracy] = useState<AccuracyData | null>(null);
  const [error, setError] = useState(false);
  const [firm, setFirm] = useState(ALL_FIRMS);
  const [tableCycle, setTableCycle] = useState<string | null>(null);

  useEffect(() => {
    fetch("/data/vote-intention.json")
      .then((response) => (response.ok ? response.json() : Promise.reject()))
      .then((payload: PollData) => {
        setData(payload);
        setTableCycle(payload.current);
        // ?casa= opens the page following one firm, so a firm's view can be shared
        const shared = new URLSearchParams(window.location.search).get("casa");
        if (shared && payload.polls.some((poll) => poll.firm === shared)) setFirm(shared);
      })
      .catch(() => setError(true));
    fetch("/data/pollster-accuracy.json")
      .then((response) => (response.ok ? response.json() : Promise.reject()))
      .then((payload: AccuracyData) => setAccuracy(payload))
      .catch(() => setAccuracy(null));
  }, []);

  useEffect(() => {
    if (!data) return;
    const url = new URL(window.location.href);
    if (firm === ALL_FIRMS) url.searchParams.delete("casa");
    else url.searchParams.set("casa", firm);
    window.history.replaceState(null, "", url);
  }, [data, firm]);

  const currentCycle = data?.cycles.find((cycle) => cycle.id === data.current);
  const currentPolls = useMemo(
    () => (data ? data.polls.filter((poll) => poll.cycle === data.current) : []),
    [data],
  );

  if (error) return <main className="state-screen"><h1>No pudimos cargar las encuestas.</h1></main>;
  if (!data || !currentCycle || !tableCycle) {
    return (
      <main className="state-screen loading-state" aria-live="polite">
        <span className="loading-mark" />
        <p className="eyebrow">Visualizaciones</p>
        <h1>Preparando las encuestas…</h1>
      </main>
    );
  }

  const heldCycles = data.cycles.filter((cycle) => cycle.held);
  const firstYear = heldCycles.at(-1)?.date.slice(0, 4);
  const lastYear = heldCycles[0]?.date.slice(0, 4);

  return (
    <main>
      <SiteHeader active="visualizaciones" status="Encuestas electorales" />

      <section className="electoral-hero">
        <div>
          <p className="eyebrow"><a href="/visualizaciones">Visualizaciones</a> · Elecciones</p>
          <h1>Encuestas electorales.</h1>
        </div>
        <p className="hero-copy">
          Cómo van los partidos rumbo a la elección de diputados de 2027, encuesta por encuesta y con
          la fuente de cada una, y qué tan cerca quedaron las casas encuestadoras del resultado en
          las elecciones federales de {firstYear} a {lastYear}.
        </p>
      </section>

      <section className="electoral-explorer">
        <Tracker cycle={currentCycle} polls={currentPolls} firm={firm} setFirm={setFirm} />
      </section>

      <PollTable data={data} cycleId={tableCycle} setCycleId={setTableCycle} firm={firm} setFirm={setFirm} />

      {accuracy && <PollAccuracy data={accuracy} firm={firm} setFirm={setFirm} />}

      <section className="method-note">
        <p className="eyebrow">Corte y metodología</p>
        <div className="method-body">
          <p>
            Encuestas de intención de voto de las páginas de Wikipedia en español e inglés, cada una
            fijada a una revisión; para 2027, la última encuesta registrada terminó su levantamiento el{" "}
            <strong>{formatDay(data.sourceThrough)}</strong>. Cuando ambas ediciones listan la misma
            encuesta se conserva una: la inglesa para 2027, que se actualiza primero, y la española
            para los ciclos anteriores. Solo se incluyen encuestas, no sondeos en redes, agregadores ni
            simulacros.
          </p>
          <p>
            La preferencia efectiva reparte a los indecisos y a quien no responde en proporción a las
            demás opciones, para comparar casas que los reportan distinto y para medirlas contra el
            porcentaje de la votación válida. El promedio del rastreador es la media simple de las
            encuestas de los {TREND_WINDOW_DAYS} días previos a cada punto, sin ponderar por casa ni
            por tamaño de muestra.
          </p>
        </div>
      </section>

      <SiteFooter note="Encuestas electorales" />
    </main>
  );
}
