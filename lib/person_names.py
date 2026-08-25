"""Pure helpers for displaying and matching person names across data sources."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Sequence


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


def _pair_initials(left: Sequence[str], right: Sequence[str]) -> bool:
    """Can every leftover token pair as an initial standing for a full token?

    Backtracking rather than a greedy sweep: with two leftovers per side a greedy
    pass can consume the wrong pair first and report a false miss. Name tails are
    tiny, so the search space never matters.
    """
    if not left:
        return not right
    head, rest = left[0], left[1:]
    for index, other in enumerate(right):
        initial_match = (len(head) == 1 and other.startswith(head)) or (
            len(other) == 1 and head.startswith(other)
        )
        if initial_match and _pair_initials(rest, [*right[:index], *right[index + 1 :]]):
            return True
    return False


def tokens_match_with_initials(left: Sequence[str], right: Sequence[str]) -> bool:
    """Token-set equality once a single letter may stand for a full given name.

    The Gaceta abbreviates trailing given names ("José G." for "José Guadalupe")
    while the chamber directories spell them out. That is the same person, but
    the token sets differ, and the Jaccard fallback scores it around 0.6 — under
    any threshold loose enough to be safe. Matching the initial explicitly is
    both stricter and more accurate than lowering that threshold.
    """
    shared = Counter(left) & Counter(right)
    left_rest = sorted((Counter(left) - shared).elements())
    right_rest = sorted((Counter(right) - shared).elements())
    if len(left_rest) != len(right_rest):
        return False
    return _pair_initials(left_rest, right_rest)


def match_person_name(query: object, candidates: Iterable[object]) -> tuple[str | None, str]:
    """Match differently ordered names without accepting ambiguous fuzzy hits.

    Exact token-set matches handle the INE ``given names + surnames`` order
    versus the Gaceta ``surnames + given names`` order. Abbreviated given names
    are then resolved against their initial, and a conservative Jaccard fallback
    covers omitted middle names. Each looser tier must resolve to exactly one
    candidate; ambiguity stops the search instead of falling through to a tier
    that would guess.
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

    by_initials = [
        candidate
        for candidate in candidate_names
        if tokens_match_with_initials(person_name_tokens(candidate), query_tokens)
    ]
    if len(by_initials) == 1:
        return by_initials[0], "initials"
    if by_initials:
        return None, "ambiguous"

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
