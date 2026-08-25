"""Canonical spellings for labels that must read the same everywhere.

None of this is chamber-specific, and none of it touches the database: these are
the vocabularies that keep one bench, one classification or one state from
reaching two front ends under two names. They live outside the chamber pipelines
because both chambers, the Streamlit app and the static web export all normalize
against the *same* table -- duplicating it per chamber is exactly how the
hemicycle legend and the vote breakdown on one page end up disagreeing.
"""

from __future__ import annotations

import re
import unicodedata


PARTY_ALIASES = {"MRN": "MORENA", "CAND_INDEPENDIENTE": "IND", "SIN GRUPO": "SG"}


def canonical_party(value: object) -> str:
    party = str(value or "").strip().upper()
    return PARTY_ALIASES.get(party, party)


# Both chambers classify against the same taxonomy but not the same spelling.
# `dictamen_de_comision(es)` is one concept written two ways, and on a page that
# lists both chambers it would otherwise render as two filter chips meaning the
# same thing. The minuta codes look like the same case and are not: each names
# the chamber a bill arrived *from*, so they stay distinct.
CLASSIFICATION_ALIASES = {
    "dictamen_de_comisiones": "dictamen_de_comision",
}


def canonical_classification_code(value: object) -> str | None:
    """Collapse spelling variants of one classification code.

    Same problem as `canonical_party` and solved in the same place: a label that
    means one thing must not reach two front ends as two things.
    """
    if not value:
        return None
    code = str(value)
    return CLASSIFICATION_ALIASES.get(code, code)


# Unlike PARTY_ALIASES (short codes only, from clean official directories),
# initiative proposer text is free-form: official full names, casing
# variants, and -- for multi-signatory initiatives -- a party abbreviation
# trailing a list of co-signer names. canonical_party_from_text() handles
# that shape; PARTY_NAME_ALIASES intentionally omits SIN GRUPO/SG, which
# never appears as a proposer's party.
PARTY_NAME_ALIASES = {
    "MORENA": "MORENA",
    "PAN": "PAN", "PARTIDO ACCION NACIONAL": "PAN",
    "PRI": "PRI", "PARTIDO REVOLUCIONARIO INSTITUCIONAL": "PRI",
    "PRD": "PRD", "PARTIDO DE LA REVOLUCION DEMOCRATICA": "PRD",
    "PVEM": "PVEM", "PARTIDO VERDE ECOLOGISTA DE MEXICO": "PVEM",
    "PARTIDO VERDE ECOLOGISTA": "PVEM", "PARTIDO VERDE": "PVEM",
    "PT": "PT", "PARTIDO DEL TRABAJO": "PT", "PARTIDO TRABAJO": "PT",
    "MC": "MC", "MOVIMIENTO CIUDADANO": "MC",
    "IND": "IND", "INDEPENDIENTE": "IND", "CAND_INDEPENDIENTE": "IND",
}


def canonical_party_from_text(value: object) -> str | None:
    """Best-effort party normalization for free-text proposer strings.

    Tries the whole string first, then each comma/semicolon-separated
    segment (handles both "NAME1, NAME2, PARTY" and "PARTY; y suscrita por
    ..." shapes). Returns None rather than guessing when nothing in the
    string matches a known party name or abbreviation -- callers should
    keep the raw text alongside this column rather than treat None as an
    error.
    """
    if not value:
        return None
    text = str(value)
    candidates = [text] + re.split(r"[,;]", text)
    for candidate in candidates:
        key = candidate.strip().upper()
        key = "".join(c for c in unicodedata.normalize("NFD", key) if unicodedata.category(c) != "Mn")
        if key in PARTY_NAME_ALIASES:
            return PARTY_NAME_ALIASES[key]
    return None


def state_key(value: object) -> str:
    text = (
        unicodedata.normalize("NFKD", str(value or ""))
        .encode("ascii", "ignore")
        .decode("ascii")
        .upper()
    )
    text = re.sub(r"[^A-Z]+", " ", text).strip()
    aliases = {
        "CDMX": "CIUDAD DE MEXICO",
        "COAHUILA": "COAHUILA DE ZARAGOZA",
        "MICHOACAN": "MICHOACAN DE OCAMPO",
        "ESTADO DE MEXICO": "MEXICO",
        "VERACRUZ": "VERACRUZ DE IGNACIO DE LA LLAVE",
    }
    return aliases.get(text, text)
