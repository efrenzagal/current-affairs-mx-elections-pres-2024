"use client";

import { type MouseEvent as ReactMouseEvent, memo, useEffect, useMemo, useRef, useState } from "react";

import { SiteFooter, SiteHeader } from "../../site-chrome";
import { partyColor, partyRank } from "../parties";
import {
  CHOICE_ORDER,
  INSTRUMENT_LABELS,
  ORIGIN_LABELS,
  STAGE_LABELS,
  TOPIC_LABELS,
  type VoteReview,
  choiceColor,
  cleanTitle,
  consensusMargin,
  instrumentLabel,
  isApproved,
  longDate,
  normalize,
  originLabel,
  reviewKey,
  reviewLabel,
  shortDate,
  stageLabel,
  topicLabel,
  voteLabel,
} from "../votes";

type Chamber = "diputados" | "senado";
type ChamberFilter = "todas" | Chamber;

type Thresholds = {
  present: number;
  quorumRequired: number;
  absoluteRequired: number;
  qualifiedRequired: number;
  quorumOk: boolean;
  simpleOk: boolean;
  absoluteOk: boolean;
  qualifiedOk: boolean;
};

type Vote = {
  id: string;
  chamber: Chamber;
  date: string;
  title: string;
  status: string | null;
  sourceUrl: string;
  gacetaUrl: string | null;
  favor: number;
  contra: number;
  abstention: number;
  absent: number;
  presentNoVote: number;
  total: number;
  topic: string | null;
  stage: string | null;
  origin: string | null;
  instrument: string | null;
  review: VoteReview;
  /** Camara only — see `add_camara_thresholds` in the exporter for why. */
  thresholds: Thresholds | null;
};

type VotesData = {
  manifest: {
    schemaVersion: number;
    legislature: number;
    sourceThrough: string;
    voteCount: number;
    chambers: Record<Chamber, number>;
    topicCount: number;
  };
  votes: Vote[];
  /**
   * Keyed `"<chamber>:<id>"`. Vote IDs are chamber-local, so this is the one
   * place the two namespaces meet and they must stay namespaced here too.
   */
  partyVotes: Record<string, Record<string, Record<string, number>>>;
};

/**
 * Names for the individual squares, mirroring `partyVotes`: for each party and
 * choice, the people in that bucket as indices into `names`.
 *
 * Its own file because the search does not need it. The list, the filters and
 * the totals all work from `votes-66.json`; this only has to arrive before a
 * reader hovers a square, so it loads in parallel and the grid degrades to
 * unnamed squares until it does.
 */
type Ballots = {
  names: string[];
  ballots: Record<string, Record<string, Record<string, number[]>>>;
};

/** Rows added per press of "mostrar más". 673 at once is unreadable. */
const PAGE_SIZE = 10;

const CHAMBER_LABELS: Record<Chamber, string> = {
  diputados: "Cámara de Diputados",
  senado: "Senado de la República",
};

const CHAMBER_SHORT: Record<Chamber, string> = {
  diputados: "Cámara",
  senado: "Senado",
};

/**
 * The four classification axes, in the order a reader narrows by them: subject
 * first, then the procedural detail. Each renders the same chip control, and
 * each derives its options from the payload, so an axis that gains a code shows
 * it without a change here.
 */
const FACETS = [
  { key: "topic", param: "tema", label: "Tema de política", labels: TOPIC_LABELS },
  { key: "stage", param: "etapa", label: "Etapa de la votación", labels: STAGE_LABELS },
  { key: "origin", param: "origen", label: "Origen", labels: ORIGIN_LABELS },
  { key: "instrument", param: "instrumento", label: "Tipo de instrumento", labels: INSTRUMENT_LABELS },
] as const;

type FacetKey = (typeof FACETS)[number]["key"];

type ResultFilter = "todas" | "aprobadas" | "rechazadas";
type MarginFilter = "todas" | "unanime" | "amplio" | "dividida";
type SortKey = "recientes" | "antiguas" | "divididas" | "participacion";

/**
 * Consensus buckets. Thresholds are round numbers chosen to separate the three
 * things a reader actually looks for — near-unanimity, a comfortable margin and
 * a genuine split — rather than to match any rule of the chambers.
 */
