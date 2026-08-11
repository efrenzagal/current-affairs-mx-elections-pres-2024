/**
 * One vocabulary for roll-call votes across every Congress dashboard.
 *
 * Three components now render a vote choice — the two hemicycle explorers, the
 * profile search and the vote explorer — and they must agree on what green
 * means, on how a dictamen title is trimmed and on how a classification code is
 * spelled in Spanish. This is the vote-side counterpart to `parties.ts`.
 *
 * The classification codes are `snake_case` and unaccented at the source, so a
 * mechanical transform can never recover "Organización y régimen del Congreso".
 * The labels below are therefore written out, and `rendered-html.test.mjs`
 * asserts every code present in the payload has one — a new topic from a future
 * classification pass fails the build instead of shipping as raw snake_case.
 */

export const CHOICE_COLORS: Record<string, string> = {
  Favor: "#267a53",
  Contra: "#bb3d48",
  "Abstención": "#d4a72c",
  Abstencion: "#d4a72c",
  Ausente: "#9b9a94",
  "Quórum *": "#537a8f",
};

/**
 * Reading order for a vote breakdown: the two directional choices first, then
 * the three ways of not taking a side. The square grid, the stacked bars and
 * every legend iterate this, so they can never disagree on ordering.
 */
export const CHOICE_ORDER = ["Favor", "Contra", "Abstención", "Ausente", "Quórum *"];

export function choiceColor(choice: string) {
  return CHOICE_COLORS[choice] ?? "#8b8b86";
}

export function voteLabel(choice: string) {
  if (choice === "Favor") return "A favor";
  if (choice === "Contra") return "En contra";
  if (choice === "Quórum *") return "Presente, sin voto";
  return choice;
}

/** Gaceta titles carry a trailing `<p> 26 de mayo de 2026` date fragment. */
export function cleanTitle(title: string) {
  return title.replace(/\s*<p>.*$/i, "").trim();
}

/** Vote titles run long; tooltips and list rows need a summary, not the dictamen. */
export function shortTitle(title: string, limit = 90) {
  const clean = cleanTitle(title);
  return clean.length > limit ? `${clean.slice(0, limit).trimEnd()}…` : clean;
}

/**
 * Accent- and case-insensitive key for search. Both search boxes on this site
 * take Spanish input, and a reader typing "gobernacion" must find
 * "Gobernación": stripping combining marks is what makes that work.
 */
export function normalize(value: string) {
  return value.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLocaleLowerCase("es").trim();
}

export function shortDate(date: string) {
  return new Intl.DateTimeFormat("es-MX", {
    day: "numeric",
    month: "short",
    year: "numeric",
    // Noon, and only the date part: a bare `new Date("2026-05-28")` parses as
    // UTC midnight and renders as the 27th anywhere west of Greenwich.
  }).format(new Date(`${date.slice(0, 10)}T12:00:00`));
}

export function longDate(date: string) {
  return new Intl.DateTimeFormat("es-MX", {
    day: "numeric",
    month: "long",
    year: "numeric",
    // Noon, and only the date part: a bare `new Date("2026-05-28")` parses as
    // UTC midnight and renders as the 27th anywhere west of Greenwich.
  }).format(new Date(`${date.slice(0, 10)}T12:00:00`));
}

export const TOPIC_LABELS: Record<string, string> = {
  administracion_publica: "Administración pública",
  agricultura_y_desarrollo_rural: "Agricultura y desarrollo rural",
  cultura_y_deporte: "Cultura y deporte",
  derechos_humanos_e_igualdad: "Derechos humanos e igualdad",
  desarrollo_social_y_vivienda: "Desarrollo social y vivienda",
  economia_e_industria: "Economía e industria",
  educacion: "Educación",
  energia: "Energía",
  finanzas_publicas: "Finanzas públicas",
  gobernacion_y_elecciones: "Gobernación y elecciones",
  infraestructura_y_transporte: "Infraestructura y transporte",
  justicia_y_seguridad: "Justicia y seguridad",
  medio_ambiente: "Medio ambiente",
  organizacion_y_regimen_del_congreso: "Organización y régimen del Congreso",
  relaciones_exteriores: "Relaciones exteriores",
  salud: "Salud",
  trabajo_y_seguridad_social: "Trabajo y seguridad social",
  // Not subject areas: the classifier's own escape hatches, kept visible so a
  // reader can tell "we looked and it isn't clear" from "we never looked".
  no_claro: "Tema no claro",
  no_aplica: "No aplica",
  otro: "Otro",
};

