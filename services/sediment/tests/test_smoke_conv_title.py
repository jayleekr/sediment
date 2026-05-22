"""Tests for the smoke-script title helper (sediment#46 §D).

The helper exists to prevent the kind of sidebar pollution Jay surfaced
on 2026-05-22. These tests verify:
  1. The helper adds the canonical `test:` prefix
  2. The prefix it adds matches a pattern in list_convs's filter
  3. Idempotent — wrapping an already-prefixed title doesn't double it
"""
from __future__ import annotations

from scripts._test_helpers import smoke_conv_title
from applications.sediment_platform.routers.conversations import _TEST_TITLE_PATTERNS


def test_adds_canonical_prefix():
    assert smoke_conv_title("kids-edu-smoke") == "test:kids-edu-smoke"
    assert smoke_conv_title("freshness-accuracy-research") == "test:freshness-accuracy-research"


def test_idempotent_when_already_prefixed():
    assert smoke_conv_title("test:foo") == "test:foo"
    assert smoke_conv_title("test:bar:baz") == "test:bar:baz"


def test_prefix_matches_router_filter():
    """The whole point: helper output must be hidden by list_convs default.

    If someone removes 'test:' from _TEST_TITLE_PATTERNS without updating
    this helper, smoke runs will pollute the sidebar again. This test
    fails loudly in that scenario.
    """
    title = smoke_conv_title("anything")
    assert any(title.startswith(pattern) for pattern in _TEST_TITLE_PATTERNS), \
        f"smoke title '{title}' does not match any _TEST_TITLE_PATTERNS — sidebar pollution risk"


def test_empty_base_still_safe():
    """Edge: empty base shouldn't crash; returns the bare prefix."""
    assert smoke_conv_title("") == "test:"
