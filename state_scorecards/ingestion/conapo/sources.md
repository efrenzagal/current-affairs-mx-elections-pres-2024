# CONAPO population source inventory

Source collection: [Proyecciones de población](https://www.datos.gob.mx/dataset/proyecciones-de-poblacion). Raw files are kept locally in `state_scorecards/data/raw_conapo/` and excluded from Git by the repository's `.gitignore`. They are grouped by CONAPO release:

| Folder | Contents |
| --- | --- |
| `proyecciones_estatales/` | National and state reconstruction and projections, 1950–2070 (files numbered `00_`–`06_`, plus the fertility workbook) |
| `proyecciones_municipales/` | Municipal reconstruction and projections, 1990–2040 (`pobproy_*.csv` and the indicators workbook) |
| `remesas/` | Quarterly remittances by municipio, 2013–2024 |

## Added 2026-09-22

| Local file | Purpose | Download and validation |
| --- | --- | --- |
| `proyecciones_estatales/00_Pob_Mitad_1950_2070.csv` | Mid-year population by state, year, single year of age, and sex. Primary population denominator for annual PIBE per capita; can also produce exact age groups. | [Public mirror](https://github.com/lapanquecita/poblacion-estimada/blob/main/data/estatal.csv), which [identifies the CONAPO source](https://www.datos.gob.mx/dataset/proyecciones-de-poblacion/resource/de522924-f4d8-4523-a6fd-6b2efe73f3af). The direct government hosts did not permit this automated download. All 3,232 state-year totals for 1970–2070 match `POB_MIT_ANIO` in the existing `05_indicadores_demograficos_proyecciones.csv` exactly. This mirrored file contains the 32 states but no national rows. SHA-256: `18c2cf4db80ab77c7f56b5ede9126c0cd8525d1056f0a15fc82e02eb0a0cfb5d`. |
| `proyecciones_estatales/4_Tasas_Especificas_Fecundidad_proyecciones.xlsx` | Annual births and age-specific fertility rates by state and five-year age group of the mother, 1950–2070 nationally and 1970–2070 by state. | [Archived CONAPO workbook](https://github.com/edf-org/mexico-oilgas-hia-data/blob/master/04_PopulationData_raw/ProyeccionesPoblacion_CONAPO_2020-2070_mun/ConDem50a19_ProyPob20a70/4_Tasas_Especificas_Fecundidad_proyecciones.xlsx); [current government resource](https://www.datos.gob.mx/dataset/proyecciones-de-poblacion/resource/932535ed-a7a2-4171-a3f2-90c2343a9a4a). Births summed across age groups match `NAC` in the existing state indicators file exactly for all 3,232 state-years. SHA-256: `19692a499034a6ea27d897636831ed7c3929b63663c07860abf9a678209a03f5`. |
| `proyecciones_municipales/3_Indicadores_Dem_00_RM.xlsx` | Municipal annual indicators, including population, median age, dependency ratios, aging indices, and municipal share of state population, 1990–2040. | [Archived CONAPO workbook](https://github.com/edf-org/mexico-oilgas-hia-data/blob/master/04_PopulationData_raw/ProyeccionesPoblacion_CONAPO_1990-2040_mun/00_Republica_mexicana/3_Indicadores_Dem_00_RM.xlsx); [current government resource](https://www.datos.gob.mx/dataset/proyecciones-de-poblacion/resource/99b28bb6-8e31-48e1-b162-85a7e4deafc3). Municipal population matches the existing `pobproy_quinq1.csv` exactly for all 126,225 municipio-years. SHA-256: `75a53c1baba9081f154be84eb4b113b1352d6fff4ad3fd2900ea8d6d5a25795d`. |

The two workbooks came from an archive of CONAPO's original Excel releases, whereas the current government portal lists CSV resources. Their key totals reconcile with the current CSVs already downloaded locally. Keep the source and release information attached when transforming them; future releases can revise historic estimates.

The existing `05_indicadores_demograficos_proyecciones.csv` already supplies annual state totals for births, deaths, migration, life expectancy, fertility, and population, so those aggregate products do not need additional copies.

## Added 2026-10-01

| Local file | Purpose | Download and validation |
| --- | --- | --- |
| `remesas/remesas_2013-2024.csv` | Family remittances received by municipio and quarter, 2013–2024, in millions of USD, with CONAPO's migration region and municipal migration-intensity grade. | [Remesas](https://www.datos.gob.mx/dataset/remesas) ([direct file](https://repodatos.atdt.gob.mx/api_update/conapo/remesas/remesas_2013-2024.csv)); the host refuses automated downloads, so it was saved from a browser. 119,424 rows: 2,488 municipio codes × 48 quarters, including one `xx999` "No identificado" code per state. National annual totals run from 23,090 million USD (2013) to 64,746 million USD (2024). SHA-256: `8bba7fb813655430620188d66179413f4c0e2d1dfd4b48b6a88a170db4789c01`. |

The file truncates its text fields: state names at 14 characters (Baja California and Baja California Sur both appear as `Baja Californi`), the state migration-intensity grade at 4 (`Muy` cannot distinguish *muy alto* from *muy bajo*), and municipio names at 25. The converter therefore keys on codes, takes municipio names from the CONAPO municipal table, and drops the state grade. The 19 municipios that CONAPO's projections include but this file does not list separately (for example San Quintín, Bacalar, and Puerto Morelos) are reported under their former parent municipios.

## Local ingestion

Run from the repository root:

```bash
python3 state_scorecards/ingestion/conapo/raw_to_parquet.py
python3 state_scorecards/ingestion/conapo/ingest.py
python3 state_scorecards/ingestion/conapo/remesas_raw_to_parquet.py
python3 state_scorecards/ingestion/conapo/remesas_ingest.py
```

The remittance scripts run after the CONAPO ones: the converter reads municipio names from the clean CONAPO municipal file, and the loader checks codes against the CONAPO warehouse tables.

The converters write seven validated Parquet files under `state_scorecards/data/clean/`:

| SQLite table | Grain | Coverage |
| --- | --- | --- |
| `fact_conapo_state_annual` | State or national × year; mid-year totals and demographic indicators | National 1950–2070; states 1970–2070 |
| `fact_conapo_state_population_age_annual` | State × year × sex × exact age | 1970–2070 |
| `fact_conapo_municipality_annual` | Municipio × year; mid-year totals and demographic indicators | 1990–2040 |
| `fact_conapo_municipality_population_age_annual` | Municipio × year × sex × five-year age band | 1990–2040 |
| `fact_conapo_municipality_life_stage_annual` | Municipio × year × sex; exact populations aged 0–11, 12–29, 30–59, and 60+ | 1990–2040 |
| `fact_conapo_state_fertility_age_annual` | State or national × year × five-year age band of mother | National 1950–2070; states 1970–2070 |
| `fact_conapo_remittances_municipality_quarterly` | Municipio × year × quarter; remittances in millions of current USD | 2013–2024 |

The loader also creates `dim_conapo_state`, matching CONAPO state codes to PIBE geography names, `view_conapo_pibe_per_capita`, and `view_conapo_population_pyramid` (state or national × year × sex × five-year band to 85+, reconstruction years 1970–2019 only, with each band's share of the geography's population; national rows are the sum of the states). The website export `web/scripts/export_conapo_states.py` reads that view. The view divides PIBE millions of constant 2018 pesos by mid-year population to give 2018 pesos per resident. The loader refuses to overwrite its existing tables unless given `--force`; replacement is published in one SQLite transaction after staging and checks. PIBE and electoral source tables are not modified.

The remaining raw CONAPO files (beginning-of-year population, detailed deaths, migration, and sex-specific life expectancy) are retained for later thematic ingestion. Their summary measures are already available in the annual state or municipal tables where applicable.

For electoral comparisons, join state-level results on `id_estado = CAST(state_code AS INTEGER)` and the election year. The existing materialized election state views have `lista_nominal_part` and `total_votos` for every state in the 2006–2024 contests; those values are repeated across party rows and must be reduced to one election–state row before computing participation. The national `dim_election.pct_participacion_ciudadana` field is populated for the 2025 judicial contests but not earlier contests. The 1994 and 2000 state materializations do not have a populated lista nominal.
