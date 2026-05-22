"""Tests for lab_lib.sse_client.

Per sediment#48. Pure-parser tests (parse_sse_chunks) — no httpx involved.
The stream_sse wrapper is a thin layer over httpx + the parser; tested
once via an httpx-mock for the happy path, no need to re-cover the
parsing branches.
"""
from __future__ import annotations

import pytest

from lab_lib.sse_client import parse_sse_chunks, extract_v


async def _achunks(*items: str):
    """Async iter that yields each item as one chunk."""
    for s in items:
        yield s


# ---------------------------------------------------------------------------
# Single event happy path
# ---------------------------------------------------------------------------

async def test_single_event_with_json_payload():
    raw = 'event: citation\ndata: {"v": {"ref": "x.md"}}\n\n'
    seen = [x async for x in parse_sse_chunks(_achunks(raw))]
    assert seen == [("citation", {"v": {"ref": "x.md"}})]


async def test_default_event_is_message_when_no_event_line():
    raw = 'data: {"v": "hello"}\n\n'
    seen = [x async for x in parse_sse_chunks(_achunks(raw))]
    assert seen == [("message", {"v": "hello"})]


# ---------------------------------------------------------------------------
# Multi-line data:
# ---------------------------------------------------------------------------

async def test_multi_line_data_joined_with_newlines():
    raw = "event: prose\ndata: line one\ndata: line two\n\n"
    seen = [x async for x in parse_sse_chunks(_achunks(raw))]
    assert seen == [("prose", "line one\nline two")]


# ---------------------------------------------------------------------------
# [DONE] terminator
# ---------------------------------------------------------------------------

async def test_done_terminator_stops_iteration():
    raw = (
        'event: delta\ndata: {"v": "first"}\n\n'
        'data: [DONE]\n\n'
        'event: delta\ndata: {"v": "should-not-appear"}\n\n'
    )
    seen = [x async for x in parse_sse_chunks(_achunks(raw))]
    assert seen == [("delta", {"v": "first"})]


# ---------------------------------------------------------------------------
# Multiple events in one network chunk
# ---------------------------------------------------------------------------

async def test_multiple_events_in_one_chunk():
    raw = (
        'event: a\ndata: 1\n\n'
        'event: b\ndata: 2\n\n'
        'event: c\ndata: 3\n\n'
    )
    seen = [x async for x in parse_sse_chunks(_achunks(raw))]
    assert seen == [("a", 1), ("b", 2), ("c", 3)]


async def test_event_split_across_chunks():
    """Network can split a frame across multiple aiter_text() chunks.
    Parser must buffer until the next \\n\\n appears."""
    raw_parts = [
        "event: delta\nda",
        'ta: {"v": "split"}\n',
        "\n",
    ]
    seen = [x async for x in parse_sse_chunks(_achunks(*raw_parts))]
    assert seen == [("delta", {"v": "split"})]


# ---------------------------------------------------------------------------
# Non-JSON data: graceful fallback
# ---------------------------------------------------------------------------

async def test_non_json_data_returned_as_string():
    raw = "event: status\ndata: streaming\n\n"
    seen = [x async for x in parse_sse_chunks(_achunks(raw))]
    assert seen == [("status", "streaming")]


# ---------------------------------------------------------------------------
# Empty data: lines skipped
# ---------------------------------------------------------------------------

async def test_empty_data_skipped():
    raw = "event: warmup\ndata: \n\n"  # data is empty string → skip
    seen = [x async for x in parse_sse_chunks(_achunks(raw))]
    assert seen == []


# ---------------------------------------------------------------------------
# extract_v convenience
# ---------------------------------------------------------------------------

def test_extract_v_pulls_v_field():
    assert extract_v({"v": "hello", "metadata": {}}) == "hello"
    assert extract_v({"v": {"ref": "x"}}) == {"ref": "x"}


def test_extract_v_passthrough_when_no_v_key():
    assert extract_v({"foo": "bar"}) == {"foo": "bar"}
    assert extract_v("plain string") == "plain string"
    assert extract_v(42) == 42
