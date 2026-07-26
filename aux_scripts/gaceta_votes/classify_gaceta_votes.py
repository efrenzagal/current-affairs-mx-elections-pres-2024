"""Prepare, submit, retrieve, and apply LLM classifications for Gaceta votes.

The default `prepare` command is entirely local: it reads election_data.db and
writes a JSONL Batch API input file. It never reads an API key or makes a
network request. The explicit `submit` and `retrieve` commands require the
OpenAI Python SDK and OPENAI_API_KEY to be configured by the user.

Examples:
    # Safe: creates data/gaceta_vote_classification/requests.jsonl only.
    python3 aux_scripts/gaceta_votes/classify_gaceta_votes.py prepare

    # Explicitly submit the already-prepared requests to OpenAI's Batch API.
    python3 aux_scripts/gaceta_votes/classify_gaceta_votes.py submit

    # Download a completed batch and turn its structured responses into CSV.
    python3 aux_scripts/gaceta_votes/classify_gaceta_votes.py retrieve BATCH_ID

    # After reviewing classifications.csv, persist accepted results to SQLite.
    python3 aux_scripts/gaceta_votes/classify_gaceta_votes.py apply \
        data/gaceta_vote_classification/classifications.csv

The model is constrained to a fixed Spanish taxonomy. `confianza` measures
model certainty, not validated correctness; review `requiere_revision = true`
rows and evaluate a hand-labelled sample before using these labels in analysis.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = ROOT / "election_data.db"
DEFAULT_OUT_DIR = ROOT / "data" / "gaceta_vote_classification"
DEFAULT_MODEL = "gpt-5-mini"

SYSTEM_PROMPT = """Clasifica votaciones nominales de la Cámara de Diputados de México.
Usa exclusivamente el título y el contexto proporcionados. No agregues hechos
que no estén en el registro. Selecciona exactamente una etiqueta permitida para
cada campo categórico y redacta la evidencia en español.

Distinciones importantes:
- Una comisión describe el origen, no necesariamente el tema de política pública.
- Una votación de artículo reservado, adición, derogación o modificación propuesta
  es articulos_reservados_o_modificacion aunque el título mencione una ley completa.
- Si el registro dice "en lo general y en lo particular", usa
  en_lo_general_y_particular. Si indica únicamente "en lo particular", usa
  en_lo_particular.
- Una moción sobre admitir, discutir, separar o devolver un asunto es procedimental.
- Para tipo_instrumento, clasifica el acto sustantivo principal: una ratificación
  de nombramiento es nombramiento_o_ratificacion aunque formalmente adopte la
  forma de punto de acuerdo.
- Para tema_politica, clasifica la materia de fondo. En una moción sin suficiente
  información de la materia, usa no_aplica y requiere_revision en true.
- Usa no_claro y establece requiere_revision en true cuando el registro no permita
  una decisión confiable. confianza es tu certeza de 0.00 a 1.00.
- evidencia debe ser una cita breve o una paráfrasis cercana del registro que
  justifique la clasificación.
"""

CLASSIFICATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "origen": {
            "type": "string",
            "enum": [
                "dictamen_de_comision", "acuerdo_institucional", "minuta_del_senado",
                "iniciativa", "asunto_directo_del_pleno", "no_claro",
            ],
        },
        "etapa_votacion": {
            "type": "string",
            "enum": [
                "en_lo_general", "en_lo_particular", "en_lo_general_y_particular",
                "articulos_reservados_o_modificacion", "procedimental",
                "asunto_completo_o_no_especificado", "no_claro",
            ],
        },
        "tipo_instrumento": {
            "type": "string",
            "enum": [
                "legislativo", "constitucional", "presupuesto_finanzas_publicas",
                "nombramiento_o_ratificacion", "acuerdo_o_proposicion", "permiso",
                "mocion_procedimental", "otro", "no_claro",
            ],
        },
        "tema_politica": {
            "type": "string",
            "enum": [
                "finanzas_publicas", "justicia_y_seguridad", "salud", "educacion",
                "medio_ambiente", "trabajo_y_seguridad_social", "gobernacion_y_elecciones",
                "relaciones_exteriores", "economia_e_industria", "infraestructura_y_transporte",
                "agricultura_y_desarrollo_rural", "derechos_humanos_e_igualdad",
                "cultura_y_deporte", "energia", "administracion_publica",
                "organizacion_y_regimen_del_congreso", "desarrollo_social_y_vivienda",
                "otro", "no_aplica", "no_claro",
            ],
        },
        "confianza": {"type": "number", "minimum": 0, "maximum": 1},
        "requiere_revision": {"type": "boolean"},
        "evidencia": {"type": "string", "description": "Justificación breve en español."},
    },
    "required": [
        "origen", "etapa_votacion", "tipo_instrumento", "tema_politica", "confianza",
        "requiere_revision", "evidencia",
    ],
}


def load_votes(db_path: Path) -> list[dict[str, Any]]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT gaceta_vote_id, legislature, vote_date, title, vote_context
            FROM dim_gaceta_vote
            ORDER BY legislature, vote_date, gaceta_vote_id
        """).fetchall()
    return [dict(row) for row in rows]


