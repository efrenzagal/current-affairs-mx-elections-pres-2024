/**
 * One party palette and one bench order for every Congress dashboard.
 *
 * The hemicycle explorers and the profile search both colour and sort by party;
 * keeping these here stops the two from drifting into different greens for
 * MORENA or a different left-to-right order.
 *
 * `MRN` and `CAND_INDEPENDIENTE` are the raw source spellings. The exporter now
 * canonicalizes them to `MORENA` and `IND`, so they should never reach the
 * client — they stay mapped anyway, so a future source that slips through is
 * mis-labelled rather than rendered in the fallback grey.
 */

export const PARTY_COLORS: Record<string, string> = {
  PT: "#c7323f",
  MORENA: "#8e2533",
  MRN: "#8e2533",
  PVEM: "#3b8b62",
  MC: "#e97935",
  PRI: "#d55d75",
  PAN: "#2d69a4",
  PRD: "#e5ad31",
  IND: "#7c7f82",
  CAND_INDEPENDIENTE: "#7c7f82",
  SG: "#7c7f82",
  LICENCIA: "#b9b4a8",
  VACANTE: "#d9d4c8",
};

/** Seating order, left to right. Unknown parties sort to the end. */
export const PARTY_ORDER = [
  "PT", "MORENA", "MRN", "PVEM", "MC", "PRI", "PAN", "PRD",
  "IND", "CAND_INDEPENDIENTE", "SG", "LICENCIA", "VACANTE",
];

export function partyRank(party: string) {
  const rank = PARTY_ORDER.indexOf(party);
  return rank === -1 ? PARTY_ORDER.length : rank;
}

export function partyColor(party: string) {
  return PARTY_COLORS[party] ?? "#74736e";
}
