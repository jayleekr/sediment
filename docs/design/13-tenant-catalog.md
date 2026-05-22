# 13 — Tenant Catalog

> **One-line:** The authoritative inventory of every tenant in the production DB — what they ingest, who has access, how they're configured. New tenant onboarding = appending a row here + adding a `seed_lab.py` block.

## 1. Why this catalog exists

Tenant state is split across `tenants`, `members`, `integrations`, and `subscriptions` rows. Reading the DB to understand "what does kids-edu actually have set up?" is slow and error-prone. This catalog mirrors the DB state, written as the *contract* the seed code targets. When seed and catalog diverge, fix the seed.

Three tenants today; this catalog tracks them and the onboarding template for tenant N+1.

## 2. Inventory at a glance

| Slug | Display name | Status | Plan | Members | Connectors | Purpose |
|---|---|---|---|---|---|---|
| `hypeproof-lab` | HypeProof Lab | active | free (internal) | 8 (Jay + 7) | Discord (8 channels, 30min) | Dogfood #1 — the original tenant |
| `kids-edu` | HypeProof Kids Edu | active | free (consent-based) | 2 (Jay, Jinyong admins) | GitHub (1 repo, hourly daytime KST) | Dogfood #2 — first non-Jay user, vault use case |
| `acme-test` | Acme Test (RLS verify) | active | free | 1 (placeholder admin) | none | Cross-tenant RLS isolation test only |

## 3. Per-tenant detail

### 3.1 `hypeproof-lab`

```yaml
slug: hypeproof-lab
display_name: HypeProof Lab
domain: sediment.hypeproof-ai.xyz
plan: free
status: active
feature_flags: {}                        # none yet
created: 2026-05-04 (Sediment launch)

subscription:
  seat_count: 8
  query_quota_per_month: 10000
  storage_quota_gb: 5

members:                                 # source: services/sediment/data/members.json
  - { display_name: Jay,         role: admin,   github_login: jayleekr,     email: jay.lee@sonatus.com }
  - { display_name: JY,          role: creator, github_login: <pending>,    email: ... }
  - { display_name: Ryan,        role: creator, github_login: <pending>,    email: ... }
  - { display_name: Kiwon,       role: creator, github_login: <pending>,    email: ... }
  - { display_name: TJ,          role: creator, github_login: <pending>,    email: ... }
  - { display_name: BH,          role: creator, github_login: <pending>,    email: ... }
  - { display_name: Sebastian,   role: creator, github_login: <pending>,    email: ... }
  - { display_name: JeHyeong,    role: creator, github_login: JeHyeong2,    email: ... }

integrations:
  - kind: discord
    source_kind: transcript
    config:
      channels: [
        "1460270044347891898",   # meeting-notes  (highest signal)
        "1458325530465009755",   # ai-에이전트
        "1458325388315852993",   # ai-워크플로우
        "1463019098685571257",   # 인사이트-공유
        "1506104152747671694",   # sediment
        "1506104118500921484",   # hypeproof-studio
        "1458782448094674944",   # ai-프로젝트
        "1458325240185884722",   # ai-실험실
      ]
      schedule: "*/30 * * * *"
      distill_strategy_default: chat_thread
      distill_strategy_overrides:
        "1460270044347891898": meeting_transcript

  # PLANNED:
  - kind: github
    source_kind: vault
    config:
      repos: ["jayleekr/hypeprooflab"]
      path_prefixes: ["vault/", "research/"]
      path_excludes: [".raw/", ".obsidian/"]
      extensions: [".md"]

  - kind: github
    source_kind: product
    config:
      repos: ["jayleekr/sediment"]
      path_prefixes: ["docs/", ""]            # docs/ + root for SPEC/DECISIONS/README
      include_only: ["SPEC.md", "DECISIONS.md", "README.md", "CLAUDE.md", "docs/**/*.md"]

  - kind: github
    source_kind: harness
    config:
      repos: ["jayleekr/hypeproof-harness"]
      path_prefixes: ["docs/", "skills/"]
      extensions: [".md"]

notification_routes:
  channels:
    sediment:          { transport: discord_webhook, secret_env: DISCORD_WEBHOOK_SEDIMENT }
    meeting-notes:     { transport: discord_webhook, secret_env: DISCORD_WEBHOOK_MEETING_NOTES }
  routes:
    "*":
      deploy.success:    { channels: [sediment] }
      deploy.failure:    { channels: [sediment], severity: critical }
      recall.regression: { channels: [sediment] }
      cost.over_budget:  { channels: [sediment] }
      daily.digest:      { channels: [sediment] }
      new_decision:      { channels: [sediment, meeting-notes] }    # split — decisions also broadcast to meeting-notes

golden_queries:
  - file: services/sediment/validator/golden_queries.yaml
  - count: 40
  - threshold: 20/40
  - baseline (2026-05-21): 27 PASS / 6 PART / 7 MISS, avg 75.0%, p50/p95 869/1241ms

phase_4_consolidate:
  enabled: true
  schedule: "15 */12 * * *"   # 09:15 + 21:15 KST
  since_hours: 13

cost_envelope:
  target_monthly_usd: 30                 # internal dogfood, generous
  alert_threshold_daily_usd: 5.00
```

