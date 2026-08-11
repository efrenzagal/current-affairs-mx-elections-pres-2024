"use client";

import { useEffect, useState } from "react";

import { SiteFooter, SiteHeader } from "../site-chrome";
import { DASHBOARDS } from "./dashboards";

/**
 * Manifest digest written by `scripts/export_gaceta_web.py`. The index prints
 * live counts from it rather than the static copy in `dashboards.ts`, so the
 * cards cannot quietly go stale after a data refresh. Only the manifests are
 * fetched: the seat and history payloads stay behind their own routes.
 */
type Summary = {
  voteCount: number;
  sourceThrough: string;
  /**
   * Seat-shaped manifests only. The vote explorer publishes a digest with no
   * seats and no roster, so everything below the first two fields is optional
   * and the card picks its stats from whichever shape it was handed.
   */
  seatCount?: number;
  substitutedSeats?: number;
  roster?: { observedAt: string };
  chambers?: Record<string, number>;
  topicCount?: number;
};

/** Subject areas in registry order, deduplicated. Drives the optional headings. */
const AREAS = [...new Set(DASHBOARDS.map((dashboard) => dashboard.area))];

function shortDate(date: string) {
  return new Intl.DateTimeFormat("es-MX", {
    day: "numeric",
    month: "short",
    year: "numeric",
  }).format(new Date(`${date.slice(0, 10)}T12:00:00`));
}

export default function VisualizacionesPage() {
  const [summary, setSummary] = useState<Record<string, Summary> | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch("/data/visualizaciones.json")
      .then((response) => (response.ok ? response.json() : null))
      .then((payload) => {
        if (!cancelled) setSummary(payload);
      })
      // A failed digest is not worth an error screen: the cards still describe
      // themselves from their static copy.
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <main>
      <SiteHeader active="visualizaciones" status="Visualizaciones" />

      <section className="articles-hero">
        <div>
          <p className="eyebrow">Tableros</p>
          <h1>Visualizaciones interactivas.</h1>
        </div>
        <p className="hero-copy">
          Tableros construidos sobre el mismo almacén que alimenta los artículos. Cada uno se
          publica con su fecha de corte y su fuente oficial a la vista, y llega hasta el registro
          individual sin salir de la página.
        </p>
      </section>

      {AREAS.map((area) => (
      <section className="dashboard-list" key={area}>
        {/* One area today, so a heading would be noise. It appears by itself the
            moment a dashboard on another subject is registered. */}
        {AREAS.length > 1 && <h2 className="dashboard-area">{area}</h2>}
        {DASHBOARDS.filter((dashboard) => dashboard.area === area).map((dashboard) => {
          const stats = summary?.[dashboard.slug];
          return (
            <article key={dashboard.slug} className="dashboard-card">
              <div className="dashboard-meta">
                <span className="dashboard-kind">Explorador</span>
                {stats && <span>Corte al {shortDate(stats.sourceThrough)}</span>}
              </div>
              <div className="article-body">
                <h2>
                  <a href={dashboard.href}>{dashboard.title}</a>
                </h2>
                <p className="article-subtitle">
                  {stats?.seatCount
                    ? `${stats.seatCount} escaños · LXVI Legislatura`
                    : dashboard.subtitle}
                </p>
                <p className="article-summary">{dashboard.summary}</p>

                {stats && (
                  <dl className="dashboard-stats">
                    <div>
                      <dt>Votaciones nominales</dt>
                      <dd>{stats.voteCount}</dd>
                    </div>
                    {stats.roster ? (
                      <>
                        <div>
                          <dt>Escaños con relevo</dt>
                          <dd>{stats.substitutedSeats}</dd>
                        </div>
                        <div>
                          <dt>Composición al</dt>
                          <dd>{shortDate(stats.roster.observedAt)}</dd>
                        </div>
                      </>
                    ) : (
                      <>
                        {stats.chambers && (
                          <div>
                            <dt>Cámaras</dt>
                            <dd>{Object.keys(stats.chambers).length}</dd>
                          </div>
                        )}
                        {stats.topicCount && (
                          <div>
                            <dt>Temas de política</dt>
                            <dd>{stats.topicCount}</dd>
                          </div>
                        )}
                      </>
                    )}
                  </dl>
                )}

                <div className="article-topics">
                  {dashboard.topics.map((topic) => (
                    <span key={topic}>{topic}</span>
                  ))}
                </div>
                <a className="article-cta" href={dashboard.href}>
                  Abrir el explorador →
                </a>
              </div>
            </article>
          );
        })}
      </section>
      ))}

      {AREAS.includes("Congreso") && (
        <section className="method-note">
          <p className="eyebrow">Sobre la composición del Congreso</p>
          <p>
            Los exploradores del Congreso abren en <strong>composición actual</strong>: quién ocupa
            cada escaño según el directorio oficial de cada cámara, incluidas las suplencias que
            entraron durante la legislatura, las licencias y las vacantes. La integración electoral
            de 2024 sigue disponible como segunda vista, porque responde una pregunta distinta —
            quién <em>ganó</em> el escaño, no quién lo está votando.
          </p>
        </section>
      )}

      <SiteFooter note="Visualizaciones" />
    </main>
  );
}
