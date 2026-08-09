/**
 * Every editable string on the landing page, in one place.
 *
 * Edit here, not in `page.tsx`. The landing is the only page whose copy is not
 * derived from data, so it is the one that goes stale when the site's scope
 * grows — keeping it in a single file makes that a one-file edit.
 *
 * Write it about the *method*, never about the current subject matter. The
 * Congress is what this site covers today; the promise (official sources, every
 * figure traceable, every table documented) is what stays true when elections,
 * climate, mobility or public finances get added. Anything that is only true of
 * the hemicycle belongs on `/visualizaciones`, not here.
 */

export const LANDING_HERO = {
  eyebrow: "Política pública de México, con datos",
  title: "Los números detrás de las decisiones públicas.",
  lead:
    "Un sitio público construido sobre fuentes oficiales. ",
  actions: [
    { href: "/visualizaciones", label: "Ver las visualizaciones →", ghost: false },
    { href: "/datos", label: "Ver el diccionario de datos", ghost: true },
  ],
};

export const LANDING_SECTIONS = [
  {
    href: "/visualizaciones",
    kicker: "Tableros",
    title: "Visualizaciones interactivas",
    copy:
      "Tableros para explorar un tema a detalle: filtrar, acercarse y llegar hasta el registro " +
      "individual sin salir de la página.",
  },
  {
    href: "/articulos",
    kicker: "Análisis",
    title: "Artículos",
    copy:
      "Piezas largas escritas sobre el mismo almacén que alimenta los tableros. Las gráficas son " +
      "interactivas y se pueden abrir a pantalla completa.",
  },
  {
    href: "/datos",
    kicker: "Almacén",
    title: "Datos",
    copy:
      "El diccionario de cada tabla que sostiene el sitio: propósito, llave primaria, grano, " +
      "columnas, dominios y valores de ejemplo reales.",
  },
];

export const LANDING_NOTE = {
  eyebrow: "Cómo funciona este sitio",
  body:
    "placeholder",
};

/** Shown above the section cards so the covered areas grow visibly, not silently. */
export const LANDING_AREAS_LABEL = "Temas cubiertos hoy";

/** Header pill copy. Keep it about the site, not about one dataset. */
export const LANDING_STATUS = "Datos públicos";
