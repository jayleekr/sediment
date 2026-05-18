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

## Frontend cutover — IN PROGRESS (scaffold + build done 2026-05-18)

The 11 Sediment UI files turned out to be **far more self-contained than
originally feared**: the only real npm deps are `next`, `react`, `react-dom`,
`react-markdown`, `remark-gfm`. The `CookieConsent` / `next-auth` / shared-lib
concern was overstated — `next-auth` appears *only* in
`app/sediment/auth/README.md` (a Phase-5 design code-fence, not real code); the
app authenticates via a localStorage bearer token minted against the Fly API
(`/api/v1/auth/dev-token`). No `@/` alias, no shared community components.

**Done:**
- Standalone Next.js 16 app scaffolded under `frontend/` (own `package.json`,
  `next.config.ts`, `tsconfig.json`, Tailwind v4, root layout + `globals.css`,
  `/` → `/sediment` redirect, `.env.local.example` → Fly API).
- The 11 files moved verbatim (zero code changes) to `frontend/app/sediment/`;
  all relative imports verified resolving.
- `npm install && npm run build` passes clean — 9 routes
  (`/`, `/sediment`, `/sediment/{admin,library,members,onboard,pricing}`,
  `/sediment/c/[id]`).

**Still NOT done (gated on Jay):**
- New Vercel project from this repo (root dir `frontend/`), `NEXT_PUBLIC_*`
  → Fly API, custom domain `sediment.hypeproof-ai.xyz`.
- E2E verify against the new deploy; repoint the dogfood team.
- **Only then** delete `web/src/app/sediment/` (and the now-duplicated
  `products/sediment/`, `.claude/curator-*`, `.github/curator-ingest`) from
  `hypeprooflab`.

Until the deploy + repoint lands, the **live dogfood UI still serves from the
community web app** (`hypeprooflab` → `web/src/app/sediment/`, Vercel
`web-nu-seven-39.vercel.app/sediment`) — deliberately left there so the team's
dogfood does not break. **Do not delete it from hypeprooflab early.**

This cutover is the top item in `NEXT.md`.
