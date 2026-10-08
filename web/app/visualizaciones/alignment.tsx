"use client";

import { type PointerEvent as ReactPointerEvent, useEffect, useMemo, useState } from "react";

import type { Chamber } from "./explorer";
import { partyColor, partyRank } from "./parties";

/**
 * Party and bloc alignment under each chamber's hemicycle: where every seat
 * sits between the two blocs, how often each pair of parties takes the same
 * position, and who most often votes against their own bench. `AlignmentCard`
 * puts the same figures under the explorer's calendar for one seat or person.
 *
 * Everything is precomputed by web/scripts/alignment.py, a port of
 * camara_de_diputados/votos/legislator_party_agreement.R; this only draws it.
 * Legislator ids are the chamber's own, so a click hands the person straight
 * to the explorer above.
 */

/**
 * `contested` keeps only roll calls where the two blocs' majorities voted
 * differently; `seated` counts absences as not agreeing (every Cámara
 * absence, and the Senado's reconstructed "Sin registro" but not its excused
 * official commissions).
 */
type Mode = "all-cast" | "contested-cast" | "all-seated" | "contested-seated";

type ExportedLegislator = {
  id: string;
  name: string;
  /** Latest bench they voted under. */
  party: string;
  votes: number;
  absences: number;
  /** Per mode: agreement with each bloc in `blocs` order, then own bench. */
  scores: Record<Mode, (number | null)[]>;
};

/** Everyone who held one seat, added together: the unit the bloc map plots. */
type ExportedSeat = {
  id: string;
  /** Whoever cast the seat's latest vote; the seat reads under their name and bench. */
  holder: string;
  holderName: string;
  party: string;
  /** People who cast at least one vote from this seat. */
  occupants: number;
  votes: number;
  absences: number;
  scores: Record<Mode, (number | null)[]>;
};

type ExportedChamber = {
  rollCalls: number;
  contestedRollCalls: number;
  /** Heatmap order: bloc by bloc. */
  parties: string[];
  /** Largest first. */
  blocs: { label: string; parties: string[] }[];
  agreement: number[][];
  pairRollCalls: number[][];
  agreementContested: number[][];
  pairRollCallsContested: number[][];
  legislators: ExportedLegislator[];
  seats: ExportedSeat[];
};

type AlignmentData = {
  manifest: { minVotes: number; modes: Mode[] };
  diputados: ExportedChamber;
  senado: ExportedChamber;
};

/** One legislator or seat as the active mode reads them. */
type AlignmentLegislator = {
  id: string;
  name: string;
  party: string;
  votes: number;
  absences: number;
  /** 1 for a person; for a seat, everyone who voted from it. */
  occupants: number;
  /** Agreement with each bloc's majority, in `blocs` order. */
  blocs: (number | null)[];
  /** Agreement with the rest of their own bench; null without a bench. */
  ownParty: number | null;
};

/** The chamber as the active mode reads it; every chart draws from this. */
type ChamberAlignment = {
  contested: boolean;
  seated: boolean;
  rollCalls: number;
  parties: string[];
  blocs: { label: string; parties: string[] }[];
  agreement: number[][];
  pairRollCalls: number[][];
  legislators: AlignmentLegislator[];
  seats: AlignmentLegislator[];
};

// One request per page view: the section unmounts while a vote is open and
// comes back when it closes.
let request: Promise<AlignmentData> | null = null;
function loadAlignment() {
  request ??= fetch("/data/alignment-66.json")
    .then((response) => {
      if (!response.ok) throw new Error(String(response.status));
      return response.json() as Promise<AlignmentData>;
    })
    .catch((error) => {
      request = null;
      throw error;
    });
  return request;
}

