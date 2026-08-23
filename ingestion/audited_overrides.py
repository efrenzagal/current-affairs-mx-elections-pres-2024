"""Audited, source-backed corrections to the LXVI congressional record.

These rows are research findings, not derivations: each one records a decision a
human reached after reading an official document, and carries the evidence that
justifies it. Keeping them as tracked CSVs under ``data/`` — the same exception
``data/municipio_cvegeo_overrides.csv`` already uses — makes a new correction a
reviewable data change instead of an edit to a Python literal, and gives the
supporting document a column to live in.

Deliberately narrow: an override earns a row only when the automatic matchers
cannot reach the right answer from the sources they have. Loosening those
matchers instead would risk linking people who merely share a surname.
"""

from __future__ import annotations

import csv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

SEAT_OVERRIDES_PATH = ROOT / "data" / "audited_seat_overrides.csv"
PERSON_ALIASES_PATH = ROOT / "data" / "audited_person_aliases.csv"

CHAMBERS = ("DIP", "SEN")


def load_seat_overrides(
    path: Path = SEAT_OVERRIDES_PATH,
) -> dict[tuple[str, str], dict[str, str | None]]:
    """``(chamber, person_id)`` -> the seat an audited document places them in.

    A post-election legal event can seat a substitute who never appears in the
    final 2024 INE integration CSV. The suplente register is the only route from
    a roll call back to a place in the hemicycle, so without these rows those
    members are unplaceable and their bench goes dark.
    """
    overrides: dict[tuple[str, str], dict[str, str | None]] = {}
    with path.open(encoding="utf-8-sig", newline="") as source:
        for row in csv.DictReader(source):
            key = (row["chamber"].strip(), row["person_id"].strip())
            overrides[key] = {
                "seatId": row["seat_id"].strip(),
                "sourceUrl": row["source_url"].strip() or None,
            }
    return overrides


def load_person_aliases(
    path: Path = PERSON_ALIASES_PATH,
) -> dict[str, dict[str, str]]:
    """``chamber`` -> ``{roll-call identity: canonical identity}``.

    Every chamber in `CHAMBERS` is always present, so a caller can index the
    result without first checking whether that chamber has any aliases yet.
    """
    aliases: dict[str, dict[str, str]] = {chamber: {} for chamber in CHAMBERS}
    with path.open(encoding="utf-8-sig", newline="") as source:
        for row in csv.DictReader(source):
            chamber = row["chamber"].strip()
            aliases.setdefault(chamber, {})[row["person_id"].strip()] = row[
                "canonical_person_id"
            ].strip()
    return aliases
