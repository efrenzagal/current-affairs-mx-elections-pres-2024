-- Presidential vote by municipality, 2018 and 2024: one row per INEGI
-- municipality (cvegeo) with each bloc's share of valid votes and turnout.
-- Runs against election_data.db (SQLite).
--
--   fact_casilla_vote            votes per casilla x party_key; num_votos_validos
--                                and total_votos repeat on every party row, so
--                                they are taken once per casilla (casilla_totals)
--   dim_casilla -> dim_geography casilla -> seccion -> INE municipio id
--   dim_municipio_map_crosswalk  INE municipio id -> INEGI cvegeo; 'unresolved'
--                                rows fall back to suggested_cvegeo
--
-- Blocs follow each candidate's coalition, so split coalition boxes
-- (PVEM_PT_MORENA, PAN_PRI_PRD, ...) count toward the candidate:
--   2024  sheinbaum = MORENA + PT + PVEM      galvez = PAN + PRI + PRD     maynez = MC
--   2018  amlo      = MORENA + PT + PES       anaya  = PAN + PRD + MC      meade  = PRI + PVEM + NA
--
-- Voto en el extranjero and casillas especiales (lista_nominal = 0) are left
-- out of turnout; specials still count toward vote shares.

WITH casilla_geo AS (
    SELECT
        c.election_id,
        c.casilla_id,
        c.lista_nominal,
        COALESCE(x.inegi_cvegeo, x.suggested_cvegeo) AS cvegeo
    FROM dim_casilla c
    JOIN dim_geography g
      ON g.geo_id = c.geo_id
     AND g.election_id = c.election_id
    JOIN dim_municipio_map_crosswalk x
      ON x.election_id = g.election_id
     AND x.id_estado = g.id_estado
     AND x.source_municipio_id = g.id_municipio
    WHERE c.election_id IN ('PRE_2018', 'PRE_2024')
      AND x.match_method <> 'non_geographic'
),

casilla_bloc AS (
    SELECT
        v.election_id,
        v.casilla_id,
        SUM(CASE
            WHEN v.election_id = 'PRE_2024' AND v.party_key IN
                 ('MORENA', 'PT', 'PVEM', 'PVEM_PT_MORENA', 'PT_MORENA', 'PVEM_MORENA', 'PVEM_PT')
              THEN v.votes
            WHEN v.election_id = 'PRE_2018' AND v.party_key IN
                 ('MORENA', 'PT', 'ENCUENTRO SOCIAL', 'PT_MORENA_PES', 'PT_MORENA', 'MORENA_PES', 'PT_PES')
              THEN v.votes
            ELSE 0 END) AS votes_incumbent_bloc,
        SUM(CASE
            WHEN v.election_id = 'PRE_2024' AND v.party_key IN
                 ('PAN', 'PRI', 'PRD', 'PAN_PRI_PRD', 'PAN_PRI', 'PAN_PRD', 'PRI_PRD')
              THEN v.votes
            WHEN v.election_id = 'PRE_2018' AND v.party_key IN
                 ('PAN', 'PRD', 'MOVIMIENTO CIUDADANO', 'PAN_PRD_MC', 'PAN_PRD', 'PAN_MC', 'PRD_MC')
              THEN v.votes
            ELSE 0 END) AS votes_pan_bloc,
        SUM(CASE
            WHEN v.election_id = 'PRE_2024' AND v.party_key = 'MC'
              THEN v.votes
            WHEN v.election_id = 'PRE_2018' AND v.party_key IN
                 ('PRI', 'PVEM', 'NUEVA ALIANZA', 'PRI_PVEM_NA', 'PRI_PVEM', 'PRI_NA', 'PVEM_NA')
              THEN v.votes
            ELSE 0 END) AS votes_third_bloc,
        MAX(v.num_votos_validos) AS votos_validos,
        MAX(v.total_votos)       AS total_votos
    FROM fact_casilla_vote v
    WHERE v.election_id IN ('PRE_2018', 'PRE_2024')
    GROUP BY v.election_id, v.casilla_id
),

municipio AS (
    SELECT
        g.cvegeo,
        b.election_id,
        SUM(b.votes_incumbent_bloc) AS votes_incumbent_bloc,
        SUM(b.votes_pan_bloc)       AS votes_pan_bloc,
        SUM(b.votes_third_bloc)     AS votes_third_bloc,
        SUM(b.votos_validos)        AS votos_validos,
        SUM(b.total_votos)          AS total_votos,
        SUM(CASE WHEN g.lista_nominal > 0 THEN b.total_votos   ELSE 0 END) AS total_votos_con_lista,
        SUM(CASE WHEN g.lista_nominal > 0 THEN g.lista_nominal ELSE 0 END) AS lista_nominal
    FROM casilla_bloc b
    JOIN casilla_geo g
      ON g.casilla_id = b.casilla_id
     AND g.election_id = b.election_id
    GROUP BY g.cvegeo, b.election_id
)

SELECT
    m24.cvegeo,
    SUBSTR(m24.cvegeo, 1, 2) AS cve_ent,

    -- 2024 (dependent variables)
    m24.lista_nominal                                                AS lista_nominal_2024,
    m24.votos_validos                                                AS votos_validos_2024,
    100.0 * m24.votes_incumbent_bloc / NULLIF(m24.votos_validos, 0)  AS pct_sheinbaum_2024,
    100.0 * m24.votes_pan_bloc       / NULLIF(m24.votos_validos, 0)  AS pct_galvez_2024,
    100.0 * m24.votes_third_bloc     / NULLIF(m24.votos_validos, 0)  AS pct_maynez_2024,
    100.0 * m24.total_votos_con_lista / NULLIF(m24.lista_nominal, 0) AS turnout_2024,

    -- 2018 (lagged controls)
    100.0 * m18.votes_incumbent_bloc / NULLIF(m18.votos_validos, 0)  AS pct_amlo_2018,
    100.0 * m18.votes_pan_bloc       / NULLIF(m18.votos_validos, 0)  AS pct_anaya_2018,
    100.0 * m18.votes_third_bloc     / NULLIF(m18.votos_validos, 0)  AS pct_meade_2018,
    100.0 * m18.total_votos_con_lista / NULLIF(m18.lista_nominal, 0) AS turnout_2018

FROM municipio m24
LEFT JOIN municipio m18
  ON m18.cvegeo = m24.cvegeo
 AND m18.election_id = 'PRE_2018'
WHERE m24.election_id = 'PRE_2024'
ORDER BY m24.cvegeo;
