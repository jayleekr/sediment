"""Provider-agnostic LLM streaming.

Three backends, one async streaming interface:

  LLM_PROVIDER=anthropic     → Anthropic API (production premium tier)
  LLM_PROVIDER=gemini        → Google Gemini API (production cheap tier)
  LLM_PROVIDER=claude_cli    → claude -p subprocess (dev, uses Jay's MAX, $0)
  LLM_PROVIDER=offline       → mock (no API call, deterministic stub)

Per-tenant override via tenant.feature_flags.llm_provider (Phase 7+).

Cost reference (per 1k query × 1k input tokens × 200 output):
  anthropic sonnet  $3/$15  ≈ $0.03/q
  gemini flash      $0.075/$0.30 ≈ $0.0006/q  (~50x cheaper)
  gemini pro        $1.25/$5   ≈ $0.012/q
  claude_cli        $0 (uses Claude Code session credit)
"""
from __future__ import annotations
import asyncio
import json
import os
import shutil
from typing import AsyncIterator, Literal, Optional

from .logging import get_logger
from .settings import settings

log = get_logger("llm")

Provider = Literal["anthropic", "gemini", "claude_cli", "offline"]


def resolve_provider(tenant_flags: Optional[dict] = None) -> Provider:
    """Resolve provider: per-tenant flag → LLM_PROVIDER env → API-key-based auto → offline.

    Note: claude_cli is NEVER auto-selected — it requires LLM_PROVIDER=claude_cli
    explicitly. Auto-picking claude_cli breaks deterministic CI (the validator
    expects predictable mock output) and leaks Jay's MAX subscription into any
    SaaS production traffic that lacks API keys (TOS violation per DECISIONS.md).
    """
    if tenant_flags and "llm_provider" in tenant_flags:
        return tenant_flags["llm_provider"]  # type: ignore[return-value]
    env_val = os.environ.get("LLM_PROVIDER", "").strip().lower()
    if env_val in {"anthropic", "gemini", "claude_cli", "offline"}:
        return env_val  # type: ignore[return-value]
    # Auto-detect from real API keys ONLY (not from claude binary presence).
    if settings.anthropic_api_key and settings.anthropic_api_key != "sk-ant-...":
        return "anthropic"
    if os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY"):
        return "gemini"
    return "offline"


# ============================================================
# Public API
# ============================================================

async def stream_chat(
    system: str,
    user: str,
    *,
    history: Optional[list[dict]] = None,
    provider: Optional[Provider] = None,
    tenant_flags: Optional[dict] = None,
    model: Optional[str] = None,
    tier: str = "default",
    max_tokens: int = 1024,
) -> AsyncIterator[str]:
    """Stream tokens from the resolved provider.

    Args:
      system: system prompt
      user:   the current user turn
      history: optional prior turns [{"role": "user"|"assistant", "content": "..."}].
               When passed, the LLM receives them BEFORE the current user turn,
               enabling multi-turn context (e.g. anaphora like "그것", "아니"
               correctly bound to the referent from prior turns).
      tier:   "heavy" → settings.llm_model_heavy (chat answer composition —
              must refuse to fabricate, cite faithfully; the product's core).
              "default" → settings.llm_model_default (cheap path).
              An explicit `model=` arg overrides the tier.

    Yields plain text chunks. Caller can re-emit via SSE.
    """
    p = provider or resolve_provider(tenant_flags)
    if model is None:
        # Provider-aware tier resolution — a Claude model name passed to the
        # Gemini API (or vice-versa) is a hard error. Pick from the matching
        # provider's tier pair.
        if p == "gemini":
            model = settings.gemini_model_heavy if tier == "heavy" else settings.gemini_model_default
        else:  # anthropic / claude_cli
            model = settings.llm_model_heavy if tier == "heavy" else settings.llm_model_default
    log.info("llm.stream.start", provider=p, model=model, tier=tier,
             system_len=len(system), user_len=len(user),
             history_turns=len(history) if history else 0)

    if p == "anthropic":
        async for tok in _stream_anthropic(system, user, model, max_tokens, history): yield tok
    elif p == "gemini":
        async for tok in _stream_gemini(system, user, model, max_tokens, history): yield tok
    elif p == "claude_cli":
        async for tok in _stream_claude_cli(system, user, model, max_tokens): yield tok
    else:
        async for tok in _stream_offline(system, user): yield tok


