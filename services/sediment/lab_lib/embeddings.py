"""Embedding wrapper — provider-pluggable, Gemini-primary.

History note (sediment#16):
  v0 (2026-05) — OpenAI-only via text-embedding-3-small (1536d). Single
                 vendor dependency, and silent zero-vector fallback
                 broke retrieval in prod when OPENAI_API_KEY went missing.
  v1 (2026-05-22) — provider-pluggable. Gemini gemini-embedding-001 with
                 output_dimensionality=1536 is the primary; OpenAI text-
                 embedding-3-small kept as fallback for environments that
                 already have an OpenAI key. Loud first-warn on zero
                 fallback so the silent-broken-state can't recur.

Provider selection (`EMBEDDING_PROVIDER` env, default `gemini`):
  - gemini  → uses GEMINI_API_KEY, model = gemini-embedding-001 @ 1536d
  - openai  → uses OPENAI_API_KEY, model = text-embedding-3-small @ 1536d
  - zero    → returns zero vectors (offline mode for tests; logs CRITICAL once)

The output dim is fixed at 1536 across providers so the `chunks.embedding
vector(1536)` schema is provider-agnostic. Gemini's gemini-embedding-001
supports Matryoshka representation — we ask for 1536 explicitly.
"""
from __future__ import annotations
import os
from typing import Sequence
from tenacity import retry, stop_after_attempt, wait_exponential

from .logging import get_logger
from .settings import settings

log = get_logger("embeddings")

EMBEDDING_DIM = 1536

_client_gemini = None
_client_openai = None
_NO_KEY_WARN_COUNT = 0


def _provider() -> str:
    """Resolve the embedding provider. Env override beats default."""
    p = (os.environ.get("EMBEDDING_PROVIDER") or "gemini").strip().lower()
    return p if p in ("gemini", "openai", "zero") else "gemini"


def _get_gemini():
    global _client_gemini
    if _client_gemini is None:
        key = (os.environ.get("GEMINI_API_KEY") or settings.gemini_api_key or "").strip()
        if not key:
            return None
        from google import genai
        _client_gemini = genai.Client(api_key=key)
    return _client_gemini


def _get_openai():
    global _client_openai
    if _client_openai is None:
        key = (os.environ.get("OPENAI_API_KEY") or settings.openai_api_key or "").strip()
        if not key:
            return None
        from openai import OpenAI
        _client_openai = OpenAI(api_key=key)
    return _client_openai


def _zero_batch(n: int) -> list[list[float]]:
    """Zero-vector fallback. Logs LOUD the first time so the broken state
    can't be silently masked (sediment#16)."""
    global _NO_KEY_WARN_COUNT
    _NO_KEY_WARN_COUNT += 1
    if _NO_KEY_WARN_COUNT == 1:
        log.error(
            "embed.no_provider_key.CRITICAL",
            count=n,
            impact="vector arm of retrieval disabled; BM25-only fallback active",
            fix="fly secrets set GEMINI_API_KEY=... (or OPENAI_API_KEY=...) -a hypeproof-sediment",
            issue="sediment#16",
        )
    else:
        log.warning("embed.no_provider_key", count=n, total_warns=_NO_KEY_WARN_COUNT)
    return [[0.0] * EMBEDDING_DIM for _ in range(n)]


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
def _embed_batch(texts: list[str]) -> list[list[float]]:
    """Embed a batch via the configured provider. Falls back to zeros only
    if the provider's key is missing (loud warning) — NEVER on transient
    API errors (tenacity retries those)."""
    provider = _provider()

    if provider == "zero":
        return _zero_batch(len(texts))

    if provider == "gemini":
        client = _get_gemini()
        if client is None:
            return _zero_batch(len(texts))
        # gemini-embedding-001 supports Matryoshka — explicit 1536d output
        # so the schema stays vector(1536) regardless of provider.
        result = client.models.embed_content(
            model="gemini-embedding-001",
            contents=texts,
            config={"output_dimensionality": EMBEDDING_DIM},
        )
        return [list(e.values) for e in result.embeddings]

    if provider == "openai":
        client = _get_openai()
        if client is None:
            return _zero_batch(len(texts))
        resp = client.embeddings.create(
            model="text-embedding-3-small",
            input=texts,
            dimensions=EMBEDDING_DIM,
        )
        return [d.embedding for d in resp.data]

    return _zero_batch(len(texts))


def embed(texts: Sequence[str], batch_size: int = 64) -> list[list[float]]:
    if not texts:
        return []
    out: list[list[float]] = []
    chunk: list[str] = []
    for t in texts:
        chunk.append(t if t else " ")
        if len(chunk) >= batch_size:
            out.extend(_embed_batch(chunk))
            chunk = []
    if chunk:
        out.extend(_embed_batch(chunk))
    return out


def embed_one(text: str) -> list[float]:
    return embed([text])[0]
