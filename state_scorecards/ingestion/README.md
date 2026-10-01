# State scorecards ingestion

One folder per source. Each follows the same path: raw download → `raw_to_parquet.py` (clean, validated Parquet in `state_scorecards/data/clean/`) → `ingest.py` (tables in `election_data.db`). Raw and clean data stay local and out of Git. Run every command from the repository root.

| Folder | Source | Run order | Notes |
| --- | --- | --- | --- |
| `inegi_pibe/` | INEGI Producto Interno Bruto por Entidad Federativa, 1980–2024 | `raw_to_parquet.py` → `ingest.py` | Joins INEGI's retropolated 1980–2002 release to the detailed 2003+ release. |
| `conapo/` | CONAPO population projections, plus quarterly remittances by municipio | `raw_to_parquet.py` → `ingest.py`, then `remesas_raw_to_parquet.py` → `remesas_ingest.py` | Provenance and tables in [`conapo/sources.md`](conapo/sources.md). The remittance scripts reuse the CONAPO helpers, so keep them in this folder. |
| `eic_2025/` | INEGI Encuesta Intercensal 2025 microdata | `download.py` → `raw_to_parquet.py` → `estimates.py` → `build_duckdb.py` | Too large for SQLite: the microdata and pre-computed estimates stay in Parquet, queried through `state_scorecards/data/eic2025.duckdb`. See [`eic_2025/sources.md`](eic_2025/sources.md). |

```bash
python3 state_scorecards/ingestion/inegi_pibe/raw_to_parquet.py
python3 state_scorecards/ingestion/inegi_pibe/ingest.py

python3 state_scorecards/ingestion/conapo/raw_to_parquet.py
python3 state_scorecards/ingestion/conapo/ingest.py
python3 state_scorecards/ingestion/conapo/remesas_raw_to_parquet.py
python3 state_scorecards/ingestion/conapo/remesas_ingest.py

python3 state_scorecards/ingestion/eic_2025/download.py
python3 state_scorecards/ingestion/eic_2025/raw_to_parquet.py
python3 state_scorecards/ingestion/eic_2025/estimates.py
python3 state_scorecards/ingestion/eic_2025/build_duckdb.py
```

Adding a source: create a folder named after it, keep the `raw_to_parquet.py` / `ingest.py` names, add a `sources.md` with download links and checks, and add a row here.
