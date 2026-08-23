"""Compatibility shim: person-name helpers now live in the ingestion layer.

These functions were always warehouse logic -- four ingestion modules import
them to decide whether two spellings are the same human -- but they lived under
`ui/`, so the data layer depended on the presentation layer. They moved to
`ingestion/person_names.py`; this re-export keeps the Streamlit modules and
`aux_scripts/` working unchanged.

Import from `ingestion.person_names` in new code.
"""

from ingestion.person_names import (  # noqa: F401
    display_person_name,
    match_person_name,
    person_name_similarity,
    person_name_tokens,
    tokens_match_with_initials,
)

__all__ = [
    "display_person_name",
    "match_person_name",
    "person_name_similarity",
    "person_name_tokens",
    "tokens_match_with_initials",
]
