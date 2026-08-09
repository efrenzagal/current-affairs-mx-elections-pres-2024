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

    # Apply deterministic checks and write review statuses locally.
    python3 aux_scripts/gaceta_votes/classify_gaceta_votes.py review

    # After resolving needs_review rows, persist the reviewed CSV to SQLite.
    python3 aux_scripts/gaceta_votes/classify_gaceta_votes.py apply \
        data/gaceta_vote_classification/classifications_reviewed.csv

The default scope is Legislature 66. Model self-confidence is intentionally
excluded. Reliability comes from literal source hints, related-roll-call
consistency, local rule checks, review statuses, evidence, and prompt lineage.
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
DEFAULT_OUT_DIR = ROOT / "data" / "gaceta_vote_classification"
DEFAULT_MODEL = "gpt-5-mini"
DEFAULT_LEGISLATURE = 66
PROMPT_VERSION = "diputados-l66-v2"

SYSTEM_PROMPT = """Clasifica votaciones nominales de la Cámara de Diputados de México.
Usa exclusivamente el título y el contexto proporcionados. No agregues hechos
que no estén en el registro. Selecciona exactamente una etiqueta permitida para
cada campo categórico y redacta la evidencia en español.

Reglas obligatorias, en orden de prioridad:
- Si ``origin_hint`` está presente, copia exactamente su valor en ``origen``.
- Si ``stage_hint`` está presente, copia exactamente su valor en
  ``etapa_votacion``. Las modificaciones propuestas o aceptadas y los artículos
  reservados siempre son articulos_reservados_o_modificacion.
- ``related_roll_calls`` contiene otras votaciones del mismo asunto. Conserva
  el mismo origen, tipo de instrumento y tema entre ellas, salvo que el contexto
  de la votación actual identifique explícitamente una materia sustantiva distinta.

Distinciones adicionales:
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
  una decisión sustentada directamente en el texto.
- evidencia debe ser una cita breve o una paráfrasis cercana del registro que
  justifique la clasificación.

Antes de responder, verifica literalmente que respetaste todos los hints y que
las etiquetas compartidas son consistentes con ``related_roll_calls``. No
produzcas ni estimes una puntuación de confianza.
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
        "requiere_revision": {"type": "boolean"},
        "evidencia": {"type": "string", "description": "Justificación breve en español."},
    },
    "required": [
        "origen", "etapa_votacion", "tipo_instrumento", "tema_politica",
        "requiere_revision", "evidencia",
    ],
}

REVIEW_STATUSES = {"rule_checked", "needs_review", "audited"}

# Source-audited Legislature 66 corrections. Entries without label changes
# record that the model value was checked against the title/context. Keeping
# these decisions in code makes reruns reproducible and reviewable.
AUDITED_OVERRIDES_L66: dict[str, dict[str, str]] = {
    "GACETA_L66_TABLA1OR1_1": {
        "tipo_instrumento": "nombramiento_o_ratificacion",
        "review_note": "La votación postula e integra la Mesa Directiva; el acto sustantivo es un nombramiento.",
    },
    "GACETA_L66_TABLA1OR1_13": {
        "etapa_votacion": "asunto_completo_o_no_especificado",
        "review_note": "La designación de cargos de la Mesa Directiva es un asunto completo, no una moción procedimental.",
    },
    "GACETA_L66_TABLA1OR1_15": {"review_note": "Áreas y empresas estratégicas: tema económico en ambas etapas."},
    "GACETA_L66_TABLA1OR1_16": {"tema_politica": "economia_e_industria", "review_note": "Áreas y empresas estratégicas: tema económico en ambas etapas."},
    "GACETA_L66_TABLA1OR1_25": {
        "etapa_votacion": "asunto_completo_o_no_especificado",
        "review_note": "La instalación del Comité de Evaluación es el acto completo del acuerdo, no una moción sobre el trámite parlamentario.",
    },
    "GACETA_L66_TABLA1OR1_26": {"tema_politica": "justicia_y_seguridad", "review_note": "La inimpugnabilidad bajo los artículos 105 y 107 corresponde al control constitucional."},
    "GACETA_L66_TABLA1OR1_27": {"tema_politica": "justicia_y_seguridad", "review_note": "La inimpugnabilidad bajo los artículos 105 y 107 corresponde al control constitucional."},
    "GACETA_L66_TABLA1OR1_29": {"tema_politica": "medio_ambiente", "review_note": "La protección y el cuidado animal fijan el tema de ambas etapas."},
    "GACETA_L66_TABLA1OR1_30": {"review_note": "La protección y el cuidado animal fijan el tema de ambas etapas."},
    "GACETA_L66_TABLA1OR1_39": {"tema_politica": "finanzas_publicas", "review_note": "La Ley Federal de Derechos es materia de ingresos públicos."},
    "GACETA_L66_TABLA1OR1_40": {"review_note": "La Ley Federal de Derechos es materia de ingresos públicos."},
    "GACETA_L66_TABLA1OR2_14": {"review_note": "La Ley de Ingresos sobre Hidrocarburos se clasifica por su acto fiscal principal."},
    "GACETA_L66_TABLA1OR2_15": {"tema_politica": "finanzas_publicas", "review_note": "La Ley de Ingresos sobre Hidrocarburos se clasifica por su acto fiscal principal."},
    "GACETA_L66_TABLA1OR2_18": {"review_note": "El título identifica fortalecimiento de la soberanía nacional."},
    "GACETA_L66_TABLA1OR2_19": {"tema_politica": "gobernacion_y_elecciones", "review_note": "El título identifica fortalecimiento de la soberanía nacional."},
    "GACETA_L66_TABLA1OR2_27": {"review_note": "El expediente de la Sección Instructora no aporta materia ni instrumento suficientes; no_claro es deliberado."},
    "GACETA_L66_TABLA1OR2_4": {
        "tipo_instrumento": "nombramiento_o_ratificacion",
        "review_note": "El pleno aprueba el listado de aspirantes insaculados para cargos judiciales; el acto sustantivo es de nombramiento.",
    },
    "GACETA_L66_TABLA1OR2_12": {
        "tipo_instrumento": "legislativo",
        "tema_politica": "cultura_y_deporte",
        "review_note": "Es un decreto de inscripción conmemorativa en el Muro de Honor, no una decisión de política exterior.",
    },
    "GACETA_L66_TABLA1OR2_34": {
        "tipo_instrumento": "nombramiento_o_ratificacion",
        "review_note": "El acuerdo cubre un cargo de la Mesa Directiva; prevalece el acto de nombramiento.",
    },
    "GACETA_L66_TABLA1EX1_14": {"review_note": "La desaparición forzada y el sistema de búsqueda se mantienen en justicia y seguridad en las cuatro etapas."},
    "GACETA_L66_TABLA1EX1_15": {"review_note": "La desaparición forzada y el sistema de búsqueda se mantienen en justicia y seguridad en las cuatro etapas."},
    "GACETA_L66_TABLA1EX1_16": {"review_note": "La desaparición forzada y el sistema de búsqueda se mantienen en justicia y seguridad en las cuatro etapas."},
    "GACETA_L66_TABLA1EX1_17": {"tema_politica": "justicia_y_seguridad", "review_note": "La desaparición forzada y el sistema de búsqueda se mantienen en justicia y seguridad en las cuatro etapas."},
    "GACETA_L66_TABLA2OR1_13": {"origen": "acuerdo_institucional", "review_note": "El estatuto interno de organización y servicio de carrera es un acuerdo institucional de la Cámara."},
    "GACETA_L66_TABLA2OR1_1": {
        "etapa_votacion": "asunto_completo_o_no_especificado",
        "review_note": "La integración de la Mesa Directiva es un asunto completo, no una moción procedimental.",
    },
    "GACETA_L66_TABLA2OR1_2": {
        "tipo_instrumento": "acuerdo_o_proposicion",
        "review_note": "Esta votación emite la convocatoria; la designación efectiva aparece en una votación posterior.",
    },
    "GACETA_L66_TABLA2OR1_28": {"tipo_instrumento": "presupuesto_finanzas_publicas", "review_note": "La reforma al IEPS es un instrumento fiscal en ambas etapas."},
    "GACETA_L66_TABLA2OR1_29": {"review_note": "La reforma al IEPS es un instrumento fiscal en ambas etapas."},
    "GACETA_L66_TABLA2OR1_72": {"review_note": "La modificación del orden del día es procedimental y no tiene tema sustantivo; no_aplica es deliberado."},
    "GACETA_L66_TABLA2OR1_80": {"review_note": "La reforma arancelaria modifica legislación comercial; se conserva legislativo en ambas etapas."},
    "GACETA_L66_TABLA2OR1_81": {"tipo_instrumento": "legislativo", "review_note": "La reforma arancelaria modifica legislación comercial; se conserva legislativo en ambas etapas."},
    "GACETA_L66_TABLA2OR2_33": {"review_note": "El límite a jubilaciones y pensiones corresponde a trabajo y seguridad social."},
    "GACETA_L66_TABLA2OR2_34": {"tema_politica": "trabajo_y_seguridad_social", "review_note": "El límite a jubilaciones y pensiones corresponde a trabajo y seguridad social."},
    "GACETA_L66_TABLA2OR2_42": {"review_note": "La comisión de Reforma Política-Electoral y los artículos reformados fijan materia de gobernación y elecciones."},
    "GACETA_L66_TABLA2OR2_43": {"tema_politica": "gobernacion_y_elecciones", "review_note": "La comisión de Reforma Política-Electoral y los artículos reformados fijan materia de gobernación y elecciones."},
}


def normalize_bill_title(title: str) -> str:
    """Collapse stage/date suffixes so sibling roll calls share one key."""
    value = re.sub(
        r"\s*<p>\s*\d{1,2}\s+de\s+\w+\s+de\s+\d{4}\s*$", "", title or "",
        flags=re.IGNORECASE,
    )
    value = re.sub(
        r"\s*\((?:en\s+lo|votaci[oó]n|art[ií]culos\s+reservados).*$", "", value,
        flags=re.IGNORECASE,
    )
    return re.sub(r"\s+", " ", value).strip().casefold()


def origin_hint(title: str, context: str) -> str | None:
    value = f"{title or ''} {context or ''}"
    if re.search(r"\bminuta\s+con\s+proyecto\b", value, re.IGNORECASE):
        return "minuta_del_senado"
    return None


def stage_hint(title: str, context: str) -> str | None:
    value = f"{title or ''} {context or ''}"
    if re.search(
        r"art[íi]culos?\s+reservados|modificaciones?\s+(?:propuestas?|aceptadas?|aprobadas?)",
        value,
        re.IGNORECASE,
    ):
        return "articulos_reservados_o_modificacion"
    if re.search(r"en\s+lo\s+general\s+y\s+en\s+lo\s+particular", value, re.IGNORECASE):
        return "en_lo_general_y_particular"
    if re.search(r"\ben\s+lo\s+particular\b", value, re.IGNORECASE):
        return "en_lo_particular"
    if re.search(r"\ben\s+lo\s+general\b", value, re.IGNORECASE):
        return "en_lo_general"
    return None


def load_votes(db_path: Path, legislature: int = DEFAULT_LEGISLATURE) -> list[dict[str, Any]]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT gaceta_vote_id, legislature, vote_date, title, vote_context
            FROM dim_gaceta_vote
            WHERE legislature = ?
            ORDER BY legislature, vote_date, gaceta_vote_id
        """, (int(legislature),)).fetchall()
    result = [dict(row) for row in rows]
    siblings: dict[str, list[dict[str, Any]]] = {}
    for row in result:
        siblings.setdefault(normalize_bill_title(row.get("title") or ""), []).append(row)
    for row in result:
        row["related_roll_calls"] = [
            {
                "gaceta_vote_id": sibling["gaceta_vote_id"],
                "vote_context": sibling.get("vote_context") or "",
            }
            for sibling in siblings[normalize_bill_title(row.get("title") or "")]
            if sibling["gaceta_vote_id"] != row["gaceta_vote_id"]
        ]
    return result


