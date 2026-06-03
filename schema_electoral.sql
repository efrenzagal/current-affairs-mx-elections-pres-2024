-- =============================================================================
-- MEXICAN FEDERAL ELECTORAL DATA — SUPABASE SCHEMA
-- =============================================================================
-- Design principles:
--   1. One "long" votes table per granularity level (partido as rows, not cols)
--   2. Separate dimension tables for geography + parties
--   3. election_id FK ties every fact row to a specific race + year
--   4. Designed to absorb 2000–2024 and future cycles without schema changes
--   5. coalition_votes table handles the coalition → party breakdown (PP view)
-- =============================================================================


-- ---------------------------------------------------------------------------
-- DIMENSION: elections
-- One row per race × year. PRE/SEN/DIP × 2000/2006/2012/2018/2024 etc.
-- ---------------------------------------------------------------------------
CREATE TABLE elections (
    election_id     SERIAL PRIMARY KEY,
    year            SMALLINT        NOT NULL,
    race_type       TEXT            NOT NULL,  -- 'PRE', 'SEN_MR', 'SEN_RP', 'DIP_MR', 'DIP_RP'
    scope           TEXT            NOT NULL DEFAULT 'NAL',  -- 'NAL', 'LOCAL'
    description     TEXT,                      -- e.g. 'Presidencia de la República 2024'
    source_zip_url  TEXT,                      -- original INE download URL
    loaded_at       TIMESTAMPTZ     DEFAULT now(),
    UNIQUE (year, race_type, scope)
);

COMMENT ON TABLE elections IS
    'Master registry of election races. FK target for all fact tables.';


-- ---------------------------------------------------------------------------
-- DIMENSION: parties
-- Canonical party list. Coalitions are also rows here.
-- ---------------------------------------------------------------------------
CREATE TABLE parties (
    party_id        SERIAL PRIMARY KEY,
    party_key       TEXT            NOT NULL UNIQUE,  -- 'MORENA', 'PAN', 'PAN_PRI_PRD', etc.
    party_name      TEXT,
    is_coalition    BOOLEAN         NOT NULL DEFAULT FALSE,
    active_from     SMALLINT,       -- year party first appeared
    active_to       SMALLINT        -- year party dissolved/deregistered (NULL = still active)
);

COMMENT ON TABLE parties IS
    'Party and coalition registry. party_key matches column names in raw INE CSVs.';

-- Seed with 2024 parties (extend for earlier cycles as needed)
INSERT INTO parties (party_key, party_name, is_coalition, active_from) VALUES
    ('PAN',           'Partido Acción Nacional',                          FALSE, 1988),
    ('PRI',           'Partido Revolucionario Institucional',              FALSE, 1929),
    ('PRD',           'Partido de la Revolución Democrática',              FALSE, 1989),
    ('PVEM',          'Partido Verde Ecologista de México',                FALSE, 1991),
    ('PT',            'Partido del Trabajo',                               FALSE, 1991),
    ('MC',            'Movimiento Ciudadano',                              FALSE, 1999),
    ('MORENA',        'Movimiento Regeneración Nacional',                  FALSE, 2014),
    ('PAN_PRI_PRD',   'Coalición PAN-PRI-PRD (Fuerza y Corazón por Méx)', TRUE,  2024),
    ('PAN_PRI',       'Coalición PAN-PRI',                                 TRUE,  2024),
    ('PAN_PRD',       'Coalición PAN-PRD',                                 TRUE,  2024),
    ('PRI_PRD',       'Coalición PRI-PRD',                                 TRUE,  2024),
    ('PVEM_PT_MORENA','Coalición PVEM-PT-MORENA (Sigamos Haciendo Hist.)', TRUE,  2024),
    ('PVEM_PT',       'Coalición PVEM-PT',                                 TRUE,  2024),
    ('PVEM_MORENA',   'Coalición PVEM-MORENA',                             TRUE,  2024),
    ('PT_MORENA',     'Coalición PT-MORENA',                               TRUE,  2024);


