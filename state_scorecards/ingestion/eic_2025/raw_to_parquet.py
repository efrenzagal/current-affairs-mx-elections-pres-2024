"""Convert the Encuesta Intercensal 2025 microdata CSVs to typed Parquet.

Reads the per-state folders written by ``download.py`` and INEGI's file
descriptor ``eic2025_micro_fd.xlsx``. The descriptor is the schema: a CSV whose
columns differ from it in name or order is rejected rather than coerced.

Outputs, under ``state_scorecards/data/clean/eic_2025/``:

* ``{viviendas,personas,migrantes}/<table>_<NN>.parquet`` -- one file per
  state and table. Read a whole folder as one dataset, e.g. in R
  ``arrow::open_dataset("…/personas")`` or in DuckDB
  ``read_parquet('…/personas/*.parquet')``.
* ``codebook_variables.parquet`` -- one row per variable: section, question,
  type, valid range and length.
* ``codebook_categories.parquet`` -- one row per category code and its label.
* ``codebook_classifiers.parquet`` -- the long classifiers the descriptor only
  references (industry, occupation, country, municipio...), one row per code.
  ``codebook_variables.classifier`` names the one each variable uses.

Values are kept exactly as published. Columns the descriptor marks
"Numérico" become int64 and the rest stay strings, so codes keep their leading
zeros. Empty cells become null, which the descriptor labels "Blanco por pase"
(question skipped by design). Sentinels such as EDAD 999 or INGTRMEN 999999
("No especificado") are *not* nulled here; that is an analysis decision, made
with the codebook at hand.

Each Parquet file records the source zip's SHA-256 from the download
manifest, so a state is reconverted only when its download changes.

Usage:
    python3 state_scorecards/ingestion/eic_2025/raw_to_parquet.py
    python3 state_scorecards/ingestion/eic_2025/raw_to_parquet.py --states 1 32 --force
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.parquet as pq


REPO_ROOT = Path(__file__).resolve().parents[3]
EIC_DIR = REPO_ROOT / "state_scorecards" / "data" / "eic_2025"
RAW_DIR = EIC_DIR / "microdatos"
MANIFEST_PATH = RAW_DIR / "manifest.csv"
DOCS_DIR = EIC_DIR / "documentacion"
DESCRIPTOR_PATH = DOCS_DIR / "eic2025_micro_fd.xlsx"
CATALOG_DIR = DOCS_DIR / "catalogos"
OUTPUT_DIR = REPO_ROOT / "state_scorecards" / "data" / "clean" / "eic_2025"

TABLES = ("viviendas", "personas", "migrantes")
SOURCE_METADATA_KEY = b"source_zip_sha256"

# Classifier name -> INEGI's catalog file (names as published, typo included).
CATALOG_FILES = {
    "actividad_economica": "ACTIVIDAD_ECONOMICA.csv",
    "causa_migracion": "CAUSA_MIGRACION.csv",
    "condicion_mental": "CONDICION_MENTAL.csv",
    "entidad": "ENTIDAD.csv",
    "entidad_pais": "ENTIDAD_PAIS.csv",
    "escolaridad_acumulada": "ESOLARIDAD_ACUMULADA.csv",
    "lengua_indigena": "LENGUA_INDIGENA.csv",
    "municipio": "MUNICIPIO.csv",
    "ocupacion": "OCUPACION.csv",
    "parentesco": "PARENTESCO.csv",
}

# Variable -> classifier. The descriptor names a classifier in free text
# ("Según clasificador de ..."); spelling the mapping out keeps a new or
# renamed reference from being matched by guesswork. CVE_ENT and CVEGEO cite
# no classifier but are the obvious keys to label a record's own location.
VARIABLE_CLASSIFIERS = {
    ("viviendas", "CVE_ENT"): "entidad",
    ("viviendas", "CVEGEO"): "municipio",
    ("personas", "CVE_ENT"): "entidad",
    ("personas", "CVEGEO"): "municipio",
    ("personas", "PARENTESCO"): "parentesco",
    ("personas", "ENT_PAIS_NAC"): "entidad_pais",
    ("personas", "COND_MENTAL_C"): "condicion_mental",
    ("personas", "QDIALECT_INALI"): "lengua_indigena",
    ("personas", "MUN_ASI"): "municipio",
    ("personas", "ENT_PAIS_ASI"): "entidad_pais",
    ("personas", "ESCOACUM"): "escolaridad_acumulada",
    ("personas", "ENT_PAIS_RES_5A"): "entidad_pais",
    ("personas", "MUN_RES_5A"): "municipio",
    ("personas", "CAUSA_MIG_C"): "causa_migracion",
    ("personas", "OCUPACION_C"): "ocupacion",
    ("personas", "ACTIVIDADES_C"): "actividad_economica",
    ("personas", "MUN_TRAB"): "municipio",
    ("personas", "ENT_PAIS_TRAB"): "entidad_pais",
    ("migrantes", "CVE_ENT"): "entidad",
    ("migrantes", "CVEGEO"): "municipio",
    ("migrantes", "MCAUSAEMIG_V"): "causa_migracion",
    ("migrantes", "MLUGORI_C"): "entidad_pais",
    ("migrantes", "MPAIDES_C"): "entidad_pais",
    ("migrantes", "MCAUSARETO_V"): "causa_migracion",
}

# MUN_ASI / MUN_RES_5A / MUN_TRAB hold only the 3-digit municipio; the state is
# in the paired ENT_PAIS_* column. The coverage check joins them as CVEGEO.
MUNICIPIO_STATE_COLUMN = {
    "MUN_ASI": "ENT_PAIS_ASI",
    "MUN_RES_5A": "ENT_PAIS_RES_5A",
    "MUN_TRAB": "ENT_PAIS_TRAB",
}

MAIN_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
CELL_RE = re.compile(r"([A-Z]+)")


# Descriptor -------------------------------------------------------------------


def _column_index(cell_address: str) -> int:
    result = 0
    for char in CELL_RE.match(cell_address).group():
        result = result * 26 + ord(char) - ord("A") + 1
    return result - 1


def read_xlsx_sheets(path: Path) -> dict[str, list[list[str | None]]]:
    """All worksheets of a workbook as rows of cell text, keyed by sheet name."""
    with zipfile.ZipFile(path) as archive:
        strings = [
            "".join(t.text or "" for t in item.iter(MAIN_NS + "t"))
            for item in ET.fromstring(archive.read("xl/sharedStrings.xml")).iter(MAIN_NS + "si")
        ]
        rels = {
            rel.attrib["Id"]: rel.attrib["Target"]
            for rel in ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        }
        sheets = {}
        for sheet in ET.fromstring(archive.read("xl/workbook.xml")).iter(MAIN_NS + "sheet"):
            target = rels[sheet.attrib[REL_NS + "id"]].lstrip("/")
            target = target if target.startswith("xl/") else f"xl/{target}"
            rows = []
            for row in ET.fromstring(archive.read(target)).iter(MAIN_NS + "row"):
                values: list[str | None] = []
                for cell in row.iter(MAIN_NS + "c"):
                    index = _column_index(cell.attrib["r"])
                    values.extend([None] * (index + 1 - len(values)))
                    value = cell.find(MAIN_NS + "v")
                    if cell.attrib.get("t") == "s" and value is not None:
                        values[index] = strings[int(value.text)]
                    elif cell.attrib.get("t") == "inlineStr":
                        values[index] = "".join(t.text or "" for t in cell.iter(MAIN_NS + "t"))
                    elif value is not None:
                        values[index] = value.text
                rows.append([v.strip() if isinstance(v, str) else v for v in values])
            sheets[sheet.attrib["name"]] = rows
    return sheets


def parse_descriptor(path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Flatten the descriptor's three table sheets into variables and categories.

    Each sheet lists a variable on a row with a numeric "Cons." and a
    "Mnemónico", followed by one row per category (label under "Pregunta y
    categoría", code under "Rango válido"). Upper-case rows in "Cons." open a
    section; rows holding only "Longitud" are subtotals and are skipped.

    Questions asked as a battery ("¿(NOMBRE) tiene por su trabajo: aguinaldo?
    vacaciones? ...") put the shared lead-in on its own row, named like
    "Prestaciones laborales"; each item's description ends "(Prestaciones
    laborales)". The lead-in becomes ``question_stem`` and ``question_full``
    joins it to the item so every variable reads as the question asked.
    """
    variables, categories = [], []
    sheets = read_xlsx_sheets(path)
    for table in TABLES:
        rows = sheets[table.upper()]
        header_at = next(i for i, row in enumerate(rows) if "Mnemónico" in row)
        header = [cell or "" for cell in rows[header_at]]
        col = {name: header.index(name) for name in (
            "Cons.", "Descripción", "Mnemónico", "Pregunta y categoría",
            "Tipo", "Rango válido", "Longitud",
        )}
        section, current, stem = None, None, None
        stem_first, previous_question = False, None
        for row in rows[header_at + 1:]:
            row = row + [None] * (len(header) - len(row))
            get = lambda name: row[col[name]]  # noqa: E731
            cons, mnemonic = get("Cons."), get("Mnemónico")
            if cons and cons.isdigit() and mnemonic:
                current = mnemonic
                description = get("Descripción") or ""
                question = get("Pregunta y categoría") or ""
                # A lead-in covers the item right after it, then every item
                # that still reads as a continuation: a lower-case fragment
                # ("ver, aun usando lentes?"), a description tagged with the
                # battery's name, or the same question as the item before.
                if stem and not (
                    stem_first
                    or question[:1].islower()
                    or description.endswith(f"({stem[0]})")
                    or question == previous_question
                ):
                    stem = None
                stem_first, previous_question = False, question
                variables.append({
                    "table_name": table,
                    "position": int(cons),
                    "variable": mnemonic,
                    "section": section,
                    "description": description,
                    "question": question,
                    "question_stem": stem[1] if stem else None,
                    "question_full": f"{stem[1]} {question}" if stem else question,
                    "data_type": get("Tipo"),
                    "valid_range": get("Rango válido"),
                    "length": int(get("Longitud")) if (get("Longitud") or "").isdigit() else None,
                })
            elif cons and not cons.isdigit():
                section, current, stem = cons, None, None
            elif not cons and not mnemonic and get("Descripción") and get("Pregunta y categoría"):
                stem, current = (get("Descripción"), get("Pregunta y categoría")), None
                stem_first = True
            elif current and get("Rango válido") is not None and get("Pregunta y categoría"):
                categories.append({
                    "table_name": table,
                    "variable": current,
                    "code": get("Rango válido"),
                    "label": get("Pregunta y categoría"),
                })
    variables = pd.DataFrame(variables)
    duplicated = variables[variables.duplicated(["table_name", "variable"], keep=False)]
    if not duplicated.empty:
        raise ValueError(f"Descriptor repeats variables: {duplicated[['table_name', 'variable']].values.tolist()}")
    for table in TABLES:
        positions = variables.loc[variables.table_name == table, "position"].tolist()
        if positions != list(range(1, len(positions) + 1)):
            raise ValueError(f"Descriptor sheet {table.upper()} has gaps in its variable numbering")
    return variables, pd.DataFrame(categories)