const MARGIN_BUCKETS: Record<Exclude<MarginFilter, "todas">, { label: string; test: (margin: number) => boolean }> = {
  unanime: { label: "Unánime o casi (≥95%)", test: (margin) => margin >= 0.95 },
  amplio: { label: "Margen amplio (60–95%)", test: (margin) => margin >= 0.6 && margin < 0.95 },
  dividida: { label: "Dividida (<60%)", test: (margin) => margin < 0.6 },
};

const SORTS: { key: SortKey; label: string }[] = [
  { key: "recientes", label: "Más recientes" },
  { key: "antiguas", label: "Más antiguas" },
  { key: "divididas", label: "Más divididas" },
  { key: "participacion", label: "Mayor participación" },
];

function voteKey(vote: Pick<Vote, "chamber" | "id">) {
  return `${vote.chamber}:${vote.id}`;
}

/** Members who registered any choice, including "presente, sin voto". */
function present(vote: Vote) {
  return vote.favor + vote.contra + vote.abstention + vote.presentNoVote;
}

function participation(vote: Vote) {
  return vote.total > 0 ? present(vote) / vote.total : 0;
}

function percent(value: number) {
  return `${Math.round(value * 100)}%`;
}

/**
 * Expand a party's counts into one square per member, in reading order.
 *
 * The counts drive the grid and the names only decorate it. `partyVotes` is the
 * chamber's own tally for that roll call and is authoritative; the per-deputy
 * table disagrees with it on six LXVI votes, filing an independent under `SP`
 * where the tally says `IND`. Sizing the grid from ballots would draw a party
 * block the official record does not contain, so a square with no matching
 * name stays unnamed rather than moving bench.
 */
function squares(
  party: string,
  counts: Record<string, number>,
  entry: Record<string, Record<string, number[]>> | undefined,
  names: string[] | undefined,
) {
  return CHOICE_ORDER.flatMap((choice) => {
    const people = entry?.[party]?.[choice];
    return Array.from({ length: counts[choice] ?? 0 }, (_, index) => ({
      choice,
      name: names && people ? names[people[index]] ?? null : null,
    }));
  });
}

const EMPTY_FACETS: Record<FacetKey, string[]> = {
  topic: [], stage: [], origin: [], instrument: [],
};

/**
 * Seed the whole reading state from the query string.
 *
 * Read at first render rather than in an effect, so a shared link never paints
 * the unfiltered archive first. Server-side there is no `window`, so the shell
 * renders the defaults — which is safe here because nothing filter-dependent
 * exists until the payload arrives, and both sides render the loading state.
 */
function initialState() {
  const blank = {
    query: "",
    chamber: "todas" as ChamberFilter,
    facets: EMPTY_FACETS,
    review: [] as string[],
    result: "todas" as ResultFilter,
    margin: "todas" as MarginFilter,
    sort: "recientes" as SortKey,
    selectedKey: null as string | null,
  };
  if (typeof window === "undefined") return blank;

  const params = new URLSearchParams(window.location.search);
  const list = (name: string) => params.get(name)?.split(",").filter(Boolean) ?? [];
  const chamber = params.get("camara");
  const result = params.get("resultado");
  const margin = params.get("margen");
  const sort = params.get("orden");
  return {
    query: params.get("q") ?? "",
    chamber: chamber === "diputados" || chamber === "senado" ? chamber : blank.chamber,
    facets: {
      topic: list("tema"),
      stage: list("etapa"),
      origin: list("origen"),
      instrument: list("instrumento"),
    },
    review: list("revision"),
    result: result === "aprobadas" || result === "rechazadas" ? result : blank.result,
    margin: margin && margin in MARGIN_BUCKETS ? (margin as MarginFilter) : blank.margin,
    sort: SORTS.some((option) => option.key === sort) ? (sort as SortKey) : blank.sort,
    selectedKey: params.get("v"),
  };
}

type PartyRow = { party: string; counts: Record<string, number>; total: number };

type HoverInfo = {
  name: string | null;
  party: string;
  choice: string;
  top: number;
  left: number;
};

