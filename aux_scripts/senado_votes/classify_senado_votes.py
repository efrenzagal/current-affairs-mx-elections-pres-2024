"""Prepare, submit, retrieve, and apply LLM classifications for Senado votes.

``prepare`` is local-only: it reads ``dim_senado_vote`` and writes Batch API
JSONL. Only the explicit ``submit`` and ``retrieve`` commands use the network.

Examples::

    python3 aux_scripts/senado_votes/classify_senado_votes.py prepare
    python3 aux_scripts/senado_votes/classify_senado_votes.py submit
    python3 aux_scripts/senado_votes/classify_senado_votes.py retrieve BATCH_ID
    python3 aux_scripts/senado_votes/classify_senado_votes.py review
    python3 aux_scripts/senado_votes/classify_senado_votes.py apply \
        data/senado_vote_classification/classifications_reviewed.csv

Review ``requiere_revision = true`` rows and a hand-labelled sample before
using model-produced labels as ground truth.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = ROOT / "election_data.db"
DEFAULT_OUT_DIR = ROOT / "data" / "senado_vote_classification"
DEFAULT_MODEL = "gpt-5-mini"
PROMPT_VERSION = "senado-v2"

SYSTEM_PROMPT = """Clasifica votaciones nominales del Senado de la República de México.
Usa exclusivamente la descripción, el tipo de votación y la metadata proporcionada.
No agregues hechos que no estén en el registro. Selecciona exactamente una etiqueta
permitida para cada campo categórico y redacta la evidencia en español.

Reglas obligatorias, en orden de prioridad:
1. Si ``origin_hint`` está presente, copia exactamente ese valor en ``origen``.
   En particular, "contiene minuta" siempre significa
   minuta_de_camara_de_diputados, aunque el texto también empiece con "Dictamen".
2. Los registros en ``related_roll_calls`` son votaciones del mismo asunto.
   Conserva el mismo origen y tipo de instrumento entre ellas. Conserva también
   el mismo tema salvo que el ``vote_type`` de la votación actual identifique
   claramente una materia sustantiva distinta dentro de un paquete legislativo.
3. Si ``source_warning`` está presente, establece requiere_revision=true. Si
   además existe ``additional_matter``, clasifica la votación como un paquete
   que incluye tanto ``description`` como ``additional_matter``; no interpretes
   ``additional_matter`` como etapa. Si las materias pertenecen a categorías
   distintas, usa otro como tema.

Distinciones adicionales:
- Para dictámenes que no contienen una minuta usa dictamen_de_comisiones.
- Una comisión ayuda a identificar el origen, no necesariamente el tema público.
- ``vote_type`` suele identificar la etapa. Una votación de artículos reservados,
  adición, derogación o modificación es articulos_reservados_o_modificacion.
- "En lo general y [de] los artículos no reservados" significa una votación
  combinada y se clasifica en_lo_general_y_particular.
- Si ``vote_type`` dice "en lo general y en lo particular", usa
  en_lo_general_y_particular. Si solo dice "en lo particular", usa
  en_lo_particular.
- Una moción para admitir, discutir, separar, devolver o modificar el trámite de
  un asunto es procedimental.
- Para tipo_instrumento clasifica el acto sustantivo: una ratificación o elección
  de una persona es nombramiento_o_ratificacion aunque se formalice por acuerdo.
- Para tema_politica clasifica la materia de fondo. Si no hay materia sustantiva,
  usa no_aplica. Si el texto no basta, usa no_claro y requiere_revision en true.
- Cuando falta ``vote_type``, no inventes una etapa: usa
  asunto_completo_o_no_especificado si la descripción identifica el asunto pero
  no la fase, o no_claro cuando ni siquiera eso sea seguro.
- confianza expresa certeza entre 0.00 y 1.00; no es una probabilidad validada.
- evidencia debe ser una cita breve o paráfrasis cercana del registro.

