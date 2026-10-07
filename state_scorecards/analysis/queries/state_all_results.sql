-- Every election in one state, federal and local: one row per election x
-- ballot label with its votes and share of valid votes. Runs against
-- election_data.db (SQLite). Parameter :id_estado (1-32), e.g. in R:
--   dbGetQuery(con, sql, params = list(id_estado = 14))
--
--   PRE / SEN / DIP   federal, 1994-2024, INE casilla data summed to the
--                     state (fact_casilla_vote). Includes voto en el
--                     extranjero and casillas especiales, so totals run a
--                     little above the state-only official figures for DIP
--   GOB               governor, 1961-2024 (fact_local_race, version
--                     'as_reported'; CDMX = jefatura de gobierno)
--   AYU               ayuntamientos summed across the state's municipios per
--                     year (version 'coal_split'; status 'ok' races only,
--                     extraordinary races left out)
--
-- Labels are lowercase with '-' between the parties of a coalition ballot,
-- the same convention as fact_local_race_vote, so 'pan-pri-prd' means the same
-- thing in every office. Federal keys are renamed to match (A. CAM. ->
-- pan-pvem, NUEVA ALIANZA -> pna, CAND_IND_1 -> indep1, C_PRI_PVEM ->
-- pri-pvem, ...). Party-only ballots and joint coalition ballots stay
-- separate rows in federal and GOB; join labels to parties to build blocs.
--
-- valid_votes = sum of labelled votes (write-ins and nulos excluded), the
-- denominator for pct. 43 governor races between 1961 and 1994 are recorded
-- as percentages, not votes (valid_votes ~ 100; 15 are flagged dfake in
-- fact_local_race): pct is right for them, votes are not. MC appears as 'mc' in federal 2009 (then
-- Convergencia; elecRetrns calls it 'conve').

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
    WHERE v.casilla_id >= printf('%02d', :id_estado)
      AND v.casilla_id <  printf('%02d', :id_estado + 1)
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
      AND r.id_estado = :id_estado
),

ayu_race AS (
    SELECT race_id, yr, election_date, nulos, nr
    FROM fact_local_race
    WHERE office = 'ayuntamiento'
      AND id_estado = :id_estado
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
    :id_estado AS id_estado,
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