export const STAGE_LABELS: Record<string, string> = {
  en_lo_general: "En lo general",
  en_lo_particular: "En lo particular",
  en_lo_general_y_particular: "En lo general y en lo particular",
  articulos_reservados_o_modificacion: "Artículos reservados o modificación",
  asunto_completo_o_no_especificado: "Asunto completo o no especificado",
  procedimental: "Procedimental",
};

export const ORIGIN_LABELS: Record<string, string> = {
  dictamen_de_comision: "Dictamen de comisión",
  iniciativa: "Iniciativa",
  acuerdo_institucional: "Acuerdo institucional",
  asunto_directo_del_pleno: "Asunto directo del pleno",
  // These two are not a spelling variant of each other: each names the chamber
  // the bill arrived from, so which one appears depends on where you are.
  minuta_del_senado: "Minuta del Senado",
  minuta_de_camara_de_diputados: "Minuta de la Cámara de Diputados",
  no_claro: "Origen no claro",
};

export const INSTRUMENT_LABELS: Record<string, string> = {
  legislativo: "Legislativo",
  constitucional: "Constitucional",
  presupuesto_finanzas_publicas: "Presupuesto y finanzas públicas",
  nombramiento_o_ratificacion: "Nombramiento o ratificación",
  acuerdo_o_proposicion: "Acuerdo o proposición",
  mocion_procedimental: "Moción procedimental",
  permiso: "Permiso",
  no_claro: "Instrumento no claro",
};

/**
 * How much scrutiny a vote's classification has had. Only the Camara ran the
 * deterministic review pass, so only it has a status; `reviewKey` folds that
 * asymmetry into one vocabulary a reader can filter on without knowing it.
 *
 * The Senado classification also carries a model `confianza` score. It is
 * deliberately not exported: a number a reader cannot act on reads as precision
 * the label does not have, and "sin revisión" already says the useful part.
 */
export type VoteReview = {
  status: string | null;
  requiresReview: boolean;
};

export const REVIEW_LABELS: Record<string, string> = {
  requiere_revision: "Marcada para revisión",
  audited: "Revisión humana",
  rule_checked: "Verificada por reglas",
  needs_review: "Requiere revisión",
  legacy_model_only: "Solo modelo",
  solo_modelo: "Solo modelo, sin revisión",
};

export function reviewKey(review: VoteReview) {
  // A flag for review outranks whatever pass last touched the row: a label
  // known to be doubtful should not read as "verificada".
  if (review.requiresReview) return "requiere_revision";
  return review.status ?? "solo_modelo";
}

export const reviewLabel = (value: string | null | undefined) => label(value, REVIEW_LABELS);

/** Mechanical fallback. Reachable only for a code with no label yet. */
export function label(value: string | null | undefined, labels?: Record<string, string>) {
  if (!value) return "Sin clasificar";
  const known = labels?.[value];
  if (known) return known;
  const spaced = value.replaceAll("_", " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

export const topicLabel = (value: string | null | undefined) => label(value, TOPIC_LABELS);
export const stageLabel = (value: string | null | undefined) => label(value, STAGE_LABELS);
export const originLabel = (value: string | null | undefined) => label(value, ORIGIN_LABELS);
export const instrumentLabel = (value: string | null | undefined) =>
  label(value, INSTRUMENT_LABELS);

/**
 * Consensus margin: the winning side's share of the votes actually cast for or
 * against. 1.0 is unanimous, 0.5 an even split. Streamlit publishes the same
 * measure (`margen` in `ui/gaceta.py`); abstentions and absences are excluded
 * from the denominator on both, because a margin is about the people who took
 * a side.
 */
export function consensusMargin(vote: { favor: number; contra: number }) {
  const effective = vote.favor + vote.contra;
  if (effective === 0) return null;
  return Math.max(vote.favor, vote.contra) / effective;
}

export function isApproved(vote: { favor: number; contra: number }) {
  return vote.favor > vote.contra;
}
