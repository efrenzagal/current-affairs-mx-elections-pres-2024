-- CONAPO municipal population, 2019: one row per municipality, used as the
-- regression weight. municipality_code is the INEGI 5-digit key (cvegeo).
-- Runs against election_data.db (SQLite).

SELECT
    municipality_code AS cvegeo,
    population_total  AS poblacion_2019
FROM fact_conapo_municipality_annual
WHERE year = 2019
ORDER BY municipality_code;
