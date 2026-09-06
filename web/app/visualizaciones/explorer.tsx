"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import {
  findDistrictState,
  resolveFederalDistrict,
  resolveMunicipalityDistricts,
  type DistrictLookupIndex,
  type DistrictResolution,
} from "../../../district_lookup/resolver";
import { SITE_NAME, SiteFooter, SiteHeader } from "../site-chrome";
import { PARTY_COLORS, partyRank } from "./parties";
import {
  CHOICE_COLORS,
  cleanTitle,
  label,
  normalize,
  shortDate,
  shortTitle,
  stageLabel,
  topicLabel,
  voteLabel,
} from "./votes";

export type Chamber = "diputados" | "senado";

/** Occupancy state published by the official directory, plus our own fallback. */
type SeatStatus = "en_funciones" | "licencia" | "vacante" | "sin_directorio";

type Seat = {
  id: string;
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
  /** Roll-call identity linked from the INE titular/suplente formula. */
  electedPersonId: string | null;
  electedName: string;
  /** Stable constitutional-seat labels copied directly from the INE record. */
  titularName: string;
  substituteName: string | null;
  electedParty: string;
  electedNameRole: "titular" | "suplente";
  /** Who the official directory shows holding it at the roster cutoff. */
  currentPersonId: string | null;
  currentName: string | null;
  currentParty: string;
  currentStatus: SeatStatus;
};

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

/**
 * Someone with a roll-call record whom neither seat snapshot names: not the
 * titular elected in 2024, not who the directory shows holding a seat today.
 *
 * The export links most of them back to a seat through the INE suplente
 * register, which is the only source that connects a voting identity to a
 * place in the chamber. `seatRole` says what that link means — a suplencia that
 * ended when the titular returned, or a person still in the seat whose roll-call
 * record the directory files under a second id.
 */
type FormerMember = {
  personId: string;
  name: string;
  party: string;
  seatId: string | null;
  seatRole: "en_funciones" | "suplencia_concluida" | null;
  relationshipSourceUrl: string | null;
};

type SeatMember = {
  personId: string;
  name: string;
  party: string;
  role: "titular" | "suplente";
  sourceUrl: string | null;
  voteCount: number;
};

type HistoryEntry = [string, string, number | null];

type SiteData = {
  manifest: {
    schemaVersion: number;
    legislature: number;
    chamber: Chamber;
    sourceThrough: string;
    seatCount: number;
    voteCount: number;
    linkedSeats: number;
    roster: { observedAt: string; sourceUrl: string };
    substitutedSeats: number;
    partyChangedSeats: number;
    onLeaveSeats: number;
    vacantSeats: number;
    currentLinkedSeats: number;
  };
  seats: Seat[];
  formerMembers?: FormerMember[];
  personAliases?: Record<string, string>;
  votes: Vote[];
  /** Keyed by person, not by seat: a seat can have had more than one occupant. */
  histories: Record<string, [string, string][]>;
  seatMembers: Record<string, SeatMember[]>;
  seatVoteConflicts?: {
    seatId: string;
    voteId: string;
    countedPersonId: string;
    reportedPersonIds: string[];
  }[];
  partyVotes: Record<string, Record<string, Record<string, number>>>;
};

/** Which identity the hemicycle names in each seat. */
type View = "actual" | "electoral";
type HistoryMode = "all" | "titular" | "suplente";

/**
 * What the panel is reading. A seat resolves its occupant through the active
 * view, so it always follows the tab. A person is one fixed identity, held by
 * its `Person.key` rather than a person id because a seat can be occupied by
 * someone the roll call never linked and who therefore has no id to hold.
 */
type Selection =
  | { kind: "seat"; id: string }
  | { kind: "person"; key: string };

const NO_HISTORY: [string, string][] = [];
const NO_SEAT_HISTORY: HistoryEntry[] = [];

const ALL_TOPICS = "todos";

/** How many search hits the dropdown will render before it stops. */
const MAX_RESULTS = 60;

const CHAMBERS: Record<Chamber, {
  dataUrl: string;
  name: string;
  short: string;
  member: string;
  memberSearch: string;
  pin: string;
  sourceName: string;
  isSenate: boolean;
  /** The other chamber, for handing a search off when this one has no match. */
  other: Chamber;
  otherName: string;
}> = {
  diputados: {
    dataUrl: "/data/legislature-66.json",
    name: "Cámara de Diputados",
    short: "Cámara de Diputados",
    member: "diputación",
    memberSearch: "Buscar diputada o diputado",
    pin: "una diputación",
    sourceName: "la Gaceta Parlamentaria de la Cámara de Diputados",
    isSenate: false,
    other: "senado",
    otherName: "el Senado",
  },
  senado: {
    dataUrl: "/data/senate-66.json",
    name: "Senado de la República",
    short: "Senado",
    member: "senaduría",
    memberSearch: "Buscar senadora o senador",
    pin: "una senaduría",
    sourceName: "el Senado de la República",
    isSenate: true,
    other: "diputados",
    otherName: "la Cámara de Diputados",
  },
};

function statusLabel(status: SeatStatus) {
  if (status === "licencia") return "Con licencia";
  if (status === "vacante") return "Vacante";
  if (status === "sin_directorio") return "Sin registro en el directorio";
  return "En funciones";
}

function electionActorLabel(actor: string | null) {
  return actor?.replaceAll("_", " · ") ?? "";
}

function seatTypeLabel(seatType: Seat["seatType"]) {
  if (seatType === "MR") return "mayoría relativa";
  if (seatType === "FM") return "primera minoría";
  return "representación proporcional";
}

/**
 * The identity a seat carries under the active view.
 *
 * `licencia` and `vacante` deliberately override the party: the directory still
 * prints a group for a member on leave, but the seat is not voting with that
 * group, and colouring it as if it were would overstate the bloc.
 */
type Occupant = {
  name: string;
  party: string;
  personId: string | null;
  status: SeatStatus;
  substituted: boolean;
  partyChanged: boolean;
};

function occupantOf(seat: Seat, view: View): Occupant {
  if (view === "electoral") {
    return {
      name: seat.titularName,
      party: seat.electedParty,
      personId: seat.electedPersonId,
      status: "en_funciones",
      substituted: false,
      partyChanged: false,
    };
  }
  const party =
    seat.currentStatus === "licencia"
      ? "LICENCIA"
      : seat.currentStatus === "vacante"
        ? "VACANTE"
        : seat.currentParty;
  return {
    name: seat.currentName ?? "Escaño vacante",
    party,
    personId: seat.currentPersonId,
    status: seat.currentStatus,
    substituted: seat.currentPersonId !== seat.electedPersonId,
    partyChanged:
      seat.currentStatus === "en_funciones" && seat.currentParty !== seat.electedParty,
  };
}

/**
 * One searchable name in this chamber, and where the hemicycle can show it.
 *
 * `view` is the answer to "which tab shows this person in a seat": current
 * occupants live in the roster view, replaced titulares in the electoral one,
 * and a concluded suplencia in neither. `seatId` is a weaker claim than `view`
 * — it is where the person sat, which is worth marking even on the tab that
 * names someone else there.
 */
type Person = {
  key: string;
  personId: string | null;
  name: string;
  party: string;
  search: string;
  seatId: string | null;
  view: View | null;
  /**
   * Read out of a seat snapshot, so the seat resolves to this same identity.
   * A former member is not: their roll-call record is a separate archive that
   * the seat cannot reach, even when we know which seat they sat in.
   */
  fromSeat: boolean;
  /** Short qualifier shown in the result row; empty for a sitting member. */
  tag: string;
};

