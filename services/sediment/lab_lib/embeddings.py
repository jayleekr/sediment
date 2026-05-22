"""Embedding wrapper — OpenAI text-embedding-3-small (1536d).

In offline mode (no key), returns zero-vectors so ingest still works mechanically.
"""
from __future__ import annotations
from typing import Sequence
from tenacity import retry, stop_after_attempt, wait_exponential

from .logging import get_logger
from .settings import settings

log = get_logger("embeddings")

_client = None
_NO_KEY_WARN_COUNT = 0


def _get_client():
    global _client
    if _client is None:
        from openai import OpenAI
        _client = OpenAI(api_key=settings.openai_api_key) if settings.openai_api_key else None
    return _client


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
def _embed_batch(texts: list[str]) -> list[list[float]]:
    """Embed a batch. Falls back to zero vectors when no API key — but logs
    LOUDLY (sediment#16) because zero vectors silently break the vector arm
    of hybrid retrieval. Every ingest call without key counts; the first one
    logs at ERROR so it can't be missed in fly logs.
    """
    global _NO_KEY_WARN_COUNT
    client = _get_client()
    if client is None:
        _NO_KEY_WARN_COUNT += 1
        if _NO_KEY_WARN_COUNT == 1:
            # First-time loud alert. Vector arm of retrieval is now broken.
            log.error(
                "embed.no_api_key.CRITICAL",
                count=len(texts),
                impact="vector arm of retrieval disabled; BM25-only fallback active",
                fix="fly secrets set OPENAI_API_KEY=sk-... -a hypeproof-sediment",
                issue="sediment#16",
            )
        else:
            log.warning("embed.no_api_key", count=len(texts), total_warns=_NO_KEY_WARN_COUNT)
        return [[0.0] * settings.embedding_dim for _ in texts]
    resp = client.embeddings.create(model=settings.embedding_model, input=texts)
    return [d.embedding for d in resp.data]


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
