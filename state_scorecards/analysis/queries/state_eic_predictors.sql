-- EIC 2025 state profile for the vote-share regressions: one row per state
-- with the share of households receiving government programs and candidate
-- confounders. Runs against state_scorecards/data/eic2025.duckdb.
--
--   resultados_publicados  INEGI's published state indicators (geo_level 'estatal')
--   estimaciones           weighted estimates built from the microdata; used
--                          here for household labor income, which INEGI does
--                          not publish (sentinels 999998/999999 excluded,
--                          real zeros kept)
--
-- All shares are percentages (0-100).

WITH published AS (
    SELECT
        CVE_ENT AS state_code,
        MAX(NOM_ENT) AS state_name,
        MAX(value) FILTER (WHERE indicator = 'PCN_HOG_GOB')    AS pct_hog_gob,      -- hogares con ingresos de programas de gobierno
        MAX(se)    FILTER (WHERE indicator = 'PCN_HOG_GOB')    AS se_hog_gob,
        MAX(value) FILTER (WHERE indicator = 'PCN_HOG_JUB')    AS pct_hog_jub,      -- jubilación o pensión
        MAX(value) FILTER (WHERE indicator = 'PCN_HOG_OPAIS')  AS pct_hog_remesas,  -- ingresos de alguien en otro país
        MAX(value) FILTER (WHERE indicator = 'PCN_HOG_ALIM_N') AS pct_hog_sin_alim, -- sin acceso a alimentos por falta de dinero
        MAX(value) FILTER (WHERE indicator = 'GRAPROES')       AS escolaridad,      -- grado promedio, 15 años y más
        MAX(value) FILTER (WHERE indicator = 'PCN_POB_IND')    AS pct_indigena,     -- se considera indígena
        MAX(value) FILTER (WHERE indicator = 'PCN_PSINDER')    AS pct_sin_salud,    -- no afiliada a servicios de salud
        MAX(value) FILTER (WHERE indicator = 'PCN_VPH_AUTOM')  AS pct_viv_auto,
        MAX(value) FILTER (WHERE indicator = 'PCN_VPH_INTER')  AS pct_viv_internet,
        MAX(value) FILTER (WHERE indicator = 'MEDIANA_POBTOT') AS edad_mediana,
        MAX(value) FILTER (WHERE indicator = 'POBTOT')         AS poblacion
    FROM resultados_publicados
    WHERE geo_level = 'estatal'
    GROUP BY CVE_ENT
),

income AS (
    SELECT
        CVE_ENT AS state_code,
        value   AS ingreso_trabajo_hogar,     -- pesos al mes, promedio por hogar
        se      AS se_ingreso_trabajo_hogar
    FROM estimaciones
    WHERE table_name = 'viviendas'
      AND variable = 'INGTRHOG'
      AND statistic = 'promedio'
      AND geo_level = 'estatal'
)

SELECT p.*, i.ingreso_trabajo_hogar, i.se_ingreso_trabajo_hogar
FROM published p
JOIN income i USING (state_code)
ORDER BY p.state_code;