def request_body(row: dict[str, Any], model: str) -> dict[str, Any]:
    record = {
        "gaceta_vote_id": row["gaceta_vote_id"],
        "legislature": row["legislature"],
        "vote_date": row["vote_date"],
        "title": row["title"] or "",
        "vote_context": row["vote_context"] or "",
    }
    return {
        "model": model,
        "input": [
            {"role": "developer", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(record, ensure_ascii=False)},
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "gaceta_vote_classification",
                "strict": True,
                "schema": CLASSIFICATION_SCHEMA,
            }
        },
    }


def prepare(args: argparse.Namespace) -> None:
    rows = load_votes(args.db)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    requests_path = args.out_dir / "requests.jsonl"
    manifest_path = args.out_dir / "manifest.json"

    with requests_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            request = {
                "custom_id": row["gaceta_vote_id"],
                "method": "POST",
                "url": "/v1/responses",
                "body": request_body(row, args.model),
            }
            handle.write(json.dumps(request, ensure_ascii=False) + "\n")

    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "db_path": str(args.db),
        "model": args.model,
        "request_count": len(rows),
        "requests_path": str(requests_path),
        "prompt_version": "v1",
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Prepared {len(rows):,} local Batch API requests: {requests_path}")
    print("No API request was made.")


def client() -> Any:
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is not set. No request was sent.")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise SystemExit("Install the SDK first: pip install openai") from exc
    return OpenAI()


def submit(args: argparse.Namespace) -> None:
    requests_path = args.out_dir / "requests.jsonl"
    if not requests_path.exists():
        raise SystemExit(f"Missing {requests_path}. Run prepare first.")
    api = client()
    with requests_path.open("rb") as handle:
        uploaded = api.files.create(file=handle, purpose="batch")
    batch = api.batches.create(
        input_file_id=uploaded.id,
        endpoint="/v1/responses",
        completion_window="24h",
        metadata={"job": "gaceta_vote_classification", "model": args.model},
    )
    (args.out_dir / "batch.json").write_text(batch.model_dump_json(indent=2), encoding="utf-8")
    print(f"Submitted batch {batch.id}. Its status is {batch.status}.")


def output_text(batch_line: dict[str, Any]) -> str:
    body = batch_line.get("response", {}).get("body", {})
    if isinstance(body.get("output_text"), str):
        return body["output_text"]
    for item in body.get("output", []):
        for content in item.get("content", []):
            if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                return content["text"]
    raise ValueError("No structured output text found in completed response")


