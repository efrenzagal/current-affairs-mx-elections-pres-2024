# State scorecards

A database of public-policy indicators for every Mexican state and, where the data allows, every municipio. The goal is a quick, holistic picture of each state first, and then the means to evaluate specific measures (pre-post comparisons, synthetic control).

## Workflow

Every source follows the same five steps:

1. **Research and download.** Find the official source and download it into `data/`. Record links, checksums and checks in the source's `sources.md`.
2. **Inspect.** Explore the raw files in RStudio and agree on a clean structure.
3. **`raw_to_parquet.py`.** Turn the raw files into validated, analysis-ready Parquet in `data/clean/`.
4. **`ingest.py`.** Load the Parquet into the warehouse, `election_data.db` at the repository root.
5. **Analyse and publish.** Analyse in RStudio, then bring the results to the website.

The ingestion scripts live in [`ingestion/`](ingestion/README.md), one folder per source, with the full run order there. Run every command from the repository root.

## Sources

| Source | Coverage | Where it ends up |
| --- | --- | --- |
| **INEGI PIBE**: state GDP by economic activity | States, 1980–2024 | `fact_pibe_state_annual` |
| **CONAPO** population projections | States 1970–2070, municipios 1990–2040 | `fact_conapo_*` tables, `dim_conapo_state`, and the views `view_conapo_pibe_per_capita` (GDP per resident) and `view_conapo_population_pyramid` |
| **CONAPO remittances** | Municipios, quarterly, 2013–2024 | `fact_conapo_remittances_municipality_quarterly` |
| **INEGI Encuesta Intercensal 2025**: microdata | 25.2M persons, 7.3M dwellings and 363k emigrants; nation, states and municipios | Parquet plus `data/eic2025.duckdb` (see below) |
| **IMCO Índice de Competitividad Estatal** | States, 2016–2026 | Downloaded to `data/imco_ice/`, not ingested yet |

Each source's provenance, validation and table grain are documented next to its scripts: [`ingestion/conapo/sources.md`](ingestion/conapo/sources.md) and [`ingestion/eic_2025/sources.md`](ingestion/eic_2025/sources.md).

## The Encuesta Intercensal is the exception

At 25 million rows, the Intercensal microdata would roughly double `election_data.db`, and SQLite is slow at the weighted aggregations it needs. So it stays in Parquet and is queried through a 1 MB DuckDB file whose tables are views over that Parquet:

```bash
python3 -m query_console --db state_scorecards/data/eic2025.duckdb
```

- `personas`, `viviendas`, `migrantes` hold the microdata, one row per sampled respondent. Always weight by `FACTOR`.
- `estimaciones` and `estimaciones_etiquetadas` hold 5.3 million pre-computed estimates: every category of every question as a total and a percentage, and every numeric variable as an average. They cover the nation, all 32 states and all 2,478 municipios. Each estimate carries INEGI's standard error, 90% confidence interval and coefficient of variation. The method reproduces INEGI's published results exactly.
- `codebook_variables`, `codebook_categories`, `codebook_classifiers`, `entidades` and `municipios` hold the question wording, answer labels and place names.

For anything already published, prefer the published figure. Before relying on a municipio estimate, check its `cv`: above about 30 it is unreliable.

## Layout

```
state_scorecards/
├── ingestion/        one folder per source: raw_to_parquet.py → ingest.py (+ sources.md)
├── analysis/         exploratory SQL and RStudio work
└── data/             local only, never committed
    ├── raw_conapo/   raw_inegi/   eic_2025/   imco_ice/     downloads, as published
    ├── clean/                                              validated Parquet
    └── eic2025.duckdb                                      queryable Intercensal
```

Everything under `data/` is excluded from Git. Only code and documentation are tracked. Raw downloads are backed up separately, and the Parquet and DuckDB files can be rebuilt from them with the scripts. Some downloads cannot be fetched again in the same form, so treat `data/` raw folders as the record and not as a cache.