/**
 * The party square blocks.
 *
 * Split out and memoized because the tooltip above it updates on every square
 * the cursor crosses, and a 500-square chamber must not re-render to move a
 * label. Its props deliberately exclude the hover state.
 *
 * One listener on the container rather than a handler per square, reading the
 * square's own data attributes. The native `title` tooltip was the first
 * attempt and is not usable here: it forces a `help` cursor, waits a second
 * before appearing, and cannot be styled.
 */
const PartyGrids = memo(function PartyGrids({
  rows,
  entry,
  names,
  onHover,
}: {
  rows: PartyRow[];
  entry: Record<string, Record<string, number[]>> | undefined;
  names: string[] | undefined;
  onHover: (info: HoverInfo | null) => void;
}) {
  function readSquare(event: ReactMouseEvent<HTMLDivElement>) {
    const square = (event.target as HTMLElement).closest<HTMLElement>(".vote-square");
    // Crossing the 2px gap between squares fires here with the grid itself as
    // the target. Hold the last reading rather than blanking it, or the tooltip
    // strobes on its way across a bench. It clears on leaving the section.
    if (!square) return;
    const rect = square.getBoundingClientRect();
    onHover({
      name: square.dataset.name || null,
      party: square.dataset.party ?? "",
      choice: square.dataset.choice ?? "",
      top: rect.top,
      // Clamped so a square at either edge of the viewport still shows its
      // whole label rather than half of one running off the page.
      left: Math.min(Math.max(rect.left + rect.width / 2, 120), window.innerWidth - 120),
    });
  }

  return (
    <div
      className="party-grids"
      onMouseOver={readSquare}
      onMouseLeave={() => onHover(null)}
    >
      {rows.map((row) => (
        <figure key={row.party} className="party-grid">
          <figcaption>
            <i className="party-key" style={{ background: partyColor(row.party) }} />
            <strong>{row.party}</strong>
            <small>{row.total}</small>
          </figcaption>
          {/* Hidden from assistive tech on purpose: 500 squares would be 500
              stops, and the counts line below says the same thing in words.
              The names are a pointer affordance, not the record. */}
          <div className="square-grid" aria-hidden="true">
            {squares(row.party, row.counts, entry, names).map((square, index) => (
              <i
                key={`${square.choice}-${index}`}
                className="vote-square"
                style={{ background: choiceColor(square.choice) }}
                data-name={square.name ?? undefined}
                data-party={row.party}
                data-choice={square.choice}
              />
            ))}
          </div>
          <p className="party-grid-counts">
            {CHOICE_ORDER.filter((choice) => (row.counts[choice] ?? 0) > 0)
              .map((choice) => `${voteLabel(choice)} ${row.counts[choice]}`)
              .join(" · ")}
          </p>
        </figure>
      ))}
    </div>
  );
});

