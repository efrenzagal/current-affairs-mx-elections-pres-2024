"use client";

import { useEffect, useMemo, useState } from "react";

import {
  electionActorLabel,
  hasRegisteredTitular,
  type Chamber,
  type Seat,
  type SeatMember,
  type SiteData,
} from "./explorer";
import { PARTY_COLORS } from "./parties";
import { CHOICE_COLORS, shortDate, shortTitle, voteLabel } from "./votes";

/**
 * A standalone version of the seat-profile panel from `Explorer`, for
 * embedding a single legislator (or seat) outside the full hemicycle — e.g.
 * in an article. Ports the same derivations (seat-merged history,
 * attendance/favor rate, vote calendar) but drops the search box, topic
 * filter and vote-detail drill-down, which only make sense alongside the
 * hemicycle itself.
 */

const CHAMBER_META: Record<Chamber, { dataUrl: string; isSenate: boolean }> = {
  diputados: { dataUrl: "/data/legislature-66.json", isSenate: false },
  senado: { dataUrl: "/data/senate-66.json", isSenate: true },
};

type HistoryEntry = [voteId: string, choice: string, memberIndex: number | null];
type HistoryMode = "all" | "titular" | "suplente";

function seatTypeLabel(seatType: Seat["seatType"]) {
  if (seatType === "MR") return "Mayoría relativa";
  if (seatType === "FM") return "Primera Minoría";
  return "Representación proporcional";
}

