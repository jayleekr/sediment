"""Pure path helpers for vault ingest (type detection + exclude rules).

Lives in lab_lib so anything in the running app can import these without
pulling in scripts.ingest_repo, which raises SystemExit at module import
when SEDIMENT_VAULT_ROOT is unset and no vault is on disk — true inside
the Fly container (vault lives in a SEPARATE repo, hypeprooflab). The
webhook endpoint needs only these pure helpers; importing the heavier
CLI module crashes it.

KEEP IN SYNC with scripts/ingest_repo.py (TYPE_MAP +
_INGEST_EXCLUDE_PREFIXES + _INGEST_EXCLUDE_BASENAMES). If those move or
become a settings table later, this file follows.
"""
from __future__ import annotations

# Map directory prefix → artifact type. Matches scripts/ingest_repo.py.
TYPE_MAP: dict[str, str] = {
    "research/daily": "research",
    "research/columns": "research",
    "web/src/content/columns/ko": "column",
    "web/src/content/columns/en": "column",
    "web/src/content/research": "research",
    "web/src/content/novels": "novel",
    "novels": "novel",
    "products": "note",
    "docs": "note",
}

# Internal/generated dirs to drop (path-parts prefix match).
_INGEST_EXCLUDE_PREFIXES: tuple[tuple[str, ...], ...] = (
    ("products", "ai-curator", "output"),
    ("products", "ai-curator", "harness"),
)

# Basename prefixes to drop regardless of directory.
_INGEST_EXCLUDE_BASENAMES: frozenset[str] = frozenset([
    "SESSION_",
    "RALPH_",
])


def detect_type(rel: str) -> str:
    """Repo-relative path → artifact type ('column' | 'research' | …)."""
    for prefix, t in TYPE_MAP.items():
        if rel.startswith(prefix):
            return t
    return "note"


def is_excluded(rel_parts: tuple[str, ...]) -> bool:
    """Should this path be dropped from ingest? Operates on tuple parts."""
    for pfx in _INGEST_EXCLUDE_PREFIXES:
        if rel_parts[: len(pfx)] == pfx:
            return True
    basename = rel_parts[-1] if rel_parts else ""
    return any(basename.startswith(p) for p in _INGEST_EXCLUDE_BASENAMES)
