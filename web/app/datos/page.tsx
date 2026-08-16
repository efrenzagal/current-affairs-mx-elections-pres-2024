"use client";

import { useEffect, useMemo, useState } from "react";

import { SITE_NAME, SiteFooter, SiteHeader } from "../site-chrome";

type Column = {
  "Column Name": string;
  "Data Type": string;
  Role: string;
  "Description (SPA)": string;
  "Description (ENG)": string;
  "Values / Domain": string;
  Notes: string;
};

type Overview = {
  "Table Name": string;
  "Primary Key": string;
  "Row Grain": string;
  "Approx. Row Count": string;
  Purpose: string;
  "Key Foreign Keys": string;
  Notes: string;
};

type RawMeta = {
  cycle: string;
  source_file: string;
  election_scope: string;
  delimiter: string;
  header_line_number: string;
  important_information: string;
};

type DictionaryTable = {
  columns: Column[];
  overview: Overview | null;
  rawMeta?: RawMeta;
  examples?: Record<string, string[]>;
  inWarehouse?: boolean;
  source: string;
  raw: boolean;
};

type CoverageRow = { year: number; PRE: string; DIP: string; SEN: string };

type LegislatureRow = {
  legislature: number;
  label: string;
  voteCount: number;
  firstVote: string;
  latestVote: string;
};

type Dictionary = {
  tables: Record<string, DictionaryTable>;
  groups: { name: string; tables: string[] }[];
  coverage: CoverageRow[];
  legislativeCoverage: { deputies: LegislatureRow[]; senate: LegislatureRow[] };
  warehouseTableCount: number;
  rawReferenceCount: number;
  sampleSize: number;
};

type Language = "spa" | "eng";

const OVERVIEW = "_overview";

/** The payload keeps the exporter's English group names; the site reads in Spanish. */
const GROUP_NAMES: Record<string, string> = {
  "Federal Electoral Results": "Resultados electorales federales",
  "Geography & Election Calendar": "Geografía y calendario electoral",
  "Cámara de Diputados Roll Calls": "Votaciones de la Cámara de Diputados",
  "Senado de la República Roll Calls": "Votaciones del Senado de la República",
  "Current Congreso Rosters": "Integración vigente del Congreso",
  "Other Warehouse Tables": "Otras tablas del almacén",
  "Raw Source References": "Referencias de fuente original",
};

const COVERAGE_LABELS: Record<string, string> = {
  loaded: "Cargada",
  missing: "Falta",
  not_held: "No hubo",
};

function groupLabel(name: string) {
  return GROUP_NAMES[name] ?? name;
}

function longDate(date: string) {
  return new Intl.DateTimeFormat("es-MX", {
    day: "numeric",
    month: "short",
    year: "numeric",
  }).format(new Date(`${date}T12:00:00`));
}

function primaryKeys(overview: Overview | null) {
  return String(overview?.["Primary Key"] ?? "")
    .split(",")
    .map((key) => key.trim())
    .filter(Boolean);
}

function CoverageMark({ status }: { status: string }) {
  const className = status === "loaded" ? "yes" : status === "missing" ? "missing" : "not-held";
  return <span className={`dict-${className}`}>{COVERAGE_LABELS[status] ?? status}</span>;
}

