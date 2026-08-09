"use client";

import {
  LANDING_AREAS_LABEL,
  LANDING_HERO,
  LANDING_NOTE,
  LANDING_SECTIONS,
  LANDING_STATUS,
} from "./site-content";
import { SiteFooter, SiteHeader } from "./site-chrome";
import { DASHBOARDS } from "./visualizaciones/dashboards";

/**
 * Landing page. Deliberately static: no data fetch, no loading state. The
 * explorers each pull megabytes of seats and histories, and the front door
 * should not pay for that before the reader has chosen where to go.
 *
 * All prose lives in `site-content.ts` — edit there. This file only decides
 * layout and what it derives from the dashboard registry, so that adding a
 * dashboard on a new subject updates the front page without touching it.
 */

/** Subject areas currently covered, in registry order, deduplicated. */
const AREAS = [...new Set(DASHBOARDS.map((dashboard) => dashboard.area))];

/** Show a few dashboards by name; the section link carries the rest. */
const FEATURED_LIMIT = 4;

export default function Home() {
  const featured = DASHBOARDS.slice(0, FEATURED_LIMIT);
  const remaining = DASHBOARDS.length - featured.length;

  return (
    <main>
      <SiteHeader active="inicio" status={LANDING_STATUS} />

      <section className="landing-hero">
        <p className="eyebrow">{LANDING_HERO.eyebrow}</p>
        <h1>{LANDING_HERO.title}</h1>
        <p className="hero-copy">{LANDING_HERO.lead}</p>
        <div className="landing-actions">
          {LANDING_HERO.actions.map((action) => (
            <a
              key={action.href}
              className={action.ghost ? "landing-cta ghost" : "landing-cta"}
              href={action.href}
            >
              {action.label}
            </a>
          ))}
        </div>
      </section>

      {AREAS.length > 0 && (
        <section className="landing-areas" aria-label={LANDING_AREAS_LABEL}>
          <span className="landing-areas-label">{LANDING_AREAS_LABEL}</span>
          <div className="landing-area-list">
            {AREAS.map((area) => (
              <span key={area}>{area}</span>
            ))}
          </div>
        </section>
      )}

      <section className="landing-grid">
        {LANDING_SECTIONS.map((section) => {
          const isDashboards = section.href === "/visualizaciones";
          return (
            <article key={section.href} className="landing-card">
              <p className="eyebrow">{section.kicker}</p>
              <h2>
                <a href={section.href}>{section.title}</a>
              </h2>
              <p>{section.copy}</p>
              {isDashboards && featured.length > 0 && (
                <div className="landing-sublinks">
                  {featured.map((dashboard) => (
                    <a key={dashboard.href} href={dashboard.href}>
                      {dashboard.title} →
                    </a>
                  ))}
                  {remaining > 0 && (
                    <a href="/visualizaciones">
                      Ver los {DASHBOARDS.length} tableros →
                    </a>
                  )}
                </div>
              )}
            </article>
          );
        })}
      </section>

      <section className="method-note" id="metodologia">
        <p className="eyebrow">{LANDING_NOTE.eyebrow}</p>
        <p>{LANDING_NOTE.body}</p>
      </section>

      <SiteFooter note="Inicio" />
    </main>
  );
}