export default function VoteExplorer() {
  const [data, setData] = useState<VotesData | null>(null);
  const [ballots, setBallots] = useState<Ballots | null>(null);
  const [error, setError] = useState(false);

  const [initial] = useState(initialState);
  const [query, setQuery] = useState(initial.query);
  const [chamber, setChamber] = useState<ChamberFilter>(initial.chamber);
  const [facets, setFacets] = useState<Record<FacetKey, string[]>>(initial.facets);
  const [review, setReview] = useState<string[]>(initial.review);
  const [result, setResult] = useState<ResultFilter>(initial.result);
  const [margin, setMargin] = useState<MarginFilter>(initial.margin);
  const [sort, setSort] = useState<SortKey>(initial.sort);
  const [selectedKey, setSelectedKey] = useState<string | null>(initial.selectedKey);
  const [hover, setHover] = useState<HoverInfo | null>(null);
  const [filtersExpanded, setFiltersExpanded] = useState(false);

  const pendingScroll = useRef(false);

  useEffect(() => {
    let cancelled = false;
    fetch("/data/votes-66.json")
      .then((response) => {
        if (!response.ok) throw new Error(String(response.status));
        return response.json();
      })
      .then((payload: VotesData) => {
        if (!cancelled) setData(payload);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });

    // Names load alongside, never blocking. They decorate the squares; a
    // failure here costs a tooltip, not the page, so it has no error state.
    fetch("/data/vote-ballots-66.json")
      .then((response) => (response.ok ? response.json() : null))
      .then((payload: Ballots | null) => {
        if (!cancelled && payload) setBallots(payload);
      })
      .catch(() => undefined);

    return () => {
      cancelled = true;
    };
  }, []);

  // Mirror the reading state back into the URL so the page can be linked at
  // whatever the reader is looking at. `replaceState` rather than `pushState`:
  // ticking four filter chips should not cost four presses of the back button.
  useEffect(() => {
    const params = new URLSearchParams();
    if (query) params.set("q", query);
    if (chamber !== "todas") params.set("camara", chamber);
    for (const facet of FACETS) {
      if (facets[facet.key].length) params.set(facet.param, facets[facet.key].join(","));
    }
    if (review.length) params.set("revision", review.join(","));
    if (result !== "todas") params.set("resultado", result);
    if (margin !== "todas") params.set("margen", margin);
    if (sort !== "recientes") params.set("orden", sort);
    if (selectedKey) params.set("v", selectedKey);
    const search = params.toString();
    window.history.replaceState(
      null,
      "",
      search ? `${window.location.pathname}?${search}` : window.location.pathname,
    );
  }, [query, chamber, facets, review, result, margin, sort, selectedKey]);

  const inChamber = useMemo(
    () => (data?.votes ?? []).filter((vote) => chamber === "todas" || vote.chamber === chamber),
    [data, chamber],
  );

  /**
   * Option lists come from the votes the *other* filters already allow, so a
   * chip that would return nothing is never offered. Counts are what make the
   * archive legible: "Energía (2)" tells a reader not to expect a trend.
   */
  const facetOptions = useMemo(() => {
    const build = (key: FacetKey) => {
      const counts = new Map<string, number>();
      for (const vote of inChamber) {
        const value = vote[key];
        if (!value) continue;
        counts.set(value, (counts.get(value) ?? 0) + 1);
      }
      return [...counts.entries()].sort((a, b) => b[1] - a[1]);
    };
    return {
      topic: build("topic"),
      stage: build("stage"),
      origin: build("origin"),
      instrument: build("instrument"),
    } as Record<FacetKey, [string, number][]>;
  }, [inChamber]);

  const reviewOptions = useMemo(() => {
    const counts = new Map<string, number>();
    for (const vote of inChamber) {
      const key = reviewKey(vote.review);
      counts.set(key, (counts.get(key) ?? 0) + 1);
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [inChamber]);

  /**
   * Switching chamber can strip a code from the options — `permiso` exists only
   * in the Senado. Such a selection is ignored rather than deleted: it has no
   * chip to un-tick, so leaving it live would empty the list for no visible
   * reason, but discarding it would silently lose the reader's filter when they
   * switch back.
   */
  const activeFacets = useMemo(() => {
    const next = {} as Record<FacetKey, string[]>;
    for (const facet of FACETS) {
      const allowed = new Set(facetOptions[facet.key].map(([value]) => value));
      next[facet.key] = facets[facet.key].filter((value) => allowed.has(value));
    }
    return next;
  }, [facets, facetOptions]);

  const activeReview = useMemo(() => {
    const allowed = new Set(reviewOptions.map(([value]) => value));
    return review.filter((value) => allowed.has(value));
  }, [review, reviewOptions]);

  const filtered = useMemo(() => {
    const terms = normalize(query).split(/\s+/).filter(Boolean);
    const matches = inChamber.filter((vote) => {
      for (const facet of FACETS) {
        const chosen = activeFacets[facet.key];
        if (chosen.length && !chosen.includes(vote[facet.key] ?? "")) return false;
      }
      if (activeReview.length && !activeReview.includes(reviewKey(vote.review))) return false;
      if (result === "aprobadas" && !isApproved(vote)) return false;
      if (result === "rechazadas" && isApproved(vote)) return false;
      if (margin !== "todas") {
        const value = consensusMargin(vote);
        if (value === null || !MARGIN_BUCKETS[margin].test(value)) return false;
      }
      if (terms.length) {
        // Search the title *and* the Spanish topic label, so typing "salud"
        // finds the health votes whose titles never use the word.
        const haystack = normalize(`${cleanTitle(vote.title)} ${topicLabel(vote.topic)}`);
        if (!terms.every((term) => haystack.includes(term))) return false;
      }
      return true;
    });

    const sorted = [...matches];
    if (sort === "recientes") sorted.sort((a, b) => b.date.localeCompare(a.date));
    if (sort === "antiguas") sorted.sort((a, b) => a.date.localeCompare(b.date));
    if (sort === "divididas") {
      sorted.sort((a, b) => (consensusMargin(a) ?? 2) - (consensusMargin(b) ?? 2));
    }
    if (sort === "participacion") sorted.sort((a, b) => participation(b) - participation(a));
    return sorted;
  }, [inChamber, activeFacets, activeReview, result, margin, query, sort]);

  /**
   * How many rows are shown. Stored together with the filter state it belongs
   * to and compared during render, so changing a filter resets the page back to
   * the top without an effect that writes state after paint.
   */
  const signature = JSON.stringify([
    query, chamber, activeFacets, activeReview, result, margin, sort,
  ]);
  const [page, setPage] = useState({ signature, visible: PAGE_SIZE });
  const visible = page.signature === signature ? page.visible : PAGE_SIZE;
  const shown = useMemo(() => filtered.slice(0, visible), [filtered, visible]);

  const selected = useMemo(
    () => data?.votes.find((vote) => voteKey(vote) === selectedKey) ?? null,
    [data, selectedKey],
  );

  const ballotEntry = selectedKey ? ballots?.ballots[selectedKey] : undefined;

  const partyRows = useMemo(() => {
    if (!selected || !data) return [];
    const breakdown = data.partyVotes[voteKey(selected)] ?? {};
    return Object.entries(breakdown)
      .map(([party, counts]) => ({
        party,
        counts,
        total: Object.values(counts).reduce((sum, count) => sum + count, 0),
      }))
      .filter((row) => row.total > 0)
      .sort((a, b) => partyRank(a.party) - partyRank(b.party) || b.total - a.total);
  }, [selected, data]);

  useEffect(() => {
    if (!pendingScroll.current || !selected) return;
    pendingScroll.current = false;
    document.getElementById("detalle")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [selected]);

  function toggleFacet(key: FacetKey, value: string) {
    setFacets((current) => ({
      ...current,
      [key]: current[key].includes(value)
        ? current[key].filter((entry) => entry !== value)
        : [...current[key], value],
    }));
  }

  const advancedFilters =
    FACETS.reduce((sum, facet) => sum + activeFacets[facet.key].length, 0) +
    activeReview.length +
    (result !== "todas" ? 1 : 0) +
    (margin !== "todas" ? 1 : 0);
  const activeFilters =
    (query ? 1 : 0) +
    (chamber !== "todas" ? 1 : 0) +
    advancedFilters;

  function clearFilters() {
    setQuery("");
    setChamber("todas");
    setFacets({ topic: [], stage: [], origin: [], instrument: [] });
    setReview([]);
    setResult("todas");
    setMargin("todas");
  }

  if (error) {
    return (
      <>
        <SiteHeader active="visualizaciones" status="LXVI Legislatura" />
        <main className="vote-page">
          <p className="vote-empty">
            No se pudo cargar el archivo de votaciones. Recarga la página para intentarlo de nuevo.
          </p>
        </main>
        <SiteFooter note="Votaciones nominales · LXVI Legislatura" />
      </>
    );
  }

  if (!data) {
    return (
      <>
        <SiteHeader active="visualizaciones" status="LXVI Legislatura" />
        <main className="vote-page">
          <p className="vote-empty">Preparando las votaciones…</p>
        </main>
        <SiteFooter note="Votaciones nominales · LXVI Legislatura" />
      </>
    );
  }

  return (
    <>
      <SiteHeader active="visualizaciones" status="LXVI Legislatura" />
      <main className="vote-page">
        <header className="vote-hero">
          <p className="eyebrow">Congreso · LXVI Legislatura</p>
          <h1>Buscador de votaciones</h1>
          <p>
            Las {data.manifest.voteCount} votaciones nominales de ambas cámaras, con el desglose por
            grupo parlamentario y la clasificación temática de cada una. Busca por texto, filtra por
            tema y abre cualquier votación para ver cómo se dividió el pleno.
          </p>
          <p className="vote-hero-meta">
            {data.manifest.chambers.diputados} votaciones de la Cámara de Diputados ·{" "}
            {data.manifest.chambers.senado} del Senado · registros hasta el{" "}
            {shortDate(data.manifest.sourceThrough)}
          </p>
        </header>

        <div className="vote-layout">
          <aside className="vote-filters" aria-label="Filtros">
            <label className="search-field">
              <span>Buscar en el título o el tema</span>
              <input
                type="search"
                value={query}
                placeholder="reforma electoral, salud, presupuesto…"
                onChange={(event) => setQuery(event.target.value)}
              />
            </label>

            <fieldset className="vote-filter-group">
              <legend>Cámara</legend>
              <div className="vote-chips">
                {(["todas", "diputados", "senado"] as ChamberFilter[]).map((value) => (
                  <button
                    key={value}
                    type="button"
                    className={chamber === value ? "active" : ""}
                    aria-pressed={chamber === value}
                    onClick={() => setChamber(value)}
                  >
                    {value === "todas" ? "Ambas" : CHAMBER_SHORT[value]}
                  </button>
                ))}
              </div>
            </fieldset>

            <button
              type="button"
              className="vote-filter-more"
              aria-expanded={filtersExpanded}
              aria-controls="vote-advanced-filters"
              onClick={() => setFiltersExpanded((current) => !current)}
            >
              <span>{filtersExpanded ? "Ocultar filtros" : "Más filtros"}</span>
              <span>{advancedFilters > 0 ? `${advancedFilters} activos` : filtersExpanded ? "−" : "+"}</span>
            </button>

            <div
              className={filtersExpanded ? "vote-filter-advanced expanded" : "vote-filter-advanced"}
              id="vote-advanced-filters"
            >
              {FACETS.map((facet) => (
                <fieldset key={facet.key} className="vote-filter-group">
                  <legend>{facet.label}</legend>
                  <div className="vote-chips">
                    {facetOptions[facet.key].map(([value, count]) => (
                      <button
                        key={value}
                        type="button"
                        className={facets[facet.key].includes(value) ? "active" : ""}
                        aria-pressed={facets[facet.key].includes(value)}
                        onClick={() => toggleFacet(facet.key, value)}
                      >
                        {facet.labels[value] ?? value} <small>{count}</small>
                      </button>
                    ))}
                  </div>
                </fieldset>
              ))}

              <fieldset className="vote-filter-group">
                <legend>Resultado</legend>
                <div className="vote-chips">
                  {(["todas", "aprobadas", "rechazadas"] as ResultFilter[]).map((value) => (
                    <button
                      key={value}
                      type="button"
                      className={result === value ? "active" : ""}
                      aria-pressed={result === value}
                      onClick={() => setResult(value)}
                    >
                      {value === "todas" ? "Todas" : value === "aprobadas" ? "Aprobadas" : "Rechazadas"}
                    </button>
                  ))}
                </div>
              </fieldset>

              <fieldset className="vote-filter-group">
                <legend>Margen de consenso</legend>
                <div className="vote-chips">
                  <button
                    type="button"
                    className={margin === "todas" ? "active" : ""}
                    aria-pressed={margin === "todas"}
                    onClick={() => setMargin("todas")}
                  >
                    Todas
                  </button>
                  {(Object.keys(MARGIN_BUCKETS) as Exclude<MarginFilter, "todas">[]).map((value) => (
                    <button
                      key={value}
                      type="button"
                      className={margin === value ? "active" : ""}
                      aria-pressed={margin === value}
                      onClick={() => setMargin(value)}
                    >
                      {MARGIN_BUCKETS[value].label}
                    </button>
                  ))}
                </div>
              </fieldset>

              <fieldset className="vote-filter-group">
                <legend>Revisión de la clasificación</legend>
                <div className="vote-chips">
                  {reviewOptions.map(([value, count]) => (
                    <button
                      key={value}
                      type="button"
                      className={review.includes(value) ? "active" : ""}
                      aria-pressed={review.includes(value)}
                      onClick={() =>
                        setReview((current) =>
                          current.includes(value)
                            ? current.filter((entry) => entry !== value)
                            : [...current, value],
                        )
                      }
                    >
                      {reviewLabel(value)} <small>{count}</small>
                    </button>
                  ))}
                </div>
              </fieldset>
            </div>

            {activeFilters > 0 && (
              <button type="button" className="vote-clear" onClick={clearFilters}>
                Limpiar filtros ({activeFilters})
              </button>
            )}
          </aside>

          <section className="vote-results" aria-label="Resultados">
            <div className="vote-results-head">
              <p>
                <strong>{filtered.length}</strong>{" "}
                {filtered.length === 1 ? "votación" : "votaciones"}
                {filtered.length !== data.manifest.voteCount && ` de ${data.manifest.voteCount}`}
              </p>
              <label className="vote-sort">
                <span>Ordenar por</span>
                <select value={sort} onChange={(event) => setSort(event.target.value as SortKey)}>
                  {SORTS.map((option) => (
                    <option key={option.key} value={option.key}>{option.label}</option>
                  ))}
                </select>
              </label>
            </div>

            {filtered.length === 0 ? (
              <p className="vote-empty">
                Ninguna votación coincide con estos filtros. Prueba a quitar alguno.
              </p>
            ) : (
              <ol className="vote-list" id="resultados">
                {shown.map((vote) => {
                  const key = voteKey(vote);
                  const value = consensusMargin(vote);
                  return (
                    <li key={key}>
                      <button
                        type="button"
                        className={key === selectedKey ? "vote-row active" : "vote-row"}
                        aria-current={key === selectedKey}
                        onClick={() => {
                          pendingScroll.current = true;
                          setSelectedKey(key);
                        }}
                      >
                        <span className="vote-row-meta">
                          <span>{shortDate(vote.date)}</span>
                          <span className="vote-row-chamber">{CHAMBER_SHORT[vote.chamber]}</span>
                          <span
                            className={isApproved(vote) ? "vote-pill approved" : "vote-pill rejected"}
                          >
                            {isApproved(vote) ? "Aprobada" : "Rechazada"}
                          </span>
                        </span>
                        <span className="vote-row-title">{cleanTitle(vote.title)}</span>
                        <span className="vote-row-foot">
                          <span className="vote-topic">{topicLabel(vote.topic)}</span>
                          {value !== null && <span>Margen {percent(value)}</span>}
                        </span>
                        <span className="vote-bar" aria-hidden="true">
                          {CHOICE_ORDER.map((choice) => {
                            const counts: Record<string, number> = {
                              Favor: vote.favor,
                              Contra: vote.contra,
                              "Abstención": vote.abstention,
                              Ausente: vote.absent,
                              "Quórum *": vote.presentNoVote,
                            };
                            const width = vote.total > 0 ? (counts[choice] / vote.total) * 100 : 0;
                            if (width === 0) return null;
                            return (
                              <span
                                key={choice}
                                style={{ width: `${width}%`, background: choiceColor(choice) }}
                              />
                            );
                          })}
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ol>
            )}

            {filtered.length > visible && (
              <div className="vote-more">
                <button
                  type="button"
                  onClick={() =>
                    setPage({ signature, visible: visible + PAGE_SIZE })
                  }
                >
                  Mostrar {Math.min(PAGE_SIZE, filtered.length - visible)} más
                </button>
                <small>
                  Mostrando {visible} de {filtered.length}. Usa los filtros para acotar la
                  búsqueda.
                </small>
              </div>
            )}
          </section>
        </div>

        {selected && (
          <section className="vote-detail-full" id="detalle">
            <div className="vote-title-block">
              <div>
                <p className="eyebrow">
                  {CHAMBER_LABELS[selected.chamber]} · LXVI Legislatura ·{" "}
                  {longDate(selected.date)}
                </p>
                <h2>{cleanTitle(selected.title)}</h2>
              </div>
              <div className="vote-links">
                <a href={selected.sourceUrl} target="_blank" rel="noreferrer">
                  Tabla de votos <span aria-hidden="true">↗</span>
                </a>
                {selected.gacetaUrl && (
                  <a href={selected.gacetaUrl} target="_blank" rel="noreferrer">
                    Gaceta del día <span aria-hidden="true">↗</span>
                  </a>
                )}
                <a href="#resultados">
                  <span aria-hidden="true">↑</span> Volver a la lista
                </a>
              </div>
            </div>

            <div className="vote-tags">
              <span className="vote-topic">{topicLabel(selected.topic)}</span>
              <span>{stageLabel(selected.stage)}</span>
              <span>{originLabel(selected.origin)}</span>
              <span>{instrumentLabel(selected.instrument)}</span>
              <span
                className={selected.review.requiresReview ? "vote-review flagged" : "vote-review"}
              >
                {reviewLabel(reviewKey(selected.review))}
              </span>
            </div>

            <div className="vote-metrics">
              {[
                ["A favor", selected.favor, "Favor"],
                ["En contra", selected.contra, "Contra"],
                ["Abstenciones", selected.abstention, "Abstención"],
                ["Ausencias", selected.absent, "Ausente"],
                ...(selected.presentNoVote > 0
                  ? [["Presente, sin voto", selected.presentNoVote, "Quórum *"] as const]
                  : []),
              ].map(([caption, value, choice]) => (
                <div key={caption as string} className="vote-metric">
                  <span className="vote-metric-key" style={{ background: choiceColor(choice as string) }} />
                  <strong>{(value as number).toLocaleString("es-MX")}</strong>
                  <small>{caption}</small>
                </div>
              ))}
              <div className="vote-metric">
                <strong>{selected.total.toLocaleString("es-MX")}</strong>
                <small>Total registrado</small>
              </div>
            </div>

            {selected.thresholds && (
              <div className="vote-thresholds">
                {[
                  {
                    title: "Quórum",
                    ok: selected.thresholds.quorumOk,
                    detail: `${selected.thresholds.present} presentes de ${selected.total} · mínimo ${selected.thresholds.quorumRequired}`,
                  },
                  {
                    title: "Mayoría simple",
                    ok: selected.thresholds.simpleOk,
                    detail: `${selected.favor} a favor contra ${selected.contra} en contra`,
                  },
                  {
                    title: "Mayoría absoluta",
                    ok: selected.thresholds.absoluteOk,
                    detail: `${selected.favor} a favor · mínimo ${selected.thresholds.absoluteRequired}`,
                  },
                  {
                    title: "Mayoría calificada",
                    ok: selected.thresholds.qualifiedOk,
                    detail: `${selected.favor} a favor · mínimo ${selected.thresholds.qualifiedRequired}`,
                  },
                ].map((item) => (
                  <div key={item.title} className={item.ok ? "vote-threshold met" : "vote-threshold"}>
                    <span aria-hidden="true">{item.ok ? "✓" : "✕"}</span>
                    <strong>{item.title}</strong>
                    <small>{item.detail}</small>
                    <span className="sr-only">{item.ok ? "Se alcanzó" : "No se alcanzó"}</span>
                  </div>
                ))}
              </div>
            )}

            <div className="vote-grid-head">
              <h3>Voto por grupo parlamentario</h3>
              <div className="vote-legend">
                {CHOICE_ORDER.filter((choice) =>
                  partyRows.some((row) => (row.counts[choice] ?? 0) > 0),
                ).map((choice) => (
                  <span key={choice}>
                    <i style={{ background: choiceColor(choice) }} />
                    {voteLabel(choice)}
                  </span>
                ))}
              </div>
            </div>

            <PartyGrids
              rows={partyRows}
              entry={ballotEntry}
              names={ballots?.names}
              onHover={setHover}
            />

            {hover && (
              <div
                className="square-tip"
                style={{ top: hover.top, left: hover.left }}
                role="presentation"
              >
                {hover.name ? (
                  <strong>{hover.name}</strong>
                ) : (
                  <strong className="square-tip-unknown">Sin nombre en el registro</strong>
                )}
                <span>
                  {hover.party} · {voteLabel(hover.choice)}
                </span>
              </div>
            )}

            <p className="vote-note">
              Cada cuadro es una legisladora o un legislador; pasa el cursor para ver su nombre. El
              desglose por grupo proviene del conteo que publica la propia cámara para esa votación,
              no de la composición actual del pleno: es el denominador correcto para ese día. La
              clasificación temática es asistida por modelo y se indica arriba con qué nivel de
              revisión cuenta.
            </p>
          </section>
        )}
      </main>
      <SiteFooter note="Votaciones nominales · LXVI Legislatura" />
    </>
  );
}