Antes de responder, verifica literalmente: (a) que respetaste ``origin_hint``;
(b) que no confundiste la palabra "dictamen" con el origen de una minuta; y
(c) que cualquier ``source_warning`` produjo requiere_revision=true.
"""

CLASSIFICATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "origen": {
            "type": "string",
            "enum": [
                "dictamen_de_comisiones",
                "minuta_de_camara_de_diputados",
                "acuerdo_institucional",
                "iniciativa",
                "asunto_directo_del_pleno",
                "no_claro",
            ],
        },
        "etapa_votacion": {
            "type": "string",
            "enum": [
                "en_lo_general",
                "en_lo_particular",
                "en_lo_general_y_particular",
                "articulos_reservados_o_modificacion",
                "procedimental",
                "asunto_completo_o_no_especificado",
                "no_claro",
            ],
        },
        "tipo_instrumento": {
            "type": "string",
            "enum": [
                "legislativo",
                "constitucional",
                "presupuesto_finanzas_publicas",
                "nombramiento_o_ratificacion",
                "acuerdo_o_proposicion",
                "permiso",
                "mocion_procedimental",
                "otro",
                "no_claro",
            ],
        },
        "tema_politica": {
            "type": "string",
            "enum": [
                "finanzas_publicas",
                "justicia_y_seguridad",
                "salud",
                "educacion",
                "medio_ambiente",
                "trabajo_y_seguridad_social",
                "gobernacion_y_elecciones",
                "relaciones_exteriores",
                "economia_e_industria",
                "infraestructura_y_transporte",
                "agricultura_y_desarrollo_rural",
                "derechos_humanos_e_igualdad",
                "cultura_y_deporte",
                "energia",
                "administracion_publica",
                "organizacion_y_regimen_del_congreso",
                "desarrollo_social_y_vivienda",
                "otro",
                "no_aplica",
                "no_claro",
            ],
        },
        "confianza": {"type": "number", "minimum": 0, "maximum": 1},
        "requiere_revision": {"type": "boolean"},
        "evidencia": {"type": "string", "description": "Justificación breve en español."},
    },
    "required": [
        "origen",
        "etapa_votacion",
        "tipo_instrumento",
        "tema_politica",
        "confianza",
        "requiere_revision",
        "evidencia",
    ],
}

TABLE_SCHEMA = """
CREATE TABLE IF NOT EXISTS fact_senado_vote_classification (
    votacion_id       INTEGER PRIMARY KEY REFERENCES dim_senado_vote(votacion_id),
    origen            TEXT NOT NULL,
    etapa_votacion    TEXT NOT NULL,
    tipo_instrumento  TEXT NOT NULL,
    tema_politica     TEXT NOT NULL,
    confianza         REAL NOT NULL CHECK (confianza >= 0 AND confianza <= 1),
    requiere_revision INTEGER NOT NULL CHECK (requiere_revision IN (0, 1)),
    evidencia         TEXT NOT NULL,
    model             TEXT NOT NULL,
    prompt_version    TEXT NOT NULL,
    classified_at     TEXT NOT NULL
)
"""

# Human-reviewed corrections for sibling roll calls that the model classified
# inconsistently despite identical descriptions. The raw Batch API CSV remains
# untouched; ``review`` writes a separate, provenance-preserving reviewed CSV.
AUDITED_OVERRIDES: dict[int, dict[str, str]] = {
    4723: {
        "tema_politica": "no_claro",
        "requiere_revision": "true",
        "review_note": "La descripción enumera artículos constitucionales sin identificar una materia única.",
    },
    4742: {
        "tema_politica": "justicia_y_seguridad",
        "review_note": "La inimpugnabilidad y los artículos 105 y 107 corresponden al régimen de control constitucional.",
    },
    4815: {
        "tema_politica": "gobernacion_y_elecciones",
        "requiere_revision": "false",
        "review_note": "El propio título identifica el fortalecimiento de la soberanía nacional como materia de la reforma.",
    },
    4880: {
        "tipo_instrumento": "legislativo",
        "review_note": "Ambas etapas reforman la misma ley y deben conservar el mismo tipo de instrumento.",
    },
    4912: {
        "tema_politica": "economia_e_industria",
        "review_note": "Ambas etapas corresponden a la Ley en Materia de Telecomunicaciones y Radiodifusión.",
    },
    4942: {
        "tema_politica": "justicia_y_seguridad",
        "review_note": "La Ley de Amparo y los tribunales administrativos fijan la materia principal de ambas etapas.",
    },
    4972: {
        "tema_politica": "trabajo_y_seguridad_social",
        "review_note": "La comisión dictaminadora y la ley principal sitúan ambas etapas en materia laboral.",
    },
    5012: {
        "tema_politica": "derechos_humanos_e_igualdad",
        "review_note": "La igualdad y el acceso de las mujeres a una vida libre de violencia son el eje común del paquete.",
    },
    5114: {
        "origen": "no_claro",
        "requiere_revision": "true",
        "review_note": "La fuente dice proyecto de decreto, pero no identifica si proviene de dictamen, minuta o iniciativa.",
    },
}


def load_votes(db_path: Path) -> list[dict[str, Any]]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT votacion_id, legislature, vote_date, period_type,
                   ordinal_period, exercise_year, description, vote_type
            FROM dim_senado_vote
            ORDER BY legislature, vote_date, votacion_id
            """
        ).fetchall()
    result = [dict(row) for row in rows]
    related: dict[str, list[dict[str, Any]]] = {}
    for row in result:
        description = (row.get("description") or "").strip()
        if description:
            related.setdefault(description, []).append(row)
    for row in result:
        siblings = related.get((row.get("description") or "").strip(), [])
        row["related_roll_calls"] = [
            {
                "votacion_id": int(sibling["votacion_id"]),
                "vote_type": sibling.get("vote_type") or "",
            }
            for sibling in siblings
            if sibling["votacion_id"] != row["votacion_id"]
        ]
    return result


