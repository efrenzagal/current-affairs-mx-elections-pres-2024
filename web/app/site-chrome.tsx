/**
 * Shared site chrome. The same header and footer are rendered by every route
 * and injected into the published Quarto articles by
 * `scripts/build_article_pages.py`, so the markup and class names here are the
 * contract that script mirrors — keep them in sync when either side changes.
 */

import articles from "../public/data/articles.json";
import { DASHBOARD_GROUPS } from "./visualizaciones/dashboards";

export type Section = "inicio" | "visualizaciones" | "articulos" | "datos";

export const SECTIONS: { key: Section; href: string; label: string }[] = [
  { key: "visualizaciones", href: "/visualizaciones", label: "Visualizaciones" },
  { key: "articulos", href: "/articulos", label: "Artículos" },
  { key: "datos", href: "/datos", label: "Datos" },
];

export const SITE_NAME = "Latitud Pública";

type MenuLink = { href: string; label: string; meta: string };
/**
 * A row of a sectioned menu: a plain link when the section holds one page,
 * otherwise a label whose pages open in a side panel on hover or focus.
 */
type MenuSection = { label: string; href: string } | { label: string; items: MenuLink[] };

type NavMenu = { overview: { href: string; label: string } } & (
  | { items: MenuLink[] }
  | { sections: MenuSection[] }
);

const NAV_MENUS: Partial<Record<Section, NavMenu>> = {
  visualizaciones: {
    overview: { href: "/visualizaciones", label: "Todas las visualizaciones" },
    sections: DASHBOARD_GROUPS.map((group): MenuSection =>
      group.items.length === 1
        ? { label: group.label, href: group.items[0].href }
        : {
            label: group.label,
            items: group.items.map((item) => ({
              href: item.href,
              label: item.label,
              meta: item.subtitle,
            })),
          },
    ),
  },
  articulos: {
    overview: { href: "/articulos", label: "Todos los artículos" },
    items: articles.map((article) => ({
      href: article.href,
      label: article.title,
      meta: article.subtitle,
    })),
  },
};

function MenuItem({ item }: { item: MenuLink }) {
  return (
    <a href={item.href}>
      <strong>{item.label}</strong>
      <span>{item.meta}</span>
    </a>
  );
}

/**
 * The side panel opens on :hover / :focus-within, so it needs no script. The
 * label takes `tabIndex` so a tap focuses it on touch screens, where the panel
 * expands in place instead (see `.nav-submenu` in globals.css).
 */
function MenuSectionRow({ section }: { section: MenuSection }) {
  if ("href" in section) {
    return (
      <a className="nav-menu-section" href={section.href}>
        {section.label}
      </a>
    );
  }
  return (
    <div className="nav-menu-group">
      <span className="nav-menu-section has-submenu" tabIndex={0} aria-haspopup="true">
        {section.label}
      </span>
      <div className="nav-submenu" role="group" aria-label={section.label}>
        {section.items.map((item) => <MenuItem item={item} key={item.href} />)}
      </div>
    </div>
  );
}

export function SiteHeader({ active, status }: { active: Section; status: string }) {
  return (
    <header className="site-header">
      <a className="brand" href="/" aria-label={`${SITE_NAME}, inicio`}>
        <span className="brand-mark">lp</span>
        <span>
          Latitud
          <br />
          Pública
        </span>
      </a>
      <nav aria-label="Navegación principal">
        {SECTIONS.map((section) => {
          const menu = NAV_MENUS[section.key];
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
              <div
                className={`nav-menu${"sections" in menu ? " nav-menu-sections" : ""}`}
                aria-label={`Opciones de ${section.label}`}
              >
                <a className="nav-menu-overview" href={menu.overview.href}>
                  {menu.overview.label}<span aria-hidden="true">→</span>
                </a>
                <div className="nav-menu-list">
                  {"sections" in menu
                    ? menu.sections.map((entry) => <MenuSectionRow section={entry} key={entry.label} />)
                    : menu.items.map((item) => <MenuItem item={item} key={item.href} />)}
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
        <span className="brand-mark">lp</span>
        <span>{SITE_NAME}</span>
      </div>
      <p>Una lectura pública de los asuntos públicos de México.</p>
      <span>{note}</span>
    </footer>
  );
}
