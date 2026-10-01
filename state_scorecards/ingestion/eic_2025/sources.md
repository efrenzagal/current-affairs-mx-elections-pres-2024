# INEGI Encuesta Intercensal 2025 source inventory

Source: [Encuesta Intercensal (EIC) 2025](https://www.inegi.org.mx/programas/eic/2025/), fieldwork 6 October – 14 November 2025, results published 2026-09-22. Raw files are kept locally under `state_scorecards/data/eic_2025/` and excluded from Git.

| Local path | What it is |
| --- | --- |
| `microdatos/eic2025_micro_NN/` | Microdata for state `NN`, as published: `viviendasNN.csv` (dwellings), `personasNN.csv` (residents), `migrantesNN.csv` (people who left for another country since 2020). The three join on `ID_VIV`; `migrantes.MPERLS` points back to a person. |
| `resultados_publicados/` | INEGI's published "Principales resultados por localidad de 50 000 y más habitantes" ([zip](https://www.inegi.org.mx/contenidos/programas/eic/2025/datosabiertos/conjunto_de_datos_eic2025_105_csv.zip)): 340 indicators for the nation, states, municipios and cities of 50,000+, each with value, standard error, 90% confidence limits and coefficient of variation. Latin-1 CSV, with its own data dictionary and metadata. It is the benchmark the microdata was validated against, and the source of official margins of error. |
| `microdatos/manifest.csv` | One row per downloaded state: URL, ETag, Last-Modified, zip SHA-256 and rows per table. |
| `documentacion/eic2025_micro_fd.xlsx` | INEGI's file descriptor: one sheet per table with every variable's question, type, valid range, length and category codes. It is the schema the converter enforces. |
| `documentacion/catalogos/` | Long classifiers referenced by the descriptor (INEGI product 889463931966): economic activity (SCIAN), occupation, country and state, municipio, indigenous language, kinship, migration cause, mental condition, schooling. File names are kept as published, including `ESOLARIDAD_ACUMULADA.csv`. `MUNICIPIO` and `ENTIDAD_PAIS` use 3-digit state codes (`001`), whereas the microdata's `CVE_ENT` uses 2 (`01`). |

Zips follow `https://www.inegi.org.mx/contenidos/programas/eic/2025/microdatos/eic2025_micro_{NN}_csv.zip`. A missing file comes back as an HTML error page with HTTP 200, so the downloader checks the content type.

## Local ingestion

Run from the repository root:

```bash
python3 state_scorecards/ingestion/eic_2025/download.py        # all 32 states; skips those in the manifest
python3 state_scorecards/ingestion/eic_2025/raw_to_parquet.py  # reconverts only states whose zip changed
python3 state_scorecards/ingestion/eic_2025/estimates.py      # every estimate, all levels, with INEGI precision (~3 min)
python3 state_scorecards/ingestion/eic_2025/published_to_parquet.py  # INEGI's published results, long (seconds)
python3 state_scorecards/ingestion/eic_2025/build_duckdb.py    # rebuilds state_scorecards/data/eic2025.duckdb (seconds)
python3 -m query_console --db state_scorecards/data/eic2025.duckdb
```

`eic2025.duckdb` is about 1 MB. Its `personas`, `viviendas` and `migrantes` are views over the Parquet below, so nothing is copied. The codebook tables are copied in, along with `entidades` and `municipios` helpers. Every column's comment holds its description and full question. The views use absolute paths: rebuild after moving the repository. From R, use `DBI::dbConnect(duckdb::duckdb(), "state_scorecards/data/eic2025.duckdb", read_only = TRUE)` after `install.packages("duckdb")`.

The converter writes `state_scorecards/data/clean/eic_2025/`:

| Output | Grain |
| --- | --- |
| `viviendas/viviendas_NN.parquet` | Dwelling |
| `personas/personas_NN.parquet` | Person |
| `migrantes/migrantes_NN.parquet` | International migrant reported by a dwelling |
| `codebook_variables.parquet` | Table × variable: section, description, question, `question_stem` (the shared lead-in of a battery like "¿(NOMBRE) tiene por su trabajo:") and `question_full`, type, valid range, length, and `classifier` for the 24 variables coded against one |
| `codebook_categories.parquet` | Table × variable × code, with its label |
| `codebook_classifiers.parquet` | Classifier × code, with its label, from `documentacion/catalogos/` |

To label a classified column, join `codebook_variables.classifier` → `codebook_classifiers.classifier`, then match the column's value to `code`. Municipios are keyed by the 5-digit `CVEGEO` (`01001`) the microdata uses, with `parent_code` holding the state. `CVEGEO` joins directly. `MUN_ASI`, `MUN_RES_5A` and `MUN_TRAB` carry only the 3-digit municipio: prefix the last two digits of their `ENT_PAIS_*` column, which applies only for Mexican states 001–032. INEGI's "no especificado" municipio stays `997999`. The converter warns when a state contains a code missing from its classifier, which would mean INEGI published a newer catalog.

Values are unchanged from the CSVs. Columns marked "Numérico" are int64 and the rest are strings, so codes keep leading zeros. Empty cells are null ("Blanco por pase": skipped by design). Sentinel codes are kept: for example `EDAD` 999 and `INGTRMEN` 999999 mean "No especificado", and `INGTRMEN` 999998 means more than $999,997.

## Using the microdata

- **Weights.** Every estimate must be weighted by `FACTOR`. Summed weights reproduce INEGI's published totals exactly. This was checked for Aguascalientes and its municipios (population, women, 65+, schooling, internet, dwellings) and for Zacatecas' population. Records with `TIPO_REG = 1` were imputed by INEGI and are part of those totals.
- **Precision.** `ESTRATO` and `UPM` carry the sample design and are needed for standard errors. The published tables give 90% confidence limits.
- **Coverage.** `COBERTURA` is 1 for a municipio that was fully enumerated, 2 for a sampled one and 3 where the sample is insufficient. INEGI publishes no municipio-level estimates for code 3.
- **Income.** `INGTRMEN` is monthly. Most answers were given per week and multiplied by 4.3, so values cluster on round numbers (8,600 = 2,000 × 4.3). Prefer means or income bands to medians.

## Published results

`published_to_parquet.py` turns INEGI's wide published file into `clean/eic_2025/resultados_publicados.parquet`: one row per place × indicator (341 indicators × 2,776 places) with `value`, `se`, `li`/`ls` (90%) and `cv`, plus `indicadores_publicados.parquet` with each indicator's section, name and description. In DuckDB they are `resultados_publicados` and `indicadores_publicados`.

- `geo_level` is `nacional`, `estatal`, `municipal`, `localidad` (cities of 50,000+) or `resto` (a state's localities under 50,000, together).
- INEGI's `MI` (insufficient sample) and `NA` (not applicable) become a null `value` with the code in `flag`. The `*`/`**` after a municipio's name moves to `municipio_flag` (`censado`, `muestra_insuficiente`).
- The script stops if the data and the dictionary disagree on indicators, if a place lacks any of the five statistics, or if the states do not sum to the national population.

These are the official figures with INEGI's universes (schooling for ages 15+, unemployment over the economically active, and so on). Prefer them to `estimaciones` whenever an indicator is published. The website's state scorecard reads them (`web/scripts/export_eic_states.py`).

## Pre-computed estimates

`estimates.py` writes `clean/eic_2025/estimaciones/{viviendas,personas,migrantes}.parquet`: 5.3 million estimates, 187 MB. In DuckDB they are the `estimaciones` view, or `estimaciones_etiquetadas` with the question, category label and place name joined in. One row is one variable × category × statistic × place:

- `statistic` is `total` (weighted count) or `porcentaje` (share of the question's universe, i.e. records where the variable is not null) for every category, and `promedio` for numeric variables, over values inside the descriptor's valid range. `_TOTAL` is the weighted number of records.
- `geo_level` is `nacional` (`CVEGEO` 00000), `estatal` (EE000) or `municipal` (EEMMM).
- `value`, `se`, `li`/`ls` (90%) and `cv` are computed exactly as INEGI computes them. `n_obs` is the unweighted number of records behind the estimate, and `psu`/`strata` the design units used.
- `flag` is `censado` for a fully enumerated municipio (no sampling error) or `muestra_insuficiente` (COBERTURA 3: totals without error; ratios kept but without precision, which INEGI publishes as "MI").
- A category absent from a place has no row; its total and percentage are zero.

INEGI's method, reverse-engineered from the published results:

1. Ultimate-cluster variance, with strata = `CVE_ENT`+`ESTRATO`, PSU = `UPM`, factor n/(n−1) and no finite-population correction.
2. Ratios are linearized.
3. Only PSUs containing the domain enter the variance and degrees of freedom.
4. Censused municipios have zero error.
5. Confidence limits use Student's t(0.95) with dof = PSUs − strata.
6. Percentages get a logit-scale interval.

It reproduces all 15,052 value/SE/CI/CV figures checked across 6 indicators, and `--validate` re-checks 11 published indicators at every level (all match exactly). Rule 3 is INEGI's convention: it slightly understates subgroup error compared with keeping empty PSUs as zeros.

Multi-response questions (`DHSERSAL1/2`, `MED_TRASLADO_*1–3`, `SEPARACION1–4`, `FINANCIAMIENTO1–3`) are estimated column by column. Combining them, as in "affiliated to IMSS in either answer", is a derived indicator. Treat municipio estimates with `cv` above about 30 as unreliable. That covers roughly a quarter of municipio percentages, mostly rare categories.