def origin_hint(description: str) -> str | None:
    """Return an unambiguous origin encoded literally in the Senate title."""
    if re.search(r"\bcontiene\s+(?:una\s+)?minuta\b", description or "", re.IGNORECASE):
        return "minuta_de_camara_de_diputados"
    return None


def source_warning(description: str, vote_type: str) -> str | None:
    """Flag known page layouts where the crawler's last line is not a vote stage."""
    value = (vote_type or "").strip()
    if not value:
        return None
    stage_like = re.match(
        r"^(?:VOTACI[ÓO]N\b|EN\s+LO\b|DE\s+LOS\s+ART[ÍI]CULOS\b|ART[ÍI]CULOS?\b)",
        value,
        re.IGNORECASE,
    )
    if stage_like:
        return None
    if re.match(r"^DICTAM(?:EN|ENES)\b", value, re.IGNORECASE):
        return (
            "vote_type contiene otro asunto incluido en la misma votación, no una etapa; "
            "trátalo como parte sustantiva adicional del paquete"
        )
    return (
        "vote_type no tiene forma reconocible de etapa y puede ser continuación o parte "
        "de la descripción; úsalo solo como contexto y requiere revisión"
    )


def request_body(row: dict[str, Any], model: str) -> dict[str, Any]:
    record = {
        "votacion_id": int(row["votacion_id"]),
        "legislature": int(row["legislature"]),
        "vote_date": row.get("vote_date"),
        "period_type": row.get("period_type"),
        "ordinal_period": row.get("ordinal_period"),
        "exercise_year": row.get("exercise_year"),
        "description": row.get("description") or "",
        "vote_type": row.get("vote_type") or "",
    }
    hint = origin_hint(record["description"])
    if hint:
        record["origin_hint"] = hint
    warning = source_warning(record["description"], record["vote_type"])
    if warning:
        record["source_warning"] = warning
        questioned_value = record["vote_type"]
        record["vote_type"] = ""
        if re.match(r"^DICTAM(?:EN|ENES)\b", questioned_value, re.IGNORECASE):
            record["additional_matter"] = questioned_value
        else:
            record["description_continuation_or_context"] = questioned_value
    if row.get("related_roll_calls"):
        record["related_roll_calls"] = row["related_roll_calls"]
    return {
        "model": model,
        "input": [
            {"role": "developer", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(record, ensure_ascii=False)},
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "senado_vote_classification",
                "strict": True,
                "schema": CLASSIFICATION_SCHEMA,
            }
        },
    }


