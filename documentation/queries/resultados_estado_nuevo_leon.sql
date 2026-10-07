-- Every election in Nuevo León (id_estado 19), federal and local: one row per
-- election x ballot label with votes and share of valid votes.
-- Nuevo León copy of state_scorecards/analysis/queries/state_all_results.sql
-- (see its header for sources and label conventions). For another state,
-- replace 19 everywhere, and '19' / '20' in fed_casilla with the state's
-- two-digit code and the next one.
-- Set Rows to blank (no limit) before running: about 400 rows, ~1 minute.

WITH fed_label (party_key, label) AS (
    VALUES
        ('A. CAM.', 'pan-pvem'),                 -- Alianza por el Cambio, 2000
        ('A. MEX.', 'prd-pt-conve-psn-pas'),     -- Alianza por México, 2000
        ('APM', 'pri-pvem'),                     -- Alianza por México, 2006
        ('PBT', 'prd-pt-conve'),                 -- Por el Bien de Todos, 2006
        ('NVA_A', 'pna'),
        ('ASDC', 'pasd'),
        ('UNO_PDM', 'pdm'),
        ('PANAL', 'pna'),
        ('NUEVA ALIANZA', 'pna'),
        ('PRI_NA', 'pri-pna'),
        ('PVEM_NA', 'pvem-pna'),
        ('PRI_PVEM_NA', 'pri-pvem-pna'),
        ('ENCUENTRO SOCIAL', 'pes'),
        ('MOVIMIENTO CIUDADANO', 'mc'),
        ('CI', 'indep'),
        ('CAND_IND1', 'indep1'),   ('CAND_IND2', 'indep2'),
        ('CAND_IND_1', 'indep1'),  ('CAND_IND_2', 'indep2'),
        ('CAND_IND_01', 'indep1'), ('CAND_IND_02', 'indep2')
),

-- Federal: casilla_id starts with the two-digit state code in every year
-- ('14_0024_B01' since 2024, '140024B010002' in 2018/2021), so this range
-- reads only the state's casillas through the primary key
fed_casilla AS (
    SELECT
        v.election_id,
        v.casilla_id,
        v.party_key,
        v.votes,
        v.num_votos_nulos,
        v.num_votos_can_nreg
    FROM fact_casilla_vote v
    WHERE v.casilla_id >= '19'
      AND v.casilla_id <  '20'
),

fed_votes AS (
    SELECT
        c.election_id,
        COALESCE(l.label,
                 LOWER(REPLACE(CASE WHEN c.party_key LIKE 'C\_%' ESCAPE '\'
                                    THEN SUBSTR(c.party_key, 3) ELSE c.party_key END,
                               '_', '-'))) AS label,
        SUM(c.votes) AS votes
    FROM fed_casilla c
    LEFT JOIN fed_label l ON l.party_key = c.party_key
    GROUP BY 1, 2
),

-- nulos and write-ins repeat on every party row of a casilla: take one per casilla
fed_invalid AS (
    SELECT
        election_id,
        SUM(nulos) AS nulos,
        SUM(nr)    AS nr,
        COUNT(*)   AS n_units
    FROM (
        SELECT election_id, casilla_id,
               MAX(num_votos_nulos) AS nulos, MAX(num_votos_can_nreg) AS nr
        FROM fed_casilla
        GROUP BY election_id, casilla_id
    )
    GROUP BY election_id
),

federal AS (
    SELECT
        'federal'          AS source,
        e.election_type    AS office,
        v.election_id,
        e.year             AS yr,
        NULL               AS election_date,
        v.label,
        v.votes,
        i.nulos,
        i.nr,
        i.n_units
    FROM fed_votes v
    JOIN dim_election e ON e.election_id = v.election_id
    JOIN fed_invalid i  ON i.election_id = v.election_id
),

governor AS (
    SELECT
        'local'            AS source,
        'GOB'              AS office,
        r.race_id          AS election_id,
        r.yr,
        r.election_date,
        v.label,
        v.votes,
        r.nulos,
        r.nr,
        1                  AS n_units
    FROM fact_local_race r
    JOIN fact_local_race_vote v
      ON v.race_id = r.race_id
     AND v.version = 'as_reported'
    WHERE r.office = 'gobernador'
      AND r.id_estado = 19
),

ayu_race AS (
    SELECT race_id, yr, election_date, nulos, nr
    FROM fact_local_race
    WHERE office = 'ayuntamiento'
      AND id_estado = 19
      AND status = 'ok'
      AND COALESCE(dextra, 0) = 0
),

ayuntamiento AS (
    SELECT
        'local'                          AS source,
        'AYU'                            AS office,
        'AYU_' || a.yr                   AS election_id,
        a.yr,
        MIN(a.election_date)             AS election_date,
        v.label,
        SUM(v.votes)                     AS votes,
        t.nulos,
        t.nr,
        t.n_units
    FROM ayu_race a
    JOIN fact_local_race_vote v
      ON v.race_id = a.race_id
     AND v.version = 'coal_split'
    JOIN (SELECT yr, SUM(nulos) AS nulos, SUM(nr) AS nr, COUNT(*) AS n_units
          FROM ayu_race GROUP BY yr) t
      ON t.yr = a.yr
    GROUP BY a.yr, v.label, t.nulos, t.nr, t.n_units
),

all_results AS (
    SELECT * FROM federal
    UNION ALL SELECT * FROM governor
    UNION ALL SELECT * FROM ayuntamiento
)

SELECT
    19 AS id_estado,
    source,
    office,
    election_id,
    yr,
    election_date,
    label,
    votes,
    SUM(votes) OVER (PARTITION BY election_id) AS valid_votes,
    100.0 * votes / SUM(votes) OVER (PARTITION BY election_id) AS pct,
    nulos,
    nr,
    n_units                                    -- casillas (federal), municipios (AYU)
FROM all_results
WHERE votes > 0
ORDER BY yr, CASE office WHEN 'PRE' THEN 1 WHEN 'SEN' THEN 2 WHEN 'DIP' THEN 3
                         WHEN 'GOB' THEN 4 ELSE 5 END,
         election_id, votes DESC;