function LegislatureTable({ rows }: { rows: LegislatureRow[] }) {
  return (
    <div className="dict-table-wrap">
      <table>
        <thead>
          <tr>
            <th>Legislatura</th>
            <th>Votaciones nominales</th>
            <th>Primera votación</th>
            <th>Última votación</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.legislature}>
              <td className="dict-column">{row.label}</td>
              <td>{row.voteCount.toLocaleString("es-MX")}</td>
              <td>{longDate(row.firstVote)}</td>
              <td>{longDate(row.latestVote)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function DictionaryPage() {
  const [model, setModel] = useState<Dictionary | null>(null);
  const [error, setError] = useState(false);
  const [current, setCurrent] = useState<string>(OVERVIEW);
  const [language, setLanguage] = useState<Language>("spa");
  const [query, setQuery] = useState("");

  useEffect(() => {
    let cancelled = false;
    fetch("/data/dictionary.json")
      .then((response) => {
        if (!response.ok) throw new Error(String(response.status));
        return response.json();
      })
      .then((payload: Dictionary) => {
        if (!cancelled) setModel(payload);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const queryNormalized = query.trim().toLocaleLowerCase("es");

  // A table matches on its own name or on anything written about its columns,
  // so a reader can find a field without knowing which table holds it.
  const visibleGroups = useMemo(() => {
    if (!model) return [];
    const matches = (name: string) => {
      if (!queryNormalized) return true;
      if (name.toLocaleLowerCase("es").includes(queryNormalized)) return true;
      return model.tables[name].columns.some((column) =>
        Object.values(column).some((value) =>
          String(value ?? "").toLocaleLowerCase("es").includes(queryNormalized),
        ),
      );
    };
    return model.groups
      .map((group) => ({ name: group.name, tables: group.tables.filter(matches) }))
      .filter((group) => group.tables.length > 0);
  }, [model, queryNormalized]);

  if (error) {
    return (
      <main className="state-screen">
        <p className="eyebrow">{SITE_NAME}</p>
        <h1>No pudimos cargar el diccionario de datos.</h1>
        <p>Actualiza la página para intentarlo de nuevo.</p>
      </main>
    );
  }

  if (!model) {
    return (
      <main className="state-screen loading-state" aria-live="polite">
        <span className="loading-mark" />
        <p className="eyebrow">{SITE_NAME}</p>
        <h1>Abriendo el diccionario…</h1>
      </main>
    );
  }

  const item = current === OVERVIEW ? null : model.tables[current];
  const documentedColumns = Object.values(model.tables)
    .filter((table) => !table.raw)
    .reduce((total, table) => total + table.columns.length, 0);

  return (
    <main>
      <SiteHeader active="datos" status="Diccionario de datos" />

      <div className="dict-app">
        <nav className="dict-sidebar" aria-label="Tablas del diccionario">
          <div className="dict-search">
            <span>⌕</span>
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Buscar tabla o columna…"
              aria-label="Buscar tabla o columna"
              autoComplete="off"
            />
          </div>
          <button
            type="button"
            className={`dict-nav-button${current === OVERVIEW ? " active" : ""}`}
            onClick={() => setCurrent(OVERVIEW)}
          >
            <span>Panorama del almacén</span>
          </button>
          {visibleGroups.map((group) => (
            <div key={group.name}>
              <p className="dict-group-title">{groupLabel(group.name)}</p>
              {group.tables.map((name) => (
                <button
                  key={name}
                  type="button"
                  className={
                    `dict-nav-button${model.tables[name].raw ? " raw" : ""}` +
                    `${current === name ? " active" : ""}`
                  }
                  onClick={() => setCurrent(name)}
                >
                  <span>{name}</span>
                  <span className="dict-count">
                    {model.tables[name].inWarehouse === false ? "pendiente" : model.tables[name].columns.length}
                  </span>
                </button>
              ))}
            </div>
          ))}
          {queryNormalized && visibleGroups.length === 0 && (
            <p className="dict-empty">Sin coincidencias para «{query.trim()}».</p>
          )}
        </nav>

        <div className="dict-content">
          {item === null ? (
            <>
              <p className="eyebrow">Panorama</p>
              <h1 className="dict-title dict-title-serif">El almacén de datos</h1>
              <p className="dict-purpose">
                Toda cifra publicada en current affairs mx sale de un almacén SQLite documentado tabla
                por tabla. Esta es esa documentación: qué contiene cada tabla, cómo se une con las demás
                y qué valores reales guarda cada columna.
              </p>

              <div className="dict-stats">
                <div className="dict-stat">
                  <strong>{model.warehouseTableCount}</strong>
                  <span>tablas normalizadas</span>
                </div>
                <div className="dict-stat">
                  <strong>{documentedColumns}</strong>
                  <span>columnas documentadas</span>
                </div>
                <div className="dict-stat">
                  <strong>{model.rawReferenceCount}</strong>
                  <span>diseños de fuente original</span>
                </div>
              </div>

              {model.coverage.length > 0 && (
                <>
                  <h2 className="dict-subtitle">Cobertura de elecciones federales</h2>
                  <p className="dict-purpose">
                    Las diputaciones se eligen cada tres años; la Presidencia y el Senado, cada seis.
                  </p>
                  <div className="dict-table-wrap">
                    <table className="dict-coverage">
                      <thead>
                        <tr>
                          <th>Año</th>
                          <th>Presidencia · 6 años</th>
                          <th>Diputaciones MR · 3 años</th>
                          <th>Senadurías MR · 6 años</th>
                        </tr>
                      </thead>
                      <tbody>
                        {model.coverage.map((row) => (
                          <tr key={row.year}>
                            <td className="dict-column">{row.year}</td>
                            <td><CoverageMark status={row.PRE} /></td>
                            <td><CoverageMark status={row.DIP} /></td>
                            <td><CoverageMark status={row.SEN} /></td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <p className="dict-note">
                    <b>Años intermedios.</b> En 1997 y 2003 faltan resultados de diputaciones.
                    La Presidencia y el Senado aparecen como «No hubo» porque esas elecciones no se
                    celebraron en años intermedios.
                  </p>
                </>
              )}

              {(model.legislativeCoverage.deputies.length > 0 ||
                model.legislativeCoverage.senate.length > 0) && (
                <>
                  <h2 className="dict-subtitle">Cobertura de votaciones nominales</h2>
                  <p className="dict-purpose">
                    Se refiere a votaciones registradas en el pleno, no a resultados electorales.
                  </p>
                  {model.legislativeCoverage.deputies.length > 0 && (
                    <>
                      <h3 className="dict-chamber-title">Cámara de Diputados</h3>
                      <LegislatureTable rows={model.legislativeCoverage.deputies} />
                    </>
                  )}
                  {model.legislativeCoverage.senate.length > 0 && (
                    <>
                      <h3 className="dict-chamber-title">Senado de la República</h3>
                      <LegislatureTable rows={model.legislativeCoverage.senate} />
                      <p className="dict-note">
                        <b>Alcance del Senado.</b> Las votaciones del Senado cubren por ahora sólo la
                        legislatura más reciente, la LXVI.
                      </p>
                    </>
                  )}
                </>
              )}

              <div className="dict-overview-grid">
                {model.groups.map((group) => (
                  <div key={group.name} className="dict-overview-card">
                    <h2>{groupLabel(group.name)}</h2>
                    <p>
                      {group.tables.length} tablas ·{" "}
                      {group.tables.map((name, index) => (
                        <span key={name}>
                          {index > 0 && ", "}
                          <button type="button" className="dict-inline-link" onClick={() => setCurrent(name)}>
                            {name}
                          </button>
                        </span>
                      ))}
                    </p>
                  </div>
                ))}
              </div>
            </>
          ) : (
            <>
              <p className={`eyebrow${item.raw ? " dict-eyebrow-raw" : ""}`}>
                {item.raw ? "Fuente original" : "Diccionario de tabla"}
              </p>
              <h1 className="dict-title">{current}</h1>

              {item.overview && (
                <>
                  <p className="dict-purpose">{item.overview.Purpose}</p>
                  <div className="dict-chips">
                    <div className="dict-chip">
                      <span className="dict-chip-key">Llave primaria</span>
                      <span className="dict-chip-value">{item.overview["Primary Key"]}</span>
                    </div>
                    <div className="dict-chip">
                      <span className="dict-chip-key">Grano de fila</span>
                      <span className="dict-chip-value">{item.overview["Row Grain"]}</span>
                    </div>
                    <div className="dict-chip">
                      <span className="dict-chip-key">Filas aprox.</span>
                      <span className="dict-chip-value">{item.overview["Approx. Row Count"]}</span>
                    </div>
                  </div>
                  {item.overview["Key Foreign Keys"] && item.overview["Key Foreign Keys"] !== "None" && (
                    <div className="dict-chips">
                      <div className="dict-chip wide">
                        <span className="dict-chip-key">Uniones importantes</span>
                        <span className="dict-chip-value">{item.overview["Key Foreign Keys"]}</span>
                      </div>
                    </div>
                  )}
                  {item.overview.Notes && (
                    <p className="dict-note"><b>Notas.</b> {item.overview.Notes}</p>
                  )}
                </>
              )}

              {item.rawMeta && (
                <>
                  <p className="dict-purpose">
                    Diseño representativo del ciclo {item.rawMeta.cycle} tal como llega de la fuente,
                    antes de normalizarse en el almacén.
                  </p>
                  <div className="dict-chips">
                    <div className="dict-chip wide">
                      <span className="dict-chip-key">Archivo de origen</span>
                      <span className="dict-chip-value">{item.rawMeta.source_file}</span>
                    </div>
                    <div className="dict-chip">
                      <span className="dict-chip-key">Alcance electoral</span>
                      <span className="dict-chip-value">{item.rawMeta.election_scope}</span>
                    </div>
                    <div className="dict-chip">
                      <span className="dict-chip-key">Delimitador</span>
                      <span className="dict-chip-value">{item.rawMeta.delimiter}</span>
                    </div>
                    <div className="dict-chip">
                      <span className="dict-chip-key">Línea de encabezado</span>
                      <span className="dict-chip-value">{item.rawMeta.header_line_number}</span>
                    </div>
                  </div>
                  {item.rawMeta.important_information && (
                    <p className="dict-note raw"><b>Notas.</b> {item.rawMeta.important_information}</p>
                  )}
                </>
              )}

              {item.inWarehouse === false && (
                <p className="dict-note">
                  <b>Pendiente en el almacén.</b> Esta tabla ya está documentada, pero todavía no
                  existe en <code>election_data.db</code>. Por eso la columna de ejemplos aparece
                  vacía: no hay filas de las cuales tomar valores reales.
                </p>
              )}

              {/* Only the description column is bilingual in the source CSVs, so the
                  control says so rather than implying it translates the whole page. */}
              <div className="dict-language">
                <span className="dict-language-label">Idioma de la descripción</span>
                <div className="dict-toggle" role="group" aria-label="Idioma de la columna Descripción">
                  <button
                    type="button"
                    className={language === "spa" ? "active" : ""}
                    onClick={() => setLanguage("spa")}
                  >
                    Español
                  </button>
                  <button
                    type="button"
                    className={language === "eng" ? "active" : ""}
                    onClick={() => setLanguage("eng")}
                  >
                    English
                  </button>
                </div>
              </div>

              <div className="dict-table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Columna</th>
                      <th>Tipo</th>
                      <th>Rol</th>
                      <th>Descripción</th>
                      <th>Valores / Dominio</th>
                      <th>Ejemplos</th>
                      <th>Notas</th>
                    </tr>
                  </thead>
                  <tbody>
                    {item.columns.map((column) => {
                      const name = column["Column Name"];
                      const examples = item.examples?.[name] ?? [];
                      return (
                        <tr key={name}>
                          <td className="dict-column">
                            {name}
                            {primaryKeys(item.overview).includes(name) && (
                              <span className="dict-badge">PK</span>
                            )}
                          </td>
                          <td className="dict-type">{column["Data Type"]}</td>
                          <td className="dict-type">{column.Role}</td>
                          <td className="dict-description">
                            {language === "spa" ? column["Description (SPA)"] : column["Description (ENG)"]}
                          </td>
                          <td className="dict-domain">{column["Values / Domain"]}</td>
                          <td className="dict-examples">
                            {examples.length === 0 ? (
                              <span className="dict-no-sample">—</span>
                            ) : (
                              examples.map((value, index) => (
                                <span key={index} className="dict-sample">{value}</span>
                              ))
                            )}
                          </td>
                          <td className="dict-domain">{column.Notes}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <p className="dict-footnote">
                {item.columns.length} columnas · hasta {model.sampleSize} valores distintos por columna ·
                fuente: <code>{item.source}</code>
              </p>
            </>
          )}
        </div>
      </div>

      <SiteFooter note="Datos · Diccionario del almacén" />
    </main>
  );
}