def request_body(row: dict[str, Any], model: str) -> dict[str, Any]:
    record = {
        "gaceta_vote_id": row["gaceta_vote_id"],
        "legislature": row["legislature"],
        "vote_date": row["vote_date"],
        "title": row["title"] or "",
        "vote_context": row["vote_context"] or "",
    }
    origin = origin_hint(record["title"], record["vote_context"])
    if origin:
        record["origin_hint"] = origin
    stage = stage_hint(record["title"], record["vote_context"])
    if stage:
        record["stage_hint"] = stage
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
                "name": "gaceta_vote_classification",
                "strict": True,
                "schema": CLASSIFICATION_SCHEMA,
            }
        },
    }


def prepare(args: argparse.Namespace) -> None:
    rows = load_votes(args.db, args.legislature)
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
        "legislature": args.legislature,
        "request_count": len(rows),
        "requests_path": str(requests_path),
        "prompt_version": PROMPT_VERSION,
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
    manifest_path = args.out_dir / "manifest.json"
    if not manifest_path.exists():
        raise SystemExit(f"Missing {manifest_path}. Run prepare first.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = {
        "legislature": args.legislature,
        "model": args.model,
        "prompt_version": PROMPT_VERSION,
    }
    stale = {key: (manifest.get(key), value) for key, value in expected.items()
             if manifest.get(key) != value}
    if stale:
        raise SystemExit(f"Prepared requests are stale or out of scope: {stale}. Run prepare again.")
    with sqlite3.connect(args.db) as conn:
        expected_count = conn.execute(
            "SELECT COUNT(*) FROM dim_gaceta_vote WHERE legislature = ?",
            (int(args.legislature),),
        ).fetchone()[0]
    if manifest.get("request_count") != expected_count:
        raise SystemExit(
            f"Expected {expected_count} Legislature {args.legislature} requests, "
            f"found {manifest.get('request_count')}. "
            "No request was sent."
        )
    api = client()
    with requests_path.open("rb") as handle:
        uploaded = api.files.create(file=handle, purpose="batch")
    batch = api.batches.create(
        input_file_id=uploaded.id,
        endpoint="/v1/responses",
        completion_window="24h",
        metadata={
            "job": "gaceta_vote_classification",
            "model": args.model,
            "legislature": str(args.legislature),
            "prompt_version": PROMPT_VERSION,
        },
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


def review(args: argparse.Namespace) -> None:
    """Apply literal rules and flag cross-roll-call inconsistencies locally."""
    if not args.csv_path.exists():
        raise SystemExit(f"Missing {args.csv_path}")
    with args.csv_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise SystemExit("No classifications to review.")
    parse_failures = [row.get("gaceta_vote_id", "") for row in rows if row.get("parse_error")]
    if parse_failures:
        raise SystemExit(f"Resolve {len(parse_failures)} parse failures before review.")

    with sqlite3.connect(args.db) as conn:
        conn.row_factory = sqlite3.Row
        source_rows = {
            row["gaceta_vote_id"]: dict(row)
            for row in conn.execute(
                """
                SELECT gaceta_vote_id, title, vote_context
                FROM dim_gaceta_vote WHERE legislature = ?
                """,
                (int(args.legislature),),
            )
        }
    csv_ids = {row["gaceta_vote_id"] for row in rows}
    if csv_ids != set(source_rows):
        raise SystemExit(
            f"Reviewed CSV scope mismatch: missing={len(set(source_rows) - csv_ids)}, "
            f"unknown={len(csv_ids - set(source_rows))}."
        )

    notes: dict[str, list[str]] = {vote_id: [] for vote_id in csv_ids}
    rule_corrected: set[str] = set()
    for row in rows:
        vote_id = row["gaceta_vote_id"]
        source = source_rows[vote_id]
        expected_origin = origin_hint(source.get("title") or "", source.get("vote_context") or "")
        expected_stage = stage_hint(source.get("title") or "", source.get("vote_context") or "")
        if expected_origin and row.get("origen") != expected_origin:
            notes[vote_id].append(
                f"Regla local corrigió origen: {row.get('origen')} → {expected_origin}."
            )
            row["origen"] = expected_origin
            rule_corrected.add(vote_id)
        if expected_stage and row.get("etapa_votacion") != expected_stage:
            notes[vote_id].append(
                f"Regla local corrigió etapa: {row.get('etapa_votacion')} → {expected_stage}."
            )
            row["etapa_votacion"] = expected_stage
            rule_corrected.add(vote_id)

    audited_ids: set[str] = set()
    if args.legislature == 66:
        for row in rows:
            vote_id = row["gaceta_vote_id"]
            override = AUDITED_OVERRIDES_L66.get(vote_id)
            if not override:
                continue
            audited_ids.add(vote_id)
            for field, value in override.items():
                if field != "review_note":
                    row[field] = value
            notes[vote_id].append(f"Revisión auditada: {override['review_note']}")

    by_bill: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        title = source_rows[row["gaceta_vote_id"]].get("title") or ""
        by_bill.setdefault(normalize_bill_title(title), []).append(row)
    sibling_inconsistent: set[str] = set()
    for siblings in by_bill.values():
        if len(siblings) < 2:
            continue
        for field in ("origen", "tipo_instrumento", "tema_politica"):
            values = sorted({row[field] for row in siblings})
            if len(values) > 1:
                message = f"Votaciones del mismo asunto discrepan en {field}: {', '.join(values)}."
                for row in siblings:
                    notes[row["gaceta_vote_id"]].append(message)
                    sibling_inconsistent.add(row["gaceta_vote_id"])

    output_fields = [
        key for key in rows[0].keys() if key not in {"review_status", "review_notes"}
    ] + ["review_status", "review_notes"]
    for row in rows:
        model_review = str(row.get("requiere_revision", "")).strip().lower() in {"true", "1"}
        unclear = "no_claro" in {
            row.get("origen"), row.get("etapa_votacion"),
            row.get("tipo_instrumento"), row.get("tema_politica"),
        }
        vote_id = row["gaceta_vote_id"]
        if vote_id in audited_ids and vote_id not in sibling_inconsistent:
            row["review_status"] = "audited"
        elif model_review or unclear or vote_id in rule_corrected or vote_id in sibling_inconsistent:
            row["review_status"] = "needs_review"
        else:
            row["review_status"] = "rule_checked"
        row["review_notes"] = " ".join(notes[row["gaceta_vote_id"]])

    args.output_path.parent.mkdir(parents=True, exist_ok=True)
    with args.output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=output_fields)
        writer.writeheader()
        writer.writerows(rows)
    status_counts: dict[str, int] = {}
    for row in rows:
        status_counts[row["review_status"]] = status_counts.get(row["review_status"], 0) + 1
    print(f"Wrote {args.output_path}: {status_counts}")


