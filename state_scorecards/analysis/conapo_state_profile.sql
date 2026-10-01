-- CONAPO state profile: every CONAPO measure for one state, one row per year
-- (1970-2070). CONAPO only, no PIBE; see conapo_state_panel.sql for that.
--
--   fact_conapo_state_annual                        all 71 indicators, as loaded
--   fact_conapo_state_population_age_annual         single-year ages rolled up to
--                                                   0-14 / 15-64 / 65+ and 18+
--                                                   (voting age), by sex
--   fact_conapo_state_fertility_age_annual          births and age-specific
--                                                   fertility rates per 1,000
--                                                   women, one column per band
--   fact_conapo_remittances_municipality_quarterly  municipios summed to the
--                                                   state, by quarter (2013-2024)
--
-- Change the state code in `params` ('00' is the national total, which has
-- fertility rows but no single-age rows; remittances are summed over all
-- states). Rows from 2020 onward are projections (see estimate_phase).
-- In the query console, clear the Rows box: a state has 101 years.

WITH params AS (
    SELECT '16' AS state_code
),

age_groups AS (
    SELECT
        a.year,
        SUM(CASE WHEN a.age < 15 THEN a.population END) AS pop_0_14,
        SUM(CASE WHEN a.age BETWEEN 15 AND 64 THEN a.population END) AS pop_15_64,
        SUM(CASE WHEN a.age >= 65 THEN a.population END) AS pop_65_plus,
        SUM(CASE WHEN a.age >= 18 THEN a.population END) AS pop_18_plus,
        SUM(CASE WHEN a.sex = 'Mujeres' AND a.age < 15 THEN a.population END) AS women_0_14,
        SUM(CASE WHEN a.sex = 'Mujeres' AND a.age BETWEEN 15 AND 64 THEN a.population END) AS women_15_64,
        SUM(CASE WHEN a.sex = 'Mujeres' AND a.age >= 65 THEN a.population END) AS women_65_plus,
        SUM(CASE WHEN a.sex = 'Mujeres' AND a.age >= 18 THEN a.population END) AS women_18_plus,
        SUM(CASE WHEN a.sex = 'Hombres' AND a.age < 15 THEN a.population END) AS men_0_14,
        SUM(CASE WHEN a.sex = 'Hombres' AND a.age BETWEEN 15 AND 64 THEN a.population END) AS men_15_64,
        SUM(CASE WHEN a.sex = 'Hombres' AND a.age >= 65 THEN a.population END) AS men_65_plus,
        SUM(CASE WHEN a.sex = 'Hombres' AND a.age >= 18 THEN a.population END) AS men_18_plus
    FROM fact_conapo_state_population_age_annual a
    JOIN params p ON p.state_code = a.state_code
    GROUP BY a.year
),

fertility AS (
    SELECT
        f.year,
        MAX(CASE WHEN f.mother_age_band = '15-19' THEN f.births END) AS births_mother_15_19,
        MAX(CASE WHEN f.mother_age_band = '20-24' THEN f.births END) AS births_mother_20_24,
        MAX(CASE WHEN f.mother_age_band = '25-29' THEN f.births END) AS births_mother_25_29,
        MAX(CASE WHEN f.mother_age_band = '30-34' THEN f.births END) AS births_mother_30_34,
        MAX(CASE WHEN f.mother_age_band = '35-39' THEN f.births END) AS births_mother_35_39,
        MAX(CASE WHEN f.mother_age_band = '40-44' THEN f.births END) AS births_mother_40_44,
        MAX(CASE WHEN f.mother_age_band = '45-49' THEN f.births END) AS births_mother_45_49,
        MAX(CASE WHEN f.mother_age_band = '15-19' THEN f.fertility_rate_per_1000 END) AS asfr_15_19,
        MAX(CASE WHEN f.mother_age_band = '20-24' THEN f.fertility_rate_per_1000 END) AS asfr_20_24,
        MAX(CASE WHEN f.mother_age_band = '25-29' THEN f.fertility_rate_per_1000 END) AS asfr_25_29,
        MAX(CASE WHEN f.mother_age_band = '30-34' THEN f.fertility_rate_per_1000 END) AS asfr_30_34,
        MAX(CASE WHEN f.mother_age_band = '35-39' THEN f.fertility_rate_per_1000 END) AS asfr_35_39,
        MAX(CASE WHEN f.mother_age_band = '40-44' THEN f.fertility_rate_per_1000 END) AS asfr_40_44,
        MAX(CASE WHEN f.mother_age_band = '45-49' THEN f.fertility_rate_per_1000 END) AS asfr_45_49
    FROM fact_conapo_state_fertility_age_annual f
    JOIN params p ON p.state_code = f.state_code
    GROUP BY f.year
),

-- Millions of current USD. Includes the state's "No identificado" (xx999)
-- municipio, so the quarters add up to the state total.
remittances AS (
    SELECT
        r.year,
        MAX(r.migration_region) AS migration_region,
        SUM(r.remittances_usd_millions) AS remittances_usd_millions,
        SUM(CASE WHEN r.quarter = 1 THEN r.remittances_usd_millions END) AS remittances_q1_usd_millions,
        SUM(CASE WHEN r.quarter = 2 THEN r.remittances_usd_millions END) AS remittances_q2_usd_millions,
        SUM(CASE WHEN r.quarter = 3 THEN r.remittances_usd_millions END) AS remittances_q3_usd_millions,
        SUM(CASE WHEN r.quarter = 4 THEN r.remittances_usd_millions END) AS remittances_q4_usd_millions
    FROM fact_conapo_remittances_municipality_quarterly r
    JOIN params p ON p.state_code IN (r.state_code, '00')
    GROUP BY r.year
)

SELECT
    c.*,
    a.pop_0_14, a.pop_15_64, a.pop_65_plus, a.pop_18_plus,
    a.women_0_14, a.women_15_64, a.women_65_plus, a.women_18_plus,
    a.men_0_14, a.men_15_64, a.men_65_plus, a.men_18_plus,
    f.births_mother_15_19, f.births_mother_20_24, f.births_mother_25_29,
    f.births_mother_30_34, f.births_mother_35_39, f.births_mother_40_44,
    f.births_mother_45_49,
    f.asfr_15_19, f.asfr_20_24, f.asfr_25_29, f.asfr_30_34,
    f.asfr_35_39, f.asfr_40_44, f.asfr_45_49,
    CASE WHEN c.state_code <> '00' THEN r.migration_region END AS migration_region,
    r.remittances_usd_millions,
    r.remittances_q1_usd_millions, r.remittances_q2_usd_millions,
    r.remittances_q3_usd_millions, r.remittances_q4_usd_millions,
    r.remittances_usd_millions * 1000000.0 / c.population_total
        AS remittances_usd_per_capita
FROM fact_conapo_state_annual c
JOIN params p ON p.state_code = c.state_code
LEFT JOIN age_groups a ON a.year = c.year
LEFT JOIN fertility f ON f.year = c.year
LEFT JOIN remittances r ON r.year = c.year
ORDER BY c.year;