def attach_classifiers(variables: pd.DataFrame) -> pd.DataFrame:
    """Add ``classifier``; fail if the descriptor cites one this script does not map."""
    keys = list(zip(variables.table_name, variables.variable))
    variables = variables.assign(classifier=[VARIABLE_CLASSIFIERS.get(key) for key in keys])
    cites = variables.valid_range.fillna("").str.contains("clasificador", case=False)
    unmapped = variables[cites & variables.classifier.isna()]
    if not unmapped.empty:
        raise ValueError(
            "Descriptor cites a classifier for unmapped variables "
            f"{unmapped[['table_name', 'variable']].values.tolist()}; add them to VARIABLE_CLASSIFIERS"
        )
    stale = sorted(set(VARIABLE_CLASSIFIERS) - set(keys))
    if stale:
        raise ValueError(f"VARIABLE_CLASSIFIERS names variables the descriptor lacks: {stale}")
    return variables


def read_classifiers(catalog_dir: Path) -> pd.DataFrame:
    """Stack INEGI's catalog CSVs into classifier / code / label rows.

    Municipios are keyed by the 5-digit CVEGEO the microdata itself uses (2-digit
    state + 3-digit municipio); INEGI's file writes the state with 3 digits.
    ``parent_code``/``parent_label`` carry a municipio's state, and ``detail``
    the school level each year of accumulated schooling equals.
    """
    frames = []
    for classifier, filename in CATALOG_FILES.items():
        raw = pd.read_csv(catalog_dir / filename, dtype=str, encoding="utf-8-sig")
        if classifier == "municipio":
            state = raw.CVE_ENT.str.zfill(3)
            mexican = state.between("001", "032")
            # The one row outside 001-032 is "Entidad/Municipio no especificado"
            # (997/999); it has no CVEGEO, so it keeps INEGI's own codes.
            if set(state[~mexican]) - {"997"}:
                raise ValueError(f"{filename}: unexpected state codes {sorted(set(state[~mexican]))}")
            state = state.where(~mexican, state.str[-2:])
            frame = pd.DataFrame({
                "code": state + raw.CVE_MUN,
                "label": raw.DESC_MUN,
                "parent_code": state,
                "parent_label": raw.DESC_ENT,
            })
        else:
            frame = pd.DataFrame({"code": raw.CLAVE, "label": raw.DESCRIPCION})
            if "EQUIVALENCIA" in raw:
                frame["detail"] = raw.EQUIVALENCIA
        if frame.code.duplicated().any():
            raise ValueError(f"{filename} repeats codes {frame.code[frame.code.duplicated()].tolist()[:5]}")
        frames.append(frame.assign(classifier=classifier))
    columns = ["classifier", "code", "label", "parent_code", "parent_label", "detail"]
    return pd.concat(frames, ignore_index=True).reindex(columns=columns)


