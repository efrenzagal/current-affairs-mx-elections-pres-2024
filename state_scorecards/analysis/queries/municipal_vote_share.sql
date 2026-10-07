-- Federal vote by municipality, 2024: one row per INEGI municipality (cvegeo)
-- x election (presidential, deputies MR, senators MR) with each bloc's share
-- of valid votes and turnout. Runs against election_data.db (SQLite).
--
--   fact_casilla_vote            votes per casilla x party_key; valid votes
--                                are the sum of party rows
--   dim_casilla -> dim_geography casilla -> seccion -> INE municipio number
--   dim_municipio_map_crosswalk  INE municipio number -> INEGI cvegeo, matched
--                                by name in electoral/materialize.py
--   cvegeo_overrides             hand fixes for 'unresolved' crosswalk rows
--                                whose suggested_cvegeo is wrong (checked
--                                against EIC names and sizes). The other 19
--                                unresolved suggestions are name variants
--                                ("GRAL. ESCOBEDO" -> General Escobedo) and
--                                are used as suggested
--
-- Blocs, 2024 (same party keys in all three races):
--   pct_coalicion_morena  MORENA + PT + PVEM
--   pct_bloque_pan        PAN + PRI + PRD
--   pct_mc                MC
--
-- Voto en el extranjero (non_geographic, seccion 0) is left out. Casillas
-- especiales (lista_nominal = 0) count toward vote shares but not turnout.

WITH cvegeo_overrides (id_estado, source_municipio_id, cvegeo) AS (
    VALUES
        ( 7,  17, '07017'),   -- CINTALAPA: suggested 07090 Tapalapa
        (17,  13, '17013'),   -- JONACATEPEC: suggested 17031 Zacatepec
        (20, 209, '20209'),   -- second SAN JUAN MIXTEPEC: suggested 20208 (the larger one)
        (20, 317, '20319'),   -- second SAN PEDRO MIXTEPEC: suggested 20318 (the larger one)
        (30, 122, '30121'),   -- OZULUAMA: suggested 30116 Oluta
        (30, 202, '30202')    -- ZONTECOMATLAN: suggested 30186 Tomatlán
),

municipio_map AS (
    SELECT
        x.election_id,
        x.id_estado,
        x.source_municipio_id,
        COALESCE(o.cvegeo, x.inegi_cvegeo, x.suggested_cvegeo) AS cvegeo,
        CASE WHEN o.cvegeo IS NOT NULL THEN 'override' ELSE x.match_method END AS match_method
    FROM dim_municipio_map_crosswalk x
    LEFT JOIN cvegeo_overrides o
      ON o.id_estado = x.id_estado
     AND o.source_municipio_id = x.source_municipio_id
    WHERE x.election_id IN ('PRE_2024', 'DIP_MR_2024', 'SEN_MR_2024')
      AND x.match_method <> 'non_geographic'
),

casilla_totals AS (
    SELECT
        v.election_id,
        v.casilla_id,
        SUM(CASE WHEN v.party_key IN
                 ('MORENA', 'PT', 'PVEM', 'PVEM_PT_MORENA', 'PT_MORENA', 'PVEM_MORENA', 'PVEM_PT')
            THEN v.votes ELSE 0 END) AS votes_coalicion_morena,
        SUM(CASE WHEN v.party_key IN
                 ('PAN', 'PRI', 'PRD', 'PAN_PRI_PRD', 'PAN_PRI', 'PAN_PRD', 'PRI_PRD')
            THEN v.votes ELSE 0 END) AS votes_bloque_pan,
        SUM(CASE WHEN v.party_key = 'MC' THEN v.votes ELSE 0 END) AS votes_mc,
        SUM(v.votes)       AS votos_validos,
        MAX(v.total_votos) AS total_votos
    FROM fact_casilla_vote v
    WHERE v.election_id IN ('PRE_2024', 'DIP_MR_2024', 'SEN_MR_2024')
    GROUP BY v.election_id, v.casilla_id
)

SELECT
    m.cvegeo,
    t.election_id,
    e.election_type,
    GROUP_CONCAT(DISTINCT m.match_method) AS match_method,
    SUM(t.votos_validos) AS votos_validos,
    SUM(CASE WHEN c.lista_nominal > 0 THEN c.lista_nominal ELSE 0 END) AS lista_nominal,

    100.0 * SUM(t.votes_coalicion_morena) / SUM(t.votos_validos) AS pct_coalicion_morena,
    100.0 * SUM(t.votes_bloque_pan)       / SUM(t.votos_validos) AS pct_bloque_pan,
    100.0 * SUM(t.votes_mc)               / SUM(t.votos_validos) AS pct_mc,
    100.0 * SUM(CASE WHEN c.lista_nominal > 0 THEN t.total_votos ELSE 0 END)
          / NULLIF(SUM(CASE WHEN c.lista_nominal > 0 THEN c.lista_nominal ELSE 0 END), 0) AS turnout

FROM casilla_totals t
JOIN dim_casilla c
  ON c.casilla_id = t.casilla_id
 AND c.election_id = t.election_id
JOIN dim_geography g
  ON g.geo_id = c.geo_id
 AND g.election_id = c.election_id
JOIN municipio_map m
  ON m.election_id = g.election_id
 AND m.id_estado = g.id_estado
 AND m.source_municipio_id = g.id_municipio
JOIN dim_election e
  ON e.election_id = t.election_id
WHERE g.seccion <> 0
GROUP BY m.cvegeo, t.election_id, e.election_type
ORDER BY m.cvegeo, e.election_type;