def retrieve(args: argparse.Namespace) -> None:
    api = client()
    batch = api.batches.retrieve(args.batch_id)
    (args.out_dir / "batch.json").write_text(batch.model_dump_json(indent=2), encoding="utf-8")
    print(f"Batch {batch.id}: {batch.status}")
    if batch.status != "completed":
        return
    if not batch.output_file_id:
        raise SystemExit("Completed batch has no output file.")

    raw_path = args.out_dir / "responses.jsonl"
    raw_path.write_bytes(api.files.content(batch.output_file_id).content)
    classifications_path = args.out_dir / "classifications.csv"
    failures = 0
    with raw_path.open(encoding="utf-8") as source, classifications_path.open("w", newline="", encoding="utf-8") as target:
        fields = ["gaceta_vote_id", *CLASSIFICATION_SCHEMA["properties"].keys(), "parse_error"]
        writer = csv.DictWriter(target, fieldnames=fields)
        writer.writeheader()
        for line in source:
            response = json.loads(line)
            result: dict[str, Any] = {"gaceta_vote_id": response.get("custom_id", "")}
            try:
                result.update(json.loads(output_text(response)))
                result["parse_error"] = ""
            except (ValueError, json.JSONDecodeError) as exc:
                failures += 1
                result["parse_error"] = str(exc)
            writer.writerow(result)
    print(f"Wrote {classifications_path}; parse failures: {failures}")


def apply(args: argparse.Namespace) -> None:
    if not args.csv_path.exists():
        raise SystemExit(f"Missing {args.csv_path}")
    fields = list(CLASSIFICATION_SCHEMA["properties"].keys())
    with args.csv_path.open(newline="", encoding="utf-8") as handle:
        rows = [row for row in csv.DictReader(handle) if not row.get("parse_error")]
    if not rows:
        raise SystemExit("No parseable classifications to apply.")
    with sqlite3.connect(args.db) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS fact_gaceta_vote_classification (
                gaceta_vote_id TEXT PRIMARY KEY REFERENCES dim_gaceta_vote(gaceta_vote_id),
                origen TEXT NOT NULL, etapa_votacion TEXT NOT NULL, tipo_instrumento TEXT NOT NULL,
                tema_politica TEXT NOT NULL, confianza REAL NOT NULL, requiere_revision INTEGER NOT NULL,
                evidencia TEXT NOT NULL, model TEXT NOT NULL, prompt_version TEXT NOT NULL,
                classified_at TEXT NOT NULL
            )
        """)
        conn.executemany("""
            INSERT INTO fact_gaceta_vote_classification
            (gaceta_vote_id, origen, etapa_votacion, tipo_instrumento, tema_politica, confianza,
             requiere_revision, evidencia, model, prompt_version, classified_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(gaceta_vote_id) DO UPDATE SET
              origen=excluded.origen, etapa_votacion=excluded.etapa_votacion,
              tipo_instrumento=excluded.tipo_instrumento,
              tema_politica=excluded.tema_politica,
              confianza=excluded.confianza, requiere_revision=excluded.requiere_revision,
              evidencia=excluded.evidencia, model=excluded.model,
              prompt_version=excluded.prompt_version, classified_at=excluded.classified_at
        """, [
            (
                row["gaceta_vote_id"], *(row[field] for field in fields[:4]),
                float(row["confianza"]), int(row["requiere_revision"].lower() == "true"),
                row["evidencia"], args.model, "v1", datetime.now(timezone.utc).isoformat(),
            )
            for row in rows
        ])
    print(f"Applied {len(rows):,} classifications to {args.db}")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--db", type=Path, default=DEFAULT_DB)
    result.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    result.add_argument("--model", default=DEFAULT_MODEL)
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("prepare", help="Create local JSONL requests; makes no API calls.")
    commands.add_parser("submit", help="Upload requests and create a Batch API job.")
    retrieve_parser = commands.add_parser("retrieve", help="Download a completed batch to CSV.")
    retrieve_parser.add_argument("batch_id")
    apply_parser = commands.add_parser("apply", help="Write reviewed CSV classifications to SQLite.")
    apply_parser.add_argument("csv_path", type=Path)
    return result


def main() -> None:
    args = parser().parse_args()
    if not args.db.exists():
        raise SystemExit(f"Database not found: {args.db}")
    if args.command == "prepare":
        prepare(args)
    elif args.command == "submit":
        submit(args)
    elif args.command == "retrieve":
        retrieve(args)
    elif args.command == "apply":
        apply(args)
    else:
        raise AssertionError(f"Unhandled command: {args.command}")


if __name__ == "__main__":
    main()
