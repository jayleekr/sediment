"""Tenant-scoped retrieval vocabulary — the data that used to be hardcoded.

sediment#139.

Retrieval ranking boosts on keyword matches: a query mentioning "칼럼" should
favour ``artifacts.type = 'column'``; one mentioning "동아일보" should favour
refs under ``donga``. Those mappings lived as Python dicts in
``lab_lib/search_utils.py`` — and, verbatim, again in ``routers/library.py``.

Every term in them is a HypeProof-workspace proper noun. Sediment is
multi-tenant, so for any other tenant they are noise at best and a wrong-repo
pull at worst, and adding a second tenant's vocabulary meant editing Python and
deploying. The terms are now rows in ``tenant_aliases`` (migration 006); this
module loads them and does the matching.

What did NOT move: the multipliers (3x type, 2x ref-prefix, 0.8x demote). Those
are retrieval tuning constants, not tenant vocabulary — see the callers.

Loading is cached per tenant for ``CACHE_TTL_SECONDS``. Retrieval runs this on
every query, and the table changes at human speed; a stale boost for up to a
minute is not a correctness problem, an extra round trip per search is a
latency one (cf. sediment#58).
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import text

CACHE_TTL_SECONDS = 60.0

_TOKEN_RE = re.compile(r"[A-Za-z0-9가-힣]+")


@dataclass(frozen=True)
class AliasIndex:
    """One tenant's retrieval vocabulary, ready to match against."""

    #: alias token → artifacts.type
    type_hints: dict[str, str] = field(default_factory=dict)
    #: alias token → substring of artifacts.ref
    ref_prefix_hints: dict[str, str] = field(default_factory=dict)
    #: ref prefixes to demote; no query token involved
    demote_ref_prefixes: tuple[str, ...] = ()

    def detect_type(self, q: str) -> Optional[str]:
        """artifacts.type implied by the query, or None.

        Token-based, NOT substring — matching "칼럼" inside "칼럼이나"
        ("column-or") used to trigger the column boost on queries that wanted
        anything but columns.

        Dict order decides ties. Rows arrive ordered by (confidence DESC,
        alias) so a curated alias beats a learned one deterministically.
        """
        tokens = set(_TOKEN_RE.findall(q.lower()))
        for alias, target in self.type_hints.items():
            if alias in tokens:
                return target
        return None

    def detect_ref_prefix(self, q: str) -> str:
        """Substring of ``artifacts.ref`` implied by the query, or ''.

        Substring matching (not token) is deliberate here and predates this
        module: project nicknames get glued to Korean particles ("동아일보의")
        and the ref they point at is not a natural-language token anyway.
        """
        ql = q.lower()
        for alias, target in self.ref_prefix_hints.items():
            if alias in ql:
                return target
        return ""


#: A tenant with no configured vocabulary. This — not the old HypeProof maps —
#: is what an unconfigured tenant now gets: no boosts, pure BM25/vector.
EMPTY_INDEX = AliasIndex()


# tenant_id → (expires_at_monotonic, index)
_cache: dict[str, tuple[float, AliasIndex]] = {}


def invalidate_cache(tenant_id: str | None = None) -> None:
    """Drop cached vocabulary. Call after writing aliases; tests use it too."""
    if tenant_id is None:
        _cache.clear()
    else:
        _cache.pop(str(tenant_id), None)


def build_index(rows) -> AliasIndex:
    """Build an index from ``(alias, target_kind, target_value)`` rows."""
    type_hints: dict[str, str] = {}
    ref_hints: dict[str, str] = {}
    demote: list[str] = []
    for alias, kind, target in rows:
        if kind == "type":
            type_hints.setdefault(alias.lower(), target)
        elif kind == "ref_prefix":
            ref_hints.setdefault(alias.lower(), target)
        elif kind == "demote_ref_prefix":
            demote.append(target)
        # 'entity' rows are reserved for the entity pages of sediment#140/#141
        # and are deliberately ignored by retrieval today.
    return AliasIndex(
        type_hints=type_hints,
        ref_prefix_hints=ref_hints,
        demote_ref_prefixes=tuple(demote),
    )


async def load_alias_index(session, tenant_id: str) -> AliasIndex:
    """Load (and cache) a tenant's retrieval vocabulary.

    Never raises: a missing table (cluster not yet migrated) or an unreadable
    one degrades to ``EMPTY_INDEX``. Losing boosts costs ranking quality;
    raising here would cost the user their search results entirely.
    """
    key = str(tenant_id)
    now = time.monotonic()
    cached = _cache.get(key)
    if cached and cached[0] > now:
        return cached[1]

    try:
        r = await session.execute(text("""
            SELECT alias, target_kind, target_value
            FROM tenant_aliases
            WHERE tenant_id = CAST(:tid AS uuid)
            ORDER BY confidence DESC, alias
        """), {"tid": key})
        index = build_index([(row[0], row[1], row[2]) for row in r])
    except Exception:
        # Deliberately broad: this runs inside the hot retrieval path and its
        # failure mode must be "unranked results", never "no results".
        import logging
        logging.getLogger("lab_lib.aliases").warning(
            "alias_index.load_failed — falling back to EMPTY_INDEX",
            exc_info=True,
        )
        index = EMPTY_INDEX

    _cache[key] = (now + CACHE_TTL_SECONDS, index)
    return index


def demote_case_sql(index: AliasIndex, alias: str = "a",
                    multiplier: float = 0.8,
                    param_prefix: str = "demote") -> tuple[str, dict]:
    """SQL factor demoting meta docs, plus the params to bind.

    Returns ``("1.0", {})`` when the tenant configured no demotions — the
    factor disappears from the expression rather than becoming a no-op CASE.

    Params are bound individually (``:demote_0``, ``:demote_1``, …) instead of
    passed as an array: the list is short, and an explicit bind per prefix
    keeps asyncpg from having to infer a text[] type inside an arithmetic
    expression.
    """
    prefixes = index.demote_ref_prefixes
    if not prefixes:
        return "1.0", {}
    params = {f"{param_prefix}_{i}": f"{p}%" for i, p in enumerate(prefixes)}
    conds = " OR ".join(
        f"{alias}.ref LIKE CAST(:{param_prefix}_{i} AS text)" for i in range(len(prefixes))
    )
    return f"CASE WHEN {conds} THEN {multiplier} ELSE 1.0 END", params
