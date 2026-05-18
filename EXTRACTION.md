# Extraction provenance

This repository (`jayleekr/sediment`, **private**) was extracted on 2026-05-18
from `jayleekr/hypeprooflab` to give the Sediment SaaS its own lifecycle,
separate from HypeProof's community content monorepo.

## Why

`hypeprooflab` is HypeProof Lab's community workspace (novels, columns,
research, the public website, deck pipeline). Sediment is a commercial,
evidence-grounded knowledge SaaS on an external-SaaS path — different
audience, different deploy lifecycle (Fly + Vercel + multi-tenant + a
validator harness), different confidentiality (DECISIONS.md, billing.py,
init.sql RLS). Mixing a commercial product into a community repo — briefly
public — was the trigger.

## What came over

| Path here | Was in hypeprooflab |
|---|---|
| repo root (`services/`, `infra/`, `harness/`, `SPEC.md`, `DECISIONS.md`, …) | `products/sediment/` |
| `frontend/app-sediment/` | `web/src/app/sediment/` — **parked, not yet a standalone app** |
| `.claude/agents/curator-*.md`, `.claude/skills/curator*`, `.claude/skills/sediment-connect/` | same paths |
| `.github/workflows/curator-ingest.yml` | same path |

`products/sediment/output/` (regenerable validator screenshots, ~381 files)
was intentionally dropped.

## History

Full pre-extraction git history is **retained in the private
`jayleekr/hypeprooflab`** (the owner chose "keep history; repo stays
private"). This repo starts clean; for archaeology of the MVP build, the
Ralph/validator iteration commits, and the AI-Curator→Sediment rename, see
hypeprooflab history up to commit `ea5899f`.

## Frontend cutover — NOT done yet

`frontend/app-sediment/` is the 11 Sediment UI files lifted verbatim from the
community Next.js app. They are **not a deployable standalone app**: they
import shared components/libs (`web/src/components/CookieConsent`, etc.) that
did not come over. Until a standalone Next.js app + its own Vercel project is
stood up here:

- The **live dogfood UI keeps serving from the community web app**
  (`hypeprooflab` → `web/src/app/sediment/`, Vercel
  `web-nu-seven-39.vercel.app/sediment`). It was deliberately **left in
  hypeprooflab** so the team's dogfood does not break.
- Do **not** delete `web/src/app/sediment/` from hypeprooflab until the
  standalone here is live and the dogfood URL is repointed.

This cutover is the top item in `NEXT.md`.