### 3.2 `kids-edu`

```yaml
slug: kids-edu
display_name: HypeProof Kids Edu
domain: null                              # uses default sediment.hypeproof-ai.xyz with auth-gated tenant routing (v2)
plan: free                                # consent-based, not paid
status: active
feature_flags: {}
created: 2026-05-21 (per sediment#13, consent from Jinyong)

subscription:
  seat_count: 8                           # default; Jinyong's team can add more
  query_quota_per_month: 10000
  storage_quota_gb: 5

members:
  - { display_name: "Jay Lee",      role: admin, github_login: jayleekr,    email: jayleekr0125@gmail.com }
  - { display_name: "Jinyong Shin", role: admin, github_login: JinyongShin, email: jinyong.shin@hypeproof.io }

integrations:
  - kind: github
    source_kind: vault
    config:
      repos: ["JinyongShin/hypeproof_kids_edu"]
      path_prefixes: ["kids_edu_vault/wiki/", "meeting_notes/"]
      path_excludes: [".raw/", ".obsidian/", "node_modules/"]
      extensions: [".md"]
      branch: null                       # use repo default
      schedule: "0 0-13 * * *"           # 09-22 KST hourly
      state:
        head_sha: "71e166202c73d86eb3a8c46a871efa7a73674fbc"   # advanced by github_repo_fetch
        last_sync_at: "2026-05-21T14:30:00Z"

notification_routes:
  channels:
    sediment:          { transport: discord_webhook, secret_env: DISCORD_WEBHOOK_SEDIMENT }
    # PLANNED: their own #kids-edu channel when Jinyong creates it
  routes:
    "*":
      deploy.success:    { channels: [sediment] }    # shared infra alerts go to #sediment
      recall.regression: { channels: [sediment] }
      daily.digest:      { channels: [sediment] }    # later: their own channel
      new_decision:      { channels: [sediment] }

golden_queries:
  - file: services/sediment/validator/golden_queries_kids_edu.yaml
  - count: 10
  - threshold: 5/10
  - baseline (2026-05-22): 5 PASS / 2 PART / 3 MISS, avg 59.2%, p50/p95 870/3700ms

phase_4_consolidate:
  enabled: false                         # no conversations yet; turn on when chat volume warrants
  schedule_when_enabled: "15 */12 * * *"

cost_envelope:
  target_monthly_usd: 10                 # smaller scope; ~200 files, hourly ingest is cheap
  alert_threshold_daily_usd: 2.00

current_state (as of 2026-05-22):
  events: 193
  artifacts: 192      # 1 file skipped (30KB Korean meeting note, chunker edge case)
  chunks: 1987        # with OpenAI embeddings
  first_e2e_smoke: 6 citations, 4 cite specs/core/ai-native-assets.md  # PASS
```

### 3.3 `acme-test`

```yaml
slug: acme-test
display_name: "Acme Test (RLS verify)"
domain: null
plan: free
status: active
feature_flags: {}
created: 2026-05-04 (alongside hypeproof-lab; cross-tenant negative test fixture)

subscription:
  seat_count: 8                          # defaults; nothing real here
  query_quota_per_month: 10000
  storage_quota_gb: 5

members:
  - { display_name: "Acme Admin", role: admin, email: admin@acme.test }

integrations: []                         # intentional — proves RLS empty-state correctness
notification_routes: {}                  # none
golden_queries: none
phase_4_consolidate: { enabled: false }

purpose:
  - E2E-08 cross-tenant negative test signs in as admin@acme.test, attempts
    to load hypeproof-lab conversation URLs, asserts no data leak
  - test_rls.py uses this slug as the "other tenant" target

keep_forever: yes (cost = near-zero; signal = high)
```

## 4. Onboarding template for tenant N+1

When a new tenant signs up (today: manual; v3: self-service), this is what needs to land:

### 4.1 Data (one transaction)

```python
# In scripts/seed_lab.py (or future admin endpoint):

tid = await upsert_tenant(s, slug="<slug>", name="<Display Name>")

await upsert_subscription(s, tid, seat_count=N, query_quota=...)

for m in admins:
    await upsert_member(s, tid, m | {"role": "admin"})

# Per integration the tenant wants:
await upsert_integration(s, tid, kind="github", config={
    "source_kind": "<vault|product|harness|transcript|artifacts>",
    "repos": [...],
    "path_prefixes": [...],
    # ...
})
```

### 4.2 Secrets (manual today)