/** The alignment payload, or null until it arrives (or if it never does). */
export function useAlignmentData() {
  const [payload, setPayload] = useState<AlignmentData | null>(null);
  useEffect(() => {
    let cancelled = false;
    loadAlignment()
      .then((loaded) => {
        if (!cancelled) setPayload(loaded);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);
  return payload;
}

const ALL = "Todos";

/**
 * Minimum participation, as a share of the chamber's roll calls so it means
 * the same in both chambers. A suplente who covered a short licencia sits at
 * an extreme on a few dozen votes; the default leaves them out.
 */
const PARTICIPATION = [
  { share: 0, label: "20+ votos" },
  { share: 0.25, label: "¼ de las votaciones" },
  { share: 0.5, label: "½" },
] as const;
const DEFAULT_SHARE = 0.25;
const PARTY_NAMES: Record<string, string> = { SG: "Sin grupo", IND: "Independiente" };
const partyName = (party: string) => PARTY_NAMES[party] ?? party;

function pct(value: number, digits = 0) {
  return value.toLocaleString("es-MX", {
    style: "percent",
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

// Same light-to-dark blue as the R heatmap, interpolated in RGB.
const HEAT_LOW = [0xee, 0xf3, 0xf8];
const HEAT_HIGH = [0x1f, 0x4e, 0x79];
function heat(t: number) {
  const clamped = Math.min(Math.max(t, 0), 1);
  const [r, g, b] = HEAT_LOW.map((low, i) => Math.round(low + (HEAT_HIGH[i] - low) * clamped));
  return `rgb(${r}, ${g}, ${b})`;
}

type Tip = { top: number; left: number; title: string; lines: string[] };

function TipBox({ tip }: { tip: Tip | null }) {
  if (!tip) return null;
  return (
    <div className="square-tip alignment-tip" style={{ top: tip.top, left: tip.left }} role="presentation">
      <strong>{tip.title}</strong>
      {tip.lines.map((line) => (
        <span key={line}>{line}</span>
      ))}
    </div>
  );
}

function clampLeft(x: number) {
  return Math.min(Math.max(x, 140), window.innerWidth - 140);
}

// --- Bloc map ---------------------------------------------------------------

// With absences counted the axis is a different question, and says so: a dot
// near the origin is someone who rarely votes, not someone who votes against.
function axisLabel(bloc: string, seated: boolean) {
  return seated ? `Votó con ${bloc}, contando ausencias →` : `Coincide con ${bloc} →`;
}

const SIZE = 600;
const MARGIN = { top: 14, right: 30, bottom: 58, left: 66 };
const PLOT = SIZE - MARGIN.left - MARGIN.right;
// Nearest dot within this many viewBox units answers the pointer.
const HIT_RADIUS = 14;
// A bench that votes as one can put dozens of seats on the same point (68 PAN
// deputies in the bloc-disagreement view). Each dot is nudged by up to this
// share on either axis, in a direction fixed by its seat id so it never moves
// between renders. The guide states the same figure.
const JITTER_SHARE = 0.02;

/** A point in the unit disc, fixed by the id. */
function jitterOf(id: string): [number, number] {
  let hash = 2166136261;
  for (let i = 0; i < id.length; i += 1) {
    hash = Math.imul(hash ^ id.charCodeAt(i), 16777619);
  }
  const angle = ((hash >>> 0) % 3600) / 3600 * 2 * Math.PI;
  const radius = Math.sqrt(((hash >>> 12) % 1000) / 1000);
  return [Math.cos(angle) * radius, Math.sin(angle) * radius];
}

function BlocMap({
  data,
  partyFilter,
  selectedSeatId,
  onSelect,
}: {
  data: ChamberAlignment;
  partyFilter: string;
  selectedSeatId: string | null;
  onSelect: (seatId: string) => void;
}) {
  const [hoverId, setHoverId] = useState<string | null>(null);
  const [tip, setTip] = useState<Tip | null>(null);
  const [blocX, blocY] = data.blocs;

  const points = useMemo(
    () => data.seats.filter((row) => row.blocs[0] != null && row.blocs[1] != null),
    [data],
  );
  // Both axes share one domain, so the diagonal means "equally with both".
  const low = useMemo(() => {
    const min = Math.min(...points.flatMap((row) => [row.blocs[0]!, row.blocs[1]!]));
    return Math.max(0, Math.floor(min * 10) / 10);
  }, [points]);
  const scale = (value: number) => ((value - low) / (1 - low)) * PLOT;
  const jitter = useMemo(() => new Map(points.map((row) => [row.id, jitterOf(row.id)])), [points]);
  // Jitter that would cross an axis bounces back inside, so a seat at 100%
  // never draws past the edge of the chart.
  const nudge = (value: number, offset: number) => {
    const moved = value + offset * JITTER_SHARE;
    return moved > 1 ? 2 - moved : moved < low ? 2 * low - moved : moved;
  };
  const cx = (row: AlignmentLegislator) =>
    MARGIN.left + scale(nudge(row.blocs[0]!, jitter.get(row.id)![0]));
  const cy = (row: AlignmentLegislator) =>
    MARGIN.top + PLOT - scale(nudge(row.blocs[1]!, -jitter.get(row.id)![1]));
  const ticks = Array.from({ length: Math.round((1 - low) * 10) + 1 }, (_, i) => low + i / 10);

  const active = (row: AlignmentLegislator) => partyFilter === ALL || row.party === partyFilter;
  const background = points.filter((row) => !active(row));
  const foreground = points.filter(active);
  const ringed = [selectedSeatId, hoverId].filter(Boolean) as string[];

  function nearest(event: ReactPointerEvent<SVGSVGElement>) {
    const rect = event.currentTarget.getBoundingClientRect();
    const ratio = SIZE / rect.width;
    const x = (event.clientX - rect.left) * ratio;
    const y = (event.clientY - rect.top) * ratio;
    let best: AlignmentLegislator | null = null;
    let bestDistance = HIT_RADIUS;
    for (const row of foreground) {
      const distance = Math.hypot(cx(row) - x, cy(row) - y);
      if (distance < bestDistance) {
        best = row;
        bestDistance = distance;
      }
    }
    return { row: best, rect, ratio };
  }

  function show(event: ReactPointerEvent<SVGSVGElement>) {
    const { row, rect, ratio } = nearest(event);
    if (!row) {
      setHoverId(null);
      setTip(null);
      return null;
    }
    setHoverId(row.id);
    setTip({
      top: rect.top + cy(row) / ratio,
      left: clampLeft(rect.left + cx(row) / ratio),
      title: row.name,
      lines: [
        `${partyName(row.party)} · ${row.votes} votos · ${row.absences} ${row.absences === 1 ? "ausencia" : "ausencias"}`,
        ...(row.occupants > 1 ? [`Escaño con ${row.occupants} ocupantes; suma sus votos`] : []),
        `${pct(row.blocs[0]!)} con ${blocX.label}`,
        `${pct(row.blocs[1]!)} con ${blocY.label}`,
        // Someone now sin grupo may still have an own-bench score from votes
        // cast before leaving it; next to "Sin grupo" it would read as nonsense.
        ...(row.ownParty != null && data.parties.includes(row.party)
          ? [`${pct(row.ownParty, 1)} con su bancada`]
          : []),
      ],
    });
    return row;
  }

  function clear() {
    setHoverId(null);
    setTip(null);
  }

  // A mouse click opens the record; on touch the first tap reads the dot and
  // a second tap on the same dot opens it.
  function release(event: ReactPointerEvent<SVGSVGElement>) {
    const previous = hoverId;
    const row = show(event);
    if (row && (event.pointerType === "mouse" || previous === row.id)) {
      clear();
      onSelect(row.id);
    }
  }

  return (
    <div className="alignment-map">
      <svg
        viewBox={`0 0 ${SIZE} ${SIZE}`}
        role="img"
        aria-label={`Cada punto es un escaño: a la derecha coincide más con ${blocX.label}; arriba, con ${blocY.label}.`}
        onPointerMove={(event) => event.pointerType === "mouse" && show(event)}
        onPointerLeave={clear}
        onPointerUp={release}
        style={{ cursor: hoverId ? "pointer" : "default" }}
      >
        {ticks.map((tick) => (
          <g key={tick} className="alignment-gridline">
            <line x1={MARGIN.left + scale(tick)} x2={MARGIN.left + scale(tick)} y1={MARGIN.top} y2={MARGIN.top + PLOT} />
            <line x1={MARGIN.left} x2={MARGIN.left + PLOT} y1={MARGIN.top + PLOT - scale(tick)} y2={MARGIN.top + PLOT - scale(tick)} />
            <text x={MARGIN.left + scale(tick)} y={MARGIN.top + PLOT + 18} textAnchor="middle">{pct(tick)}</text>
            <text x={MARGIN.left - 8} y={MARGIN.top + PLOT - scale(tick) + 4} textAnchor="end">{pct(tick)}</text>
          </g>
        ))}
        <line
          className="alignment-diagonal"
          x1={MARGIN.left}
          y1={MARGIN.top + PLOT}
          x2={MARGIN.left + PLOT}
          y2={MARGIN.top}
        />
        <text className="alignment-axis" x={MARGIN.left + PLOT / 2} y={SIZE - 10} textAnchor="middle">
          {axisLabel(blocX.label, data.seated)}
        </text>
        <text
          className="alignment-axis"
          transform={`translate(16 ${MARGIN.top + PLOT / 2}) rotate(-90)`}
          textAnchor="middle"
        >
          {axisLabel(blocY.label, data.seated)}
        </text>
        {background.map((row) => (
          <circle key={row.id} cx={cx(row)} cy={cy(row)} r={3.4} className="alignment-dot-muted" />
        ))}
        {foreground.map((row) => (
          <circle key={row.id} cx={cx(row)} cy={cy(row)} r={4.6} fill={partyColor(row.party)} className="alignment-dot" />
        ))}
        {ringed.map((id) => {
          const row = points.find((point) => point.id === id);
          return row ? <circle key={`ring-${id}`} cx={cx(row)} cy={cy(row)} r={8} className="alignment-ring" /> : null;
        })}
      </svg>
      <TipBox tip={tip} />
    </div>
  );
}

// --- Party heatmap ----------------------------------------------------------

function PartyHeatmap({ data, partyFilter }: { data: ChamberAlignment; partyFilter: string }) {
  const [tip, setTip] = useState<Tip | null>(null);
  const n = data.parties.length;
  const offDiagonal = data.agreement.flatMap((row, i) => row.filter((_, j) => i !== j));
  const low = Math.floor(Math.min(...offDiagonal) * 20) / 20;
  const shade = (value: number) => (value - low) / (1 - low);
  // Bloc outlines, as grid spans over the cells (row/column 1 hold labels).
  let start = 0;
  const outlines = data.blocs.map((bloc) => {
    const span = { from: start + 2, to: start + 2 + bloc.parties.length };
    start += bloc.parties.length;
    return span;
  });
  const dimmed = (a: string, b: string) =>
    partyFilter !== ALL && data.parties.includes(partyFilter) && a !== partyFilter && b !== partyFilter;

  return (
    <div
      className="alignment-heatmap"
      style={{ gridTemplateColumns: `auto repeat(${n}, minmax(0, 1fr))` }}
      onPointerLeave={() => setTip(null)}
    >
      {data.parties.map((party, j) => (
        <span
          key={`col-${party}`}
          className="alignment-heat-label is-column"
          style={{ gridRow: 1, gridColumn: j + 2 }}
        >
          {party}
        </span>
      ))}
      {data.parties.map((rowParty, i) => [
        <span
          key={`row-${rowParty}`}
          className="alignment-heat-label"
          style={{ gridRow: i + 2, gridColumn: 1 }}
        >
          {rowParty}
        </span>,
        ...data.parties.map((colParty, j) => {
          const value = data.agreement[i][j];
          return (
            <span
              key={`${rowParty}-${colParty}`}
              className={`alignment-heat-cell${dimmed(rowParty, colParty) ? " is-dim" : ""}`}
              style={{
                background: heat(shade(value)),
                color: shade(value) > 0.5 ? "#fffdf8" : "var(--ink)",
                gridRow: i + 2,
                gridColumn: j + 2,
              }}
              onPointerEnter={(event) => {
                if (i === j) return setTip(null);
                const rect = event.currentTarget.getBoundingClientRect();
                setTip({
                  top: rect.top,
                  left: clampLeft(rect.left + rect.width / 2),
                  title: `${rowParty} y ${colParty}`,
                  lines: [`Mismo voto en ${pct(value)} de ${data.pairRollCalls[i][j]} votaciones`],
                });
              }}
            >
              {pct(value)}
            </span>
          );
        }),
      ])}
      {outlines.map((span) => (
        <span
          key={span.from}
          className="alignment-heat-bloc"
          style={{ gridRow: `${span.from} / ${span.to}`, gridColumn: `${span.from} / ${span.to}` }}
        />
      ))}
      <TipBox tip={tip} />
    </div>
  );
}

// --- Least aligned ----------------------------------------------------------

function LeastAligned({
  data,
  partyFilter,
  selectedPersonId,
  onSelect,
}: {
  data: ChamberAlignment;
  partyFilter: string;
  selectedPersonId: string | null;
  onSelect: (personId: string) => void;
}) {
  const rows = data.legislators
    .filter(
      (row) =>
        row.ownParty != null &&
        data.parties.includes(row.party) &&
        (partyFilter === ALL || row.party === partyFilter),
    )
    .sort((a, b) => a.ownParty! - b.ownParty! || b.votes - a.votes)
    .slice(0, 10);
  if (!rows.length) {
    return <p className="alignment-empty">Sin bancada propia con qué comparar.</p>;
  }
  // Dots on a shared track, never bars: the values sit just under 100%, so a
  // truncated bar would exaggerate the gaps.
  const low = Math.floor(rows[0].ownParty! * 100 - 1) / 100;
  const position = (value: number) => `${((value - low) / (1 - low)) * 100}%`;

  return (
    <ol className="alignment-list">
      {rows.map((row) => (
        <li key={row.id}>
          <button
            type="button"
            className={row.id === selectedPersonId ? "is-selected" : ""}
            onClick={() => onSelect(row.id)}
          >
            <span className="alignment-list-name">
              <strong>{row.name}</strong>
              <small>
                <i style={{ background: partyColor(row.party) }} />
                {partyName(row.party)} · {row.votes} votos
              </small>
            </span>
            <span className="alignment-list-track" aria-hidden="true">
              <i style={{ left: position(row.ownParty!), background: partyColor(row.party) }} />
            </span>
            <span className="alignment-list-value">{pct(row.ownParty!, 1)}</span>
          </button>
        </li>
      ))}
      <li className="alignment-list-scale" aria-hidden="true">
        <span />
        <span>
          <small>{pct(low)}</small>
          <small>{pct(1)}</small>
        </span>
        <span />
      </li>
    </ol>
  );
}

// --- Card under the explorer's calendar -------------------------------------

function read(row: ExportedLegislator | ExportedSeat, mode: Mode) {
  const scores = row.scores[mode];
  return {
    id: row.id,
    party: row.party,
    votes: row.votes,
    absences: row.absences,
    blocs: scores.slice(0, -1),
    ownParty: scores[scores.length - 1],
  };
}

const CARD_COLUMNS: Mode[] = ["all-cast", "all-seated", "contested-cast", "contested-seated"];

/**
 * One seat's or one person's agreement with each bloc and their own bench,
 * every way the bloc map can count it. Follows the panel above it: a seat when
 * a seat is selected (all its occupants' votes), a person otherwise.
 */
export function AlignmentCard({
  chamber,
  seatId,
  personId,
}: {
  chamber: Chamber;
  seatId: string | null;
  personId: string | null;
}) {
  const payload = useAlignmentData();
  if (!payload) return null;
  const data = payload[chamber];
  const entry: ExportedLegislator | ExportedSeat | undefined = seatId
    ? data.seats.find((row) => row.id === seatId)
    : personId
      ? data.legislators.find((row) => row.id === personId)
      : undefined;

  if (!entry) {
    return (
      <div className="panel-alignment">
        <p className="panel-kicker">Coincidencia con los bloques</p>
        <p className="calendar-empty">
          Menos de {payload.manifest.minVotes} votos emitidos: no alcanza para calcularla.
        </p>
      </div>
    );
  }

  const rows = [
    ...data.blocs.map((bloc, index) => ({ label: bloc.label, party: bloc.parties[0], index })),
    // Only for a current bench: a score from before going sin grupo would
    // read as nonsense next to "Sin grupo".
    ...(data.parties.includes(entry.party)
      ? [{ label: `Su bancada (${entry.party})`, party: entry.party, index: data.blocs.length }]
      : []),
  ];
  const cell = (mode: Mode, index: number) => {
    const value = entry.scores[mode][index];
    return value == null ? "—" : pct(value, 1);
  };

  return (
    <div className="panel-alignment">
      <p className="panel-kicker">
        Coincidencia con los bloques{seatId ? " · todo el escaño" : ""}
      </p>
      <table className="panel-alignment-table">
        <thead>
          <tr>
            <th rowSpan={2} />
            <th colSpan={2}>Todas las votaciones</th>
            <th colSpan={2}>Desacuerdo entre bloques</th>
          </tr>
          <tr>
            <th>Emitidos</th>
            <th>Con ausencias</th>
            <th>Emitidos</th>
            <th>Con ausencias</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.label}>
              <th scope="row">
                <i style={{ background: partyColor(row.party) }} />
                {row.label}
              </th>
              {CARD_COLUMNS.map((mode) => (
                <td key={mode}>{cell(mode, row.index)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="alignment-caption">
        {seatId && "Suma los votos de todas las personas que ocuparon el escaño. "}
        Emitidos: sobre las votaciones en que votó. Con ausencias: faltar cuenta como no coincidir.
        Desacuerdo: solo las {data.contestedRollCalls} votaciones en que los bloques votaron
        distinto.
      </p>
    </div>
  );
}

// --- Section ----------------------------------------------------------------

export default function AlignmentSection({
  chamber,
  selectedSeatId,
  selectedPersonId,
  names,
  onSelectSeat,
  onSelectPerson,
}: {
  chamber: Chamber;
  selectedSeatId: string | null;
  selectedPersonId: string | null;
  /** The explorer's own spelling for each person id, so a name reads the
   * same here as in the panel above. The export's name is the fallback. */
  names: Map<string, string>;
  onSelectSeat: (seatId: string) => void;
  onSelectPerson: (personId: string) => void;
}) {
  const [payload, setPayload] = useState<AlignmentData | null>(null);
  const [failed, setFailed] = useState(false);
  const [partyFilter, setPartyFilter] = useState(ALL);
  const [contested, setContested] = useState(false);
  const [seated, setSeated] = useState(false);
  const [minShare, setMinShare] = useState<number>(DEFAULT_SHARE);

  useEffect(() => {
    let cancelled = false;
    loadAlignment()
      .then((loaded) => {
        if (!cancelled) setPayload(loaded);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const raw = payload?.[chamber];
  const data = useMemo<ChamberAlignment | null>(() => {
    if (!raw) return null;
    const mode: Mode = `${contested ? "contested" : "all"}-${seated ? "seated" : "cast"}`;
    return {
      contested,
      seated,
      rollCalls: contested ? raw.contestedRollCalls : raw.rollCalls,
      parties: raw.parties,
      blocs: raw.blocs,
      // Absences have no bearing on how parties vote, so only friction moves the heatmap.
      agreement: contested ? raw.agreementContested : raw.agreement,
      pairRollCalls: contested ? raw.pairRollCallsContested : raw.pairRollCalls,
      legislators: raw.legislators
        .filter((row) => row.votes >= minShare * raw.rollCalls)
        .map((row) => ({ ...read(row, mode), name: names.get(row.id) ?? row.name, occupants: 1 })),
      seats: raw.seats
        .filter((row) => row.votes >= minShare * raw.rollCalls)
        .map((row) => ({
          ...read(row, mode),
          name: names.get(row.holder) ?? row.holderName,
          occupants: row.occupants,
        })),
    };
  }, [raw, names, contested, seated, minShare]);
  // From everyone exported, so a chip never vanishes under the reader when the
  // participation floor rises past its last member.
  const parties = useMemo(
    () =>
      raw
        ? [...new Set(raw.legislators.map((row) => row.party))].sort(
            (a, b) => partyRank(a) - partyRank(b),
          )
        : [],
    [raw],
  );

  if (failed) return null;
  if (!data || !payload) {
    return (
      <section className="alignment" id="bloques" aria-busy="true">
        <p className="alignment-loading">Cargando bloques de votación…</p>
      </section>
    );
  }

  const [blocX, blocY] = data.blocs;
  const shown = data.seats.filter(
    (row) => partyFilter === ALL || row.party === partyFilter,
  ).length;
  // Which side of the diagonal each highlighted dot falls on, read from its
  // real position (never the jitter): below-right is closer to the first bloc.
  const sides = { x: 0, y: 0, tied: 0 };
  for (const row of data.seats) {
    if (partyFilter !== ALL && row.party !== partyFilter) continue;
    const [x, y] = row.blocs;
    if (x == null || y == null) continue;
    if (x > y) sides.x += 1;
    else if (y > x) sides.y += 1;
    else sides.tied += 1;
  }
  const minVotes = Math.max(payload.manifest.minVotes, Math.ceil(minShare * (raw?.rollCalls ?? 0)));
  const scope = contested
    ? `${data.rollCalls} votaciones en que los bloques votaron distinto`
    : `${data.rollCalls} votaciones`;

  return (
    <section className="alignment" id="bloques">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Bloques de votación</p>
          <h2>Entre los dos bloques</h2>
        </div>
        <p>
          Qué tan seguido cada legisladora o legislador votó igual que la mayoría de cada bloque. Los
          bloques salen de los datos: agrupan a los partidos que más votan juntos.
        </p>
      </div>

      <div className="chamber-card alignment-card">
        <div className="toolbar">
          <span className="toolbar-label">Partido</span>
          <div className="party-filter" aria-label="Resaltar un partido">
            {[ALL, ...parties].map((party) => (
              <button
                key={party}
                type="button"
                className={partyFilter === party ? "active" : ""}
                aria-pressed={partyFilter === party}
                onClick={() => setPartyFilter(party)}
              >
                {party !== ALL && <i className="alignment-swatch" style={{ background: partyColor(party) }} />}
                {partyName(party)}
              </button>
            ))}
          </div>
        </div>
        <div className="toolbar alignment-modes">
          <span className="toolbar-label">Votaciones</span>
          <div className="alignment-toggles">
            <button
              type="button"
              aria-pressed={contested}
              className={contested ? "active" : ""}
              onClick={() => setContested((value) => !value)}
            >
              Solo con desacuerdo entre bloques
            </button>
            <button
              type="button"
              aria-pressed={seated}
              className={seated ? "active" : ""}
              onClick={() => setSeated((value) => !value)}
            >
              Contar ausencias
            </button>
          </div>
          <span className="toolbar-label">Mínimo</span>
          <div className="alignment-toggles" role="radiogroup" aria-label="Participación mínima">
            {PARTICIPATION.map((option) => (
              <button
                key={option.share}
                type="button"
                role="radio"
                aria-checked={minShare === option.share}
                className={`is-radio${minShare === option.share ? " active" : ""}`}
                onClick={() => setMinShare(option.share)}
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>
        <div className="alignment-map-layout">
          <BlocMap
            data={data}
            partyFilter={partyFilter}
            selectedSeatId={selectedSeatId}
            onSelect={onSelectSeat}
          />
          <div className="alignment-guide">
            <div className="alignment-sides">
              <p className="panel-kicker">
                Escaños de cada lado de la diagonal{partyFilter !== ALL ? ` · ${partyName(partyFilter)}` : ""}
              </p>
              <div>
                <strong>{sides.x}</strong>
                <span>
                  <i style={{ background: partyColor(blocX.parties[0]) }} />
                  Abajo a la derecha: más cerca de {blocX.label}
                </span>
              </div>
              <div>
                <strong>{sides.y}</strong>
                <span>
                  <i style={{ background: partyColor(blocY.parties[0]) }} />
                  Arriba a la izquierda: más cerca de {blocY.label}
                </span>
              </div>
              {sides.tied > 0 && <small>{sides.tied} justo sobre la diagonal.</small>}
            </div>
            <p className="panel-kicker">Cómo leerlo</p>
            <p><strong>Hacia la derecha</strong>, vota como {blocX.label}.</p>
            <p><strong>Hacia arriba</strong>, vota como {blocY.label}.</p>
            {contested ? (
              <p>
                Sin las votaciones casi unánimes, la coincidencia con el bloque contrario se desploma:
                quien queda lejos de la esquina de su bloque se aparta justo cuando hay desacuerdo.
              </p>
            ) : (
              <p>
                Sobre la diagonal coincidiría igual con ambos. Muchas votaciones son casi unánimes, así
                que incluso el bloque contrario suele coincidir más de la mitad de las veces.
              </p>
            )}
            {seated && (
              <p>
                Con las ausencias, faltar cuenta como no coincidir: quien falta seguido se acerca al
                origen porque no vota, no porque vote en contra.
              </p>
            )}
            <p className="alignment-guide-note">
              Cada punto es un escaño y suma los votos de todas las personas que lo ocuparon; los
              puntos se separan hasta dos puntos porcentuales para no encimarse.{" "}
              {shown} {shown === 1 ? "escaño" : "escaños"} con al menos {minVotes} votos emitidos;{" "}
              {scope}. Haz clic en un punto para abrir su historial.
            </p>
          </div>
        </div>
      </div>

      <div className="alignment-grid">
        <div className="chamber-card alignment-panel">
          <p className="panel-kicker">Coincidencia entre partidos</p>
          <PartyHeatmap data={data} partyFilter={partyFilter} />
          <p className="alignment-caption">
            Porcentaje de {contested ? "las votaciones con desacuerdo entre bloques" : "votaciones"} en que la
            mayoría de ambos partidos votó igual. Los recuadros marcan los bloques.
          </p>
        </div>
        <div className="chamber-card alignment-panel">
          <p className="panel-kicker">
            {/* Counting absences turns the list into who shows up least, not who dissents. */}
            {seated ? "Quienes menos votan con su bancada" : "Quienes más se apartan de su bancada"}
            {partyFilter !== ALL ? ` · ${partyName(partyFilter)}` : ""}
          </p>
          <LeastAligned
            data={data}
            partyFilter={partyFilter}
            selectedPersonId={selectedPersonId}
            onSelect={onSelectPerson}
          />
          <p className="alignment-caption">
            Porcentaje de sus {seated ? "votaciones" : "votos"} que coincidieron con la mayoría del resto
            de su bancada{seated ? "; una ausencia cuenta como no coincidir" : ""}.
          </p>
        </div>
      </div>
    </section>
  );
}
