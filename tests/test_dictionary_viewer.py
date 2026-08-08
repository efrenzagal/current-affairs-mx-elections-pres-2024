import importlib.util
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BUILDER_PATH = (
    PROJECT_ROOT / "documentation" / "table_dictionaries" / "build_viewer.py"
)

spec = importlib.util.spec_from_file_location("dictionary_viewer", BUILDER_PATH)
dictionary_viewer = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(dictionary_viewer)


class DictionaryViewerTests(unittest.TestCase):
    def test_payload_includes_every_warehouse_and_raw_dictionary(self):
        missing_db = PROJECT_ROOT / "database-that-does-not-exist.db"
        payload = dictionary_viewer.build_payload(missing_db)

        warehouse_tables = {
            name for name, table in payload["tables"].items() if not table["raw"]
        }
        raw_tables = {
            name for name, table in payload["tables"].items() if table["raw"]
        }

        self.assertEqual(len(warehouse_tables), payload["warehouseTableCount"])
        self.assertEqual(len(raw_tables), payload["rawReferenceCount"])
        self.assertEqual(payload["coverage"], [])
        self.assertEqual(payload["legislativeCoverage"], {"deputies": [], "senate": []})
        self.assertEqual(payload["sampleSize"], 5)
        self.assertIn("fact_casilla_vote", warehouse_tables)
        self.assertIn("fact_senador_vote", warehouse_tables)
        self.assertIn("2024_SEE_PRE_NAL_CAS", raw_tables)
        self.assertEqual(
            payload["tables"]["2021_DIPUTACIONES_CAS"]["examples"]["ID_ESTADO"],
            ["1"],
        )

    def test_table_examples_are_distinct_non_null_and_limited(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            db_path = Path(temporary_directory) / "samples.db"
            with sqlite3.connect(db_path) as connection:
                connection.execute("CREATE TABLE example_table (value TEXT)")
                connection.executemany(
                    "INSERT INTO example_table VALUES (?)",
                    [(None,), ("A",), ("A",), ("B",), ("C",), ("D",), ("E",), ("F",)],
                )
            tables = {
                "example_table": {
                    "raw": False,
                    "columns": [{"Column Name": "value"}],
                }
            }

            examples = dictionary_viewer.load_table_examples(db_path, tables)

            self.assertEqual(
                examples["example_table"]["value"], ["A", "B", "C", "D", "E"]
            )

    def test_legislative_coverage_groups_chambers_by_legislature(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            db_path = Path(temporary_directory) / "coverage.db"
            with sqlite3.connect(db_path) as connection:
                connection.execute(
                    "CREATE TABLE dim_gaceta_vote (legislature INTEGER, vote_date TEXT)"
                )
                connection.execute(
                    "CREATE TABLE dim_senado_vote (legislature INTEGER, vote_date TEXT)"
                )
                connection.executemany(
                    "INSERT INTO dim_gaceta_vote VALUES (?, ?)",
                    [(65, "2021-09-07"), (65, "2024-04-30"), (66, "2024-08-29")],
                )
                connection.execute(
                    "INSERT INTO dim_senado_vote VALUES (?, ?)", (66, "2024-09-03")
                )

            coverage = dictionary_viewer.load_legislative_coverage(db_path)

            self.assertEqual([row["label"] for row in coverage["deputies"]], ["LXV", "LXVI"])
            self.assertEqual(coverage["deputies"][0]["voteCount"], 2)
            self.assertEqual(coverage["senate"][0]["label"], "LXVI")

    def test_election_coverage_distinguishes_missing_from_not_held(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            db_path = Path(temporary_directory) / "elections.db"
            with sqlite3.connect(db_path) as connection:
                connection.execute(
                    "CREATE TABLE dim_election (year INTEGER, election_type TEXT)"
                )
                connection.executemany(
                    "INSERT INTO dim_election VALUES (?, ?)",
                    [(1994, "PRE"), (2000, "PRE"), (2000, "DIP"), (2000, "SEN")],
                )

            coverage = dictionary_viewer.load_election_coverage(db_path)
            by_year = {row["year"]: row for row in coverage}

            self.assertEqual(by_year[1997]["PRE"], "not_held")
            self.assertEqual(by_year[1997]["DIP"], "missing")
            self.assertEqual(by_year[1997]["SEN"], "not_held")
            self.assertEqual(by_year[2000]["PRE"], "loaded")

    def test_rendered_viewer_is_self_contained_and_safe_for_script_embedding(self):
        payload = dictionary_viewer.build_payload(
            PROJECT_ROOT / "database-that-does-not-exist.db"
        )
        payload["tables"]["dim_election"]["overview"]["Notes"] = "</script>"

        html = dictionary_viewer.render_html(payload)

        self.assertTrue(html.startswith("<!doctype html>"))
        self.assertIn("const MODEL=", html)
        self.assertIn("<th>Examples</th>", html)
        self.assertIn("<\\/script>", html)
        self.assertNotIn("const MODEL=__PAYLOAD__", html)
        self.assertNotIn("<link ", html)
        self.assertNotIn("<script src=", html)

    def test_cli_writes_the_default_viewer_payload(self):
        payload = dictionary_viewer.build_payload(
            PROJECT_ROOT / "database-that-does-not-exist.db"
        )
        expected = dictionary_viewer.render_html(payload)

        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "viewer.html"
            subprocess.run(
                [
                    sys.executable,
                    str(BUILDER_PATH),
                    "--db",
                    str(PROJECT_ROOT / "database-that-does-not-exist.db"),
                    "--output",
                    str(output),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(output.read_text(encoding="utf-8"), expected)


if __name__ == "__main__":
    unittest.main()