| What | Where | How to set |
|---|---|---|
| Discord webhook URL for tenant's primary channel | Fly secret `DISCORD_WEBHOOK_<TENANTSLUG>` | `fly secrets set DISCORD_WEBHOOK_X=... -a hypeproof-sediment` |
| GitHub PAT (if tenant's repos are private) | Fly secret `GITHUB_TOKEN_<TENANTSLUG>` or shared | `fly secrets set ...` |
| Tenant-specific OAuth client (if branded subdomain) | Vercel env per-tenant | Vercel UI |

### 4.3 Golden queries

Per-tenant `validator/golden_queries_<slug>.yaml` — 10-40 queries grounded in the tenant's ingested content. Threshold = 50% of count to start.

### 4.4 Coverage update

Add a row to:
- `README.md §4 "Tenant-level coverage at a glance"`
- `01-architecture-overview.md §9 Coverage matrix`
- This file (a new §3.<N> block)

## 5. Tenant size sizing

| Tenant scale | Approx tokens/mo | LLM cost/mo | DB rows/mo | Single-VM headroom |
|---|---|---|---|---|
| **Dogfood (1-2 admins, 200 artifacts)** | ~250K | ~$2 | ~10K | comfortable |
| **D archetype (5-10 people, 1K artifacts)** | ~2M | ~$5 | ~50K | comfortable |
| **A archetype (20-50 people, 5K artifacts)** | ~10M | ~$15 | ~250K | upper bound — start watching |
| **B archetype (50-200 people)** | ~50M | ~$50 | ~1M | dedicated VM or shard |
| **C archetype (200+ people)** | ~200M+ | ~$200+ | ~5M+ | dedicated VM mandatory |

Y1 expected mix: 30-40 D + 10-15 A. Per-tenant LLM cost cap at $5 forces strict Haiku-for-volume / Sonnet-only-for-chat discipline.

## 6. Tenant decommission

When a tenant cancels or violates terms:

1. Set `tenants.status = 'cancelled'` (soft delete — auth rejects new requests)
2. After 30-day grace period: `DELETE FROM tenants WHERE slug = '...'` — cascade removes every row
3. Export: before delete, run `pg_dump --table=... --table=... WHERE tenant_id = ...` to per-tenant SQL file, hand to tenant (PIPA Article 21 right-to-export)
4. Revoke fly secrets, vercel env (per-tenant ones)
5. Update this catalog (remove or move to "Decommissioned" section)
6. Audit log entry: `action: 'tenant.deleted'`, `payload: {slug, by, at}`

No tenant has been decommissioned yet. `acme-test` stays forever (RLS regression coverage).

## 7. Cross-tenant operational events

| Event | All tenants affected? | Notification |
|---|---|---|
| Schema migration | yes | platform admin email; tenants NOT notified (boring) |
| LLM provider outage | yes | platform admin paged; tenants get "degraded" banner |
| Single-tenant data corruption | one | platform admin pages; that tenant admin emailed |
| Cost over-budget (system-wide) | platform | `cost.over_budget` to `#sediment` (operator channel) |
| Per-tenant quota exceeded | one | that tenant's admin email + their primary channel |

Today only the first three are wired. Per-tenant quota notifications: planned in 07.

## 8. Boundary principle (for this doc)

> **This catalog reflects DB state. The DB is the source of truth. If they diverge, update this file.**
>
> Allowed: humans read this for orientation; seed_lab.py keeps this and DB in sync
> Forbidden: code reading this YAML at runtime to make decisions (the DB has those)

The single test: *"If I drop this file, does anything break?"* Should be no — pure documentation.

## 9. Coverage matrix

See `01-architecture-overview.md §9`. This file expands the per-tenant column.

## 10. Open questions

- **Q1**: When the first paying tenant signs up, how do we ceremoniously onboard? *Draft:* a step-by-step runbook + a single command (`make onboard SLUG=... ADMIN_EMAIL=...`) that runs all of §4.
- **Q2**: Should `kids-edu`'s primary notification channel migrate to a #kids-edu Discord channel when Jinyong creates one? *Recommended:* yes, immediately upon channel existence — propose in #12 issue update.
- **Q3**: Do we need a separate `Decommissioned` section in this file once tenants leave, or just remove? *Recommended:* separate section with `decommissioned_at` so historical reads stay clean.

## 11. References

- `services/sediment/scripts/seed_lab.py` — current bootstrap (mirror of §3)
- `services/sediment/data/members.json` — hypeproof-lab member roster source
- `services/sediment/validator/golden_queries.yaml` — hypeproof-lab golden
- `services/sediment/validator/golden_queries_kids_edu.yaml` — kids-edu golden
- `infra/init.sql` — `tenants`, `subscriptions`, `members`, `integrations` schema
- [02-multitenancy-and-rbac.md](./02-multitenancy-and-rbac.md) — RLS isolation contract
- [04-collection-engine.md](./04-collection-engine.md) — connector config schema
- [07-notifications.md](./07-notifications.md) — routes.yaml format
- [12-source-kinds-catalog.md](./12-source-kinds-catalog.md) — source_kind values

## Changelog

- 2026-05-22 — v0.1 — first inventory: hypeproof-lab + kids-edu + acme-test.
