# Table Dictionaries

This folder documents the ingested election-data warehouse and the raw source
files that feed it.

Start with `overview.csv`. It describes each normalized table, its primary key,
row grain, purpose, and important joins.

## Normalized Warehouse Tables

These files describe the SQLite/parquet schema after ingestion:

- `dim_election.csv`
- `dim_geography.csv`
- `dim_casilla.csv`
- `dim_party.csv`
- `dim_candidatos.csv`
- `fact_casilla_vote.csv`

They define column names, data types, key roles, Spanish and English
descriptions, value domains, and cycle-specific notes.

## Raw Source References

Files prefixed with a year describe representative source files before
normalization:

- `[1994] 1994_PRE_CAS_94.csv`
- `[2000] 2000_DAT_DISTRITALES_CAS.csv`
- `[2006] 2006_ESTADISTICAS_TXT_CAS.csv`
- `[2015] DIPUTADOS_CAS.csv`
- `[2021] DIPUTACIONES_CAS.csv`
- `[2024] 2024_SEE_PRE_NAL_CAS.csv`

`raw_cycle_examples.csv` summarizes the source layout by cycle, including
delimiters, header locations, example rows, and important quirks discovered
during ingestion.
