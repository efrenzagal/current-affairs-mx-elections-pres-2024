-- Every EIC 2025 municipal indicator that is a rate or an average, long
-- format: one row per municipality x indicator. Feeds the second-regressor
-- screen. Runs against state_scorecards/data/eic2025.duckdb.
--
-- Kept: percentages (PCN_*, plus PNC_P6A14AN, a typo in INEGI's own code)
-- and averages/ratios (schooling, median age, dependency ratios, fertility,
-- occupants per dwelling). Left out: raw counts (POBTOT, TOTHOG, ...), which
-- mostly measure municipality size, and the _F / _M sex breakdowns of each
-- indicator. 218 indicators.
--
-- Some indicators are complements of each other (PCN_HOG_ALIM vs
-- PCN_HOG_ALIM_N add to 100), so they show up as mirror images in the screen.

SELECT
    r.CVE_ENT || r.CVE_MUN AS cvegeo,
    r.indicator,
    p.section,
    p.name,
    r.value
FROM resultados_publicados r
JOIN indicadores_publicados p
  ON p.indicator = r.indicator
WHERE r.geo_level = 'municipal'
  AND r.value IS NOT NULL
  AND r.indicator NOT LIKE '%\_F' ESCAPE '\'
  AND r.indicator NOT LIKE '%\_M' ESCAPE '\'
  AND (
        r.indicator LIKE 'PCN\_%' ESCAPE '\'
     OR r.indicator LIKE 'PNC\_%' ESCAPE '\'
     OR r.indicator IN ('GRAPROES', 'MEDIANA_POBTOT', 'INDICE_ENV',
                        'RAZON_DEP_TOT', 'RAZON_DEP_INF', 'RAZON_DEP_VEJ',
                        'PROM_HNV', 'TGF', 'PROM_OCUP', 'PRO_OCUP_C')
  )
ORDER BY cvegeo, p.position;
