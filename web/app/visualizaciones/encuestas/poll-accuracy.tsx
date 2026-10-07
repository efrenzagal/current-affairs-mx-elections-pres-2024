"use client";

import { useMemo, useRef, useState } from "react";

import {
  ALL_FIRMS,
  type AccuracyData,
  BLOC_COLORS,
  BLOC_LABELS,
  formatDay,
  pct,
  signed,
  useWidth,
} from "./poll-data";

// The accuracy views mirror vote_intention/analysis/pollster_accuracy.R; the
// numbers come precomputed from scripts/export_vote_intention.py.

const RANKED_BLOCS = ["Izquierda", "PAN", "PRI"];

function ci(se: number | null | undefined) {
  return se == null ? null : 1.96 * se;
}

function Scorecard({ data, firm, setFirm }: { data: AccuracyData; firm: string; setFirm: (firm: string) => void }) {
  const rows = data.scorecard;
  const max = Math.max(...rows.map((row) => row.mae + (ci(row.maeSe) ?? 0)));
  const scale = (value: number) => `${Math.max(0, Math.min(100, (value / max) * 100))}%`;

  return (
    <article className="electoral-panel polls-accuracy-card">
      <header className="polls-card-header">
        <p className="eyebrow">1 · Precisión por casa</p>
        <h3>¿Quién se ha equivocado menos?</h3>
        <p>
          Error absoluto medio por opción en las encuestas publicadas a {data.windowDays} días o menos
          de la elección, contra el porcentaje de la votación válida. Cada elección pesa lo mismo, para
          que una casa que publicó cada semana no cuente más. Solo casas con {data.minCycles} o más
          elecciones. Toca una fila para seguir a la casa en toda la página.
        </p>
      </header>
      <div className="dict-table-wrap polls-table-wrap">
        <table className="polls-table polls-scorecard">
          <thead>
            <tr>
              <th className="polls-sticky">Encuestadora</th>
              <th className="polls-bar-head">Error absoluto medio (pts, IC 95%)</th>
              <th className="num" title="Ventaja de la encuesta menos ventaja real del primer lugar. Positivo: exageró la ventaja del ganador.">Sesgo en la ventaja</th>
              <th className="num" title="Error cuadrático medio en la ventaja frente al que produciría solo el muestreo aleatorio con la muestra reportada.">Error en la ventaja vs. muestreo</th>
              <th className="num">Ganador correcto</th>
              <th className="num">Encuestas</th>
              <th>Elecciones</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const half = ci(row.maeSe);
              return (
                <tr
                  key={row.firm}
                  className={`polls-clickable ${firm === row.firm ? "polls-row-active" : ""}`}
                  onClick={() => setFirm(firm === row.firm ? ALL_FIRMS : row.firm)}
                >
                  <td className="polls-sticky"><strong>{row.firm}</strong></td>
                  <td>
                    <div className="polls-bar" aria-label={`${row.mae.toFixed(1)} puntos`}>
                      {half != null && (
                        <span className="polls-bar-ci" style={{ left: scale(row.mae - half), right: `calc(100% - ${scale(row.mae + half)})` }} />
                      )}
                      <span className="polls-bar-dot" style={{ left: scale(row.mae) }} />
                      <em>{row.mae.toFixed(1)}</em>
                    </div>
                  </td>
                  <td className="num">
                    {signed(row.leadBias)}
                    {ci(row.leadBiasSe) != null && <small>± {ci(row.leadBiasSe)!.toFixed(1)}</small>}
                  </td>
                  <td className="num">
                    {row.leadRmse.toFixed(1)}
                    {row.samplingSe != null && <small>esperado {row.samplingSe.toFixed(1)}</small>}
                  </td>
                  <td className="num">{(row.calledWinner * 100).toFixed(0)}%</td>
                  <td className="num">{row.nPolls}</td>
                  <td className="polls-years">{row.cycles.join(", ")}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </article>
  );
}

function IndustryError({ data }: { data: AccuracyData }) {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const IND_WIDTH = Math.max(300, useWidth(wrapRef, 860));
  const narrow = IND_WIDTH < 600;
  const IND_HEIGHT = narrow ? 260 : 300;
  const IND_MARGIN = { top: 16, right: 8, bottom: 44, left: 34 };
  const cycles = data.cycles;
  const rows = data.industry.filter((row) => RANKED_BLOCS.includes(row.bloc));
  const extent = Math.max(4, ...rows.map((row) => Math.abs(row.error) + (ci(row.se) ?? 0)));
  const limit = Math.ceil(extent / 2) * 2;
  const plotWidth = IND_WIDTH - IND_MARGIN.left - IND_MARGIN.right;
  const plotHeight = IND_HEIGHT - IND_MARGIN.top - IND_MARGIN.bottom;
  const band = plotWidth / cycles.length;
  const bar = Math.min(18, (band * 0.8) / RANKED_BLOCS.length);
  const y = (value: number) => IND_MARGIN.top + ((limit - value) / (2 * limit)) * plotHeight;
  const ticks = [-limit, -limit / 2, 0, limit / 2, limit];

  return (
    <article className="electoral-panel polls-accuracy-card">
      <header className="polls-card-header">
        <p className="eyebrow">2 · Error de toda la industria</p>
        <h3>Cuando todas fallan hacia el mismo lado</h3>
        <p>
          Promedio entre casas del error de sus últimas encuestas, por bloque. Arriba de cero, las
          encuestas le dieron más de lo que obtuvo; abajo, menos. Las barras finas son el intervalo
          de 95% entre casas.
        </p>
      </header>
      <div className="approval-line-legend">
        {RANKED_BLOCS.map((bloc) => (
          <span key={bloc} className="approval-line-legend-item">
            <i className="approval-line-swatch" style={{ background: BLOC_COLORS[bloc], height: 8, width: 12 }} />
            {BLOC_LABELS[bloc]}
          </span>
        ))}
      </div>
      <div className="approval-chart-wrap" ref={wrapRef}>
        <svg className="approval-chart" viewBox={`0 0 ${IND_WIDTH} ${IND_HEIGHT}`} role="img" aria-label="Error promedio de las encuestadoras por elección y bloque">
          {ticks.map((tick) => (
            <g key={tick}>
              <line x1={IND_MARGIN.left} x2={IND_WIDTH - IND_MARGIN.right} y1={y(tick)} y2={y(tick)} className={tick === 0 ? "polls-zero-line" : "approval-gridline"} />
              <text x={IND_MARGIN.left - 8} y={y(tick) + 3} className="approval-axis-label" textAnchor="end">{signed(tick, 0)}</text>
            </g>
          ))}
          {cycles.map((cycle, index) => {
            const center = IND_MARGIN.left + band * (index + 0.5);
            const [kind, year] = cycle.label.split(" ");
            return (
              <g key={cycle.id}>
                <text x={center} y={IND_HEIGHT - IND_MARGIN.bottom + 16} className="approval-axis-label" textAnchor="middle">{year}</text>
                <text x={center} y={IND_HEIGHT - IND_MARGIN.bottom + 29} className="approval-axis-label polls-axis-sub" textAnchor="middle">{kind === "Presidencial" ? (narrow ? "Pres." : "Presidencial") : narrow ? "Dip." : "Diputados"}</text>
                {RANKED_BLOCS.map((bloc, blocIndex) => {
                  const row = rows.find((candidate) => candidate.cycle === cycle.id && candidate.bloc === bloc);
                  if (!row) return null;
                  const left = center + (blocIndex - (RANKED_BLOCS.length - 1) / 2) * bar - bar / 2;
                  const half = ci(row.se);
                  return (
                    <g key={bloc}>
                      <rect
                        x={left + 1}
                        width={bar - 2}
                        y={Math.min(y(row.error), y(0))}
                        height={Math.max(1, Math.abs(y(row.error) - y(0)))}
                        fill={BLOC_COLORS[bloc]}
                        opacity={0.85}
                      >
                        <title>
                          {`${cycle.label} · ${BLOC_LABELS[bloc]}: ${signed(row.error)} pts` +
                            (half != null ? ` (± ${half.toFixed(1)})` : "") +
                            ` · ${row.nFirms} casas · resultado ${row.result.toFixed(1)}%`}
                        </title>
                      </rect>
                      {half != null && (
                        <line x1={left + bar / 2} x2={left + bar / 2} y1={y(row.error - half)} y2={y(row.error + half)} className="polls-whisker" />
                      )}
                    </g>
                  );
                })}
              </g>
            );
          })}
        </svg>
      </div>
    </article>
  );
}

function effectColor(value: number, limit: number) {
  // Same diverging scale as the R heatmap: red under, blue over, white at zero
  const t = Math.max(-1, Math.min(1, value / limit));
  const [r, g, b] = t >= 0 ? [33, 102, 172] : [178, 24, 43];
  const mix = (channel: number) => Math.round(255 + (channel - 255) * Math.abs(t));
  return `rgb(${mix(r)}, ${mix(g)}, ${mix(b)})`;
}

function HouseEffects({ data, firm, setFirm }: { data: AccuracyData; firm: string; setFirm: (firm: string) => void }) {
  const byFirm = useMemo(() => {
    const map = new Map<string, Map<string, AccuracyData["houseEffects"][number]>>();
    for (const row of data.houseEffects) {
      if (!map.has(row.firm)) map.set(row.firm, new Map());
      map.get(row.firm)!.set(row.bloc, row);
    }
    return map;
  }, [data]);
  const firms = [...byFirm.keys()].sort(
    (a, b) => (byFirm.get(b)?.get("Izquierda")?.effect ?? 0) - (byFirm.get(a)?.get("Izquierda")?.effect ?? 0),
  );
  const limit = Math.max(...data.houseEffects.map((row) => Math.abs(row.effect)));

  return (
    <article className="electoral-panel polls-accuracy-card">
      <header className="polls-card-header">
        <p className="eyebrow">3 · Efecto casa</p>
        <h3>¿Hacia dónde se inclina cada casa?</h3>
        <p>
          El error de la casa menos el error promedio de la industria en la misma elección, promediado
          entre elecciones: lo que se debe a la casa y no al año. Azul, le da más al bloque que el
          resto de las casas; rojo, menos. Ordenado por el efecto sobre la izquierda (Cárdenas, PRD,
          López Obrador, MORENA, Sheinbaum).
        </p>
      </header>
      <div className="dict-table-wrap polls-table-wrap">
        <table className="polls-table polls-heatmap">
          <thead>
            <tr>
              <th className="polls-sticky">Encuestadora</th>
              {RANKED_BLOCS.map((bloc) => <th key={bloc} className="num">{BLOC_LABELS[bloc]}</th>)}
            </tr>
          </thead>
          <tbody>
            {firms.map((name) => (
              <tr
                key={name}
                className={`polls-clickable ${firm === name ? "polls-row-active" : ""}`}
                onClick={() => setFirm(firm === name ? ALL_FIRMS : name)}
              >
                <td className="polls-sticky"><strong>{name}</strong></td>
                {RANKED_BLOCS.map((bloc) => {
                  const row = byFirm.get(name)?.get(bloc);
                  if (!row) return <td key={bloc} className="num polls-heat-empty">—</td>;
                  const half = ci(row.se);
                  return (
                    <td
                      key={bloc}
                      className="num polls-heat"
                      style={{ background: effectColor(row.effect, limit), color: Math.abs(row.effect) > limit * 0.6 ? "#fff" : undefined }}
                      title={`${name} · ${BLOC_LABELS[bloc]}: ${signed(row.effect)} pts${half != null ? ` ± ${half.toFixed(1)}` : ""} · error bruto ${signed(row.rawBias)} · ${row.nCycles} elecciones`}
                    >
                      {signed(row.effect)}
                      <small>{row.nCycles} elec.</small>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </article>
  );
}

function LastPolls({ data, firm, setFirm }: { data: AccuracyData; firm: string; setFirm: (firm: string) => void }) {
  const cycles = [...data.cycles].reverse();
  const [cycleId, setCycleId] = useState(cycles[0]?.id);
  const cycle = data.cycles.find((candidate) => candidate.id === cycleId)!;
  const detail = data.lastPolls[cycleId];
  const winner = detail.blocs[0]?.bloc;
  const polls = [...detail.polls].sort(
    (a, b) => (b.values[winner]?.[0] ?? -1) - (a.values[winner]?.[0] ?? -1) || a.pollster.localeCompare(b.pollster, "es"),
  );
  const domains = new Map(
    detail.blocs.map(({ bloc, result }) => {
      const values = polls.flatMap((poll) => (poll.values[bloc] ? [poll.values[bloc][0], poll.values[bloc][1]] : [])).concat(result);
      return [bloc, [Math.min(...values) - 1.5, Math.max(...values) + 1.5] as const];
    }),
  );

  return (
    <article className="electoral-panel polls-accuracy-card">
      <header className="polls-card-header">
        <p className="eyebrow">4 · Última encuesta de cada casa</p>
        <h3>{cycle.label}: la última palabra contra el resultado</h3>
        <p>
          La última encuesta que publicó cada casa en el año previo a la elección, por bloque. La línea
          vertical es el resultado y el punto, la encuesta; las filas tenues se publicaron más de{" "}
          {data.windowDays} días antes y no cuentan en la precisión.
        </p>
      </header>
      <div className="polls-cycle-buttons party-filter" role="group" aria-label="Elección">
        {cycles.map((option) => (
          <button key={option.id} type="button" className={option.id === cycleId ? "active" : ""} onClick={() => setCycleId(option.id)}>
            {option.label}
          </button>
        ))}
      </div>
      <div className="dict-table-wrap polls-table-wrap">
        <table className="polls-table polls-last">
          <thead>
            <tr>
              <th className="polls-sticky">Encuestadora</th>
              {detail.blocs.map(({ bloc, result, nominee }) => (
                <th key={bloc}>
                  <i className="polls-swatch" style={{ background: BLOC_COLORS[bloc] }} />
                  {nominee ?? BLOC_LABELS[bloc]}
                  <small>resultado {pct(result)}</small>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {polls.map((poll) => (
              <tr
                key={`${poll.pollster}-${poll.date}`}
                className={`polls-clickable ${poll.final ? "" : "polls-row-early"} ${firm === poll.firm ? "polls-row-active" : ""}`}
                onClick={() => setFirm(firm === poll.firm ? ALL_FIRMS : poll.firm)}
              >
                <td className="polls-sticky">
                  <strong>{poll.pollster}</strong>
                  <small>{formatDay(poll.date)} · {poll.daysBefore} días antes</small>
                </td>
                {detail.blocs.map(({ bloc }) => {
                  const value = poll.values[bloc];
                  if (!value) return <td key={bloc} className="polls-heat-empty">—</td>;
                  const [estimate, result] = value;
                  const [low, high] = domains.get(bloc)!;
                  const at = (v: number) => ((v - low) / (high - low)) * 100;
                  return (
                    <td key={bloc} className="polls-strip-cell">
                      <svg className="polls-strip" viewBox="0 0 100 14" preserveAspectRatio="none" aria-hidden="true">
                        <line x1={at(result)} x2={at(result)} y1={0} y2={14} className="polls-strip-result" />
                        <line x1={at(result)} x2={at(estimate)} y1={7} y2={7} className="polls-strip-gap" />
                      </svg>
                      <span className="polls-strip-dot" style={{ left: `${at(estimate)}%`, background: BLOC_COLORS[bloc] }} />
                      <span className="polls-strip-text">
                        {estimate.toFixed(1)} <small>({signed(estimate - result)})</small>
                      </span>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </article>
  );
}

export default function PollAccuracy({
  data,
  firm,
  setFirm,
}: {
  data: AccuracyData;
  firm: string;
  setFirm: (firm: string) => void;
}) {
  const first = data.cycles[0]?.year;
  const last = data.cycles.at(-1)?.year;
  return (
    <section className="polls-accuracy" id="precision">
      <header className="polls-section-header">
        <div>
          <p className="eyebrow">Precisión histórica · {first}–{last}</p>
          <h2>¿Qué tan cerca quedaron las encuestas?</h2>
        </div>
        <p>
          Cada encuesta, en preferencia efectiva, contra el porcentaje de la votación válida de su
          elección federal. Los votos de coalición marcados para varios partidos se reparten por partes
          iguales, como hace el INE.
          {firm !== ALL_FIRMS && <> Destacando <strong>{firm}</strong>.</>}
        </p>
      </header>
      <Scorecard data={data} firm={firm} setFirm={setFirm} />
      <IndustryError data={data} />
      <HouseEffects data={data} firm={firm} setFirm={setFirm} />
      <LastPolls data={data} firm={firm} setFirm={setFirm} />
    </section>
  );
}