# ============================================================
# Anthropic
# ============================================================

async def _stream_anthropic(system: str, user: str, model: Optional[str],
                             max_tokens: int,
                             history: Optional[list[dict]] = None) -> AsyncIterator[str]:
    if not settings.anthropic_api_key or settings.anthropic_api_key == "sk-ant-...":
        log.warning("llm.anthropic.no_key — falling back to offline")
        async for t in _stream_offline(system, user): yield t
        return
    from anthropic import AsyncAnthropic
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    m = model or settings.llm_model_default
    # Anthropic requires alternating user/assistant. We trust the caller to
    # produce well-formed history (latest = assistant turn before this user
    # turn). Empty content rows are dropped to satisfy API validation.
    msgs: list[dict] = []
    for h in (history or []):
        c = (h.get("content") or "").strip()
        if c and h.get("role") in ("user", "assistant"):
            msgs.append({"role": h["role"], "content": c})
    msgs.append({"role": "user", "content": user})
    # Prompt caching: the system prompt (instructions + citation-format rules)
    # is byte-identical across every chat turn. Marking it ephemeral lets
    # Anthropic serve it from cache — ~90% input-token discount on hits, 5min
    # TTL (refreshed by each request within the window). Caller-side cost on
    # the high-volume chat path drops from ~$0.05/q to ~$0.01/q at Sonnet.
    system_blocks = [{
        "type": "text",
        "text": system,
        "cache_control": {"type": "ephemeral"},
    }]
    async with client.messages.stream(
        model=m,
        max_tokens=max_tokens,
        system=system_blocks,
        messages=msgs,
    ) as stream:
        async for txt in stream.text_stream:
            yield txt


# ============================================================
# Gemini
# ============================================================

def _gemini_contents(system: str, user: str,
                      history: Optional[list[dict]]) -> list[dict]:
    """Build Gemini `contents`. Gemini has no system role — fold the system
    prompt into the first user turn. History roles map user→user,
    assistant→model. Empty turns dropped (API rejects empty parts).
    """
    contents: list[dict] = []
    first = True
    for h in (history or []):
        c = (h.get("content") or "").strip()
        role = h.get("role")
        if not c or role not in ("user", "assistant"):
            continue
        g_role = "user" if role == "user" else "model"
        text = f"{system}\n\n---\n\n{c}" if (first and g_role == "user") else c
        if first and g_role == "user":
            first = False
        contents.append({"role": g_role, "parts": [{"text": text}]})
    # Current turn. If no prior user turn carried the system prompt, prepend it.
    cur = f"{system}\n\n---\n\n{user}" if first else user
    contents.append({"role": "user", "parts": [{"text": cur}]})
    return contents


