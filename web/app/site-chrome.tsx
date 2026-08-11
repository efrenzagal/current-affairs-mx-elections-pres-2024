/**
 * Shared site chrome. The same header and footer are rendered by every route
 * and injected into the published Quarto articles by
 * `scripts/build_article_pages.py`, so the markup and class names here are the
 * contract that script mirrors — keep them in sync when either side changes.
 */

import articles from "../public/data/articles.json";
import { DASHBOARDS } from "./visualizaciones/dashboards";

export type Section = "inicio" | "visualizaciones" | "articulos" | "datos";

export const SECTIONS: { key: Section; href: string; label: string }[] = [
  { key: "visualizaciones", href: "/visualizaciones", label: "Visualizaciones" },
  { key: "articulos", href: "/articulos", label: "Artículos" },
  { key: "datos", href: "/datos", label: "Datos" },
];

export const SITE_NAME = "current affairs mx";

const NAV_MENUS = {
  visualizaciones: {
    overview: { href: "/visualizaciones", label: "Todas las visualizaciones" },
    items: DASHBOARDS.map((dashboard) => ({
      href: dashboard.href,
      label: dashboard.title,
      meta: dashboard.area,
    })),
  },
  articulos: {
    overview: { href: "/articulos", label: "Todos los artículos" },
    items: articles.map((article) => ({
      href: article.href,
      label: article.title,
      meta: article.subtitle,
    })),
  },
} satisfies Partial<Record<Section, {
  overview: { href: string; label: string };
  items: { href: string; label: string; meta: string }[];
}>>;

export function SiteHeader({ active, status }: { active: Section; status: string }) {
  return (
    <header className="site-header">
      <a className="brand" href="/" aria-label={`${SITE_NAME}, inicio`}>
        <span className="brand-mark">ca</span>
        <span>
          current affairs
          <br />
          mx
        </span>
      </a>
      <nav aria-label="Navegación principal">
        {SECTIONS.map((section) => {
          const menu = NAV_MENUS[section.key as keyof typeof NAV_MENUS];
          if (!menu) {
            return (
              <div className="nav-item" key={section.key}>
                <a
                  href={section.href}
                  className={`nav-trigger${section.key === active ? " active" : ""}`}
                  aria-current={section.key === active ? "page" : undefined}
                >
                  {section.label}
                </a>
              </div>
            );
          }
          return (
            <details className="nav-item has-menu" key={section.key} name="site-navigation">
              <summary
                className={`nav-trigger${section.key === active ? " active" : ""}`}
              >
                {section.label}
                <span className="nav-chevron" aria-hidden="true">⌄</span>
              </summary>
              <div className="nav-menu" aria-label={`Opciones de ${section.label}`}>
                <a className="nav-menu-overview" href={menu.overview.href}>
                  {menu.overview.label}<span aria-hidden="true">→</span>
                </a>
                <div className="nav-menu-list">
                  {menu.items.map((item) => (
                    <a href={item.href} key={item.href}>
                      <strong>{item.label}</strong>
                      <span>{item.meta}</span>
                    </a>
                  ))}
                </div>
              </div>
            </details>
          );
        })}
      </nav>
      <div className="header-status">
        <span /> {status}
      </div>
    </header>
  );
}

export function SiteFooter({ note }: { note: string }) {
  return (
    <footer>
      <div className="brand footer-brand">
        <span className="brand-mark">ca</span>
        <span>{SITE_NAME}</span>
      </div>
      <p>Una lectura pública de los asuntos públicos de México.</p>
      <span>{note}</span>
    </footer>
  );
}