-- ---------------------------------------------------------------------------
-- DIMENSION: estados
-- ---------------------------------------------------------------------------
CREATE TABLE estados (
    id_estado       SMALLINT        PRIMARY KEY,  -- matches INE ID_ESTADO
    nombre_estado   TEXT            NOT NULL,
    circunscripcion SMALLINT        -- 1–5, can vary by redistricting cycle
);

INSERT INTO estados (id_estado, nombre_estado) VALUES
    (0,  'VOTO EN EL EXTRANJERO'),
    (1,  'AGUASCALIENTES'),
    (2,  'BAJA CALIFORNIA'),
    (3,  'BAJA CALIFORNIA SUR'),
    (4,  'CAMPECHE'),
    (5,  'COAHUILA'),
    (6,  'COLIMA'),
    (7,  'CHIAPAS'),
    (8,  'CHIHUAHUA'),
    (9,  'CIUDAD DE MEXICO'),
    (10, 'DURANGO'),
    (11, 'GUANAJUATO'),
    (12, 'GUERRERO'),
    (13, 'HIDALGO'),
    (14, 'JALISCO'),
    (15, 'MEXICO'),
    (16, 'MICHOACAN'),
    (17, 'MORELOS'),
    (18, 'NAYARIT'),
    (19, 'NUEVO LEON'),
    (20, 'OAXACA'),
    (21, 'PUEBLA'),
    (22, 'QUERETARO'),
    (23, 'QUINTANA ROO'),
    (24, 'SAN LUIS POTOSI'),
    (25, 'SINALOA'),
    (26, 'SONORA'),
    (27, 'TABASCO'),
    (28, 'TAMAULIPAS'),
    (29, 'TLAXCALA'),
    (30, 'VERACRUZ'),
    (31, 'YUCATAN'),
    (32, 'ZACATECAS');


-- ---------------------------------------------------------------------------
-- DIMENSION: distritos
-- Federal electoral districts. 300 total + district 0 = overseas.
-- ---------------------------------------------------------------------------
CREATE TABLE distritos (
    id_estado               SMALLINT    NOT NULL REFERENCES estados(id_estado),
    id_distrito_federal     SMALLINT    NOT NULL,
    cabecera_distrital      TEXT,
    PRIMARY KEY (id_estado, id_distrito_federal)
);

COMMENT ON TABLE distritos IS
    'Federal electoral districts. Populated from the DIS-level CSVs.';


-- ---------------------------------------------------------------------------
-- FACT: votes_nacional
-- Single-row national totals per election.
-- ---------------------------------------------------------------------------
CREATE TABLE votes_nacional (
    id                      BIGSERIAL   PRIMARY KEY,
    election_id             INT         NOT NULL REFERENCES elections(election_id),
    party_id                INT         NOT NULL REFERENCES parties(party_id),
    vote_type               TEXT        NOT NULL DEFAULT 'partido',
    -- 'partido'   = raw party column (NAL view)
    -- 'partido_pp'= coalition votes redistributed back to parties (PP view)
    -- 'candidato' = votes by candidate/coalition bloc (CAND view)
    num_votos               BIGINT,
    num_votos_validos       BIGINT,
    num_votos_nulos         BIGINT,
    num_votos_can_nreg      BIGINT,
    total_votos             BIGINT,
    lista_nominal           BIGINT,
    estados                 SMALLINT,
    distritos               SMALLINT,
    municipios              INT,
    secciones               INT,
    casillas                INT,
    UNIQUE (election_id, party_id, vote_type)
);


-- ---------------------------------------------------------------------------
-- FACT: votes_estado
-- One row per state × party × election.
-- ---------------------------------------------------------------------------
CREATE TABLE votes_estado (
    id                      BIGSERIAL   PRIMARY KEY,
    election_id             INT         NOT NULL REFERENCES elections(election_id),
    id_estado               SMALLINT    NOT NULL REFERENCES estados(id_estado),
    party_id                INT         NOT NULL REFERENCES parties(party_id),
    vote_type               TEXT        NOT NULL DEFAULT 'partido',
    num_votos               BIGINT,
    num_votos_validos       BIGINT,
    num_votos_nulos         BIGINT,
    num_votos_can_nreg      BIGINT,
    total_votos             BIGINT,
    lista_nominal           BIGINT,
    distritos               SMALLINT,
    municipios              INT,
    secciones               INT,
    casillas                INT,
    UNIQUE (election_id, id_estado, party_id, vote_type)
);


