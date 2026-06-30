# Mexican Federal Election Results Explorer

Work-in-progress data pipeline and Streamlit explorer for Mexican federal
election results at casilla, seccion, municipio, estado, and time-series levels.

The project normalizes official INE result files from multiple election cycles
into a common SQLite warehouse, materializes analysis-ready parquet files, and
serves those parquets through a Streamlit dashboard.

## Current Scope

The repository currently includes ingestion and materialization code for several
federal election cycles, including presidential, deputies, and senate contests
where available. Older source files differ substantially by year, so the
pipeline keeps cycle-specific converters and then maps the cleaned outputs into
a shared schema.

The Streamlit app is focused on the online/lightweight deployment path:

- state and municipality result views
- materialized parquet inputs
- multi-year state-level party time series
- preprocessed municipio GeoJSON for map joins

## Repository Layout

- `ingestion/` - cycle-specific converters, SQLite ingestion, and parquet
  materialization.
- `ine_explorer_streamlit_online.py` - Streamlit app backed by materialized
  parquet files.
- `data/materialized/` - final parquet artifacts used by the Streamlit app.
- `documentation/table_dictionaries/` - warehouse table dictionaries and raw
  source-file references.
- `aux_scripts/` - exploratory R scripts and QA checks used during validation.
- `graveyard/` - older notebooks and scripts kept as historical reference.

## Data Policy

Raw files, cleaned intermediate files, and local SQLite databases are not meant
to be uploaded to the repository.

The intended checked-in data artifacts are the finished Streamlit parquets in
`data/materialized/`, plus lightweight documentation files that explain how the
warehouse and source files are structured.

## Rebuild Flow

Install dependencies:

```bash
pip install -r requirements.txt
```

Build or refresh the SQLite warehouse from cleaned cycle parquets:

```bash
python ingestion/pipeline.py
```

Materialize Streamlit-ready parquets:

```bash
python ingestion/materialize.py
```

Run the app locally:

```bash
streamlit run ine_explorer_streamlit_online.py
```

## Documentation

Start with `documentation/table_dictionaries/overview.csv` for the normalized
warehouse schema. The table dictionary README explains the distinction between
warehouse dictionaries and raw source-file references.

## Status

This project is still under active development. The core pipeline structure is
in place, but documentation, QA coverage, and the final online data-storage
strategy are still evolving.
