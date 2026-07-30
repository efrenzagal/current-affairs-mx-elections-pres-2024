"""Pure helpers for displaying and matching person names across data sources."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable


_LOWERCASE_PARTICLES = {"de", "del", "la", "las", "los", "y"}


def display_person_name(value: object) -> str:
    """Use consistent title case while retaining Spanish name particles."""
    words = str(value or "").strip().split()
    displayed = []
    for index, word in enumerate(words):
        titled = word.title()
        if index > 0 and titled.lower() in _LOWERCASE_PARTICLES:
            titled = titled.lower()
        displayed.append(titled)
    return " ".join(displayed)


def person_name_tokens(value: object) -> tuple[str, ...]:
    """Return accent-free, order-independent tokens for cross-source matching."""
    ascii_name = (
        unicodedata.normalize("NFKD", str(value or ""))
        .encode("ascii", "ignore")
        .decode("ascii")
        .upper()
    )
    tokens = re.findall(r"[A-Z]+", ascii_name)
    # The Gaceta frequently abbreviates María as “M.” or “Ma.”.
    tokens = ["MARIA" if token in {"M", "MA"} else token for token in tokens]
    return tuple(sorted(tokens))


def person_name_similarity(left: object, right: object) -> float:
    """Jaccard similarity of normalized, order-independent name tokens."""
    left_tokens = set(person_name_tokens(left))
    right_tokens = set(person_name_tokens(right))
    union = left_tokens | right_tokens
    return len(left_tokens & right_tokens) / len(union) if union else 0.0


def match_person_name(query: object, candidates: Iterable[object]) -> tuple[str | None, str]:
    """Match differently ordered names without accepting ambiguous fuzzy hits.

    Exact token-set matches handle the INE ``given names + surnames`` order
    versus the Gaceta ``surnames + given names`` order. A conservative Jaccard
    fallback covers initials and omitted middle names.
    """
    candidate_names = [str(candidate) for candidate in candidates]
    query_tokens = person_name_tokens(query)
    if not query_tokens:
        return None, "none"

    exact = [
        candidate
        for candidate in candidate_names
        if person_name_tokens(candidate) == query_tokens
    ]
    if exact:
        return exact[0], "exact"

    scored = []
    for candidate in candidate_names:
        score = person_name_similarity(query, candidate)
        scored.append((score, candidate))
    scored.sort(reverse=True)
    if not scored:
        return None, "none"

    best_score, best_candidate = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else 0.0
    if best_score >= 0.67 and best_score - second_score >= 0.15:
        return best_candidate, "approximate"
    return None, "none"
