/**
 * One federal district a municipality falls in, with the number of its
 * secciones that sit there. The index lists these heaviest-first, because a
 * split is rarely even: Cuauhtémoc is 300 secciones in district 12 against 89
 * in district 2, and Cuernavaca is 209 against 2.
 */
export type DistrictShare = {
  district: number;
  secciones: number;
};

export type DistrictMunicipality = {
  id: number | null;
  name: string;
  districts: DistrictShare[];
};

export type DistrictState = {
  id: number;
  name: string;
  municipalities: DistrictMunicipality[];
};

export type DistrictLookupIndex = {
  schemaVersion: number;
  electionId: string;
  source: string;
  states: DistrictState[];
};

export type DistrictResolution = {
  status: "resolved" | "ambiguous" | "not_found";
  state: DistrictState | null;
  municipality: DistrictMunicipality | null;
  /** Heaviest first, each carrying the share of the municipality it holds. */
  districts: (DistrictShare & { share: number })[];
  message: string;
};

const STATE_ALIASES: Record<string, string> = {
  DISTRITO_FEDERAL: "CIUDAD_DE_MEXICO",
  ESTADO_DE_MEXICO: "MEXICO",
  COAHUILA: "COAHUILA_DE_ZARAGOZA",
  MICHOACAN: "MICHOACAN_DE_OCAMPO",
  VERACRUZ: "VERACRUZ_DE_IGNACIO_DE_LA_LLAVE",
};

export function normalizeDistrictLocation(value: string) {
  return value
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toUpperCase()
    .replace(/[^A-Z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
}

function canonicalState(value: string) {
  const normalized = normalizeDistrictLocation(value);
  return STATE_ALIASES[normalized] ?? normalized;
}

export function findDistrictState(index: DistrictLookupIndex, stateName: string) {
  const wanted = canonicalState(stateName);
  return index.states.find((state) => canonicalState(state.name) === wanted) ?? null;
}

/**
 * Addressable identity for one municipality. Oaxaca holds two pairs of
 * distinct municipalities that share a name (SAN JUAN MIXTEPEC 208/209 and
 * SAN PEDRO MIXTEPEC 316/317), each sitting in a different federal district,
 * so the name on its own cannot pick one out.
 */
export function municipalityKey(municipality: DistrictMunicipality) {
  return municipality.id === null
    ? normalizeDistrictLocation(municipality.name)
    : String(municipality.id);
}

export function resolveMunicipalityDistricts(
  index: DistrictLookupIndex,
  stateName: string,
  key: string,
): DistrictResolution {
  const state = findDistrictState(index, stateName);
  if (!state) {
    return { status: "not_found", state: null, municipality: null, districts: [], message: "No encontramos el estado." };
  }
  const municipality = state.municipalities.find(
    (candidate) => municipalityKey(candidate) === key,
  ) ?? null;
  if (!municipality) {
    return { status: "not_found", state, municipality: null, districts: [], message: "No encontramos el municipio en el marco electoral de 2024." };
  }
  const total = municipality.districts.reduce((sum, entry) => sum + entry.secciones, 0);
  const districts = [...municipality.districts]
    .sort((a, b) => b.secciones - a.secciones || a.district - b.district)
    .map((entry) => ({ ...entry, share: total > 0 ? entry.secciones / total : 0 }));
  return {
    status: districts.length === 1 ? "resolved" : "ambiguous",
    state,
    municipality,
    districts,
    message: districts.length === 1
      ? `Distrito federal ${districts[0].district}`
      : `Este municipio abarca ${districts.length} distritos federales; el primero cubre la mayor parte.`,
  };
}
