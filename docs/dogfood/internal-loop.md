# Internal dogfood loop — 정리 → 인용가능 vault (turnkey for the 5/28 session)

> Scope: **internal members only**, not public/egress/GATE-B (per 2026-05-19
> plan). This is the GATE-A lever: make the team actually depend on Sediment
> by making "왜 이렇게 결정했나" answerable.

## The loop (plan Diagram 3), and what was broken

```
Discord (#weekly 미팅노트 · #daily-research · #인사이트-공유)   conversations
        │  Mother capture cron → events (discord_ingest.py)        │
        └──────────────┬───────────────────────────────────────────┘
                        ▼
              distill.py  ── reuses consolidate_memory _extract (Anthropic
              ("정리")        tool-use) → {decision, why, action}
                        ▼
        ┌───────────────┴────────────────┐
        ▼                                  ▼
  decisions table              decision markdown  ── POST /v1/ingest/document
  (+ source_artifact_id ◄───── (type=decision,    → chunk+embed+upsert
   linked back)                  ref=decision/<slug>)        ▼
                                                       chunks ⨝ artifacts
                                                       = RAG-retrievable & CITABLE
```

**Two breaks distill.py closes** (were structural, not config):
1. `consolidate_memory.py` only read `conversations` → Discord #weekly notes
   were never distilled. distill.py adds the `events` source.
2. `decisions` table was invisible to RAG (retriever reads only
   chunks⨝artifacts). distill.py lands each decision as a `type=decision`
   artifact (idempotent by `ref=decision/<topic-slug>` = vault-differ
   new/update/known) and links `decisions.source_artifact_id`.

## Run (5/28 dedicated session — turnkey)

```bash
cd services/sediment
# 0. offline sanity (no DB/LLM needed — proves logic, degrades honestly)
.venv/bin/python -m scripts.distill --dry-run

# 1. live: needs DATABASE_URL + ANTHROPIC_API_KEY (HypeProof's own key —
#    NOT the borrowed Sonatus key) + the vault_ingester running.
.venv/bin/python -m scripts.distill --since-hours 168

# 2. verify the break is closed — ask the live API a "why" question:
#    it should now cite a decision/<slug> artifact.
```

Acceptance: a question like *"왜 sediment를 분리했지?"* returns an answer
**with a citation to a `decision/...` artifact** — previously impossible.

## Honesty / guardrails

- No ANTHROPIC key → distill **skips with a flag, never fabricates** a
  decision. Borrowed Sonatus key must not be used in prod (NEXT.md P5).
- `#잡담` excluded (noise). Only commitment-grade items extracted (reused
  consolidate `_SYSTEM`: "do not invent decisions to fill the schema").
- ref-slug idempotency = re-running updates, never duplicates → vault stays
  signal, not noise (the whole point of "정리").
- This does NOT touch repo structure (that's the separate Restruct session),
  Studio (no access / post-gate), or pgvector (GATE B, parked).

## Relation to consolidate_memory.py

`distill.py` supersedes it for the dogfood loop (adds events source + the
artifact-landing keystone). `consolidate_memory.py` is left intact — the
Restruct session decides whether to retire or fold it in.
