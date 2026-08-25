"""Audited, source-backed corrections to the LXVI Camara de Diputados record.

These rows are research findings, not derivations: each one records a decision a
human reached after reading an official document, and carries the evidence that
justifies it. Keeping them as tracked CSVs under ``data/`` — the same exception
``data/municipio_cvegeo_overrides.csv`` already uses — makes a new correction a
reviewable data change instead of an edit to a Python literal, and gives the
supporting document a column to live in.

Deliberately narrow: an override earns a row only when the automatic matchers
cannot reach the right answer from the sources they have. Loosening those
matchers instead would risk linking people who merely share a surname.

The two CSVs stay shared and carry a ``chamber`` column: they are the audit
trail a human edits and reviews, and one file per correction type keeps that
review in one place. This module reads only the ``DIP`` rows.
"""

from __future__ import annotations

import csv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

SEAT_OVERRIDES_PATH = ROOT / "data" / "audited_seat_overrides.csv"
PERSON_ALIASES_PATH = ROOT / "data" / "audited_person_aliases.csv"

CHAMBER = "DIP"


def load_seat_overrides(
    path: Path = SEAT_OVERRIDES_PATH,
) -> dict[str, dict[str, str | None]]:
    """``person_id`` -> the seat an audited document places them in.

    A post-election legal event can seat a substitute who never appears in the
    final 2024 INE integration CSV. The suplente register is the only route from
    a roll call back to a place in the hemicycle, so without these rows those
    members are unplaceable and their bench goes dark.
    """
    overrides: dict[str, dict[str, str | None]] = {}
    with path.open(encoding="utf-8-sig", newline="") as source:
        for row in csv.DictReader(source):
            if row["chamber"].strip() != CHAMBER:
                continue
            overrides[row["person_id"].strip()] = {
                "seatId": row["seat_id"].strip(),
                "sourceUrl": row["source_url"].strip() or None,
            }
    return overrides


def load_person_aliases(
    path: Path = PERSON_ALIASES_PATH,
) -> dict[str, str]:
    """``roll-call identity`` -> ``canonical identity``, for this chamber only."""
    aliases: dict[str, str] = {}
    with path.open(encoding="utf-8-sig", newline="") as source:
        for row in csv.DictReader(source):
            if row["chamber"].strip() != CHAMBER:
                continue
            aliases[row["person_id"].strip()] = row["canonical_person_id"].strip()
    return aliases