def unmatched_codes(table: pa.Table, table_name: str, classifiers: dict[str, set[str]]) -> dict[str, int]:
    """Rows per classified column whose code is missing from its classifier."""
    frame = table.to_pandas()
    report = {}
    for (name, column), classifier in VARIABLE_CLASSIFIERS.items():
        if name != table_name:
            continue
        values = frame[column]
        if column in MUNICIPIO_STATE_COLUMN:
            state = frame[MUNICIPIO_STATE_COLUMN[column]]
            # Only Mexican states (001-032) have municipios; abroad stays unpaired.
            in_mexico = values.notna() & state.between("001", "032")
            values = (state.str[-2:] + values)[in_mexico]
        values = values.dropna()
        if pd.api.types.is_numeric_dtype(values):
            # ESCOACUM is "Numérico" but its classifier codes are text ("17").
            values = values.astype("int64").astype(str)
        missing = int((~values.isin(classifiers[classifier])).sum())
        if missing:
            report[column] = missing
    return report


# Conversion -------------------------------------------------------------------


def read_manifest() -> dict[str, dict[str, str]]:
    if not MANIFEST_PATH.exists():
        sys.exit(f"No download manifest at {MANIFEST_PATH}; run download.py first.")
    with MANIFEST_PATH.open(newline="", encoding="utf-8") as stream:
        return {row["state"]: row for row in csv.DictReader(stream)}


