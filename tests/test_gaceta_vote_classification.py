import argparse
import csv
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from aux_scripts.gaceta_votes.classify_gaceta_votes import (
    PROMPT_VERSION,
    apply,
    load_votes,
    prepare,
    request_body,
    review,
)


class GacetaVoteClassificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "test.db"
        with sqlite3.connect(self.db) as conn:
            conn.execute("""
                CREATE TABLE dim_gaceta_vote (
                    gaceta_vote_id TEXT PRIMARY KEY, legislature INTEGER,
                    vote_date TEXT, title TEXT, vote_context TEXT
                )
            """)
            conn.executemany(
                "INSERT INTO dim_gaceta_vote VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        "L66_GENERAL", 66, "2025-01-01",
                        "Minuta con proyecto de decreto sobre telecomunicaciones "
                        "(en lo general y en lo particular los artículos no reservados). "
                        "<p> 01 de enero de 2025",
                        "En lo general y en lo particular los artículos no reservados.",
                    ),
                    (
                        "L66_RESERVED", 66, "2025-01-01",
                        "Minuta con proyecto de decreto sobre telecomunicaciones "
                        "(en lo particular los artículos reservados con modificaciones aceptadas). "
                        "<p> 01 de enero de 2025",
                        "En lo particular los artículos reservados con modificaciones aceptadas.",
                    ),
                    ("L65_OLD", 65, "2024-01-01", "Dictamen anterior", ""),
                ],
            )

    def tearDown(self):
        self.temp.cleanup()

    def _write_raw_csv(self) -> Path:
        path = self.root / "classifications.csv"
        fields = [
            "gaceta_vote_id", "origen", "etapa_votacion", "tipo_instrumento",
            "tema_politica", "requiere_revision", "evidencia", "parse_error",
        ]
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for vote_id in ("L66_GENERAL", "L66_RESERVED"):
                writer.writerow({
                    "gaceta_vote_id": vote_id,
                    "origen": "dictamen_de_comision",
                    "etapa_votacion": "en_lo_particular",
                    "tipo_instrumento": "legislativo",
                    "tema_politica": "economia_e_industria",
                    "requiere_revision": "false",
                    "evidencia": "Minuta sobre telecomunicaciones.",
                    "parse_error": "",
                })
        return path

    def test_prepare_is_scoped_to_legislature_66(self):
        out = self.root / "out"
        prepare(argparse.Namespace(
            db=self.db, out_dir=out, model="test-model", legislature=66
        ))
        requests = (out / "requests.jsonl").read_text(encoding="utf-8").splitlines()
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(len(requests), 2)
        self.assertEqual(manifest["legislature"], 66)
        self.assertEqual(manifest["prompt_version"], PROMPT_VERSION)

    def test_request_has_hints_siblings_and_no_confidence(self):
        rows = load_votes(self.db, 66)
        body = request_body(rows[1], "test-model")
        record = json.loads(body["input"][1]["content"])
        self.assertEqual(record["origin_hint"], "minuta_del_senado")
        self.assertEqual(record["stage_hint"], "articulos_reservados_o_modificacion")
        self.assertEqual(len(record["related_roll_calls"]), 1)
        self.assertNotIn("confianza", body["text"]["format"]["schema"]["properties"])

    def test_review_corrects_rules_and_apply_migrates_legacy_confidence(self):
        raw = self._write_raw_csv()
        reviewed = self.root / "classifications_reviewed.csv"
        review(argparse.Namespace(
            csv_path=raw, output_path=reviewed, db=self.db, legislature=66
        ))
        with reviewed.open(encoding="utf-8") as handle:
            output = {r["gaceta_vote_id"]: r for r in csv.DictReader(handle)}
        self.assertEqual(output["L66_GENERAL"]["origen"], "minuta_del_senado")
        self.assertEqual(
            output["L66_RESERVED"]["etapa_votacion"],
            "articulos_reservados_o_modificacion",
        )
        self.assertEqual(output["L66_GENERAL"]["review_status"], "needs_review")

        with sqlite3.connect(self.db) as conn:
            conn.execute("""
                CREATE TABLE fact_gaceta_vote_classification (
                    gaceta_vote_id TEXT PRIMARY KEY, origen TEXT NOT NULL,
                    etapa_votacion TEXT NOT NULL, tipo_instrumento TEXT NOT NULL,
                    tema_politica TEXT NOT NULL, confianza REAL NOT NULL,
                    requiere_revision INTEGER NOT NULL, evidencia TEXT NOT NULL,
                    model TEXT NOT NULL, prompt_version TEXT NOT NULL,
                    classified_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                INSERT INTO fact_gaceta_vote_classification VALUES
                ('L65_OLD','dictamen_de_comision','asunto_completo_o_no_especificado',
                 'legislativo','otro',0.95,0,'Legacy','old-model','v1','2024-01-01')
            """)
        apply(argparse.Namespace(
            csv_path=reviewed, db=self.db, model="test-model", legislature=66,
            allow_needs_review=True,
        ))
        with sqlite3.connect(self.db) as conn:
            columns = {row[1] for row in conn.execute(
                "PRAGMA table_info(fact_gaceta_vote_classification)"
            )}
            legacy_status = conn.execute(
                "SELECT review_status FROM fact_gaceta_vote_classification "
                "WHERE gaceta_vote_id='L65_OLD'"
            ).fetchone()[0]
            count = conn.execute(
                "SELECT COUNT(*) FROM fact_gaceta_vote_classification"
            ).fetchone()[0]
        self.assertNotIn("confianza", columns)
        self.assertIn("review_status", columns)
        self.assertEqual(legacy_status, "legacy_model_only")
        self.assertEqual(count, 3)


if __name__ == "__main__":
    unittest.main()
