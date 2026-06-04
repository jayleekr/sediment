"""Tests for the smoke-script title helper (sediment#46 §D).

The helper exists to prevent the kind of sidebar pollution Jay surfaced
on 2026-05-22. These tests verify:
  1. The helper adds the canonical `test:` prefix
  2. The prefix it adds matches a pattern in list_convs's filter
  3. Idempotent — wrapping an already-prefixed title doesn't double it
"""
from __future__ import annotations

import httpx
import pytest

from scripts import _test_helpers as helpers
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


@pytest.mark.asyncio
async def test_mint_token_posts_tenant_slug():
    seen: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["json"] = request.read().decode()
        return httpx.Response(200, json={"token": "tok"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        token = await helpers._mint_token(
            client,
            "https://sediment.example",
            "jayleekr0125@gmail.com",
            "kids-edu",
        )

    assert token == "tok"
    assert seen["url"] == "https://sediment.example/api/v1/auth/dev-token"
    assert '"tenant_slug":"kids-edu"' in seen["json"].replace(" ", "")


@pytest.mark.asyncio
async def test_ask_question_uses_pre_minted_token_without_dev_token(monkeypatch):
    async def fail_mint(*_args, **_kwargs):
        raise AssertionError("_mint_token must not run when token is provided")

    async def fake_new_conv(_client, _api, token, title):
        assert token == "provided-token"
        assert title == "test:kids-edu-smoke"
        return "conv-1"

    async def fake_save_user_msg(_client, _api, token, conv_id, query):
        assert token == "provided-token"
        assert conv_id == "conv-1"
        assert query == "AI Native 마인드 7종 설명해줘"

    async def fake_stream_sse(*_args, **kwargs):
        assert kwargs["headers"] == {"Authorization": "Bearer provided-token"}
        yield "citation", {"v": {"ref": "kids_edu_vault/wiki/ai-native-assets.md"}}
        yield "delta", {"v": "answer"}

    monkeypatch.setattr(helpers, "_mint_token", fail_mint)
    monkeypatch.setattr(helpers, "_new_conv", fake_new_conv)
    monkeypatch.setattr(helpers, "_save_user_msg", fake_save_user_msg)
    monkeypatch.setattr(helpers, "stream_sse", fake_stream_sse)

    result = await helpers.ask_question(
        api="https://sediment.example",
        email="jayleekr0125@gmail.com",
        tenant_slug="kids-edu",
        token="provided-token",
        query="AI Native 마인드 7종 설명해줘",
        title_base="kids-edu-smoke",
    )

    assert result["answer"] == "answer"
    assert result["citations"] == [{"ref": "kids_edu_vault/wiki/ai-native-assets.md"}]
