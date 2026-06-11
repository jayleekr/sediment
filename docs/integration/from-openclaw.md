# Integrating from OpenClaw (or any chat-substrate bot)

> **Audience**: anyone whose bot/agent already lives in a chat substrate
> (Discord, Slack, Telegram, ...) and wants to add Sediment-cited Q&A as
> a tool. The most common consumer today is OpenClaw's Discord bot
> (Mother), which sits in HypeProof HQ.

## Why this doc exists

Sediment is the memory **layer**. It is NOT a Discord bot, Slack bot,
or any chat client. Earlier we briefly shipped a Sediment-side Discord
Gateway listener and immediately regretted it — two bots claiming the
same channel real estate, identity confusion, and a layering violation
(Sediment caring about Discord's Gateway protocol).

The right model: **the client owns the chat substrate; Sediment exposes
a clean Q&A surface; the client calls Sediment as one of its tools.**

```
Discord channel
    │
    ▼  user @-mentions OpenClaw's bot (Mother)
    │
OpenClaw's existing message handler
    │
    │  "is this a question Sediment can answer?"
    ▼  yes → fetch from Sediment
    │
Sediment API / CLI / MCP
    │  cited answer + 3 source refs
    ▼
OpenClaw posts back to the same Discord channel as Mother
```

OpenClaw stays in charge of Discord identity, permissions, rate-limits,
moderation. Sediment stays in charge of memory, retrieval, citations.

## Four ways to call Sediment

### 1. HTTP — `/v1/sediment/stream` (recommended for bots)

```python
# OpenClaw message handler — pseudocode
import httpx, json

SEDIMENT_BASE = "https://hypeproof-sediment.fly.dev"
SEDIMENT_TOKEN = os.environ["SEDIMENT_API_TOKEN"]  # minted once per bot identity

async def on_mention(msg):
    # 1. create a conversation (one per question is fine — disposable)
    conv = await httpx.post(f"{SEDIMENT_BASE}/api/v1/conversations",
        json={"title": f"openclaw-{msg.id}"},
        headers={"Authorization": f"Bearer {SEDIMENT_TOKEN}"}).json()
    conv_id = conv["id"]

    # 2. stream the answer + citations
    citations, answer = [], []
    async with httpx.stream("POST", f"{SEDIMENT_BASE}/v1/sediment/stream",
        json={"conv_id": conv_id, "query": msg.text_without_mention},
        headers={"Authorization": f"Bearer {SEDIMENT_TOKEN}"}) as r:
        async for frame in parse_sse(r):
            if frame.event == "citation":
                citations.append(frame.data["v"])
            elif frame.event == "delta":
                answer.append(frame.data["v"])

    # 3. post back to Discord as YOUR bot's reply
    body = "".join(answer)
    if citations:
        body += "\n📚 " + ", ".join(c["ref"] for c in citations[:3])
    await msg.reply(body)
```

**Token**: mint once via `/api/v1/auth/dev-token` (for now) or via
GitHub OAuth + admin grant (later). Tenant scoping happens server-side
based on the token's tenant — you don't pass tenant in the request.

### 2. CLI — `sediment ask`

Best if your bot runs on a machine where you can `brew install
jayleekr/sediment/sediment`:

```python
import asyncio, json

async def on_mention(msg):
    proc = await asyncio.create_subprocess_exec(
        "sediment", "ask", msg.text_without_mention,
        "--account", "openclaw-bot@example.com",
        "--format", "json",
        stdout=asyncio.subprocess.PIPE,
    )
    out, _ = await proc.communicate()
    result = json.loads(out)
    body = result["answer"]
    if result.get("citations"):
        body += "\n📚 " + ", ".join(c["ref"] for c in result["citations"][:3])
    await msg.reply(body)
```

Zero auth complexity on the bot side (CLI uses keychain). Slightly more
overhead than direct HTTP — one Python subprocess per call.

### 3. MCP — for AI agents that already speak MCP

If your bot uses Claude Code / Claude Agent SDK / any MCP-aware host:
add `sediment-mcp` as an MCP server. Tools exposed:
- `vault.search` — hybrid retrieval
- `vault.ask` — full Q&A with cited streaming
- `vault.recent` — last-N artifacts by type

See `services/sediment-mcp/README.md` or `/sediment-connect` skill.

### 4. The `decide()` hint (optional, but powerful)

If your bot wants Sediment's opinion on whether an incoming event is
"a question we should answer" before generating an answer, you can call
`decide()` over your event:

```python
from lab_lib.collection_agent import decide
from lab_lib.connectors.base import NormalizedEvent

ev = NormalizedEvent(
    source="discord", kind="message", external_id=msg.id, ts=msg.created_at,
    payload={
        "content": msg.text,
        "channel": msg.channel.name,
        "is_bot": msg.author.bot,
        "is_bot_mention": bot.user in msg.mentions,
    },
)
d = decide(ev, integration_config={"source_kind": "transcript"})
if d.reply:
    # Sediment says: yes, this looks like a question for us
    answer = await call_sediment_ask(d.reply_query)
    await msg.reply(answer)
elif d.ingest:
    # Sediment will ingest this when its discord_fetch poll runs;
    # nothing extra for OpenClaw to do
    pass
else:
    # Noise / bot self-message / etc. — skip
    pass
```

The hint already filters bot self-messages (anti-loop) and noise
channels. Reusing it means OpenClaw and Sediment agree on what "this
deserves an answer" means.

## What NOT to do

- ❌ Don't run a Sediment-named bot in the same guild that already has
  Mother. Discord allows one Gateway session per bot token; OpenClaw
  loses if Sediment also tries to listen.
- ❌ Don't post Sediment answers as "Sediment" via webhook in a channel
  where Mother answers via bot identity. Users will be confused about
  who said what.
- ❌ Don't poll Sediment from inside a Discord message handler with
  a long timeout — that blocks the Gateway thread. Use the async HTTP
  client + background task pattern.

## What about other substrates?

The same pattern works for Slack, Telegram, MS Teams, in-house apps:
- Your bot owns the substrate (auth, identity, rate-limits)
- Your bot calls Sediment via HTTP/CLI/MCP for cited answers
- Your bot posts back via the substrate's normal reply mechanism

No new connector code in Sediment per substrate — the substrate-specific
bot in YOUR code is the one that knows how to reply.

## Per-tenant auth

OpenClaw operates on behalf of one tenant (`hypeproof-lab` for the Lab
dogfood). It mints a tenant-scoped token once at startup. All Sediment
calls inherit that tenant's scope (RLS at the DB level). If OpenClaw
serves multiple tenants in the future, it mints one token per tenant
and picks based on which guild/channel the question came from.

## Reference

- API contract: `docs/design/06-retrieval-and-chat.md §6` (SSE wrapper)
- Auth: `docs/design/03-auth.md`
- `decide()` hint: `docs/design/04-collection-engine.md §5`
- CLI: `https://github.com/jayleekr/sediment-cli-releases`
- MCP: `services/sediment-mcp/`