def prepare(args: argparse.Namespace) -> None:
    rows = load_votes(args.db)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    requests_path = args.out_dir / "requests.jsonl"
    with requests_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            request = {
                "custom_id": str(row["votacion_id"]),
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
        "prompt_version": PROMPT_VERSION,
    }
    (args.out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Prepared {len(rows):,} local Senate Batch API requests: {requests_path}")
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
        metadata={"job": "senado_vote_classification", "model": args.model},
    )
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "batch.json").write_text(
        batch.model_dump_json(indent=2), encoding="utf-8"
    )
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
    args.out_dir.mkdir(parents=True, exist_ok=True)
    api = client()
    batch = api.batches.retrieve(args.batch_id)
    (args.out_dir / "batch.json").write_text(
        batch.model_dump_json(indent=2), encoding="utf-8"
    )
    print(f"Batch {batch.id}: {batch.status}")
    if batch.status != "completed":
        return
    if not batch.output_file_id:
        raise SystemExit("Completed batch has no output file.")

    raw_path = args.out_dir / "responses.jsonl"
    raw_path.write_bytes(api.files.content(batch.output_file_id).content)
    classifications_path = args.out_dir / "classifications.csv"
    failures = 0
    fields = ["votacion_id", *CLASSIFICATION_SCHEMA["properties"].keys(), "parse_error"]
    with raw_path.open(encoding="utf-8") as source, classifications_path.open(
        "w", newline="", encoding="utf-8"
    ) as target:
        writer = csv.DictWriter(target, fieldnames=fields)
        writer.writeheader()
        for line in source:
            response = json.loads(line)
            result: dict[str, Any] = {"votacion_id": response.get("custom_id", "")}
            try:
                result.update(json.loads(output_text(response)))
                result["parse_error"] = ""
            except (ValueError, json.JSONDecodeError) as exc:
                failures += 1
                result["parse_error"] = str(exc)
            writer.writerow(result)
    print(f"Wrote {classifications_path}; parse failures: {failures}")


def review(args: argparse.Namespace) -> None:
    """Apply documented human corrections without overwriting raw model output."""
    if not args.csv_path.exists():
        raise SystemExit(f"Missing {args.csv_path}")
    with args.csv_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise SystemExit("No classifications to review.")
    known_ids = {int(row["votacion_id"]) for row in rows}
    missing = sorted(set(AUDITED_OVERRIDES) - known_ids)
    if missing:
        raise SystemExit(f"Audited vote IDs missing from CSV: {missing}")

    fields = list(rows[0])
    for row in rows:
        vote_id = int(row["votacion_id"])
        override = AUDITED_OVERRIDES.get(vote_id)
        if not override:
            continue
        note = override["review_note"]
        for field, value in override.items():
            if field != "review_note":
                row[field] = value
        row["evidencia"] = f"{row['evidencia']} Revisión auditada: {note}"

    args.output_path.parent.mkdir(parents=True, exist_ok=True)
    with args.output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print(
        f"Wrote {args.output_path} with {len(AUDITED_OVERRIDES)} audited corrections."
    )


