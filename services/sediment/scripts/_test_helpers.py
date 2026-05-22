"""Shared helpers for smoke / accuracy / validator scripts.

Single purpose: make sure any conversation created by a non-user script
carries a title prefix that the sidebar's `list_convs` filter hides by
default. Without this, every smoke run pollutes the user-facing sidebar
and we end up writing cleanup_test_conversations.py (which we did, once).

Usage:

    from scripts._test_helpers import smoke_conv_title

    # In a smoke or accuracy script:
    title = smoke_conv_title("kids-edu-readme-check")
    # → "test:kids-edu-readme-check"
    # The "test:" prefix matches _TEST_TITLE_PATTERNS in conversations.py,
    # so this conv won't appear in any user's sidebar by default.

Per sediment#46. The "test:" prefix is the canonical marker — adding new
patterns to _TEST_TITLE_PATTERNS is a code-change; using this helper is
a convention with zero filter coupling.
"""
from __future__ import annotations


_PREFIX = "test:"


def smoke_conv_title(base: str) -> str:
    """Return a conversation title that the sidebar filter will hide.

    `base` should be human-readable so a developer scanning DB rows can
    tell which script created it (`test:kids-edu-smoke`, `test:freshness-accuracy-2026-05-22`).

    Idempotent: if `base` already starts with the prefix, returns it
    unchanged.
    """
    if base.startswith(_PREFIX):
        return base
    return f"{_PREFIX}{base}"