function buildPeople(data: SiteData): Person[] {
  const people: Person[] = [];
  for (const seat of data.seats) {
    if (seat.currentName && seat.currentStatus !== "vacante") {
      people.push({
        key: `seat-actual:${seat.id}`,
        personId: seat.currentPersonId,
        name: seat.currentName,
        party: seat.currentParty,
        search: normalize(seat.currentName),
        seatId: seat.id,
        view: "actual",
        fromSeat: true,
        tag: seat.currentStatus === "en_funciones" ? "" : statusLabel(seat.currentStatus),
      });
    }
    // The elected titular is a second identity for the same seat, and only
    // worth offering once someone else holds it: otherwise the two entries
    // would be the same person twice.
    if (seat.electedPersonId && seat.electedPersonId !== seat.currentPersonId) {
      people.push({
        key: `seat-electoral:${seat.id}`,
        personId: seat.electedPersonId,
        name: seat.electedName,
        party: seat.electedParty,
        search: normalize(seat.electedName),
        seatId: seat.id,
        view: "electoral",
        fromSeat: true,
        tag: seat.electedNameRole === "titular" ? "Titular electo en 2024" : "Suplente registrado",
      });
    }
  }
  for (const former of data.formerMembers ?? []) {
    people.push({
      key: `person:${former.personId}`,
      personId: former.personId,
      name: former.name,
      party: former.party,
      search: normalize(former.name),
      seatId: former.seatId,
      // Only the roster view can show one of these, and only when the person is
      // still in the seat. A concluded suplencia belongs to no tab.
      view: former.seatRole === "en_funciones" ? "actual" : null,
      fromSeat: false,
      tag:
        former.seatRole === "en_funciones"
          ? "Registro alterno"
          : former.seatId
            ? "Suplencia concluida"
            : "Sin escaño identificado",
    });
  }
  return people.sort((a, b) => a.search.localeCompare(b.search, "es"));
}

/**
 * Seat coordinates always come from the *electoral* party and name, never the
 * current occupant. A seat is a constitutional object: it must not slide across
 * the hemicycle when a member changes bench, or switching views would look like
 * the chamber rearranged itself.
 */
