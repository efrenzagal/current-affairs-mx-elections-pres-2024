/**
 * Shared site chrome. The same header and footer are rendered by every route
 * and injected into the published Quarto articles by
 * `scripts/build_article_pages.py`, so the markup and class names here are the
 * contract that script mirrors — keep them in sync when either side changes.
 */

export type Section = "inicio" | "visualizaciones" | "articulos" | "datos";

export const SECTIONS: { key: Section; href: string; label: string }[] = [
  { key: "visualizaciones", href: "/visualizaciones", label: "Visualizaciones" },
  { key: "articulos", href: "/articulos", label: "Artículos" },
  { key: "datos", href: "/datos", label: "Datos" },
];

export const SITE_NAME = "current affairs mx";

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
        {SECTIONS.map((section) => (
          <a
            key={section.key}
            href={section.href}
            className={section.key === active ? "active" : undefined}
            aria-current={section.key === active ? "page" : undefined}
          >
            {section.label}
          </a>
        ))}
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