def _validated_row(row: dict[str, str], known_vote_ids: set[int]) -> tuple[Any, ...]:
    try:
        vote_id = int(row["votacion_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Invalid votacion_id") from exc
    if vote_id not in known_vote_ids:
        raise ValueError(f"Unknown Senate votacion_id: {vote_id}")

    categorical = ["origen", "etapa_votacion", "tipo_instrumento", "tema_politica"]
    for field in categorical:
        allowed = CLASSIFICATION_SCHEMA["properties"][field]["enum"]
        if row.get(field) not in allowed:
            raise ValueError(f"{vote_id}: invalid {field}: {row.get(field)!r}")
    try:
        confidence = float(row["confianza"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{vote_id}: invalid confianza") from exc
    if not 0 <= confidence <= 1:
        raise ValueError(f"{vote_id}: confianza must be between 0 and 1")
    review_text = str(row.get("requiere_revision", "")).strip().lower()
    if review_text not in {"true", "false", "1", "0"}:
        raise ValueError(f"{vote_id}: invalid requiere_revision")
    evidence = str(row.get("evidencia", "")).strip()
    if not evidence:
        raise ValueError(f"{vote_id}: evidencia is required")
    return (
        vote_id,
        row["origen"],
        row["etapa_votacion"],
        row["tipo_instrumento"],
        row["tema_politica"],
        confidence,
        int(review_text in {"true", "1"}),
        evidence,
    )


def validate_semantics(
    rows: list[dict[str, str]], source_rows: dict[int, dict[str, Any]]
) -> None:
    """Reject violations of deterministic source rules before touching SQLite."""
    errors: list[str] = []
    for row in rows:
        vote_id = int(row["votacion_id"])
        source = source_rows[vote_id]
        hint = origin_hint(source.get("description") or "")
        if hint and row.get("origen") != hint:
            errors.append(f"{vote_id}: origen must be {hint}")
        warning = source_warning(
            source.get("description") or "", source.get("vote_type") or ""
        )
        review = str(row.get("requiere_revision", "")).strip().lower()
        if warning and review not in {"true", "1"}:
            errors.append(f"{vote_id}: source_warning requires requiere_revision=true")
    if errors:
        preview = "; ".join(errors[:10])
        suffix = f"; and {len(errors) - 10} more" if len(errors) > 10 else ""
        raise ValueError(preview + suffix)


def apply(args: argparse.Namespace) -> None:
    if not args.csv_path.exists():
        raise SystemExit(f"Missing {args.csv_path}")
    with args.csv_path.open(newline="", encoding="utf-8") as handle:
        rows = [row for row in csv.DictReader(handle) if not row.get("parse_error")]
    if not rows:
        raise SystemExit("No parseable classifications to apply.")

    with sqlite3.connect(args.db) as conn:
        conn.row_factory = sqlite3.Row
        source_rows = {
            int(row["votacion_id"]): dict(row)
            for row in conn.execute(
                "SELECT votacion_id, description, vote_type FROM dim_senado_vote"
            )
        }
        known_vote_ids = set(source_rows)
        try:
            validated = [_validated_row(row, known_vote_ids) for row in rows]
            validate_semantics(rows, source_rows)
        except ValueError as exc:
            raise SystemExit(f"Classification CSV failed validation: {exc}") from exc
        conn.execute(TABLE_SCHEMA)
        classified_at = datetime.now(timezone.utc).isoformat()
        conn.executemany(
            """
            INSERT INTO fact_senado_vote_classification
                (votacion_id, origen, etapa_votacion, tipo_instrumento,
                 tema_politica, confianza, requiere_revision, evidencia,
                 model, prompt_version, classified_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(votacion_id) DO UPDATE SET
                origen=excluded.origen,
                etapa_votacion=excluded.etapa_votacion,
                tipo_instrumento=excluded.tipo_instrumento,
                tema_politica=excluded.tema_politica,
                confianza=excluded.confianza,
                requiere_revision=excluded.requiere_revision,
                evidencia=excluded.evidencia,
                model=excluded.model,
                prompt_version=excluded.prompt_version,
                classified_at=excluded.classified_at
            """,
            [
                (*row, args.model, PROMPT_VERSION, classified_at)
                for row in validated
            ],
        )
    print(f"Applied {len(validated):,} Senate classifications to {args.db}")


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
    review_parser = commands.add_parser(
        "review", help="Write a reviewed CSV with documented audited corrections."
    )
    review_parser.add_argument(
        "csv_path", type=Path, nargs="?",
        default=DEFAULT_OUT_DIR / "classifications.csv",
    )
    review_parser.add_argument(
        "--output", dest="output_path", type=Path,
        default=DEFAULT_OUT_DIR / "classifications_reviewed.csv",
    )
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
    elif args.command == "review":
        review(args)
    elif args.command == "apply":
        apply(args)
    else:
        raise AssertionError(f"Unhandled command: {args.command}")


if __name__ == "__main__":
    main()
