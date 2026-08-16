import argparse
import csv
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from camara_de_senadores.votos.classify_senado_votes import (
    AUDITED_OVERRIDES,
    PROMPT_VERSION,
    apply,
    load_votes,
    prepare,
    request_body,
    review,
)


class SenadoVoteClassificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "test.db"
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                """
                CREATE TABLE dim_senado_vote (
                    votacion_id INTEGER PRIMARY KEY, legislature INTEGER,
                    vote_date TEXT, period_type TEXT, ordinal_period TEXT,
                    exercise_year TEXT, description TEXT, vote_type TEXT
                )
                """
            )
            conn.execute(
                """
                INSERT INTO dim_senado_vote VALUES
                (42, 66, '2025-01-01', 'ORDINARIO', 'PRIMER', 'PRIMER',
                 'Dictamen que contiene minuta de la Cámara de Diputados en materia de salud.',
                 'VOTACIÓN EN LO GENERAL Y LOS ARTÍCULOS NO RESERVADOS')
                """
            )

    def tearDown(self):
        self.temp.cleanup()

    def test_request_contains_senate_source_fields_and_strict_schema(self):
        body = request_body(
            {
                "votacion_id": 42,
                "legislature": 66,
                "vote_date": "2025-01-01",
                "period_type": "ORDINARIO",
                "ordinal_period": "PRIMER",
                "exercise_year": "PRIMER",
                "description": "Dictamen sobre salud",
                "vote_type": "EN LO GENERAL",
            },
            "test-model",
        )
        record = json.loads(body["input"][1]["content"])
        self.assertEqual(record["description"], "Dictamen sobre salud")
        self.assertEqual(record["vote_type"], "EN LO GENERAL")
        self.assertTrue(body["text"]["format"]["strict"])
        self.assertEqual(body["text"]["format"]["name"], "senado_vote_classification")

    def test_request_adds_deterministic_minuta_hint(self):
        rows = load_votes(self.db)
        body = request_body(rows[0], "test-model")
        record = json.loads(body["input"][1]["content"])
        self.assertEqual(record["origin_hint"], "minuta_de_camara_de_diputados")

    def test_request_flags_non_stage_vote_type(self):
        body = request_body(
            {
                "votacion_id": 42,
                "legislature": 66,
                "description": "Dictamen sobre salud",
                "vote_type": "Dictamen sobre desarrollo rural",
            },
            "test-model",
        )
        record = json.loads(body["input"][1]["content"])
        self.assertIn("source_warning", record)
        self.assertEqual(record["vote_type"], "")
        self.assertEqual(record["additional_matter"], "Dictamen sobre desarrollo rural")

    def test_prepare_is_local_and_writes_one_request(self):
        out = self.root / "output"
        prepare(argparse.Namespace(db=self.db, out_dir=out, model="test-model"))
        lines = (out / "requests.jsonl").read_text(encoding="utf-8").splitlines()
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["custom_id"], "42")
        self.assertEqual(manifest["prompt_version"], PROMPT_VERSION)
        self.assertEqual(manifest["request_count"], 1)

    def test_apply_validated_reviewed_csv(self):
        csv_path = self.root / "classifications.csv"
        fields = [
            "votacion_id", "origen", "etapa_votacion", "tipo_instrumento",
            "tema_politica", "confianza", "requiere_revision", "evidencia", "parse_error",
        ]
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerow({
                "votacion_id": "42",
                "origen": "minuta_de_camara_de_diputados",
                "etapa_votacion": "en_lo_general",
                "tipo_instrumento": "legislativo",
                "tema_politica": "salud",
                "confianza": "0.91",
                "requiere_revision": "false",
                "evidencia": "Minuta en materia de salud.",
                "parse_error": "",
            })
        apply(argparse.Namespace(db=self.db, csv_path=csv_path, model="test-model"))
        with sqlite3.connect(self.db) as conn:
            row = conn.execute(
                """
                SELECT origen, tema_politica, confianza, requiere_revision,
                       model, prompt_version
                FROM fact_senado_vote_classification WHERE votacion_id = 42
                """
            ).fetchone()
        self.assertEqual(row, (
            "minuta_de_camara_de_diputados", "salud", 0.91, 0,
            "test-model", PROMPT_VERSION,
        ))

    def test_apply_rejects_minuta_origin_violation(self):
        csv_path = self.root / "bad_classifications.csv"
        fields = [
            "votacion_id", "origen", "etapa_votacion", "tipo_instrumento",
            "tema_politica", "confianza", "requiere_revision", "evidencia", "parse_error",
        ]
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerow({
                "votacion_id": "42",
                "origen": "dictamen_de_comisiones",
                "etapa_votacion": "en_lo_general",
                "tipo_instrumento": "legislativo",
                "tema_politica": "salud",
                "confianza": "0.95",
                "requiere_revision": "false",
                "evidencia": "Dictamen en materia de salud.",
                "parse_error": "",
            })
        with self.assertRaisesRegex(SystemExit, "origen must be minuta"):
            apply(argparse.Namespace(db=self.db, csv_path=csv_path, model="test-model"))

    def test_review_writes_separate_audited_csv(self):
        source = self.root / "classifications.csv"
        output = self.root / "classifications_reviewed.csv"
        fields = [
            "votacion_id", "origen", "etapa_votacion", "tipo_instrumento",
            "tema_politica", "confianza", "requiere_revision", "evidencia", "parse_error",
        ]
        with source.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for vote_id in sorted(AUDITED_OVERRIDES):
                writer.writerow({
                    "votacion_id": vote_id,
                    "origen": "dictamen_de_comisiones",
                    "etapa_votacion": "en_lo_particular",
                    "tipo_instrumento": "legislativo",
                    "tema_politica": "otro",
                    "confianza": "0.9",
                    "requiere_revision": "false",
                    "evidencia": "Salida del modelo.",
                    "parse_error": "",
                })
        review(argparse.Namespace(csv_path=source, output_path=output))
        with output.open(encoding="utf-8") as handle:
            reviewed = {int(r["votacion_id"]): r for r in csv.DictReader(handle)}
        self.assertEqual(reviewed[4912]["tema_politica"], "economia_e_industria")
        self.assertIn("Revisión auditada", reviewed[4912]["evidencia"])


if __name__ == "__main__":
    unittest.main()
