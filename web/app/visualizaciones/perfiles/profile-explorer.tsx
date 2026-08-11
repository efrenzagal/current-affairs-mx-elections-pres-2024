"use client";

import { useEffect, useMemo, useState } from "react";

import { SITE_NAME, SiteFooter, SiteHeader } from "../../site-chrome";
import { partyColor, partyRank } from "../parties";
import {
  CHOICE_COLORS,
  cleanTitle,
  normalize,
  shortDate,
  stageLabel,
  topicLabel,
  voteLabel as choiceLabel,
} from "../votes";

/** Sentinel for "no party filter"; no real party key collides with it. */
const ALL_PARTIES = "todos";

type Chamber = "diputados" | "senado";
type ChamberFilter = "todos" | Chamber;
type SeatStatus = "en_funciones" | "licencia" | "vacante" | "sin_directorio";
type ProfileRole = "current" | "former";

type Seat = {
  id: string;
  seatType: "MR" | "FM" | "RP";
  state: string | null;
  district: number | null;
  districtSeat: string | null;
  circunscripcion: number | null;
  listNumber: number | null;
  electionActor: string | null;
  winningVotes: number | null;
  winningPct: number | null;
  electedPersonId: string | null;
  electedName: string;
  electedParty: string;
  electedNameRole: "titular" | "suplente";
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

type FormerMember = {
  personId: string;
  name: string;
  party: string;
};

type SiteData = {
  manifest: {
    legislature: number;
    chamber: Chamber;
    sourceThrough: string;
    roster: { observedAt: string };
  };
  seats: Seat[];
  formerMembers?: FormerMember[];
  votes: Vote[];
  histories: Record<string, [string, string][]>;
};

type Member = {
  key: string;
  chamber: Chamber;
  seat: Seat | null;
  role: ProfileRole;
  personId: string | null;
  name: string;
  party: string;
  searchName: string;
  data: SiteData;
};

type VoteRecord = { vote: Vote; choice: string };

const DATASETS: Record<Chamber, string> = {
  diputados: "/data/legislature-66.json",
  senado: "/data/senate-66.json",
};

const FILTERS: { value: ChamberFilter; label: string }[] = [
  { value: "todos", label: "Ambas cámaras" },
  { value: "diputados", label: "Diputados" },
  { value: "senado", label: "Senado" },
];

function chamberLabel(chamber: Chamber) {
  return chamber === "diputados" ? "Cámara de Diputados" : "Senado de la República";
}

function memberNoun(chamber: Chamber) {
  return chamber === "diputados" ? "Diputación" : "Senaduría";
}

function statusLabel(status: SeatStatus | "historical") {
  if (status === "historical") return "Anterior";
  if (status === "licencia") return "Con licencia";
  if (status === "sin_directorio") return "Sin registro en el directorio";
  return "En funciones";
}

function seatTypeLabel(type: Seat["seatType"]) {
  if (type === "MR") return "Mayoría relativa";
  if (type === "FM") return "Primera minoría";
  return "Representación proporcional";
}

function geographyLabel(seat: Seat) {
  if (seat.seatType === "RP") {
    const list = seat.listNumber ? ` · lugar ${seat.listNumber}` : "";
    const region = seat.circunscripcion ? `Circunscripción ${seat.circunscripcion}` : "Lista nacional";
    return `${region}${list}`;
  }
  const district = seat.district ? `Distrito ${seat.district}` : null;
  return [seat.state, district, seat.districtSeat].filter(Boolean).join(" · ");
}

function choiceBucket(choice: string): "favor" | "contra" | "abstention" | "noVote" {
  if (choice === "Favor") return "favor";
  if (choice === "Contra") return "contra";
  if (choice === "Abstención" || choice === "Abstencion") return "abstention";
  return "noVote";
}

function percent(value: number, total: number) {
  if (!total) return "0.0%";
  return `${((value / total) * 100).toLocaleString("es-MX", {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  })}%`;
}

export default function ProfileExplorer() {
  const [datasets, setDatasets] = useState<Record<Chamber, SiteData> | null>(null);
  const [error, setError] = useState(false);
  const [chamberFilter, setChamberFilter] = useState<ChamberFilter>("todos");
  const [partyFilter, setPartyFilter] = useState<string>(ALL_PARTIES);
  const [query, setQuery] = useState("");
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [topic, setTopic] = useState("todos");
  const [selectedVoteId, setSelectedVoteId] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all(
      (Object.entries(DATASETS) as [Chamber, string][]).map(async ([chamber, url]) => {
        const response = await fetch(url);
        if (!response.ok) throw new Error(`${chamber}: ${response.status}`);
        return [chamber, (await response.json()) as SiteData] as const;
      }),
    )
      .then((entries) => {
        if (!cancelled) setDatasets(Object.fromEntries(entries) as Record<Chamber, SiteData>);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });
    return () => { cancelled = true; };
  }, []);

  const members = useMemo(() => {
    if (!datasets) return [];
    const result: Member[] = [];
    for (const chamber of ["diputados", "senado"] as Chamber[]) {
      const data = datasets[chamber];
      for (const seat of data.seats) {
        if (seat.currentName && seat.currentStatus !== "vacante") {
          result.push({
            key: `${chamber}:current:${seat.currentPersonId ?? seat.id}`,
            chamber,
            seat,
            role: "current",
            personId: seat.currentPersonId,
            name: seat.currentName,
            party: seat.currentParty,
            searchName: normalize(seat.currentName),
            data,
          });
        }

        // Preserve the elected occupant as a historical profile after a
        // suplencia, but only when that person has a voting record to show.
        const formerHistory = seat.electedPersonId
          ? data.histories[seat.electedPersonId] ?? []
          : [];
        if (
          seat.electedPersonId &&
          seat.electedPersonId !== seat.currentPersonId &&
          formerHistory.length
        ) {
          result.push({
            key: `${chamber}:former:${seat.electedPersonId}`,
            chamber,
            seat,
            role: "former",
            personId: seat.electedPersonId,
            name: seat.electedName,
            party: seat.electedParty,
            searchName: normalize(seat.electedName),
            data,
          });
        }
      }
      for (const former of data.formerMembers ?? []) {
        result.push({
          key: `${chamber}:former:${former.personId}`,
          chamber,
          seat: null,
          role: "former",
          personId: former.personId,
          name: former.name,
          party: former.party,
          searchName: normalize(former.name),
          data,
        });
      }
    }
    return result.sort(
      (a, b) => a.searchName.localeCompare(b.searchName, "es") ||
        a.chamber.localeCompare(b.chamber) ||
        a.role.localeCompare(b.role),
    );
  }, [datasets]);

  const inChamber = useMemo(
    () => members.filter(
      (member) => chamberFilter === "todos" || member.chamber === chamberFilter,
    ),
    [members, chamberFilter],
  );

  /**
   * Parties offered by the filter, with a live count. Derived from the chamber
   * selection rather than the whole Congress: offering PRD in a Senate-only view
   * that has no PRD senators would produce a button that always yields nothing.
   */
  const parties = useMemo(() => {
    const counts = new Map<string, number>();
    for (const member of inChamber) {
      const party = member.party;
      counts.set(party, (counts.get(party) ?? 0) + 1);
    }
    return [...counts.entries()].sort(
      ([a], [b]) => partyRank(a) - partyRank(b) || a.localeCompare(b, "es"),
    );
  }, [inChamber]);

  // A party can vanish when the chamber changes. Fall back rather than leave the
  // list empty behind a filter the reader can no longer see selected.
  useEffect(() => {
    if (partyFilter === ALL_PARTIES) return;
    if (!parties.some(([party]) => party === partyFilter)) setPartyFilter(ALL_PARTIES);
  }, [parties, partyFilter]);

  const filteredMembers = useMemo(() => {
    const needle = normalize(query);
    return inChamber.filter((member) =>
      (partyFilter === ALL_PARTIES || member.party === partyFilter) &&
      (!needle || member.searchName.includes(needle)),
    );
  }, [inChamber, partyFilter, query]);

  const selected = members.find((member) => member.key === selectedKey) ?? members[0] ?? null;

  const records = useMemo(() => {
    if (!selected?.personId) return [];
    const votes = new Map(selected.data.votes.map((vote) => [vote.id, vote]));
    return (selected.data.histories[selected.personId] ?? [])
      .map(([voteId, choice]) => {
        const vote = votes.get(voteId);
        return vote ? { vote, choice } : null;
      })
      .filter((record): record is VoteRecord => record !== null)
      .sort((a, b) => a.vote.date.localeCompare(b.vote.date) || a.vote.id.localeCompare(b.vote.id));
  }, [selected]);

  const topics = useMemo(() => {
    const counts = new Map<string, number>();
    for (const record of records) {
      const key = record.vote.topic ?? "sin_clasificar";
      counts.set(key, (counts.get(key) ?? 0) + 1);
    }
    return [...counts.entries()].sort((a, b) => topicLabel(a[0]).localeCompare(topicLabel(b[0]), "es"));
  }, [records]);

  const filteredRecords = useMemo(
    () => topic === "todos"
      ? records
      : records.filter((record) => (record.vote.topic ?? "sin_clasificar") === topic),
    [records, topic],
  );

  const totals = useMemo(() => {
    const summary: Record<"favor" | "contra" | "abstention" | "noVote", number> = {
      favor: 0, contra: 0, abstention: 0, noVote: 0,
    };
    for (const record of filteredRecords) summary[choiceBucket(record.choice)] += 1;
    return summary;
  }, [filteredRecords]);

  const years = useMemo(() => {
    const grouped = new Map<string, VoteRecord[]>();
    for (const record of filteredRecords) {
      const year = record.vote.date.slice(0, 4);
      grouped.set(year, [...(grouped.get(year) ?? []), record]);
    }
    return [...grouped.entries()];
  }, [filteredRecords]);

  const selectedVote = filteredRecords.find((record) => record.vote.id === selectedVoteId) ?? null;

  function chooseMember(member: Member) {
    setSelectedKey(member.key);
    setTopic("todos");
    setSelectedVoteId(null);
  }

  function chooseChamber(next: ChamberFilter) {
    setChamberFilter(next);
    setQuery("");
    if (next === "todos" || selected?.chamber === next) return;
    const firstInChamber = members.find((member) => member.chamber === next);
    if (firstInChamber) chooseMember(firstInChamber);
  }

  const hasFilters = Boolean(query) || partyFilter !== ALL_PARTIES;

  function clearFilters() {
    setQuery("");
    setPartyFilter(ALL_PARTIES);
  }

  if (error) {
    return (
      <main className="state-screen">
        <p className="eyebrow">{SITE_NAME}</p>
        <h1>No pudimos cargar los perfiles legislativos.</h1>
        <p>Actualiza la página para intentarlo de nuevo.</p>
      </main>
    );
  }

  if (!datasets || !selected) {
    return (
      <main className="state-screen loading-state" aria-live="polite">
        <span className="loading-mark" />
        <p className="eyebrow">{SITE_NAME}</p>
        <h1>Preparando los perfiles…</h1>
      </main>
    );
  }

  const seat = selected.seat;
  const isFormer = selected.role === "former";
  const total = filteredRecords.length;
  const electionLabel = !isFormer && seat?.currentPersonId === seat?.electedPersonId
    ? "Partido de elección"
    : "Partido que ganó el escaño";

  return (
    <main>
      <SiteHeader active="visualizaciones" status="LXVI Legislatura" />

      <section className="profile-hero">
        <div>
          <p className="eyebrow"><a href="/visualizaciones">Visualizaciones</a> · Congreso</p>
          <h1>Perfil legislativo.</h1>
        </div>
        <p className="hero-copy">
          Busca legisladores actuales y anteriores de la LXVI Legislatura. Lee cada voto nominal,
          compáralo por tema y distingue quién ganó el escaño de quien lo ocupa actualmente.
        </p>
      </section>

      <section className="profile-explorer">
        <div className="profile-controls" aria-label="Buscar perfiles legislativos">
          <div className="profile-control-group">
            <span className="profile-control-label">Cámara</span>
            <div className="profile-chamber-tabs" role="group" aria-label="Filtrar por cámara">
              {FILTERS.map((filter) => (
                <button
                  type="button"
                  key={filter.value}
                  className={chamberFilter === filter.value ? "active" : ""}
                  aria-pressed={chamberFilter === filter.value}
                  onClick={() => chooseChamber(filter.value)}
                >
                  {filter.label}
                </button>
              ))}
            </div>
          </div>
          <div className="profile-control-group profile-legislature">
            <span className="profile-control-label">Legislatura</span>
            <strong>LXVI · actual</strong>
          </div>
          <label className="profile-search">
            <span className="profile-control-label">Nombre</span>
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Buscar en ambas cámaras"
              aria-label="Buscar legisladora o legislador por nombre"
            />
          </label>
          {/* Current profiles use their present parliamentary group; historical
              profiles use the party with which they won the seat. */}
          <div className="profile-control-group profile-party-group">
            <span className="profile-control-label">Partido / grupo parlamentario</span>
            <div className="profile-party-filter" role="group" aria-label="Filtrar por partido o grupo parlamentario">
              <button
                type="button"
                className={partyFilter === ALL_PARTIES ? "active" : ""}
                aria-pressed={partyFilter === ALL_PARTIES}
                onClick={() => setPartyFilter(ALL_PARTIES)}
              >
                Todos <span className="profile-party-count">{inChamber.length}</span>
              </button>
              {parties.map(([party, count]) => (
                <button
                  type="button"
                  key={party}
                  className={partyFilter === party ? "active" : ""}
                  aria-pressed={partyFilter === party}
                  onClick={() => setPartyFilter(party)}
                >
                  <i style={{ background: partyColor(party) }} aria-hidden="true" />
                  {party} <span className="profile-party-count">{count}</span>
                </button>
              ))}
            </div>
          </div>
        </div>

        <div className="profile-layout">
          <aside className="profile-results" aria-label="Resultados de legisladores">
            <div className="profile-results-heading">
              <span>{filteredMembers.length} resultados</span>
              {hasFilters && <button type="button" onClick={clearFilters}>Limpiar</button>}
            </div>
            <div className="profile-result-list">
              {filteredMembers.map((member) => (
                <button
                  type="button"
                  key={member.key}
                  className={member.key === selected.key ? "active" : ""}
                  aria-current={member.key === selected.key ? "true" : undefined}
                  onClick={() => chooseMember(member)}
                >
                  <strong>{member.name}</strong>
                  <span>
                    <i
                      className="profile-result-party"
                      style={{ background: partyColor(member.party) }}
                      aria-hidden="true"
                    />
                    {chamberLabel(member.chamber)} · {member.party}
                    {member.role === "former" ? " · Anterior" : ""}
                  </span>
                </button>
              ))}
              {!filteredMembers.length && <p className="profile-empty">No encontramos un nombre con esos filtros.</p>}
            </div>
          </aside>

          <article className="profile-card">
            <header className="profile-identity">
              <div>
                <p className="eyebrow">{chamberLabel(selected.chamber)} · LXVI</p>
                <h2>{selected.name}</h2>
                <p>
                  {seat
                    ? `${memberNoun(selected.chamber)} · ${seatTypeLabel(seat.seatType)} · ${geographyLabel(seat)}`
                    : `${memberNoun(selected.chamber)} anterior · Sin escaño vinculado al corte actual`}
                </p>
              </div>
              <span className={`profile-status status-${isFormer ? "historical" : seat?.currentStatus}`}>
                {statusLabel(isFormer ? "historical" : seat?.currentStatus ?? "sin_directorio")}
              </span>
            </header>

            {seat ? (
              <dl className="profile-affiliations">
                <div>
                  <dt>{isFormer ? "Partido de elección" : electionLabel}</dt>
                  <dd>{seat.electedParty}</dd>
                  {seat.electionActor && <small>{seat.electionActor.replaceAll("_", " · ")}</small>}
                </div>
                <div>
                  <dt>{isFormer ? "Ocupante actual" : "Grupo parlamentario actual"}</dt>
                  <dd>{isFormer ? seat.currentName ?? "Vacante" : seat.currentParty}</dd>
                  {isFormer
                    ? <small>{seat.currentName ? seat.currentParty : "El escaño no tiene ocupante"}</small>
                    : seat.currentParty !== seat.electedParty && <small>Cambió respecto del resultado electoral</small>}
                </div>
                <div>
                  <dt>Resultado del escaño</dt>
                  <dd>{seat.winningVotes?.toLocaleString("es-MX") ?? "Lista"}</dd>
                  <small>{seat.winningPct !== null ? `${seat.winningPct.toLocaleString("es-MX")}% de la votación` : "Sin voto individual"}</small>
                </div>
              </dl>
            ) : (
              <dl className="profile-affiliations">
                <div>
                  <dt>Último grupo registrado</dt>
                  <dd>{selected.party}</dd>
                  <small>Afiliación reportada en su votación más reciente</small>
                </div>
                <div>
                  <dt>Estado en el corte actual</dt>
                  <dd>Anterior</dd>
                  <small>Ya no figura como ocupante de un escaño</small>
                </div>
                <div>
                  <dt>Trayectoria disponible</dt>
                  <dd>{records.length.toLocaleString("es-MX")}</dd>
                  <small>Votaciones nominales en la LXVI Legislatura</small>
                </div>
              </dl>
            )}

            <div className="profile-topic-row">
              <label>
                <span>Filtrar la trayectoria por tema</span>
                <select
                  value={topic}
                  onChange={(event) => {
                    setTopic(event.target.value);
                    setSelectedVoteId(null);
                  }}
                >
                  <option value="todos">Todos los temas ({records.length})</option>
                  {topics.map(([value, count]) => (
                    <option key={value} value={value}>{topicLabel(value)} ({count})</option>
                  ))}
                </select>
              </label>
              <p>{total} de {records.length} votaciones en este corte</p>
            </div>

            <div className="profile-metrics" aria-label="Resumen de votaciones del perfil">
              {[
                ["Votaciones", total, total ? "100.0%" : "0.0%", "total"],
                ["A favor", totals.favor, percent(totals.favor, total), "favor"],
                ["En contra", totals.contra, percent(totals.contra, total), "contra"],
                ["Abstenciones", totals.abstention, percent(totals.abstention, total), "abstention"],
                ["No votó", totals.noVote, percent(totals.noVote, total), "no-vote"],
              ].map(([metricLabel, value, pct, key]) => (
                <div key={String(key)} className={`profile-metric metric-${key}`}>
                  <span>{metricLabel}</span><strong>{value}</strong><small>{pct}</small>
                </div>
              ))}
            </div>

            <section className="profile-calendar" aria-labelledby="profile-calendar-title">
              <div className="profile-section-heading">
                <div><p className="eyebrow">Trayectoria nominal</p><h3 id="profile-calendar-title">Voto por voto</h3></div>
                <div className="profile-calendar-key" aria-label="Colores del sentido del voto">
                  {["Favor", "Contra", "Abstención", "Ausente"].map((choice) => (
                    <span key={choice}><i style={{ background: CHOICE_COLORS[choice] }} />{choiceLabel(choice)}</span>
                  ))}
                </div>
              </div>

              <div className="profile-calendar-years">
                {years.map(([year, yearRecords]) => (
                  <div className="profile-calendar-year" key={year}>
                    <strong>{year}</strong>
                    <div className="profile-calendar-track">
                      {yearRecords.map((record) => (
                        <button
                          type="button"
                          key={record.vote.id}
                          className={selectedVoteId === record.vote.id ? "selected" : ""}
                          style={{ background: CHOICE_COLORS[record.choice] ?? CHOICE_COLORS.Ausente }}
                          onClick={() => setSelectedVoteId(record.vote.id)}
                          aria-label={`${shortDate(record.vote.date)}: ${choiceLabel(record.choice)}. ${cleanTitle(record.vote.title)}`}
                          title={`${shortDate(record.vote.date)} · ${choiceLabel(record.choice)}\n${cleanTitle(record.vote.title)}`}
                        />
                      ))}
                    </div>
                  </div>
                ))}
                {!years.length && <p className="profile-empty">No hay votaciones para este tema.</p>}
              </div>
            </section>

            {selectedVote && (
              <section className="profile-vote-detail" aria-live="polite">
                <div>
                  <p className="eyebrow">{shortDate(selectedVote.vote.date)} · {topicLabel(selectedVote.vote.topic)}</p>
                  <h3>{cleanTitle(selectedVote.vote.title)}</h3>
                  {selectedVote.vote.stage && <p>{stageLabel(selectedVote.vote.stage)}</p>}
                </div>
                <div className="profile-vote-choice">
                  <span>Votó</span>
                  <strong style={{ color: CHOICE_COLORS[selectedVote.choice] }}>{choiceLabel(selectedVote.choice)}</strong>
                  <a href={selectedVote.vote.sourceUrl} target="_blank" rel="noreferrer">Fuente oficial ↗</a>
                </div>
              </section>
            )}

            {!records.length && (
              <p className="profile-no-history">
                El directorio identifica a esta persona, pero el corte publicado todavía no enlaza
                su identidad con una votación nominal. No atribuimos el historial de otra persona.
              </p>
            )}
          </article>
        </div>
      </section>

      <section className="method-note">
        <p className="eyebrow">Cómo leerlo</p>
        <div className="method-body">
          <p>
            Cada rectángulo representa una votación nominal registrada para la persona seleccionada.
            Los porcentajes usan como denominador las votaciones visibles después de aplicar el filtro.
            “No votó” reúne ausencias y registros de presencia sin sentido de voto.
          </p>
          <p>
            La identidad y el grupo actual vienen del directorio oficial; el partido de elección y el
            resultado pertenecen al escaño de 2024. En una suplencia, esos datos describen el escaño,
            no una elección individual de quien hoy lo ocupa.
          </p>
        </div>
      </section>

      <SiteFooter note="Perfiles legislativos · LXVI Legislatura" />
    </main>
  );
}