function seatCoordinates(seats: Seat[]) {
  const ordered = [...seats].sort(
    (a, b) =>
      partyRank(a.electedParty) - partyRank(b.electedParty) ||
      a.titularName.localeCompare(b.titularName, "es"),
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

export default function Explorer({ chamber }: { chamber: Chamber }) {
  const config = CHAMBERS[chamber];
  const isSenate = config.isSenate;

  const [data, setData] = useState<SiteData | null>(null);
  const [error, setError] = useState(false);
  const [view, setView] = useState<View>("actual");
  const [selection, setSelection] = useState<Selection | null>(null);
  const [selectedVoteId, setSelectedVoteId] = useState<string | null>(null);
  const [hoveredSeatId, setHoveredSeatId] = useState<string | null>(null);
  // A name handed over from the other chamber's explorer, which links here when
  // its own search comes up empty. Seeded at mount rather than in an effect;
  // after that the box belongs to the reader. The server render never reaches
  // the search box — it stops at the loading shell — so there is nothing to
  // mismatch on hydration.
  const [query, setQuery] = useState(() =>
    typeof window === "undefined"
      ? ""
      : new URLSearchParams(window.location.search).get("q") ?? "",
  );
  const [partyFilter, setPartyFilter] = useState("Todos");
  const [voteFilterParty, setVoteFilterParty] = useState("Todos");
  const [stateFilter, setStateFilter] = useState("Todos");
  const [districtFilter, setDistrictFilter] = useState("Todos");
  const [districtIndex, setDistrictIndex] = useState<DistrictLookupIndex | null>(null);
  const [municipalityFilter, setMunicipalityFilter] = useState("");
  const [postalCode, setPostalCode] = useState("");
  const [districtResolution, setDistrictResolution] = useState<DistrictResolution | null>(null);
  const [postalLoading, setPostalLoading] = useState(false);
  const [districtQuery, setDistrictQuery] = useState("");
  const [districtOpen, setDistrictOpen] = useState(false);
  const [topic, setTopic] = useState(ALL_TOPICS);
  const [historyMode, setHistoryMode] = useState<HistoryMode>("all");
  const pendingScroll = useRef(false);

  useEffect(() => {
    let cancelled = false;
    fetch(config.dataUrl)
      .then((response) => {
        if (!response.ok) throw new Error(String(response.status));
        return response.json();
      })
      .then((payload: SiteData) => {
        if (cancelled) return;
        setData(payload);
        const first = payload.seats[0];
        setSelection(first ? { kind: "seat", id: first.id } : null);
        // No vote is preselected: the vote detail only appears once the reader
        // opens one, from the history list or the calendar.
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });
    return () => {
      cancelled = true;
    };
  }, [config.dataUrl]);

  useEffect(() => {
    if (isSenate) return;
    let cancelled = false;
    fetch("/data/federal-district-lookup.json")
      .then((response) => {
        if (!response.ok) throw new Error(String(response.status));
        return response.json();
      })
      .then((payload: DistrictLookupIndex) => {
        if (!cancelled) setDistrictIndex(payload);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [isSenate]);

  // Runs after the vote-detail section has been mounted by the render that
  // `openVote` triggered, which is the only point where it can be scrolled to.
  useEffect(() => {
    if (!pendingScroll.current || !selectedVoteId) return;
    pendingScroll.current = false;
    // When the chamber is already on screen, its decision hemicycle is directly
    // below the selected vote, so scrolling past the main explorer would hide
    // useful context.
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
  const occupants = useMemo(() => {
    const map = new Map<string, Occupant>();
    for (const seat of data?.seats ?? []) map.set(seat.id, occupantOf(seat, view));
    return map;
  }, [data, view]);

  const people = useMemo(() => (data ? buildPeople(data) : []), [data]);
  const peopleByKey = useMemo(
    () => new Map(people.map((person) => [person.key, person])),
    [people],
  );
  const formerById = useMemo(
    () => new Map((data?.formerMembers ?? []).map((member) => [member.personId, member])),
    [data],
  );

  /**
   * The identity the panel reads, and whether the hemicycle can point at it.
   *
   * A seat click resolves through the active view, so it is always on the floor.
   * A search hit instead carries one fixed identity: picking the titular of a
   * seat that has changed hands must keep showing the titular, even while the
   * active tab labels that seat with whoever replaced them. That mismatch is
   * what dims the chamber. The tab is a global control over all 500 seats and
   * never moves on its own — the reader can cross over with the link the panel
   * offers, and that is the only way it changes.
   */
  const reading = useMemo(() => {
    if (!data || !selection) return null;
    if (selection.kind === "seat") {
      const seat = data.seats.find((candidate) => candidate.id === selection.id);
      if (!seat) return null;
      const occupant = occupantOf(seat, view);
      return { ...occupant, seat, onFloor: true, homeView: view, former: null };
    }
    const person = peopleByKey.get(selection.key);
    if (!person) return null;
    const former = person.personId ? formerById.get(person.personId) ?? null : null;
    const seat = person.seatId
      ? data.seats.find((candidate) => candidate.id === person.seatId) ?? null
      : null;
    const onFloor = seat !== null && person.view === view;
    const occupant = onFloor ? occupantOf(seat!, view) : null;
    return {
      name: person.name,
      party: person.party,
      personId: person.personId,
      // The seat flags describe an occupancy, so they only mean anything while
      // the tab actually shows this identity in the seat.
      status: occupant?.status ?? null,
      substituted: occupant?.substituted ?? false,
      partyChanged: occupant?.partyChanged ?? false,
      seat,
      onFloor,
      homeView: person.view,
      former,
    };
  }, [data, selection, view, peopleByKey, formerById]);

  const selectedSeat = reading?.seat ?? null;
  /** Ringed as a live selection, versus marked only as a seat of origin. */
  const floorSeatId = reading?.onFloor ? selectedSeat?.id ?? null : null;
  const originSeatId = reading && !reading.onFloor ? selectedSeat?.id ?? null : null;

  const previewSeat = hoveredSeatId
    ? data?.seats.find((seat) => seat.id === hoveredSeatId) ?? null
    : reading?.onFloor
      ? selectedSeat
      : null;
  const previewOccupant = previewSeat ? occupants.get(previewSeat.id)! : null;

  // A shared empty array keeps the identity stable across renders, so the
  // memos downstream of a history do not recompute for every unlinked seat.
  const seatHistories = useMemo(() => {
    const result = new Map<string, HistoryEntry[]>();
    if (!data) return result;
    const order = new Map(data.votes.map((vote, index) => [vote.id, index]));
    const conflicts = new Map(
      (data.seatVoteConflicts ?? []).map((conflict) => [
        `${conflict.seatId}:${conflict.voteId}`,
        conflict.countedPersonId,
      ]),
    );
    for (const [seatId, members] of Object.entries(data.seatMembers)) {
      const byVote = new Map<string, HistoryEntry>();
      members.forEach((member, memberIndex) => {
        for (const [voteId, choice] of data.histories[member.personId] ?? []) {
          const existing = byVote.get(voteId);
          if (!existing || conflicts.get(`${seatId}:${voteId}`) === member.personId) {
            byVote.set(voteId, [voteId, choice, memberIndex]);
          }
        }
      });
      result.set(
        seatId,
        [...byVote.values()].sort(
          (left, right) =>
            (order.get(left[0]) ?? order.size) - (order.get(right[0]) ?? order.size),
        ),
      );
    }
    return result;
  }, [data]);
  const profilePersonId = reading?.personId ?? null;
  const isSeatHistory = selection?.kind === "seat" && Boolean(selectedSeat);
  const selectedSeatMembers = selectedSeat ? data?.seatMembers[selectedSeat.id] ?? [] : [];
  const hasSubstituteVotes = selectedSeatMembers.some(
    (member) => member.role === "suplente" && member.voteCount > 0,
  );
  const fullHistory = useMemo(() => {
    if (!data) return NO_SEAT_HISTORY;
    if (selection?.kind === "seat" && selectedSeat) {
      const members = data.seatMembers[selectedSeat.id] ?? [];
      return (seatHistories.get(selectedSeat.id) ?? NO_SEAT_HISTORY).filter((entry) => {
        if (historyMode === "all") return true;
        return entry[2] !== null && members[entry[2]]?.role === historyMode;
      });
    }
    if (!profilePersonId) return NO_SEAT_HISTORY;
    return (data.histories[profilePersonId] ?? []).map(
      ([voteId, choice]) => [voteId, choice, null] as HistoryEntry,
    );
  }, [data, selection, selectedSeat, profilePersonId, historyMode, seatHistories]);
  const previewHistory = previewSeat
    ? seatHistories.get(previewSeat.id) ?? NO_HISTORY
    : NO_HISTORY;
  const selectedVote = selectedVoteId ? votesById.get(selectedVoteId) ?? null : null;

  const topics = useMemo(() => {
    const counts = new Map<string, number>();
    for (const [voteId] of fullHistory) {
      const key = votesById.get(voteId)?.topic ?? "sin_clasificar";
      counts.set(key, (counts.get(key) ?? 0) + 1);
    }
    return [...counts.entries()].sort((a, b) =>
      topicLabel(a[0]).localeCompare(topicLabel(b[0]), "es"),
    );
  }, [fullHistory, votesById]);

  /** The record the panel and the calendar both read, after the topic filter. */
  const history = useMemo(() => {
    if (topic === ALL_TOPICS) return fullHistory;
    return fullHistory.filter(
      ([voteId]) => (votesById.get(voteId)?.topic ?? "sin_clasificar") === topic,
    );
  }, [fullHistory, topic, votesById]);

  const parties = useMemo(() => {
    if (!data) return [];
    return [...new Set([...occupants.values()].map((occupant) => occupant.party))].sort(
      (a, b) => partyRank(a) - partyRank(b),
    );
  }, [data, occupants]);

  const partyCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const occupant of occupants.values()) {
      counts.set(occupant.party, (counts.get(occupant.party) ?? 0) + 1);
    }
    return counts;
  }, [occupants]);

  // Only MR/FM seats carry a state; RP/list seats run on a national ballot and
  // stay muted whenever a specific state is chosen, same as the Python explorer
  // this hemicycle replaced.
  const states = useMemo(() => {
    if (!data) return [];
    return [...new Set(data.seats.map((seat) => seat.state).filter((state): state is string => Boolean(state)))].sort(
      (a, b) => a.localeCompare(b, "es"),
    );
  }, [data]);

  // Districts only exist under a chosen state (numbering restarts at 1 in
  // every state) and only for MR seats — the Senado runs no district ballot,
  // so this stays empty there and the control disappears rather than search
  // a filter with nothing to find.
  const districtsForState = useMemo(() => {
    if (!data || stateFilter === "Todos") return [];
    return data.seats
      .filter((seat): seat is Seat & { district: number } => seat.state === stateFilter && seat.district !== null)
      .map((seat) => ({ district: seat.district, label: `Distrito ${seat.district}${seat.districtSeat ? ` · ${seat.districtSeat}` : ""}` }))
      .sort((a, b) => a.district - b.district);
  }, [data, stateFilter]);

  const lookupState = useMemo(
    () => districtIndex && stateFilter !== "Todos"
      ? findDistrictState(districtIndex, stateFilter)
      : null,
    [districtIndex, stateFilter],
  );

  const municipalitiesForState = useMemo(
    () => [...(lookupState?.municipalities ?? [])].sort((a, b) => a.name.localeCompare(b.name, "es")),
    [lookupState],
  );

  function applyDistrictResolution(result: DistrictResolution) {
    setDistrictResolution(result);
    if (result.state && data) {
      const matchingState = data.seats.find(
        (seat) => seat.stateId === result.state!.id && seat.state,
      )?.state;
      if (matchingState) setStateFilter(matchingState);
    }
    if (result.municipality) setMunicipalityFilter(result.municipality.name);
    setDistrictFilter(result.districts.length === 1 ? String(result.districts[0]) : "Todos");
    setDistrictQuery("");
    setDistrictOpen(false);
  }

  async function findByPostalCode() {
    if (!districtIndex) return;
    setPostalLoading(true);
    const result = await resolveFederalDistrict(districtIndex, { postalCode });
    applyDistrictResolution(result);
    setPostalLoading(false);
  }

  const queryNormalized = normalize(query);
  const results = useMemo(() => {
    if (queryNormalized.length < 2) return [];
    return people.filter((person) => person.search.includes(queryNormalized));
  }, [people, queryNormalized]);

  // Calendar strip for the *selected* person, not the hovered one: these squares
  // are click targets, so they must not shift under the cursor on hover.
  const calendarYears = useMemo(() => {
    const byYear = new Map<string, {
      voteId: string;
      choice: string;
      date: string;
      title: string;
      member: SeatMember | null;
    }[]>();
    for (const entry of history) {
      const [voteId, choice] = entry;
      const vote = votesById.get(voteId);
      if (!vote) continue;
      const year = vote.date.slice(0, 4);
      const bucket = byYear.get(year);
      const calendarEntry = {
        voteId,
        choice,
        date: vote.date,
        title: vote.title,
        member:
          isSeatHistory && selectedSeat && entry[2] !== null
            ? data?.seatMembers[selectedSeat.id]?.[entry[2]] ?? null
            : null,
      };
      if (bucket) bucket.push(calendarEntry);
      else byYear.set(year, [calendarEntry]);
    }
    return [...byYear.entries()]
      .map(([year, entries]) => ({
        year,
        entries: entries.sort(
          (a, b) => a.date.localeCompare(b.date) || a.voteId.localeCompare(b.voteId),
        ),
      }))
      .sort((a, b) => b.year.localeCompare(a.year));
  }, [history, votesById, isSeatHistory, selectedSeat, data]);

  // How every seat voted on the open roll call. Seats absent from the record
  // are deliberately left out rather than defaulted to "Ausente": the source
  // does not distinguish "did not vote" from "not in the chamber that day",
  // and for the Senado that gap is large enough to matter.
  const choiceBySeat = useMemo(() => {
    const map = new Map<string, string>();
    if (!data || !selectedVoteId) return map;
    for (const seat of data.seats) {
      const entry = (seatHistories.get(seat.id) ?? NO_HISTORY).find(
        ([voteId]) => voteId === selectedVoteId,
      );
      if (entry) map.set(seat.id, entry[1]);
    }
    return map;
  }, [data, selectedVoteId, seatHistories]);

  const choiceTotals = useMemo(() => {
    const totals = new Map<string, number>();
    for (const choice of choiceBySeat.values()) {
      const normalizedChoice = choice === "Abstencion" ? "Abstención" : choice;
      totals.set(normalizedChoice, (totals.get(normalizedChoice) ?? 0) + 1);
    }
    return totals;
  }, [choiceBySeat]);

  const unrecordedSeats = data ? data.seats.length - choiceBySeat.size : 0;

  // Party and state both mute seats rather than remove them, so the chamber's
  // shape never jumps as a filter changes. The name search no longer mutes:
  // searching now picks a person outright, and a filter that also dimmed the
  // chamber on every keystroke made the two controls fight over the same pixels.
  const isSeatVisible = (seat: Seat) => {
    if (stateFilter !== "Todos" && seat.state !== stateFilter) return false;
    if (districtFilter !== "Todos" && String(seat.district) !== districtFilter) return false;
    const occupant = occupants.get(seat.id);
    if (!occupant) return true;
    return partyFilter === "Todos" || occupant.party === partyFilter;
  };

  // Whoever actually cast this seat's vote on the open roll call, which is
  // its own historical fact rather than something either tab's occupant
  // snapshot can stand in for. A seat that has since changed hands or party
  // still shows here under the party that cast the vote, not today's or
  // 2024's tenant — those tabs answer "who sits here", not "who voted".
  const voteVoterBySeat = useMemo(() => {
    const map = new Map<string, SeatMember>();
    if (!data || !selectedVoteId) return map;
    for (const [seatId, members] of Object.entries(data.seatMembers)) {
      const entry = (seatHistories.get(seatId) ?? NO_HISTORY).find(
        ([voteId]) => voteId === selectedVoteId,
      );
      const memberIndex = entry?.[2] ?? null;
      const member = memberIndex !== null ? members[memberIndex] : null;
      if (member) map.set(seatId, member);
    }
    return map;
  }, [data, selectedVoteId, seatHistories]);

  const isVoteSeatVisible = (seat: Seat) => {
    if (stateFilter !== "Todos" && seat.state !== stateFilter) return false;
    if (districtFilter !== "Todos" && String(seat.district) !== districtFilter) return false;
    return voteFilterParty === "Todos" || voteVoterBySeat.get(seat.id)?.party === voteFilterParty;
  };

  /** Every selection resets the reading below it; only the route differs. */
  function select(next: Selection) {
    setSelection(next);
    // Clear the open vote and the topic: both belong to the previous person.
    setSelectedVoteId(null);
    setTopic(ALL_TOPICS);
    setHistoryMode("all");
  }

  function selectSeat(seat: Seat) {
    select({ kind: "seat", id: seat.id });
  }

  function openVote(voteId: string) {
    // The detail section is unmounted until a vote is chosen, so the scroll has
    // to wait for the render that creates it (see the effect above).
    pendingScroll.current = true;
    setSelectedVoteId(voteId);
    // Belongs to the previous roll call's party lineup, which the new vote
    // does not share.
    setVoteFilterParty("Todos");
  }

  /**
   * Show a search hit as itself. The view tab deliberately does not move: it
   * relabels every seat in the chamber, and having it flip as a side effect of
   * picking one name made the whole hemicycle lurch under the reader. When the
   * active tab cannot show this identity the chamber dims behind it instead,
   * and the panel offers the crossing as a link.
   *
   * Seat identities the active view can place become plain seat selections, so
   * following a name and clicking its seat land on exactly the same state. A
   * former member never collapses that way even when we know their seat: their
   * roll call is a separate record, and resolving them through the seat would
   * quietly swap it for the current occupant's and drop those votes off the site.
   */
  function selectPerson(person: Person) {
    select(
      person.fromSeat && person.seatId && person.view === view
        ? { kind: "seat", id: person.seatId }
        : { kind: "person", key: person.key },
    );
    setQuery("");
  }

  function selectView(nextView: View) {
    if (nextView === view) return;
    setView(nextView);
    // The open vote and the party filter both belong to the identities the
    // previous view resolved; neither survives the switch coherently.
    setSelectedVoteId(null);
    setPartyFilter("Todos");
    // The selection itself survives: a person keeps their identity across the
    // switch, which is what makes the panel's "ver la otra integración" link
    // land on the same person rather than on an arbitrary seat.
  }

  if (error) {
    return (
      <main className="state-screen">
        <p className="eyebrow">{SITE_NAME}</p>
        <h1>No pudimos cargar el corte de votaciones.</h1>
        <p>Actualiza la página para intentarlo de nuevo.</p>
      </main>
    );
  }

  if (!data || !reading) {
    return (
      <main className="state-screen loading-state" aria-live="polite">
        <span className="loading-mark" />
        <p className="eyebrow">{SITE_NAME}</p>
        <h1>Preparando el pleno…</h1>
      </main>
    );
  }

  const activeVotes = history.filter(([, choice]) =>
    ["Favor", "Contra", "Abstención", "Abstencion"].includes(choice),
  );
  const attendance = history.length
    ? 1 - history.filter(([, choice]) => choice === "Ausente").length / history.length
    : 0;
  const favorRate = activeVotes.length
    ? activeVotes.filter(([, choice]) => choice === "Favor").length / activeVotes.length
    : 0;
  const previewActive = previewHistory.filter(([, choice]) => choice !== "Ausente");
  const partyVotePercentages = Object.entries((selectedVote && data.partyVotes[selectedVote.id]) ?? {})
    .map(([party, counts]) => {
      const favor = counts.Favor ?? 0;
      const contra = counts.Contra ?? 0;
      const abstention = counts["Abstención"] ?? counts.Abstencion ?? 0;
      const absent = counts.Ausente ?? 0;
      const total = favor + contra + abstention + absent;
      return { party, favor, contra, abstention, absent, total };
    })
    .filter((row) => row.total > 0)
    .sort((a, b) => partyRank(a.party) - partyRank(b.party));
  const rosterCutoff = data.manifest.roster.observedAt.slice(0, 10);
  const isCurrentView = view === "actual";
  // No seat at all: nothing to mark, so the chamber goes fully dark.
  const seatless = !reading.seat;
  // A seat we can name but the active tab does not show this person in.
  const offFloor = !reading.onFloor;
  const formerCount = data.formerMembers?.length ?? 0;
  const otherViewLabel =
    reading.homeView === "electoral" ? "la integración de 2024" : "quién ocupa el escaño hoy";

  return (
    <main>
      <SiteHeader active="visualizaciones" status="LXVI Legislatura" />

      <section className="hero" id="inicio">
        <div>
          <p className="eyebrow">
            <a href="/visualizaciones">Visualizaciones</a> · {config.name}
          </p>
          <h1>Así vota el pleno.</h1>
        </div>
        <p className="hero-copy">
          Explora las {data.manifest.voteCount} votaciones nominales de la LXVI Legislatura.
        </p>
        <div className="hero-stats" aria-label="Resumen del conjunto de datos">
          <div><strong>{data.manifest.seatCount}</strong><span>escaños</span></div>
          <div><strong>{data.manifest.voteCount}</strong><span>votaciones</span></div>
          <div><strong>{data.manifest.substitutedSeats}</strong><span>escaños con relevo</span></div>
        </div>
      </section>

      <section className="explorer" id="pleno">
        {/* Exactly two answers, never a date picker. "Quién está hoy" and "quién
            fue electo" are the only two questions a reader has; exposing the
            roster snapshot as a third axis made the page feel like a database. */}
        <div className="view-tabs" role="tablist" aria-label="Identidad mostrada en cada escaño">
          <button
            type="button"
            role="tab"
            aria-selected={isCurrentView}
            className={isCurrentView ? "active" : ""}
            onClick={() => selectView("actual")}
          >
            Quién ocupa el escaño hoy
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={!isCurrentView}
            className={!isCurrentView ? "active" : ""}
            onClick={() => selectView("electoral")}
          >
            Quién lo ganó en 2024
          </button>
        </div>

        <div className="section-heading">
          <div>
            <p className="eyebrow">Explorador interactivo</p>
            <h2>El pleno, escaño por escaño</h2>
          </div>
          <p>Pasa el cursor para una lectura rápida. Haz clic para fijar {config.pin}.</p>
        </div>

        <div className="explorer-grid">
          <div className="chamber-card">
            <div className="toolbar">
              <span className="toolbar-label">Partido</span>
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

            {!isSenate && districtIndex && (
              <div className="district-finder">
                <div>
                  <strong>Encuentra tu distrito</strong>
                  <span>Selecciona estado y municipio; el código postal está en beta.</span>
                </div>
                <form
                  className="postal-lookup"
                  onSubmit={(event) => {
                    event.preventDefault();
                    void findByPostalCode();
                  }}
                >
                  <label>
                    <span className="sr-only">Código postal</span>
                    <input
                      value={postalCode}
                      inputMode="numeric"
                      maxLength={5}
                      pattern="[0-9]{5}"
                    placeholder="Código postal (beta)"
                      onChange={(event) => setPostalCode(event.target.value.replace(/\D/g, ""))}
                    />
                  </label>
                  <button type="submit" disabled={postalLoading || postalCode.length !== 5}>
                    {postalLoading ? "Buscando…" : "Buscar"}
                  </button>
                </form>
                {districtResolution && !districtResolution.state && (
                  <p className="postal-feedback" aria-live="polite">
                    {districtResolution.message}
                  </p>
                )}
              </div>
            )}

            <div className="toolbar">
              <span className="toolbar-label">Estado</span>
              <label className="toolbar-select">
                <span className="sr-only">Filtrar por estado</span>
                <select
                  value={stateFilter}
                  onChange={(event) => {
                    setStateFilter(event.target.value);
                    setDistrictFilter("Todos");
                    setMunicipalityFilter("");
                    setDistrictResolution(null);
                    setDistrictQuery("");
                    setDistrictOpen(false);
                  }}
                >
                  <option value="Todos">Todos los estados</option>
                  {states.map((state) => (
                    <option key={state} value={state}>
                      {state}
                    </option>
                  ))}
                </select>
              </label>
            </div>

            {!isSenate && municipalitiesForState.length > 0 && (
              <div className="toolbar municipality-toolbar">
                <span className="toolbar-label">Municipio</span>
                <label className="toolbar-select">
                  <span className="sr-only">Seleccionar municipio</span>
                  <select
                    value={municipalityFilter}
                    onChange={(event) => {
                      const municipality = event.target.value;
                      setMunicipalityFilter(municipality);
                      if (!districtIndex || !municipality || stateFilter === "Todos") {
                        setDistrictResolution(null);
                        setDistrictFilter("Todos");
                        return;
                      }
                      applyDistrictResolution(
                        resolveMunicipalityDistricts(districtIndex, stateFilter, municipality),
                      );
                    }}
                  >
                    <option value="">Selecciona municipio</option>
                    {municipalitiesForState.map((municipality) => (
                      <option key={`${municipality.id}-${municipality.name}`} value={municipality.name}>
                        {municipality.name}
                      </option>
                    ))}
                  </select>
                </label>
                {districtResolution && (
                  <div className="district-resolution" aria-live="polite">
                    <span>{districtResolution.message}</span>
                    {districtResolution.districts.length > 1 && (
                      <div>
                        {districtResolution.districts.map((district) => (
                          <button
                            type="button"
                            key={district}
                            className={districtFilter === String(district) ? "active" : ""}
                            onClick={() => setDistrictFilter(String(district))}
                          >
                            Distrito {district}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}

            {districtsForState.length > 0 && (
              <div className="toolbar">
                <span className="toolbar-label">Distrito</span>
                <div className="district-search">
                  <label className="toolbar-select">
                    <span className="sr-only">Buscar distrito</span>
                    <input
                      type="text"
                      value={
                        districtOpen
                          ? districtQuery
                          : (districtsForState.find((option) => String(option.district) === districtFilter)?.label ?? "")
                      }
                      placeholder="Todos los distritos"
                      onFocus={() => {
                        setDistrictOpen(true);
                        setDistrictQuery("");
                      }}
                      onClick={() => {
                        setDistrictOpen(true);
                        setDistrictQuery("");
                      }}
                      onBlur={() => setDistrictOpen(false)}
                      onChange={(event) => setDistrictQuery(event.target.value)}
                    />
                  </label>
                  {districtOpen && (
                    // Selecting an option must not blur the input first — a blur
                    // would close this list before the click landed on it.
                    <div className="district-results" role="listbox" aria-label="Distritos" onMouseDown={(event) => event.preventDefault()}>
                      <button
                        type="button"
                        role="option"
                        aria-selected={districtFilter === "Todos"}
                        onClick={() => {
                          setDistrictFilter("Todos");
                          setDistrictQuery("");
                          setDistrictOpen(false);
                        }}
                      >
                        Todos los distritos
                      </button>
                      {districtsForState
                        .filter((option) => {
                          const q = normalize(districtQuery);
                          return q.length === 0 || normalize(option.label).includes(q) || String(option.district).includes(q);
                        })
                        .map((option) => (
                          <button
                            type="button"
                            key={option.district}
                            role="option"
                            aria-selected={districtFilter === String(option.district)}
                            onClick={() => {
                              setDistrictFilter(String(option.district));
                              setDistrictQuery("");
                              setDistrictOpen(false);
                            }}
                          >
                            {option.label}
                          </button>
                        ))}
                    </div>
                  )}
                </div>
              </div>
            )}

            <div
              id="hemiciclo"
              className={`hemicycle ${isSenate ? "senate-hemicycle" : ""} ${
                offFloor ? "hemicycle-off" : ""
              }`}
              role="group"
              aria-label={
                seatless
                  ? `Hemiciclo de ${data.manifest.seatCount} escaños, ninguno corresponde a la persona seleccionada`
                  : offFloor
                    ? `Hemiciclo de ${data.manifest.seatCount} escaños, atenuado: se marca el escaño de origen de ${reading.name}`
                    : `Hemiciclo de ${data.manifest.seatCount} escaños, coloreado por partido`
              }
            >
              <div className="dais" aria-hidden="true" />
              {coords.map((seat) => {
                const occupant = occupants.get(seat.id)!;
                const isOrigin = seat.id === originSeatId;
                return (
                  <button
                    key={seat.id}
                    type="button"
                    aria-label={
                      isOrigin
                        ? `Escaño titular de ${seat.titularName}; escaño de origen de ${reading.name}; hoy lo ocupa ${occupant.name}, ${occupant.party}${seat.substituteName ? `; suplente registrado: ${seat.substituteName}` : ""}`
                        : `${seat.titularName}, titular; ${occupant.party}; ${seatTypeLabel(seat.seatType)}${seat.substituteName ? `; suplente registrado: ${seat.substituteName}` : ""}`
                    }
                    className={`seat-dot seat-${seat.seatType.toLowerCase()} ${
                      seat.id === floorSeatId ? "selected" : ""
                    } ${isOrigin ? "seat-origin-mark" : ""} ${
                      isSeatVisible(seat) ? "" : "muted"
                    } ${occupant.status === "vacante" ? "seat-vacant" : ""
                    }`}
                    style={{
                      left: `${seat.x}%`,
                      top: `${seat.y}%`,
                      // The origin mark is hollow: a filled dot at full opacity
                      // in a dimmed chamber reads as a normal selection, which
                      // is the one thing it must not be mistaken for.
                      backgroundColor: isOrigin
                        ? "transparent"
                        : PARTY_COLORS[occupant.party] ?? "#74736e",
                      borderColor: isOrigin
                        ? PARTY_COLORS[occupant.party] ?? "#74736e"
                        : undefined,
                    }}
                    onMouseEnter={() => setHoveredSeatId(seat.id)}
                    onMouseLeave={() => setHoveredSeatId(null)}
                    onFocus={() => setHoveredSeatId(seat.id)}
                    onBlur={() => setHoveredSeatId(null)}
                    onClick={() => selectSeat(seat)}
                  />
                );
              })}
              {offFloor && (
                <p className="hemicycle-note">
                  {seatless ? (
                    <>
                      No pudimos vincular a <strong>{reading.name}</strong> con un escaño de esta
                      cámara: su nombre no aparece en el registro de suplencias del INE. Su
                      historial de votación sigue a la derecha.
                    </>
                  ) : (
                    <>
                      <strong>{reading.name}</strong>{" "}
                      {reading.former?.seatRole === "suplencia_concluida"
                        ? "cubrió la suplencia del escaño marcado"
                        : reading.homeView === "electoral"
                          ? "ganó el escaño marcado en 2024"
                          : "ocupa hoy el escaño marcado"}
                      , que en esta vista aparece a nombre de{" "}
                      <strong>{occupants.get(reading.seat!.id)!.name}</strong>.
                      {reading.homeView && (
                        <>
                          {" "}
                          <button
                            type="button"
                            className="hemicycle-note-link"
                            onClick={() => selectView(reading.homeView!)}
                          >
                            Ver {otherViewLabel} →
                          </button>
                        </>
                      )}
                    </>
                  )}
                </p>
              )}
            </div>

            {previewSeat && previewOccupant && (
              <div className="hover-reader" aria-live="polite">
                <div className="party-badge" style={{ background: PARTY_COLORS[previewOccupant.party] }}>
                  {previewOccupant.party}
                </div>
                <div className="hover-identity">
                  <strong>{previewSeat.titularName}</strong>
                  <span>
                    {previewSeat.seatType} · {previewSeat.state ?? `Circunscripción ${previewSeat.circunscripcion}`}
                  </span>
                  {previewSeat.substituteName && (
                    <span className="registered-substitute">Suplente: {previewSeat.substituteName}</span>
                  )}
                  {previewOccupant.substituted ? (
                    <span className="election-result-preview">
                      En funciones: {previewOccupant.name}
                    </span>
                  ) : (
                    previewSeat.seatType !== "RP" && previewSeat.winningPct !== null && (
                      <span className="election-result-preview">
                        {previewSeat.seatType === "FM" ? "Primera minoría en 2024 con " : "Ganó en 2024 con "}
                        {previewSeat.winningPct.toLocaleString("es-MX", {
                          minimumFractionDigits: 1,
                          maximumFractionDigits: 2,
                        })}% · {previewSeat.winningVotes?.toLocaleString("es-MX")} votos
                      </span>
                    )
                  )}
                </div>
                <span className="hover-count">{previewActive.length} participaciones</span>
              </div>
            )}

            <div className="legend party-legend" aria-label="Composición por partido">
              {parties.map((party) => {
                const count = partyCounts.get(party) ?? 0;
                return (
                  <span key={party}>
                    <i style={{ background: PARTY_COLORS[party] ?? "#74736e" }} />
                    <b>{party}</b>
                    <strong>{count} escaños</strong>
                    <small>{((count / data.manifest.seatCount) * 100).toFixed(1)}%</small>
                  </span>
                );
              })}
            </div>
            <div className="seat-type-key" aria-label="Tipo de elección del escaño">
              <span><i className="key-square" /> Mayoría relativa · {data.seats.filter((seat) => seat.seatType === "MR").length}</span>
              {isSenate && <span><i className="key-diamond" /> Primera minoría · 32</span>}
              <span><i className="key-circle" /> Representación proporcional · {data.seats.filter((seat) => seat.seatType === "RP").length}</span>
            </div>

          </div>

          <aside className="deputy-panel" id="historial">
            <div className="panel-search">
              <label className="panel-search-field">
                <span aria-hidden="true">⌕</span>
                <input
                  type="search"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder={config.memberSearch}
                  aria-label={config.memberSearch}
                />
              </label>
              {queryNormalized.length >= 2 && (
                <div className="panel-results" role="listbox" aria-label="Resultados de la búsqueda">
                  {results.slice(0, MAX_RESULTS).map((person) => (
                    <button
                      type="button"
                      key={person.key}
                      role="option"
                      aria-selected={false}
                      onClick={() => selectPerson(person)}
                    >
                      <i style={{ background: PARTY_COLORS[person.party] ?? "#74736e" }} aria-hidden="true" />
                      <span className="panel-result-copy">
                        <strong>{person.name}</strong>
                        <small>
                          {person.party}
                          {person.tag ? ` · ${person.tag}` : ""}
                        </small>
                      </span>
                    </button>
                  ))}
                  {results.length > MAX_RESULTS && (
                    <p className="panel-results-more">
                      y {results.length - MAX_RESULTS} más. Escribe un poco más para acotar.
                    </p>
                  )}
                  {!results.length && (
                    <p className="panel-results-empty">
                      Nadie con ese nombre en {config.name}.{" "}
                      <a href={`/visualizaciones/${config.other}?q=${encodeURIComponent(query.trim())}`}>
                        Buscar en {config.otherName} →
                      </a>
                    </p>
                  )}
                </div>
              )}
            </div>

            <div className="deputy-heading">
              <div>
                <span className="large-party" style={{ color: PARTY_COLORS[reading.party] }}>
                  {reading.party}
                </span>
                <h2>{isSeatHistory && selectedSeat ? selectedSeat.titularName : reading.name}</h2>
                <p>
                  {seatless
                    ? `${config.short} · sin escaño identificado`
                    : !isSenate && selectedSeat!.seatType === "MR"
                      ? `${selectedSeat!.state} · Distrito ${selectedSeat!.district}${selectedSeat!.districtSeat ? ` · ${selectedSeat!.districtSeat}` : ""}`
                      : !isSenate
                        ? `Representación proporcional · Circunscripción ${selectedSeat!.circunscripcion} · Lista ${selectedSeat!.listNumber}`
                        : selectedSeat!.seatType === "RP"
                          ? `Representación proporcional · Lista nacional · Posición ${selectedSeat!.listNumber}`
                          : `${seatTypeLabel(selectedSeat!.seatType)} · ${selectedSeat!.state}`}
                  {reading.status && isCurrentView ? ` · ${statusLabel(reading.status)}` : ""}
                  {selectedSeat?.substituteName && (
                    <><br />Suplente: {selectedSeat.substituteName}</>
                  )}
                </p>
              </div>
              {!seatless && (
                <span className="seat-number">
                  {coords.findIndex((seat) => seat.id === selectedSeat!.id) + 1}
                </span>
              )}
            </div>

            {seatless && (
              <p className="seat-flag">
                Votó en esta cámara, pero no pudimos vincular a esta persona con un escaño: no
                aparece en el registro de suplencias del INE ni en el directorio. Su historial es
                el de las votaciones en las que sí participó.
              </p>
            )}

            {/* Where this record sat. A former member needs it even when the
                chamber is lit, because the seat is ringed under a different
                name than the one the panel is reading. */}
            {reading.former && selectedSeat && (
              <p className="seat-origin">
                {reading.former.seatRole === "en_funciones" ? (
                  <>
                    Ocupa este escaño hoy. La lista nominal registra estas votaciones bajo una
                    segunda identidad, separada de la de{" "}
                    <strong>{occupants.get(selectedSeat.id)!.name}</strong>.
                  </>
                ) : (
                  <>
                    Cubrió la suplencia de este escaño, ganado en 2024 por{" "}
                    <strong>{selectedSeat.titularName}</strong> ({selectedSeat.electedParty}), que
                    hoy vuelve a ocuparlo.{" "}
                    {reading.former.relationshipSourceUrl && (
                      <a href={reading.former.relationshipSourceUrl} target="_blank" rel="noreferrer">
                        Ver fuente oficial ↗
                      </a>
                    )}
                  </>
                )}
              </p>
            )}

            {/* The same person, seen from the tab that names someone else in
                their seat. Naming that occupant answers "then where are they?". */}
            {offFloor && !seatless && !reading.former && (
              <p className="seat-origin">
                {reading.homeView === "electoral" ? (
                  <>
                    Ganó este escaño en 2024. Hoy lo ocupa{" "}
                    <strong>{selectedSeat!.currentName}</strong> ({selectedSeat!.currentParty}).
                  </>
                ) : (
                  <>
                    Ocupa este escaño hoy. En 2024 lo ganó{" "}
                    <strong>{selectedSeat!.titularName}</strong> ({selectedSeat!.electedParty}).
                  </>
                )}
              </p>
            )}

            {reading.onFloor && isCurrentView && reading.substituted && (
              <p className="seat-origin">
                {isSeatHistory ? (
                  <>En funciones: <strong>{reading.name}</strong> · suplencia.</>
                ) : (
                  <>Suplente del escaño de <strong>{selectedSeat!.titularName}</strong>.</>
                )}
              </p>
            )}

            {reading.status === "licencia" && (
              <p className="seat-flag">
                El directorio marca a esta persona con licencia. El escaño no se atribuye a un grupo
                activo hasta identificar una suplencia en una fuente oficial.
              </p>
            )}
            {reading.status === "vacante" && (
              <p className="seat-flag">Este escaño figura vacante en el directorio oficial consultado.</p>
            )}
            {reading.onFloor && isCurrentView && !profilePersonId && reading.status !== "vacante" && (
              <p className="seat-flag">
                Esta persona aún no tiene votaciones nominales enlazadas en la base local. Las
                cifras de abajo quedan en cero hasta que aparezca en una votación descargada.
              </p>
            )}

            {isSeatHistory && hasSubstituteVotes && (
              <div className="seat-history-attribution">
                <p>
                  <strong>Este historial combina el escaño.</strong> Incluye votos emitidos por la
                  persona titular y por quienes cubrieron una suplencia.
                </p>
                <div className="occupant-filter" role="group" aria-label="Separar votos por ocupante">
                  {([
                    ["all", "Todo el escaño"],
                    ["titular", "Titular"],
                    ["suplente", "Suplencias"],
                  ] as [HistoryMode, string][]).map(([mode, text]) => (
                    <button
                      key={mode}
                      type="button"
                      className={historyMode === mode ? "active" : ""}
                      aria-pressed={historyMode === mode}
                      onClick={() => {
                        setHistoryMode(mode);
                        setTopic(ALL_TOPICS);
                        setSelectedVoteId(null);
                      }}
                    >
                      {text}
                    </button>
                  ))}
                </div>
                <ul>
                  {selectedSeatMembers
                    .filter((member) => member.voteCount > 0)
                    .map((member) => (
                      <li key={member.personId}>
                        <span>
                          {member.name} · {member.role === "titular" ? "titular" : "suplencia"} ·{" "}
                          {member.voteCount} votos
                        </span>
                        {member.sourceUrl && (
                          <a href={member.sourceUrl} target="_blank" rel="noreferrer">
                            fuente oficial ↗
                          </a>
                        )}
                      </li>
                    ))}
                </ul>
              </div>
            )}

            <div className={`deputy-metrics ${seatless ? "metrics-seatless" : ""}`}>
              <div><strong>{history.length}</strong><span>{isSeatHistory ? "registros del escaño" : "registros"}</span></div>
              <div><strong>{attendance.toLocaleString("es-MX", { style: "percent", maximumFractionDigits: 0 })}</strong><span>asistencia</span></div>
              <div><strong>{favorRate.toLocaleString("es-MX", { style: "percent", maximumFractionDigits: 0 })}</strong><span>voto a favor</span></div>
              {!seatless && (
                <div className="election-metric">
                  {selectedSeat!.seatType !== "RP" && selectedSeat!.winningPct !== null ? (
                    <>
                      <strong>{selectedSeat!.winningPct.toLocaleString("es-MX", {
                        minimumFractionDigits: 1,
                        maximumFractionDigits: 2,
                      })}%</strong>
                      <span>{selectedSeat!.winningVotes?.toLocaleString("es-MX")} votos · {selectedSeat!.seatType === "FM" ? "primera minoría" : "elección 2024"}</span>
                      {selectedSeat!.electionActor && (
                        <small>{electionActorLabel(selectedSeat!.electionActor)}</small>
                      )}
                    </>
                  ) : (
                    <>
                      <strong>Lista {selectedSeat!.listNumber}</strong>
                      <span>asignación RP · 2024</span>
                    </>
                  )}
                </div>
              )}
            </div>

            <div className="history-label">
              <span>Calendario de votación</span>
              <label className="history-topic">
                <span className="sr-only">Filtrar el historial por tema</span>
                <select
                  value={topic}
                  onChange={(event) => {
                    setTopic(event.target.value);
                    // The open vote may not survive the new filter.
                    setSelectedVoteId(null);
                  }}
                >
                  <option value={ALL_TOPICS}>Todos los temas ({fullHistory.length})</option>
                  {topics.map(([value, count]) => (
                    <option key={value} value={value}>
                      {topicLabel(value)} ({count})
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <div className="panel-calendar">
              {calendarYears.length === 0 && (
                <p className="calendar-empty">
                  {topic !== ALL_TOPICS
                    ? "No hay votaciones de este tema en el historial de esta persona."
                    : profilePersonId
                      ? "Este escaño todavía no tiene votaciones registradas."
                      : "Esta persona aún no tiene votaciones nominales enlazadas en la base local."}
                </p>
              )}
              {calendarYears.map(({ year, entries }) => (
                <div className="calendar-year" key={year}>
                  <span className="calendar-year-label">{year}</span>
                  <div className="calendar-track" role="group" aria-label={`Votaciones de ${year}`}>
                    {entries.map(({ voteId, choice, date, title, member }) => (
                      <button
                        type="button"
                        key={voteId}
                        className={`calendar-cell ${selectedVoteId === voteId ? "selected" : ""}`}
                        style={{ background: CHOICE_COLORS[choice] ?? "#8b8b86" }}
                        title={`${shortDate(date)} · ${voteLabel(choice)}${member ? ` · ${member.name}${member.role === "suplente" ? " (suplencia)" : ""}` : ""} · ${shortTitle(title)}`}
                        aria-label={`${shortDate(date)}, ${voteLabel(choice)}${member ? `, emitido por ${member.name}${member.role === "suplente" ? " como suplente" : ""}` : ""}, ${shortTitle(title)}`}
                        aria-pressed={selectedVoteId === voteId}
                        onClick={() => openVote(voteId)}
                      />
                    ))}
                  </div>
                </div>
              ))}
              {calendarYears.length > 0 && (
                <div className="calendar-key">
                  <span><i style={{ background: CHOICE_COLORS.Favor }} /> Favor</span>
                  <span><i style={{ background: CHOICE_COLORS.Contra }} /> Contra</span>
                  <span><i style={{ background: CHOICE_COLORS["Abstención"] }} /> Abst.</span>
                  <span><i style={{ background: CHOICE_COLORS.Ausente }} /> Ausente</span>
                  {!isSenate && <span><i style={{ background: CHOICE_COLORS["Quórum *"] }} /> Presente, sin voto</span>}
                  <span className="calendar-hint">Cada rectángulo, una votación.</span>
                </div>
              )}
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
              <p className="vote-stage">{stageLabel(selectedVote.stage)}</p>
            )}
          </div>
          <a href={selectedVote.sourceUrl} target="_blank" rel="noreferrer">
            Ver fuente oficial <span aria-hidden="true">↗</span>
          </a>
        </div>

        <div className="vote-tags">
          <span>{isSenate ? label(selectedVote.status) : stageLabel(selectedVote.stage)}</span>
          <span>{topicLabel(selectedVote.topic)}</span>
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

          <div className="decision-hemicycle-panel">
            <div className="decision-hemicycle-heading">
              <div>
                <p className="panel-kicker">Decisión en el pleno</p>
              </div>
              <div className="party-filter" aria-label="Filtrar por partido">
                {["Todos", ...partyVotePercentages.map((row) => row.party)].map((party) => (
                  <button
                    key={party}
                    className={voteFilterParty === party ? "active" : ""}
                    onClick={() => setVoteFilterParty(party)}
                    type="button"
                  >
                    {party}
                  </button>
                ))}
              </div>
            </div>
            <div
              className={`hemicycle decision-hemicycle ${isSenate ? "senate-hemicycle" : ""}`}
              role="img"
              aria-label={`Hemiciclo de ${data.manifest.seatCount} escaños, coloreado por sentido del voto${
                voteFilterParty !== "Todos" ? `, atenuado a ${voteFilterParty}` : ""
              }`}
            >
              <div className="dais" aria-hidden="true" />
              {coords.map((seat) => {
                const occupant = occupants.get(seat.id)!;
                const choice = choiceBySeat.get(seat.id);
                const unrecorded = !choice;
                // The tooltip and the filter both read the person who actually cast
                // this vote, which for a seat that changed hands or party since can
                // differ from either tab's occupant.
                const voter = voteVoterBySeat.get(seat.id);
                return (
                  <span
                    key={seat.id}
                    className={`seat-dot seat-${seat.seatType.toLowerCase()} ${
                      seat.id === floorSeatId ? "selected" : ""
                    } ${unrecorded ? "no-record" : ""} ${isVoteSeatVisible(seat) ? "" : "muted"}`}
                    title={`${voter?.name ?? occupant.name} · ${voter?.party ?? occupant.party}: ${choice ? voteLabel(choice) : "sin registro en esta votación"}`}
                    style={{
                      left: `${seat.x}%`,
                      top: `${seat.y}%`,
                      backgroundColor: unrecorded
                        ? "transparent"
                        : CHOICE_COLORS[choice ?? ""] ?? "#8b8b86",
                    }}
                  />
                );
              })}
            </div>
            <div className="legend legend-vote decision-legend">
              {["Favor", "Contra", "Abstención", "Ausente", "Quórum *"]
                .filter((choice) => (choiceTotals.get(choice) ?? 0) > 0)
                .map((choice) => {
                  const count = choiceTotals.get(choice) ?? 0;
                  return (
                    <span key={choice}>
                      <i style={{ background: CHOICE_COLORS[choice] }} />
                      {voteLabel(choice)} <strong>{count}</strong> · {((count / data.manifest.seatCount) * 100).toFixed(1)}%
                    </span>
                  );
                })}
              {unrecordedSeats > 0 && (
                <span>
                  <i className="key-hollow" />
                  Sin registro <strong>{unrecordedSeats}</strong> · {((unrecordedSeats / data.manifest.seatCount) * 100).toFixed(1)}%
                </span>
              )}
            </div>
            <div className="party-vote-percentages" aria-label="Porcentaje de voto dentro de cada partido">
              <p>Porcentaje dentro de cada partido</p>
              <div>
                {partyVotePercentages.map((row) => (
                  <span key={row.party}>
                    <b style={{ color: PARTY_COLORS[row.party] ?? "#333" }}>{row.party}</b>
                    <small>
                      Favor {((row.favor / row.total) * 100).toFixed(0)}% · Contra {((row.contra / row.total) * 100).toFixed(0)}%
                      {row.abstention > 0 && ` · Abst. ${((row.abstention / row.total) * 100).toFixed(0)}%`}
                      {row.absent > 0 && ` · Ausente ${((row.absent / row.total) * 100).toFixed(0)}%`}
                    </small>
                  </span>
                ))}
              </div>
            </div>
          </div>
        </div>
      </section>
      )}

      <section className="method-note" id="metodologia">
        <p className="eyebrow">Corte y metodología</p>
        {/* The section is a two-column grid: eyebrow, then one body cell.
            Extra paragraphs go inside the wrapper, never as grid siblings. */}
        <div className="method-body">
          <p>
            Votaciones nominales publicadas por {config.sourceName}. Corte al{" "}
            <strong>{shortDate(data.manifest.sourceThrough)}</strong>. La composición actual proviene
            del <a href={data.manifest.roster.sourceUrl} target="_blank" rel="noreferrer">directorio
            oficial</a> observado el <strong>{shortDate(rosterCutoff)}</strong>; el resultado
            electoral de 2024 proviene del INE. Los temas y etapas son clasificaciones analíticas;
            los registros individuales conservan su vínculo con la fuente oficial. Cada tabla del
            almacén que sostiene estas cifras está documentada en el{" "}
            <a href="/datos">diccionario de datos</a>.
          </p>
          <p>
            Las gráficas incluyen todos los registros de votación de la LXVI Legislatura publicados
            y descargados de la fuente oficial, vinculados con un escaño constitucional. Los escaños
            omitidos por la fuente permanecen como <strong>«Sin registro»</strong>: no se imputan como
            ausencias ni se les asigna un voto inventado.
          </p>
          <p>
            Un escaño puede haber tenido más de un ocupante. Al seleccionar una curul, el historial
            combina todos los votos vinculados con ese escaño y permite separarlos entre titular y
            suplencias. La suma es del <em>escaño</em>: cada registro sigue nombrando a la persona
            que realmente emitió el voto y nunca se presenta como voto personal del titular.{" "}
            <strong>{data.manifest.seatCount - data.manifest.currentLinkedSeats}</strong> ocupantes
            actuales todavía no aparecen en ninguna votación descargada.
          </p>
          <p>
            La búsqueda alcanza tres identidades distintas: quien ocupa el escaño hoy, quien lo ganó
            en 2024 y las <strong>{formerCount}</strong> personas con votaciones registradas que ya
            no tienen escaño. Una búsqueda por persona conserva su historial individual; un clic en
            el hemiciclo abre el historial combinado del escaño. Las relaciones de suplencia enlazan
            la fuente oficial usada para asignar esos votos a la curul.
          </p>
        </div>
      </section>

      <SiteFooter note={`Datos · ${config.short}`} />
    </main>
  );
}
