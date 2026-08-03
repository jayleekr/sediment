"""Intra-tenant visibility for artifacts — and how derived pages inherit it.

sediment#140.

The problem this exists to solve
--------------------------------
RLS enforces the TENANT boundary (``infra/init.sql`` — every tenant-scoped
table carries ``tenant_id = current_tenant_id()``). It says nothing about
boundaries *inside* a tenant. That was fine while every artifact was a raw
document ingested from one shared vault.

It stops being fine the moment Sediment synthesizes. A derived page is composed
from several sources that may not share an audience, so **synthesis is itself a
disclosure path**: a page summarizing five documents is readable by anyone who
can read the page, regardless of who could read the five.

The rule
--------
A derived page inherits the MOST RESTRICTIVE visibility among its sources.

This is deliberately conservative. Over-restricting a synthesized page is
recoverable (someone re-shares it); under-restricting is not (the content is
already out). It also has to be decided *before* derived pages accumulate —
applying it retroactively would require re-deriving every page from sources
whose visibility at synthesis time is no longer knowable.

Extending the ladder
--------------------
``VISIBILITY_LADDER`` is ordered most-restrictive first. Adding a level (say
``'group'`` between private and tenant) is a one-line change here plus the
matching CHECK constraint in a new migration — every caller goes through
``inherit_visibility`` / ``is_more_restrictive`` and needs no edit. Keep this
list in sync with ``artifacts_visibility_check`` (migration 005).
"""
from __future__ import annotations

from typing import Iterable, Optional

# Most restrictive → least restrictive. Index IS the restrictiveness rank.
VISIBILITY_LADDER: tuple[str, ...] = ("private", "tenant")

#: What an artifact gets when nobody says otherwise. Matches the column DEFAULT
#: in migration 005, so pre-#140 rows and new raw ingests behave identically.
DEFAULT_VISIBILITY = "tenant"


class UnknownVisibility(ValueError):
    """Raised for a visibility value not on the ladder.

    Fail loud rather than defaulting: a typo silently widening a page to
    tenant-wide is exactly the failure this module exists to prevent.
    """


def rank(visibility: str) -> int:
    """Restrictiveness rank — lower is more restrictive."""
    try:
        return VISIBILITY_LADDER.index(visibility)
    except ValueError as exc:
        raise UnknownVisibility(
            f"unknown visibility {visibility!r}; expected one of {VISIBILITY_LADDER}"
        ) from exc


def is_more_restrictive(a: str, b: str) -> bool:
    """True if ``a`` is strictly more restrictive than ``b``."""
    return rank(a) < rank(b)


def inherit_visibility(source_visibilities: Iterable[Optional[str]]) -> str:
    """Visibility for a derived page composed from the given sources.

    Returns the most restrictive value present. ``None`` entries are treated as
    ``DEFAULT_VISIBILITY`` (a source row predating migration 005).

    An EMPTY source set returns ``DEFAULT_VISIBILITY``, not the most restrictive
    value — a page derived from nothing is not secret, it is unsourced. Callers
    that can produce sourceless derived pages should be questioning that first.
    """
    most_restrictive: Optional[str] = None
    for raw in source_visibilities:
        value = raw or DEFAULT_VISIBILITY
        rank(value)  # validate — raises UnknownVisibility
        if most_restrictive is None or is_more_restrictive(value, most_restrictive):
            most_restrictive = value
    return most_restrictive or DEFAULT_VISIBILITY


def sql_rank_expr(expr: str) -> str:
    """SQL CASE mapping a visibility expression to its restrictiveness rank.

    Generated from :data:`VISIBILITY_LADDER` so that comparisons in SQL cannot
    drift from comparisons in Python when a level is added. Unknown/NULL maps to
    rank 0 (most restrictive) — fail-closed, matching :class:`UnknownVisibility`
    on the Python side.

    Used to express "never widen visibility" in an UPSERT without hardcoding a
    particular level::

        CASE WHEN {rank(EXCLUDED.visibility)} <= {rank(artifacts.visibility)}
             THEN EXCLUDED.visibility ELSE artifacts.visibility END
    """
    whens = " ".join(
        f"WHEN '{level}' THEN {i}" for i, level in enumerate(VISIBILITY_LADDER)
    )
    return f"(CASE {expr} {whens} ELSE 0 END)"


def visibility_filter_sql(alias: str = "a", param: str = "viewer_member_id") -> str:
    """SQL predicate restricting a read to what one member may see.

    Usage (the parameter must ALWAYS be bound, even when NULL — asyncpg cannot
    infer the type of an unbound parameter inside a CASE/OR)::

        sql += " AND " + visibility_filter_sql("a")
        params["viewer_member_id"] = viewer_member_id  # str uuid or None

    Semantics: tenant-visible rows are readable by everyone in the tenant;
    anything more restrictive is readable only by its author. A NULL viewer
    (service identity, unresolved member) therefore sees tenant rows only —
    fail-closed. Service jobs that legitimately need everything use
    ``service_session`` and skip this filter entirely.
    """
    return (
        f"({alias}.visibility = 'tenant' OR {alias}.author_id "
        f"= CAST(NULLIF(:{param}, '') AS uuid))"
    )


def viewer_member_id(identity) -> str:
    """Bindable viewer id for :func:`visibility_filter_sql`.

    Returns '' (→ SQL NULL via NULLIF) for service identities, whose synthetic
    ``member_id`` ("service:...") is not a uuid and would blow up the cast.
    """
    member_id = getattr(identity, "member_id", "") or ""
    if getattr(identity, "is_service", False):
        return ""
    return member_id