export function LegislatorCard({ chamber, seatId }: { chamber: Chamber; seatId: string }) {
  const meta = CHAMBER_META[chamber];
  const [data, setData] = useState<SiteData | null>(null);
  const [error, setError] = useState(false);
  const [historyMode, setHistoryMode] = useState<HistoryMode>("all");

  useEffect(() => {
    let cancelled = false;
    fetch(meta.dataUrl)
      .then((response) => {
        if (!response.ok) throw new Error(String(response.status));
        return response.json();
      })
      .then((payload: SiteData) => {
        if (!cancelled) setData(payload);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });
    return () => {
      cancelled = true;
    };
  }, [meta.dataUrl]);

  const seat = data?.seats.find((candidate) => candidate.id === seatId) ?? null;
  const members = useMemo<SeatMember[]>(
    () => (data && seat ? data.seatMembers[seat.id] ?? [] : []),
    [data, seat],
  );
  const votesById = useMemo(
    () => new Map((data?.votes ?? []).map((vote) => [vote.id, vote])),
    [data],
  );

  // Same merge as Explorer's seatHistories, scoped to this one seat: every
  // member's votes, deduped by seat-vote-conflict resolution where one exists.
  const fullHistory = useMemo<HistoryEntry[]>(() => {
    if (!data || !seat) return [];
    const conflicts = new Map(
      (data.seatVoteConflicts ?? [])
        .filter((conflict) => conflict.seatId === seat.id)
        .map((conflict) => [conflict.voteId, conflict.countedPersonId]),
    );
    const byVote = new Map<string, HistoryEntry>();
    members.forEach((member, memberIndex) => {
      for (const [voteId, choice] of data.histories[member.personId] ?? []) {
        const existing = byVote.get(voteId);
        if (!existing || conflicts.get(voteId) === member.personId) {
          byVote.set(voteId, [voteId, choice, memberIndex]);
        }
      }
    });
    return [...byVote.values()].sort((left, right) => {
      const a = votesById.get(left[0])?.date ?? "";
      const b = votesById.get(right[0])?.date ?? "";
      return a.localeCompare(b);
    });
  }, [data, seat, members, votesById]);

  const hasSubstituteVotes = members.some(
    (member) => member.role === "suplente" && member.voteCount > 0,
  );

  const history = useMemo(() => {
    if (historyMode === "all") return fullHistory;
    return fullHistory.filter(
      (entry) => entry[2] !== null && members[entry[2]]?.role === historyMode,
    );
  }, [fullHistory, historyMode, members]);

  const activeVotes = history.filter(([, choice]) =>
    ["Favor", "Contra", "Abstención", "Abstencion"].includes(choice),
  );
  // "Sin registro" (Senado only) is our own inference for a vote the source
  // silently omitted the senator from — see CHOICE_COLORS in votes.ts. It
  // counts against participación exactly like an explicit "Ausente": both
  // mean the person did not cast a ballot on that vote.
  const attendance = history.length
    ? 1 -
      history.filter(([, choice]) => choice === "Ausente" || choice === "Sin registro").length /
        history.length
    : 0;
  const favorRate = activeVotes.length
    ? activeVotes.filter(([, choice]) => choice === "Favor").length / activeVotes.length
    : 0;

  const calendarYears = useMemo(() => {
    const byYear = new Map<
      string,
      { voteId: string; choice: string; date: string; title: string; member: SeatMember | null }[]
    >();
    for (const [voteId, choice, memberIndex] of history) {
      const vote = votesById.get(voteId);
      if (!vote) continue;
      const year = vote.date.slice(0, 4);
      const entry = {
        voteId,
        choice,
        date: vote.date,
        title: vote.title,
        member: memberIndex !== null ? members[memberIndex] ?? null : null,
      };
      const bucket = byYear.get(year);
      if (bucket) bucket.push(entry);
      else byYear.set(year, [entry]);
    }
    return [...byYear.entries()]
      .map(([year, entries]) => ({
        year,
        entries: entries.sort((a, b) => a.date.localeCompare(b.date)),
      }))
      .sort((a, b) => a.year.localeCompare(b.year));
  }, [history, votesById, members]);

  if (error) {
    return (
      <div className="deputy-panel">
        <p className="seat-flag">No pudimos cargar este perfil.</p>
      </div>
    );
  }
  if (!data || !seat) {
    return (
      <div className="deputy-panel">
        <p className="seat-flag">Cargando…</p>
      </div>
    );
  }

  const party = seat.currentParty || seat.electedParty;
  // Matches Explorer's seat-history heading: the 2024 titular's name stays
  // fixed regardless of the Titular/Suplencias toggle below, same as the
  // full hemicycle. Only a seat with no registered titular falls back to
  // whoever the directory shows holding it today.
  const headingName = hasRegisteredTitular(seat)
    ? seat.titularName
    : seat.currentName ?? "Escaño vacante";
  const locationLine = meta.isSenate
    ? seat.seatType === "RP"
      ? `Representación proporcional · Lista nacional · Posición ${seat.listNumber}`
      : `${seatTypeLabel(seat.seatType)} · ${seat.state}`
    : seat.seatType === "MR"
      ? `${seat.state} · Distrito ${seat.district}${seat.districtSeat ? ` · ${seat.districtSeat}` : ""}`
      : `Representación proporcional · Circunscripción ${seat.circunscripcion} · Lista ${seat.listNumber}`;

  return (
    <div className="deputy-panel">
      <div className="deputy-heading">
        <div>
          <span className="large-party" style={{ color: PARTY_COLORS[party] }}>
            {party}
          </span>
          <h2>{headingName}</h2>
          <p>
            {locationLine}
            {!hasRegisteredTitular(seat) && seat.currentName ? (
              <>
                <br />
                Suplente en funciones · sin titular registrado
              </>
            ) : seat.substituteName && (
              <>
                <br />
                Suplente: {seat.substituteName}
              </>
            )}
          </p>
        </div>
      </div>

      {hasSubstituteVotes && (
        <div className="seat-history-attribution">
          <p>
            <strong>Este historial combina el escaño.</strong> Incluye votos emitidos por la
            persona titular y por quienes cubrieron una suplencia.
          </p>
          <div className="occupant-filter" role="group" aria-label="Separar votos por ocupante">
            {(
              [
                ["all", "Todo el escaño"],
                ["titular", "Titular"],
                ["suplente", "Suplencias"],
              ] as [HistoryMode, string][]
            ).map(([mode, text]) => (
              <button
                key={mode}
                type="button"
                className={historyMode === mode ? "active" : ""}
                aria-pressed={historyMode === mode}
                onClick={() => setHistoryMode(mode)}
              >
                {text}
              </button>
            ))}
          </div>
          <ul>
            {members
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

      <div className="deputy-metrics">
        <div>
          <strong>{history.length}</strong>
          <span>registros del escaño</span>
        </div>
        <div>
          <strong>
            {attendance.toLocaleString("es-MX", { style: "percent", maximumFractionDigits: 0 })}
          </strong>
          <span>participación</span>
        </div>
        <div>
          <strong>
            {favorRate.toLocaleString("es-MX", { style: "percent", maximumFractionDigits: 0 })}
          </strong>
          <span>voto a favor</span>
        </div>
        <div className="election-metric">
          {seat.seatType !== "RP" && seat.winningPct !== null ? (
            <>
              <strong>
                {seat.winningPct.toLocaleString("es-MX", {
                  minimumFractionDigits: 1,
                  maximumFractionDigits: 2,
                })}
                %
              </strong>
              <span>
                {seat.winningVotes?.toLocaleString("es-MX")} votos ·{" "}
                {seat.seatType === "FM" ? "Primera Minoría" : "elección 2024"}
              </span>
              {seat.electionActor && <small>{electionActorLabel(seat.electionActor)}</small>}
            </>
          ) : (
            <>
              <strong>Lista {seat.listNumber}</strong>
              <span>asignación RP · 2024</span>
            </>
          )}
        </div>
      </div>

      <div className="history-label">
        <span>Calendario de votación</span>
      </div>
      <div className="panel-calendar">
        {calendarYears.length === 0 && (
          <p className="calendar-empty">Este escaño todavía no tiene votaciones registradas.</p>
        )}
        {calendarYears.map(({ year, entries }) => (
          <div className="calendar-year" key={year}>
            <span className="calendar-year-label">{year}</span>
            <div className="calendar-track" role="group" aria-label={`Votaciones de ${year}`}>
              {entries.map(({ voteId, choice, date, title, member }) => (
                <span
                  key={voteId}
                  className="calendar-cell"
                  style={{ background: CHOICE_COLORS[choice] ?? "#8b8b86" }}
                  title={`${shortDate(date)} · ${voteLabel(choice)}${
                    member ? ` · ${member.name}${member.role === "suplente" ? " (suplencia)" : ""}` : ""
                  } · ${shortTitle(title)}`}
                />
              ))}
            </div>
          </div>
        ))}
        {calendarYears.length > 0 && (
          <div className="calendar-key">
            <span>
              <i style={{ background: CHOICE_COLORS.Favor }} /> Favor
            </span>
            <span>
              <i style={{ background: CHOICE_COLORS.Contra }} /> Contra
            </span>
            <span>
              <i style={{ background: CHOICE_COLORS["Abstención"] }} /> Abst.
            </span>
            <span>
              <i style={{ background: CHOICE_COLORS.Ausente }} /> Ausente
            </span>
            {meta.isSenate && (
              <span>
                <i style={{ background: CHOICE_COLORS["Sin registro"] }} /> Sin registro
              </span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
