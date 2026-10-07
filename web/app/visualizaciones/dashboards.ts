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
    slug: "encuestas",
    href: "/visualizaciones/encuestas",
    area: "Elecciones",
    title: "Encuestas electorales",
    subtitle: "Diputados 2027 · precisión histórica 1994–2024",
    summary:
      "Sigue la intención de voto rumbo a la elección de diputados de 2027, encuesta por encuesta " +
      "y con la fuente de cada una, y compara qué tan cerca quedó cada casa encuestadora del " +
      "resultado en nueve elecciones federales.",
    scope: "Encuestas de intención de voto federales desde 1994",
    topics: ["Elecciones", "Encuestas", "Casas encuestadoras"],
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
    slug: "judicial",
    href: "/visualizaciones/judicial",
    area: "Elecciones",
    title: "Elección judicial 2025",
    subtitle: "Primera elección popular del Poder Judicial · 32 entidades",
    summary:
      "Participación por estado en la primera elección judicial de México, comparada con la " +
      "presidencial de 2024, y quiénes ganaron las cuatro carreras de representación nacional o " +
      "regional: SCJN, Tribunal de Disciplina Judicial y ambas salas del TEPJF.",
    scope: "6 carreras judiciales · escala nacional y estatal",
    topics: ["Elecciones", "Poder Judicial", "Estados", "Participación"],
  },
  {
    slug: "votaciones",
    href: "/visualizaciones/votaciones",
    area: "Congreso",
    title: "Buscador de votaciones",
    subtitle: "686 votaciones nominales · Ambas cámaras · LXVI Legislatura",
    summary:
      "Busca cualquier votación por texto o por tema y ábrela para ver el resultado completo: " +
      "cifras a favor, en contra, abstenciones y ausencias, el desglose cuadro por cuadro de cada " +
      "grupo parlamentario y, en la Cámara, si se alcanzó el quórum y cada tipo de mayoría.",
    scope: "686 votaciones clasificadas por tema, etapa, origen e instrumento",
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
      "historial nominal de cada diputación. Selecciona una curul o busca por nombre —también a " +
      "quienes ya dejaron el pleno—, filtra la trayectoria por tema y abre cualquier votación " +
      "para ver cómo se dividieron los grupos parlamentarios.",
    scope: "297 votaciones nominales",
    topics: ["Congreso", "Votaciones nominales", "Composición", "Perfiles"],
  },
  {
    slug: "senado",
    href: "/visualizaciones/senado",
    area: "Congreso",
    title: "Senado de la República",
    subtitle: "128 escaños · LXVI Legislatura",
    summary:
      "El mismo explorador para la cámara alta, incluidos los escaños de primera minoría y la " +
      "lista nacional, con la misma búsqueda por nombre y filtro por tema. Las suplencias en " +
      "funciones y las vacantes se muestran como estados propios, no como el resultado " +
      "electoral de 2024.",
    scope: "389 votaciones nominales",
    topics: ["Congreso", "Votaciones nominales", "Composición", "Perfiles"],
  },
  {
    // Lives at `/estados`, not under `/visualizaciones/`, so links already
    // shared to the profile keep working.
    slug: "estados",
    href: "/estados",
    area: "Estados",
    title: "Conoce tu estado",
    subtitle: "32 entidades y la nacional · desde 1970",
    summary:
      "Un perfil por entidad, con la nacional como referencia: la Encuesta Intercensal 2025, " +
      "el PIB estatal y medio siglo de población, con su pirámide animada, natalidad, mortalidad " +
      "y esperanza de vida.",
    scope: "Intercensal 2025, PIB estatal y conciliación demográfica CONAPO",
    topics: ["Estados", "Población", "Economía"],
  },
];

/**
 * How the dashboards are grouped in the header menu and on the index. Groups
 * are editorial ("Brújula legislativa" bundles three Congress explorers), so
 * they are declared here rather than derived from `area`, which stays the
 * subject tag the landing page lists. `label` overrides a dashboard's title
 * where the group name already says it.
 */
type GroupSpec = { label: string; items: { slug: string; label?: string }[] };

const GROUP_SPECS: GroupSpec[] = [
  { label: "Aprobación presidencial", items: [{ slug: "aprobacion" }] },
  { label: "Encuestas electorales", items: [{ slug: "encuestas" }] },
  {
    label: "Brújula legislativa",
    items: [{ slug: "diputados" }, { slug: "senado" }, { slug: "votaciones" }],
  },
  {
    label: "Geografía electoral",
    items: [
      { slug: "trayectoria", label: "Elecciones federales · INE" },
      { slug: "judicial" },
    ],
  },
  { label: "Conoce tu estado", items: [{ slug: "estados" }] },
];

export type DashboardGroup = {
  label: string;
  items: (Dashboard & { label: string })[];
};

// Resolved at module load, so a mistyped slug or a dashboard left out of every
// group fails the build instead of silently vanishing from the menu.
export const DASHBOARD_GROUPS: DashboardGroup[] = GROUP_SPECS.map((group) => ({
  label: group.label,
  items: group.items.map(({ slug, label }) => {
    const dashboard = DASHBOARDS.find((candidate) => candidate.slug === slug);
    if (!dashboard) throw new Error(`DASHBOARD_GROUPS: unknown dashboard "${slug}"`);
    return { ...dashboard, label: label ?? dashboard.title };
  }),
}));

const grouped = DASHBOARD_GROUPS.flatMap((group) => group.items.map((item) => item.slug));
for (const dashboard of DASHBOARDS) {
  if (grouped.filter((slug) => slug === dashboard.slug).length !== 1) {
    throw new Error(`DASHBOARD_GROUPS: "${dashboard.slug}" must sit in exactly one group`);
  }
}
