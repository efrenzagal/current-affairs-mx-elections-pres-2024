-- CONAPO state panel: one row per state (or national, state_code '00') x year,
-- with every state-level CONAPO measure side by side.
--
--   fact_conapo_state_annual                 all 71 indicators, as loaded
--   fact_conapo_state_population_age_annual  single-year ages rolled up to
--                                            0-14 / 15-64 / 65+ by sex
--   fact_conapo_state_fertility_age_annual   births and age-specific fertility
--                                            rates pivoted to one column per
--                                            five-year band of the mother
--   fact_pibe_state_annual                   GDP per resident, 2018 pesos
--                                            (1980-2024 only)
--
-- Coverage differs by source: national rows start in 1950, states in 1970; the
-- single-age table has no national rows; PIBE ends in 2024. Rows from
-- 2020 onward are CONAPO projections (see estimate_phase), not observations.
--
-- Run from R with DBI::dbGetQuery(con, readr::read_file(<this file>)).

WITH age_groups AS (
    SELECT
        state_code,
        year,
        SUM(population) AS age_table_population_total,
        SUM(CASE WHEN age < 15 THEN population END) AS pop_0_14,
        SUM(CASE WHEN age BETWEEN 15 AND 64 THEN population END) AS pop_15_64,
        SUM(CASE WHEN age >= 65 THEN population END) AS pop_65_plus,
        SUM(CASE WHEN sex = 'Mujeres' AND age < 15 THEN population END) AS women_0_14,
        SUM(CASE WHEN sex = 'Mujeres' AND age BETWEEN 15 AND 64 THEN population END) AS women_15_64,
        SUM(CASE WHEN sex = 'Mujeres' AND age >= 65 THEN population END) AS women_65_plus,
        SUM(CASE WHEN sex = 'Hombres' AND age < 15 THEN population END) AS men_0_14,
        SUM(CASE WHEN sex = 'Hombres' AND age BETWEEN 15 AND 64 THEN population END) AS men_15_64,
        SUM(CASE WHEN sex = 'Hombres' AND age >= 65 THEN population END) AS men_65_plus
    FROM fact_conapo_state_population_age_annual
    GROUP BY state_code, year
),

fertility AS (
    SELECT
        state_code,
        year,
        MAX(CASE WHEN mother_age_band = '15-19' THEN births END) AS births_mother_15_19,
        MAX(CASE WHEN mother_age_band = '20-24' THEN births END) AS births_mother_20_24,
        MAX(CASE WHEN mother_age_band = '25-29' THEN births END) AS births_mother_25_29,
        MAX(CASE WHEN mother_age_band = '30-34' THEN births END) AS births_mother_30_34,
        MAX(CASE WHEN mother_age_band = '35-39' THEN births END) AS births_mother_35_39,
        MAX(CASE WHEN mother_age_band = '40-44' THEN births END) AS births_mother_40_44,
        MAX(CASE WHEN mother_age_band = '45-49' THEN births END) AS births_mother_45_49,
        MAX(CASE WHEN mother_age_band = '15-19' THEN fertility_rate_per_1000 END) AS asfr_15_19,
        MAX(CASE WHEN mother_age_band = '20-24' THEN fertility_rate_per_1000 END) AS asfr_20_24,
        MAX(CASE WHEN mother_age_band = '25-29' THEN fertility_rate_per_1000 END) AS asfr_25_29,
        MAX(CASE WHEN mother_age_band = '30-34' THEN fertility_rate_per_1000 END) AS asfr_30_34,
        MAX(CASE WHEN mother_age_band = '35-39' THEN fertility_rate_per_1000 END) AS asfr_35_39,
        MAX(CASE WHEN mother_age_band = '40-44' THEN fertility_rate_per_1000 END) AS asfr_40_44,
        MAX(CASE WHEN mother_age_band = '45-49' THEN fertility_rate_per_1000 END) AS asfr_45_49
    FROM fact_conapo_state_fertility_age_annual
    GROUP BY state_code, year
),

-- PIBE is keyed by geography name, CONAPO by state code; dim_conapo_state
-- bridges the two. Values are millions of 2018 pesos, so x 1,000,000 / mid-year
-- population gives 2018 pesos per resident. Same logic as the warehouse view
-- view_conapo_pibe_per_capita, written out against the base tables.
pibe_per_capita AS (
    SELECT
        s.state_code,
        p.year,
        p.activity_id,
        p.concept_name,
        p.value * 1000000.0 / c.population_total AS value_per_capita_2018_mxn
    FROM fact_pibe_state_annual p
    JOIN dim_conapo_state s
      ON s.pibe_geography = p.geography
    JOIN fact_conapo_state_annual c
      ON c.state_code = s.state_code
     AND c.year = p.year
    WHERE p.unit = 'Millones de pesos a precios de 2018'
      AND p.value IS NOT NULL
      AND c.population_total > 0
      AND p.activity_id IN ('TOTAL', 'PRIMARY', 'SECONDARY', 'TERTIARY')
),

pibe AS (
    SELECT
        state_code,
        year,
        MAX(CASE WHEN activity_id = 'TOTAL'
                  AND concept_name = 'Producto interno bruto, a precios de mercado'
                 THEN value_per_capita_2018_mxn END) AS gdp_per_capita_2018_mxn,
        MAX(CASE WHEN activity_id = 'TOTAL' AND concept_name = 'Valor agregado bruto'
                 THEN value_per_capita_2018_mxn END) AS gva_per_capita_2018_mxn,
        MAX(CASE WHEN activity_id = 'PRIMARY' THEN value_per_capita_2018_mxn END)
            AS gva_primary_per_capita_2018_mxn,
        MAX(CASE WHEN activity_id = 'SECONDARY' THEN value_per_capita_2018_mxn END)
            AS gva_secondary_per_capita_2018_mxn,
        MAX(CASE WHEN activity_id = 'TERTIARY' THEN value_per_capita_2018_mxn END)
            AS gva_tertiary_per_capita_2018_mxn
    FROM pibe_per_capita
    GROUP BY state_code, year
)

SELECT
    d.pibe_geography,
    c.*,
    a.age_table_population_total,
    a.pop_0_14, a.pop_15_64, a.pop_65_plus,
    a.women_0_14, a.women_15_64, a.women_65_plus,
    a.men_0_14, a.men_15_64, a.men_65_plus,
    f.births_mother_15_19, f.births_mother_20_24, f.births_mother_25_29,
    f.births_mother_30_34, f.births_mother_35_39, f.births_mother_40_44,
    f.births_mother_45_49,
    f.asfr_15_19, f.asfr_20_24, f.asfr_25_29, f.asfr_30_34,
    f.asfr_35_39, f.asfr_40_44, f.asfr_45_49,
    p.gdp_per_capita_2018_mxn,
    p.gva_per_capita_2018_mxn,
    p.gva_primary_per_capita_2018_mxn,
    p.gva_secondary_per_capita_2018_mxn,
    p.gva_tertiary_per_capita_2018_mxn
FROM fact_conapo_state_annual c
JOIN dim_conapo_state d USING (state_code)
LEFT JOIN age_groups a USING (state_code, year)
LEFT JOIN fertility f USING (state_code, year)
LEFT JOIN pibe p USING (state_code, year)
ORDER BY c.state_code, c.year;
