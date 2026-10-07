-- EIC 2025 municipal profile for the vote-share regressions: one row per
-- municipality (cvegeo = CVE_ENT || CVE_MUN, the INEGI 5-digit key) with the
-- share of households receiving government programs and candidate
-- confounders. Runs against state_scorecards/data/eic2025.duckdb.
--
--   resultados_publicados  INEGI's published municipal indicators
--   estimaciones           weighted estimates built from the microdata; used
--                          for household labor income, which INEGI does not
--                          publish (sentinels 999998/999999 excluded, real
--                          zeros kept)
--
-- municipio_flag marks municipalities INEGI fully enumerated ('censado') or
-- where the sample was too small for some indicators ('muestra_insuficiente');
-- those can have nulls. All shares are percentages (0-100).

WITH published AS (
    SELECT
        CVE_ENT || CVE_MUN AS cvegeo,
        CVE_ENT            AS state_code,
        MAX(NOM_ENT)        AS state_name,
        MAX(NOM_MUN)        AS municipio,
        MAX(municipio_flag) AS municipio_flag,
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
        MAX(value) FILTER (WHERE indicator = 'POBTOT')         AS poblacion         -- regression weight
    FROM resultados_publicados
    WHERE geo_level = 'municipal'
    GROUP BY CVE_ENT, CVE_MUN
),

income AS (
    SELECT
        CVE_ENT || CVE_MUN AS cvegeo,
        value AS ingreso_trabajo_hogar,     -- pesos al mes, promedio por hogar
        se    AS se_ingreso_trabajo_hogar
    FROM estimaciones
    WHERE table_name = 'viviendas'
      AND variable = 'INGTRHOG'
      AND statistic = 'promedio'
      AND geo_level = 'municipal'
)

SELECT p.*, i.ingreso_trabajo_hogar, i.se_ingreso_trabajo_hogar
FROM published p
LEFT JOIN income i USING (cvegeo)
ORDER BY p.cvegeo;
