export type DistrictMunicipality = {
  id: number | null;
  name: string;
  districts: number[];
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
  districts: number[];
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

export function resolveMunicipalityDistricts(
  index: DistrictLookupIndex,
  stateName: string,
  municipalityName: string,
): DistrictResolution {
  const state = findDistrictState(index, stateName);
  if (!state) {
    return { status: "not_found", state: null, municipality: null, districts: [], message: "No encontramos el estado." };
  }
  const wanted = normalizeDistrictLocation(municipalityName);
  const municipality = state.municipalities.find(
    (candidate) => normalizeDistrictLocation(candidate.name) === wanted,
  ) ?? null;
  if (!municipality) {
    return { status: "not_found", state, municipality: null, districts: [], message: "No encontramos el municipio en el marco electoral de 2024." };
  }
  const districts = [...municipality.districts].sort((a, b) => a - b);
  return {
    status: districts.length === 1 ? "resolved" : "ambiguous",
    state,
    municipality,
    districts,
    message: districts.length === 1
      ? `Distrito federal ${districts[0]}`
      : `Este municipio abarca ${districts.length} distritos federales.`,
  };
}

type PostalPlace = { "place name": string; state: string };
type PostalResponse = { places?: PostalPlace[] };

export async function resolveFederalDistrict(
  index: DistrictLookupIndex,
  query: { postalCode: string } | { state: string; municipality: string },
): Promise<DistrictResolution> {
  if ("state" in query) {
    return resolveMunicipalityDistricts(index, query.state, query.municipality);
  }
  const postalCode = query.postalCode.trim();
  if (!/^\d{5}$/.test(postalCode)) {
    return { status: "not_found", state: null, municipality: null, districts: [], message: "Escribe un código postal de cinco dígitos." };
  }
  try {
    const response = await fetch(`https://api.zippopotam.us/MX/${postalCode}`);
    if (!response.ok) throw new Error(String(response.status));
    const payload = await response.json() as PostalResponse;
    for (const place of payload.places ?? []) {
      const result = resolveMunicipalityDistricts(index, place.state, place["place name"]);
      if (result.status !== "not_found") return result;
    }
    const first = payload.places?.[0];
    const state = first ? findDistrictState(index, first.state) : null;
    return {
      status: "not_found",
      state,
      municipality: null,
      districts: [],
      message: "Ubicamos el estado, pero no el municipio. Selecciónalo abajo para continuar.",
    };
  } catch {
    return { status: "not_found", state: null, municipality: null, districts: [], message: "No pudimos consultar ese código postal. Usa estado y municipio." };
  }
}

