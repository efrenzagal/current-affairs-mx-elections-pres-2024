-- Federal vote by state, 2018 and 2024: one row per state x election
-- (presidential, deputies MR, senators MR) with each bloc's share of valid
-- votes and turnout. Runs against election_data.db (SQLite).
--
--   fact_casilla_vote  votes per casilla x party_key. Valid votes are the sum
--                      of party rows (num_votos_validos is empty for 2018;
--                      in 2024 the two match exactly). total_votos repeats on
--                      every party row, so it is taken once per casilla
--   dim_casilla        id_estado, lista_nominal, and seccion via dim_geography
--
-- Blocs follow each year's coalitions and count split coalition boxes. The
-- same party keys are used in all three races of a year:
--                          2018                    2024
--   pct_morena             MORENA alone            MORENA alone
--   pct_coalicion_morena   MORENA + PT + PES       MORENA + PT + PVEM
--   pct_bloque_pan         PAN + PRD + MC          PAN + PRI + PRD
--   pct_tercer_bloque      PRI + PVEM + NA         MC
-- Independents are in votos_validos but in no bloc.
--
-- Voto en el extranjero (seccion 0) is left out because the EIC counts
-- resident households only. Casillas especiales (lista_nominal = 0) count
-- toward vote shares but not turnout.

WITH casilla_totals AS (
    SELECT
        v.election_id,
        v.casilla_id,
        SUM(CASE WHEN v.party_key = 'MORENA' THEN v.votes ELSE 0 END) AS votes_morena,
        SUM(CASE
            WHEN v.election_id LIKE '%2018' AND v.party_key IN
                 ('MORENA', 'PT', 'ENCUENTRO SOCIAL', 'PT_MORENA_PES', 'PT_MORENA', 'MORENA_PES', 'PT_PES')
              THEN v.votes
            WHEN v.election_id LIKE '%2024' AND v.party_key IN
                 ('MORENA', 'PT', 'PVEM', 'PVEM_PT_MORENA', 'PT_MORENA', 'PVEM_MORENA', 'PVEM_PT')
              THEN v.votes
            ELSE 0 END) AS votes_coalicion_morena,
        SUM(CASE
            WHEN v.election_id LIKE '%2018' AND v.party_key IN
                 ('PAN', 'PRD', 'MOVIMIENTO CIUDADANO', 'PAN_PRD_MC', 'PAN_PRD', 'PAN_MC', 'PRD_MC')
              THEN v.votes
            WHEN v.election_id LIKE '%2024' AND v.party_key IN
                 ('PAN', 'PRI', 'PRD', 'PAN_PRI_PRD', 'PAN_PRI', 'PAN_PRD', 'PRI_PRD')
              THEN v.votes
            ELSE 0 END) AS votes_bloque_pan,
        SUM(CASE
            WHEN v.election_id LIKE '%2018' AND v.party_key IN
                 ('PRI', 'PVEM', 'NUEVA ALIANZA', 'PRI_PVEM_NA', 'PRI_PVEM', 'PRI_NA', 'PVEM_NA')
              THEN v.votes
            WHEN v.election_id LIKE '%2024' AND v.party_key = 'MC'
              THEN v.votes
            ELSE 0 END) AS votes_tercer_bloque,
        SUM(v.votes)       AS votos_validos,
        MAX(v.total_votos) AS total_votos
    FROM fact_casilla_vote v
    WHERE v.election_id IN ('PRE_2018', 'DIP_MR_2018', 'SEN_MR_2018',
                            'PRE_2024', 'DIP_MR_2024', 'SEN_MR_2024')
    GROUP BY v.election_id, v.casilla_id
)

SELECT
    PRINTF('%02d', c.id_estado) AS state_code,
    g.nombre_estado             AS state_name,
    t.election_id,
    e.election_type,
    e.year,
    SUM(t.votos_validos)        AS votos_validos,
    SUM(CASE WHEN c.lista_nominal > 0 THEN c.lista_nominal ELSE 0 END) AS lista_nominal,

    100.0 * SUM(t.votes_morena)           / SUM(t.votos_validos) AS pct_morena,
    100.0 * SUM(t.votes_coalicion_morena) / SUM(t.votos_validos) AS pct_coalicion_morena,
    100.0 * SUM(t.votes_bloque_pan)       / SUM(t.votos_validos) AS pct_bloque_pan,
    100.0 * SUM(t.votes_tercer_bloque)    / SUM(t.votos_validos) AS pct_tercer_bloque,
    100.0 * SUM(CASE WHEN c.lista_nominal > 0 THEN t.total_votos ELSE 0 END)
          / SUM(CASE WHEN c.lista_nominal > 0 THEN c.lista_nominal ELSE 0 END) AS turnout

FROM casilla_totals t
JOIN dim_casilla c
  ON c.casilla_id = t.casilla_id
 AND c.election_id = t.election_id
JOIN dim_geography g
  ON g.geo_id = c.geo_id
 AND g.election_id = c.election_id
JOIN dim_election e
  ON e.election_id = t.election_id
WHERE g.seccion <> 0
GROUP BY c.id_estado, g.nombre_estado, t.election_id, e.election_type, e.year
ORDER BY e.year, e.election_type, c.id_estado;