def schema_for(table: str, variables: pd.DataFrame) -> pa.Schema:
    spec = variables[variables.table_name == table].sort_values("position")
    # int64, not int32: every value fits in 32 bits, but FACTOR * INGTRMEN does
    # not, and an int32 product overflows silently in pandas and R.
    return pa.schema([
        pa.field(row.variable, pa.int64() if row.data_type == "Numérico" else pa.string())
        for row in spec.itertuples()
    ])


def is_current(path: Path, source_sha: str) -> bool:
    if not path.exists():
        return False
    metadata = pq.read_schema(path).metadata or {}
    return metadata.get(SOURCE_METADATA_KEY) == source_sha.encode()


def convert_table(csv_path: Path, schema: pa.Schema, expected_rows: int, source_sha: str, out_path: Path) -> pa.Table:
    with csv_path.open(encoding="ascii") as stream:
        header = stream.readline().rstrip("\r\n").split(",")
    if header != schema.names:
        missing = [name for name in schema.names if name not in header]
        extra = [name for name in header if name not in schema.names]
        raise ValueError(
            f"{csv_path.name} columns differ from the descriptor"
            f" (missing {missing}, unexpected {extra}, or reordered)"
        )

    table = pacsv.read_csv(
        csv_path,
        read_options=pacsv.ReadOptions(encoding="ascii"),
        convert_options=pacsv.ConvertOptions(
            column_types=schema,
            null_values=[""],
            strings_can_be_null=True,
            quoted_strings_can_be_null=True,
        ),
    ).select(schema.names)
    if table.num_rows != expected_rows:
        raise ValueError(f"{csv_path.name}: {table.num_rows:,} rows, manifest says {expected_rows:,}")
    if table.column("FACTOR").null_count:
        raise ValueError(f"{csv_path.name}: FACTOR is null in {table.column('FACTOR').null_count} rows")

    table = table.replace_schema_metadata({SOURCE_METADATA_KEY: source_sha.encode()})
    out_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = out_path.with_suffix(".parquet.tmp")
    pq.write_table(table, temporary, compression="zstd")
    temporary.replace(out_path)
    return table


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--states", type=int, nargs="+", metavar="N", help="INEGI state codes 1-32 (default: all downloaded)")
    parser.add_argument("--force", action="store_true", help="Reconvert even when the Parquet is current")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = read_manifest()
    states = [f"{n:02d}" for n in args.states] if args.states else sorted(manifest)
    not_downloaded = [state for state in states if state not in manifest]
    if not_downloaded:
        sys.exit(f"Not in the download manifest: {not_downloaded}")

    variables, categories = parse_descriptor(DESCRIPTOR_PATH)
    variables = attach_classifiers(variables)
    classifiers = read_classifiers(CATALOG_DIR)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    variables.to_parquet(OUTPUT_DIR / "codebook_variables.parquet", index=False)
    categories.to_parquet(OUTPUT_DIR / "codebook_categories.parquet", index=False)
    classifiers.to_parquet(OUTPUT_DIR / "codebook_classifiers.parquet", index=False)
    print(
        f"Codebook: {len(variables)} variables, {len(categories)} category codes, "
        f"{len(classifiers):,} classifier codes in {classifiers.classifier.nunique()} classifiers"
    )
    codes_by_classifier = classifiers.groupby("classifier").code.agg(set).to_dict()

    schemas = {table: schema_for(table, variables) for table in TABLES}
    for state in states:
        entry = manifest[state]
        source_sha = entry["zip_sha256"]
        written = []
        for table in TABLES:
            out_path = OUTPUT_DIR / table / f"{table}_{state}.parquet"
            if not args.force and is_current(out_path, source_sha):
                continue
            csv_path = RAW_DIR / f"eic2025_micro_{state}" / f"{table}{state}.csv"
            converted = convert_table(csv_path, schemas[table], int(entry[f"{table}_rows"]), source_sha, out_path)
            written.append(f"{table} {converted.num_rows:,}")
            # A code outside its classifier means INEGI published a newer
            # catalog; the data is still kept, but its labels would be missing.
            for column, count in unmatched_codes(converted, table, codes_by_classifier).items():
                print(f"[{state}] WARNING {table}.{column}: {count:,} rows with codes missing from its classifier")
        print(f"[{state}] " + (", ".join(written) if written else "up to date"))

    for table in TABLES:
        size = sum(path.stat().st_size for path in (OUTPUT_DIR / table).glob("*.parquet"))
        print(f"{table}: {size / 1_048_576:,.0f} MB of Parquet")


if __name__ == "__main__":
    main()