-- ---------------------------------------------------------------------------
-- FACT: votes_distrito
-- One row per district × party × election.
-- ---------------------------------------------------------------------------
CREATE TABLE votes_distrito (
    id                      BIGSERIAL   PRIMARY KEY,
    election_id             INT         NOT NULL REFERENCES elections(election_id),
    id_estado               SMALLINT    NOT NULL REFERENCES estados(id_estado),
    id_distrito_federal     SMALLINT    NOT NULL,
    party_id                INT         NOT NULL REFERENCES parties(party_id),
    vote_type               TEXT        NOT NULL DEFAULT 'partido',
    num_votos               BIGINT,
    num_votos_validos       BIGINT,
    num_votos_nulos         BIGINT,
    num_votos_can_nreg      BIGINT,
    total_votos             BIGINT,
    lista_nominal           BIGINT,
    secciones               INT,
    casillas                INT,
    FOREIGN KEY (id_estado, id_distrito_federal)
        REFERENCES distritos(id_estado, id_distrito_federal),
    UNIQUE (election_id, id_estado, id_distrito_federal, party_id, vote_type)
);


-- ---------------------------------------------------------------------------
-- FACT: votes_municipio
-- One row per municipio × party × election.
-- ---------------------------------------------------------------------------
CREATE TABLE votes_municipio (
    id                      BIGSERIAL   PRIMARY KEY,
    election_id             INT         NOT NULL REFERENCES elections(election_id),
    id_estado               SMALLINT    NOT NULL REFERENCES estados(id_estado),
    id_municipio            INT         NOT NULL,
    municipio_nombre        TEXT,
    party_id                INT         NOT NULL REFERENCES parties(party_id),
    vote_type               TEXT        NOT NULL DEFAULT 'partido',
    num_votos               BIGINT,
    num_votos_validos       BIGINT,
    num_votos_nulos         BIGINT,
    num_votos_can_nreg      BIGINT,
    total_votos             BIGINT,
    lista_nominal           BIGINT,
    secciones               INT,
    casillas                INT,
    UNIQUE (election_id, id_estado, id_municipio, party_id, vote_type)
);

COMMENT ON COLUMN votes_municipio.id_municipio IS
    'INE municipality ID. Note: ID 0 = overseas votes within that state grouping.';


-- ---------------------------------------------------------------------------
-- FACT: votes_seccion
-- One row per sección × party × election.
-- ---------------------------------------------------------------------------
CREATE TABLE votes_seccion (
    id                      BIGSERIAL   PRIMARY KEY,
    election_id             INT         NOT NULL REFERENCES elections(election_id),
    id_estado               SMALLINT    NOT NULL REFERENCES estados(id_estado),
    id_distrito_federal     SMALLINT,
    id_municipio            INT,
    seccion                 INT         NOT NULL,
    party_id                INT         NOT NULL REFERENCES parties(party_id),
    vote_type               TEXT        NOT NULL DEFAULT 'partido',
    num_votos               BIGINT,
    num_votos_validos       BIGINT,
    num_votos_nulos         BIGINT,
    num_votos_can_nreg      BIGINT,
    total_votos             BIGINT,
    lista_nominal           BIGINT,
    casillas                INT,
    UNIQUE (election_id, id_estado, seccion, party_id, vote_type)
);


