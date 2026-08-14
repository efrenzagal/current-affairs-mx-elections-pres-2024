/**
 * Registry for the Visualizaciones section.
 *
 * Articles are data-driven from `public/data/articles.json` because Python
 * publishes them. Dashboards are React routes, so the list lives here in
 * TypeScript: adding one means adding a route anyway, and this way a typo in a
 * slug is a build error rather than a dead card.
 */

export type Dashboard = {
  slug: string;
  href: string;
  /**
   * Subject area, e.g. "Congreso", "Elecciones", "Finanzas públicas". The
   * landing page derives the list of covered areas from this field, so adding a
   * dashboard on a new topic advertises that topic automatically instead of
   * requiring someone to remember to update the front page.
   */
  area: string;
  title: string;
  subtitle: string;
  summary: string;
  /** Filled from the dataset manifest at runtime; static text is the fallback. */
  scope: string;
  topics: string[];
};

export const DASHBOARDS: Dashboard[] = [
  {
    slug: "aprobacion",
    href: "/visualizaciones/aprobacion",
    area: "Presidencia",
    title: "Aprobación presidencial",
    subtitle: "6 sexenios · 1994–2026",
    summary:
      "Compara la aprobación de cada presidente en el mismo punto de su mandato. Elige a quién " +
      "destacar y filtra por casa encuestadora para ver el resto de los sexenios como referencia.",
    scope: "Encuestas de aprobación alineadas por mes de mandato",
    topics: ["Presidencia", "Encuestas", "Aprobación"],
  },
  {
    slug: "trayectoria",
    href: "/visualizaciones/trayectoria",
    area: "Elecciones",
    title: "Geografía electoral",
    subtitle: "32 entidades · Presidencia, Senado y Diputaciones",
    summary:
      "Selecciona un estado y compara la trayectoria del voto presidencial, del Senado y de las " +
      "diputaciones federales. Cambia de ciclo para explorar coaliciones y partidos.",
    scope: "18 elecciones federales · escala nacional y estatal",
    topics: ["Elecciones", "Estados", "Trayectoria", "Partidos y coaliciones"],
  },
  {
    slug: "perfiles",
    href: "/visualizaciones/perfiles",
    area: "Congreso",
    title: "Perfiles legislativos",
    subtitle: "628 escaños · Cámara de Diputados y Senado · LXVI Legislatura",
    summary:
      "Busca a una diputada, diputado, senadora o senador y recorre su trayectoria nominal. " +
      "Compara votos a favor, en contra, abstenciones y ausencias; filtra por tema y distingue " +
      "el partido que ganó el escaño del grupo parlamentario actual.",
    scope: "673 votaciones nominales entre ambas cámaras",
    topics: ["Congreso", "Perfiles", "Temas", "Votaciones nominales"],
  },
  {
    slug: "votaciones",
    href: "/visualizaciones/votaciones",
    area: "Congreso",
    title: "Buscador de votaciones",
    subtitle: "673 votaciones nominales · Ambas cámaras · LXVI Legislatura",
    summary:
      "Busca cualquier votación por texto o por tema y ábrela para ver el resultado completo: " +
      "cifras a favor, en contra, abstenciones y ausencias, el desglose cuadro por cuadro de cada " +
      "grupo parlamentario y, en la Cámara, si se alcanzó el quórum y cada tipo de mayoría.",
    scope: "673 votaciones clasificadas por tema, etapa, origen e instrumento",
    topics: ["Congreso", "Votaciones nominales", "Temas", "Búsqueda"],
  },
  {
    slug: "diputados",
    href: "/visualizaciones/diputados",
    area: "Congreso",
    title: "Cámara de Diputados",
    subtitle: "500 escaños · LXVI Legislatura",
    summary:
      "El pleno escaño por escaño, con la composición vigente del directorio oficial y el " +
      "historial nominal de cada diputación. Selecciona una curul, recorre sus votaciones y " +
      "abre cualquiera para ver cómo se dividieron los grupos parlamentarios.",
    scope: "295 votaciones nominales",
    topics: ["Congreso", "Votaciones nominales", "Composición"],
  },
  {
    slug: "senado",
    href: "/visualizaciones/senado",
    area: "Congreso",
    title: "Senado de la República",
    subtitle: "128 escaños · LXVI Legislatura",
    summary:
      "El mismo explorador para la cámara alta, incluidos los escaños de primera minoría y la " +
      "lista nacional. Las suplencias en funciones y las vacantes se muestran como estados " +
      "propios, no como el resultado electoral de 2024.",
    scope: "378 votaciones nominales",
    topics: ["Congreso", "Votaciones nominales", "Composición"],
  },
];