def _validated_row(row: dict[str, str], known_ids: set[str]) -> tuple[Any, ...]:
    vote_id = row.get("gaceta_vote_id", "")
    if vote_id not in known_ids:
        raise ValueError(f"Unknown Legislature 66 vote ID: {vote_id}")
    for field in ("origen", "etapa_votacion", "tipo_instrumento", "tema_politica"):
        allowed = CLASSIFICATION_SCHEMA["properties"][field]["enum"]
        if row.get(field) not in allowed:
            raise ValueError(f"{vote_id}: invalid {field}: {row.get(field)!r}")
    review_text = str(row.get("requiere_revision", "")).strip().lower()
    if review_text not in {"true", "false", "1", "0"}:
        raise ValueError(f"{vote_id}: invalid requiere_revision")
    if row.get("review_status") not in REVIEW_STATUSES:
        raise ValueError(f"{vote_id}: invalid or missing review_status")
    evidence = str(row.get("evidencia", "")).strip()
    if not evidence:
        raise ValueError(f"{vote_id}: evidencia is required")
    return (
        vote_id, row["origen"], row["etapa_votacion"], row["tipo_instrumento"],
        row["tema_politica"], int(review_text in {"true", "1"}), evidence,
        row["review_status"], str(row.get("review_notes", "")).strip(),
    )