async def _stream_gemini(system: str, user: str, model: Optional[str],
                          max_tokens: int,
                          history: Optional[list[dict]] = None) -> AsyncIterator[str]:
    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        log.warning("llm.gemini.no_key — falling back to offline")
        async for t in _stream_offline(system, user): yield t
        return

    contents = _gemini_contents(system, user, history)
    try:
        # google-genai is the modern SDK (2026). Falls back to google-generativeai.
        from google import genai
        client = genai.Client(api_key=api_key)
        m = model or os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

        # gemini-2.5-pro has volatile capacity — intermittent 503 UNAVAILABLE
        # even with billing on (observed 2026-05-15). For an "instant" SoT
        # tool a raw 503 is unacceptable UX. Strategy: retry the requested
        # model twice with backoff, then fall back to gemini-2.5-flash (much
        # higher capacity, still strong) for the remaining attempts. Only
        # genuinely unrecoverable errors surface to the user.
        fallback = "gemini-2.5-flash"
        attempts = [m, m, fallback if m != fallback else m]
        last_err: Exception | None = None
        for i, mdl in enumerate(attempts):
            try:
                stream = await client.aio.models.generate_content_stream(
                    model=mdl,
                    contents=contents,
                    config={"max_output_tokens": max_tokens},
                )
                async for chunk in stream:
                    if hasattr(chunk, "text") and chunk.text:
                        yield chunk.text
                if i > 0:
                    log.warning("llm.gemini.recovered", model=mdl, attempt=i + 1)
                return
            except Exception as e:  # noqa: BLE001 — classify below
                msg = str(e)
                transient = ("503" in msg or "UNAVAILABLE" in msg
                             or "429" in msg or "RESOURCE_EXHAUSTED" in msg
                             or "overloaded" in msg.lower())
                last_err = e
                if not transient or i == len(attempts) - 1:
                    raise
                log.warning("llm.gemini.retry", model=mdl, next=attempts[i + 1],
                            attempt=i + 1, err=msg[:120])
                await asyncio.sleep(2 * (i + 1))
        if last_err:
            raise last_err
    except ImportError:
        # Older google-generativeai SDK — no multi-turn here (legacy fallback).
        try:
            import google.generativeai as genai_legacy  # type: ignore
            genai_legacy.configure(api_key=api_key)
            m = model or os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
            gmodel = genai_legacy.GenerativeModel(m, system_instruction=system)
            resp = await asyncio.to_thread(
                gmodel.generate_content, user, stream=True,
                generation_config={"max_output_tokens": max_tokens},
            )
            for chunk in resp:
                if chunk.text:
                    yield chunk.text
        except ImportError:
            log.error("llm.gemini.sdk_missing — pip install google-genai")
            async for t in _stream_offline(system, user): yield t


# ============================================================
# Claude Code CLI subprocess (dev mode — $0)
# ============================================================

async def _stream_claude_cli(system: str, user: str, model: Optional[str],
                              max_tokens: int) -> AsyncIterator[str]:
    """Spawn `claude -p` and stream its stdout.

    Requires user to be logged in to Claude Code (~/.claude/credentials).
    Cost = $0 (uses Jay's MAX subscription).
    NOT suitable for production SaaS — only dev/dogfood.
    """
    if not shutil.which("claude"):
        log.warning("llm.claude_cli.not_installed — falling back to offline")
        async for t in _stream_offline(system, user): yield t
        return

    m = model or settings.llm_model_default
    # Bundle system + user prompt
    prompt = f"<system>\n{system}\n</system>\n\n<user>\n{user}\n</user>"

    proc = await asyncio.create_subprocess_exec(
        "claude", "-p",
        "--dangerously-skip-permissions",
        "--model", m,
        "--output-format", "stream-json",
        "--verbose",  # claude CLI requires --verbose with --print + stream-json
        "--max-turns", "1",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert proc.stdin is not None and proc.stdout is not None

    proc.stdin.write(prompt.encode("utf-8"))
    await proc.stdin.drain()
    proc.stdin.close()

    # Parse stream-json output: one JSON object per line. Track whether we
    # already yielded any assistant text so the final `result` block doesn't
    # duplicate. The previous `not any(True for _ in [])` was a tautology that
    # caused double-emit.
    yielded_any = False
    async for line in proc.stdout:
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        # Claude Code stream-json format: {"type":"assistant","message":{"content":[{"type":"text","text":"..."}]}}
        if obj.get("type") == "assistant":
            content = obj.get("message", {}).get("content", [])
            for block in content:
                if block.get("type") == "text":
                    txt = block.get("text", "")
                    if txt:
                        yielded_any = True
                        yield txt
        elif obj.get("type") == "result" and obj.get("subtype") == "success":
            # Only emit final result if no incremental assistant blocks arrived
            r = obj.get("result", "")
            if r and not yielded_any:
                yield r

    await proc.wait()


# ============================================================
# Offline mock (CI-safe, no API)
# ============================================================

async def _stream_offline(system: str, user: str) -> AsyncIterator[str]:
    msg = (
        f"[offline LLM mock]\n"
        f"system_len={len(system)} user_len={len(user)}\n"
        f"Set LLM_PROVIDER=anthropic|gemini|claude_cli + matching API key.\n"
    )
    for word in msg.split(" "):
        yield word + " "
        await asyncio.sleep(0.01)