-- ---------------------------------------------------------------------------
-- FACT: votes_casilla
-- Most granular. One row per casilla × party × election.
-- ~170k casillas × ~15 parties = ~2.5M rows per election cycle.
-- ---------------------------------------------------------------------------
CREATE TABLE votes_casilla (
    id                      BIGSERIAL   PRIMARY KEY,
    election_id             INT         NOT NULL REFERENCES elections(election_id),
    id_estado               SMALLINT    NOT NULL REFERENCES estados(id_estado),
    id_distrito_federal     SMALLINT,
    id_municipio            INT,
    seccion                 INT,
    tipo_casilla            TEXT,       -- 'B', 'C', 'MEC', etc.
    id_casilla              SMALLINT,
    ext_contigua            SMALLINT,
    acta_casilla_mec        TEXT,
    urna_electronica        BOOLEAN,
    party_id                INT         NOT NULL REFERENCES parties(party_id),
    vote_type               TEXT        NOT NULL DEFAULT 'partido',
    num_votos               BIGINT,
    num_votos_validos       BIGINT,
    num_votos_nulos         BIGINT,
    num_votos_can_nreg      BIGINT,
    total_votos             BIGINT,
    lista_nominal           BIGINT,
    estatus_acta            TEXT,
    -- No UNIQUE constraint at casilla level — too many NULLs in compound key
    -- Use election_id + acta_casilla_mec + party_id for dedup if needed
    CONSTRAINT votes_casilla_natural_key
        UNIQUE NULLS NOT DISTINCT (
            election_id, id_estado, id_distrito_federal,
            seccion, tipo_casilla, id_casilla, ext_contigua,
            party_id, vote_type
        )
);

CREATE INDEX idx_votes_casilla_election  ON votes_casilla(election_id);
CREATE INDEX idx_votes_casilla_estado    ON votes_casilla(id_estado);
CREATE INDEX idx_votes_casilla_municipio ON votes_casilla(id_municipio);
CREATE INDEX idx_votes_casilla_seccion   ON votes_casilla(seccion);


-- ---------------------------------------------------------------------------
-- FACT: winners
-- From INTEGRACION_CARGOS_PEF — the certified winners per district/race.
-- ---------------------------------------------------------------------------
CREATE TABLE winners (
    id                          BIGSERIAL   PRIMARY KEY,
    election_id                 INT         NOT NULL REFERENCES elections(election_id),
    id_estado                   SMALLINT    REFERENCES estados(id_estado),
    id_distrito_federal         SMALLINT,
    tipo_candidatura            TEXT,       -- 'PRE', 'DIP_MR'
    coalicion_key               TEXT,       -- 'PVEM_PT_MORENA', 'PAN_PRI_PRD', etc.
    partido_politico            TEXT,       -- individual party credited with win
    persona_candidata           TEXT,
    persona_candidata_suplente  TEXT,
    identidad_sexo              SMALLINT,   -- 1=M, 2=F (INE encoding)
    accion_afirmativa           TEXT,
    votacion_ganador            BIGINT,
    porcentaje_votacion         NUMERIC(6,4),
    ruta_constancia             TEXT
);

COMMENT ON TABLE winners IS
    'Certified election winners from INTEGRACION_CARGOS_PEF. One row per seat.';


-- ---------------------------------------------------------------------------
-- USEFUL VIEWS
-- ---------------------------------------------------------------------------

-- Total votes by party at national level (most common query)
CREATE VIEW v_national_results AS
SELECT
    e.year,
    e.race_type,
    p.party_key,
    p.party_name,
    p.is_coalition,
    vn.vote_type,
    vn.num_votos,
    vn.num_votos_validos,
    vn.total_votos,
    vn.lista_nominal,
    ROUND(100.0 * vn.num_votos / NULLIF(vn.num_votos_validos, 0), 4) AS pct_votos_validos,
    ROUND(100.0 * vn.total_votos / NULLIF(vn.lista_nominal, 0), 4)   AS turnout_pct
FROM votes_nacional vn
JOIN elections e USING (election_id)
JOIN parties   p USING (party_id);

-- State-level results with turnout
CREATE VIEW v_estado_results AS
SELECT
    e.year,
    e.race_type,
    est.nombre_estado,
    p.party_key,
    vn.vote_type,
    vn.num_votos,
    vn.num_votos_validos,
    vn.total_votos,
    vn.lista_nominal,
    ROUND(100.0 * vn.num_votos / NULLIF(vn.num_votos_validos, 0), 4) AS pct_votos_validos,
    ROUND(100.0 * vn.total_votos / NULLIF(vn.lista_nominal, 0), 4)   AS turnout_pct
FROM votes_estado vn
JOIN elections e    USING (election_id)
JOIN estados   est  USING (id_estado)
JOIN parties   p    USING (party_id);