def ensure_classification_table_v2(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(fact_gaceta_vote_classification)")}
    if not columns:
        conn.execute("""
            CREATE TABLE fact_gaceta_vote_classification (
                gaceta_vote_id TEXT PRIMARY KEY REFERENCES dim_gaceta_vote(gaceta_vote_id),
                origen TEXT NOT NULL, etapa_votacion TEXT NOT NULL,
                tipo_instrumento TEXT NOT NULL, tema_politica TEXT NOT NULL,
                requiere_revision INTEGER NOT NULL, evidencia TEXT NOT NULL,
                review_status TEXT NOT NULL, review_notes TEXT NOT NULL,
                model TEXT NOT NULL, prompt_version TEXT NOT NULL, classified_at TEXT NOT NULL
            )
        """)
        return
    if "confianza" not in columns and {"review_status", "review_notes"}.issubset(columns):
        return
    conn.execute("DROP TABLE IF EXISTS fact_gaceta_vote_classification_next")
    conn.execute("""
        CREATE TABLE fact_gaceta_vote_classification_next (
            gaceta_vote_id TEXT PRIMARY KEY REFERENCES dim_gaceta_vote(gaceta_vote_id),
            origen TEXT NOT NULL, etapa_votacion TEXT NOT NULL,
            tipo_instrumento TEXT NOT NULL, tema_politica TEXT NOT NULL,
            requiere_revision INTEGER NOT NULL, evidencia TEXT NOT NULL,
            review_status TEXT NOT NULL, review_notes TEXT NOT NULL,
            model TEXT NOT NULL, prompt_version TEXT NOT NULL, classified_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        INSERT INTO fact_gaceta_vote_classification_next
        SELECT gaceta_vote_id, origen, etapa_votacion, tipo_instrumento, tema_politica,
               requiere_revision, evidencia,
               CASE WHEN requiere_revision = 1 THEN 'needs_review' ELSE 'legacy_model_only' END,
               'Migrated from the legacy model-confidence workflow.',
               model, prompt_version, classified_at
        FROM fact_gaceta_vote_classification
    """)
    conn.execute("DROP TABLE fact_gaceta_vote_classification")
    conn.execute(
        "ALTER TABLE fact_gaceta_vote_classification_next "
        "RENAME TO fact_gaceta_vote_classification"
    )


def apply(args: argparse.Namespace) -> None:
    if not args.csv_path.exists():
        raise SystemExit(f"Missing {args.csv_path}")
    with args.csv_path.open(newline="", encoding="utf-8") as handle:
        rows = [row for row in csv.DictReader(handle) if not row.get("parse_error")]
    if not rows:
        raise SystemExit("No parseable classifications to apply.")
    unresolved = [
        row.get("gaceta_vote_id", "")
        for row in rows if row.get("review_status") == "needs_review"
    ]
    if unresolved and not getattr(args, "allow_needs_review", False):
        raise SystemExit(
            f"Refusing to apply {len(unresolved)} unresolved needs_review rows. "
            "Audit them and set review_status=audited, or explicitly pass "
            "--allow-needs-review to retain them as a visible review queue."
        )
    with sqlite3.connect(args.db) as conn:
        conn.row_factory = sqlite3.Row
        source_rows = {
            row["gaceta_vote_id"]: dict(row)
            for row in conn.execute(
                """
                SELECT gaceta_vote_id, title, vote_context
                FROM dim_gaceta_vote WHERE legislature = ?
                """,
                (int(args.legislature),),
            )
        }
        if {row["gaceta_vote_id"] for row in rows} != set(source_rows):
            raise SystemExit(
                f"Apply requires the complete Legislature {args.legislature} reviewed CSV."
            )
        try:
            validated = [_validated_row(row, set(source_rows)) for row in rows]
            for row in rows:
                source = source_rows[row["gaceta_vote_id"]]
                expected_origin = origin_hint(source.get("title") or "", source.get("vote_context") or "")
                expected_stage = stage_hint(source.get("title") or "", source.get("vote_context") or "")
                if expected_origin and row["origen"] != expected_origin:
                    raise ValueError(
                        f"{row['gaceta_vote_id']}: origen must be {expected_origin}"
                    )
                if expected_stage and row["etapa_votacion"] != expected_stage:
                    raise ValueError(
                        f"{row['gaceta_vote_id']}: etapa must be {expected_stage}"
                    )
        except ValueError as exc:
            raise SystemExit(f"Classification CSV failed validation: {exc}") from exc
        ensure_classification_table_v2(conn)
        classified_at = datetime.now(timezone.utc).isoformat()
        conn.executemany("""
            INSERT INTO fact_gaceta_vote_classification
            (gaceta_vote_id, origen, etapa_votacion, tipo_instrumento, tema_politica,
             requiere_revision, evidencia, review_status, review_notes,
             model, prompt_version, classified_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(gaceta_vote_id) DO UPDATE SET
              origen=excluded.origen, etapa_votacion=excluded.etapa_votacion,
              tipo_instrumento=excluded.tipo_instrumento,
              tema_politica=excluded.tema_politica,
              requiere_revision=excluded.requiere_revision,
              evidencia=excluded.evidencia, review_status=excluded.review_status,
              review_notes=excluded.review_notes, model=excluded.model,
              prompt_version=excluded.prompt_version, classified_at=excluded.classified_at
        """, [(*row, args.model, PROMPT_VERSION, classified_at) for row in validated])
    print(f"Applied {len(rows):,} classifications to {args.db}")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--db", type=Path, default=DEFAULT_DB)
    result.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    result.add_argument("--model", default=DEFAULT_MODEL)
    result.add_argument("--legislature", type=int, default=DEFAULT_LEGISLATURE)
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("prepare", help="Create local JSONL requests; makes no API calls.")
    commands.add_parser("submit", help="Upload requests and create a Batch API job.")
    retrieve_parser = commands.add_parser("retrieve", help="Download a completed batch to CSV.")
    retrieve_parser.add_argument("batch_id")
    review_parser = commands.add_parser(
        "review", help="Apply local rules and write a review-status CSV."
    )
    review_parser.add_argument(
        "csv_path", type=Path, nargs="?", default=DEFAULT_OUT_DIR / "classifications.csv"
    )
    review_parser.add_argument(
        "--output", dest="output_path", type=Path,
        default=DEFAULT_OUT_DIR / "classifications_reviewed.csv",
    )
    apply_parser = commands.add_parser("apply", help="Write reviewed CSV classifications to SQLite.")
    apply_parser.add_argument("csv_path", type=Path)
    apply_parser.add_argument(
        "--allow-needs-review", action="store_true",
        help="Apply unresolved rows while preserving their visible needs_review status.",
    )
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
