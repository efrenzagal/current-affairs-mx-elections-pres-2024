/**
 * Shapes of `vote-intention.json` and `pollster-accuracy.json`, both written by
 * `scripts/export_vote_intention.py`, plus the formatting the tracker, the
 * table and the accuracy section share.
 */

import { type RefObject, useEffect, useState } from "react";

import { partyColor } from "../parties";

/**
 * The element's content width, so a chart can draw in real pixels instead of
 * scaling an 860px drawing down to a phone, where its labels shrink to ~4px.
 */
export function useWidth(ref: RefObject<HTMLElement | null>, fallback: number) {
  const [width, setWidth] = useState(fallback);
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.round(entry.contentRect.width)));
    observer.observe(element);
    return () => observer.disconnect();
  }, [ref]);
  return width;
}

/** [label, share as published, share with undecided removed] */
export type PollOption = [string, number | null, number | null];

export type Poll = {
  id: string;
  cycle: string;
  /** Pollster family, the unit the accuracy analysis is computed on. */
  firm: string;
  pollster: string;
  client: string | null;
  fieldStart: string | null;
  fieldEnd: string | null;
  date: string;
  dateType: string | null;
  precision: string | null;
  daysBefore: number | null;
  n: number | null;
  basis: string;
  level: string;
  phase: string | null;
  /** The poll's own release or the article reporting it. */
  url: string | null;
  /** The pinned Wikipedia revision the row was read from. */
  wiki: string | null;
  options: PollOption[];
  others: number | null;
  undecided: number | null;
  none: number | null;
};

export type Cycle = {
  id: string;
  label: string;
  date: string;
  held: boolean;
  columns: string[];
  results: Record<string, number> | null;
  sources: { lang: string; url: string }[];
};

export type PollData = {
  schemaVersion: number;
  current: string;
  sourceThrough: string;
  cycles: Cycle[];
  polls: Poll[];
};

export type ScorecardRow = {
  firm: string;
  cycles: number[];
  nCycles: number;
  nPolls: number;
  mae: number;
  maeSe: number | null;
  leadBias: number;
  leadBiasSe: number | null;
  leadRmse: number;
  samplingSe: number | null;
  calledWinner: number;
};

export type IndustryRow = {
  cycle: string;
  label: string;
  year: number;
  bloc: string;
  nFirms: number;
  error: number;
  se: number | null;
  result: number;
};

export type HouseEffect = {
  firm: string;
  bloc: string;
  nCycles: number;
  effect: number;
  se: number | null;
  rawBias: number;
};

export type LastPoll = {
  pollster: string;
  firm: string;
  date: string;
  daysBefore: number;
  level: string;
  final: boolean;
  /** bloc -> [estimate, result] */
  values: Record<string, [number, number]>;
};

export type AccuracyData = {
  schemaVersion: number;
  windowDays: number;
  minCycles: number;
  blocs: string[];
  cycles: { id: string; label: string; year: number; date: string }[];
  scorecard: ScorecardRow[];
  industry: IndustryRow[];
  houseEffects: HouseEffect[];
  lastPolls: Record<string, { blocs: { bloc: string; result: number; nominee: string | null }[]; polls: LastPoll[] }>;
};

export const ALL_FIRMS = "";

// Parties registered in 2026 have no colour in the Congress palette yet.
const EXTRA_PARTY_COLORS: Record<string, string> = { PAZ: "#4f8fa6", SOMOS: "#8467ad" };

export function optionColor(label: string) {
  return EXTRA_PARTY_COLORS[label] ?? partyColor(label);
}

export const BLOC_COLORS: Record<string, string> = {
  Izquierda: partyColor("MORENA"),
  PAN: partyColor("PAN"),
  PRI: partyColor("PRI"),
  Otros: "#8a8f8c",
};

export const BLOC_LABELS: Record<string, string> = {
  Izquierda: "Izquierda",
  PAN: "PAN",
  PRI: "PRI",
  Otros: "Otros",
};

const MONTHS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];

function parts(date: string) {
  const [year, month, day] = date.split("-").map(Number);
  return { year, month, day };
}

/** Days since the epoch, for placing dates on an axis. */
export function dayNumber(date: string) {
  const { year, month, day } = parts(date);
  return Date.UTC(year, month - 1, day || 1) / 86_400_000;
}

export function dateFromDay(day: number) {
  return new Date(day * 86_400_000).toISOString().slice(0, 10);
}

export function formatDay(date: string) {
  const { year, month, day } = parts(date);
  return `${day} ${MONTHS[month - 1]} ${year}`;
}

export function formatMonth(date: string) {
  const { year, month } = parts(date);
  return `${MONTHS[month - 1]} ${year}`;
}

/** Fieldwork as the source gives it: "25–29 ago 2026", "27 abr – 2 may 2026". */
export function formatFieldwork(poll: Pick<Poll, "fieldStart" | "fieldEnd" | "date" | "precision">) {
  if (poll.precision === "mes") return formatMonth(poll.date);
  const end = poll.fieldEnd ?? poll.date;
  const start = poll.fieldStart;
  if (!start || start === end) return formatDay(end);
  const a = parts(start);
  const b = parts(end);
  if (a.year !== b.year) return `${formatDay(start)} – ${formatDay(end)}`;
  if (a.month !== b.month) return `${a.day} ${MONTHS[a.month - 1]} – ${b.day} ${MONTHS[b.month - 1]} ${b.year}`;
  return `${a.day}–${b.day} ${MONTHS[b.month - 1]} ${b.year}`;
}

export function pct(value: number | null | undefined, digits = 1) {
  return value == null ? "—" : `${value.toFixed(digits)}%`;
}

export function signed(value: number | null | undefined, digits = 1) {
  if (value == null) return "—";
  const text = Math.abs(value).toFixed(digits);
  return value > 0.05 ? `+${text}` : value < -0.05 ? `−${text}` : text;
}

// Surnames as the press uses them; names not listed fall back to the last word.
const SHORT_NAMES: Record<string, string> = {
  "Andrés Manuel López Obrador": "López Obrador",
  "Diego Fernández de Cevallos": "Fernández de Cevallos",
  "Enrique Peña Nieto": "Peña Nieto",
  "Josefina Vázquez Mota": "Vázquez Mota",
  "Jorge Álvarez Máynez": "Álvarez Máynez",
  "Jaime Rodríguez Calderón": "Rodríguez Calderón",
  "Samuel García": "S. García",
  "Salvemos a México (PT-Convergencia)": "Salvemos a México",
};

/** "Claudia Sheinbaum" -> "Sheinbaum"; party keys stay as they are. */
export function shortLabel(label: string) {
  if (SHORT_NAMES[label]) return SHORT_NAMES[label];
  if (/^[A-ZÁÉÍÓÚÑ-]+$/.test(label) || !label.includes(" ")) return label;
  return label.split(" ").at(-1) ?? label;
}
