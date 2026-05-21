# Notifications & Broadcast — design (v0.1)

> 2026-05-21. Companion to `collection-and-distillation.md` (v0.3, INBOUND).
> This doc is OUTBOUND — how Sediment talks BACK to the channels it learns
> from. Designed multi-tenant from day one because the same plumbing serves
> dogfood (HypeProof Lab) and the first paying tenant identically; only
> config differs.

---

## 0. Why this exists

The Collection Agent (v0.3) made Sediment listen to a tenant's substrate
(Discord today, Slack/Notion/Email tomorrow). But silence in the other
direction means:

- Deploy ships → no signal → team doesn't know what changed
- Smoke fails at 3am → no signal → recall regression silently rots
- Decisions extracted overnight → no signal → vault gets richer but the
  team doesn't see it; dogfood feedback loop never closes

The fix is mechanically symmetric: a `notifications` service that, given
an event, routes it to the tenant's chosen channels through the SAME
substrate the Collection Agent reads from. Doing AND knowing in one place.

```
                      DISCORD / SLACK / EMAIL / MD-FILES
                          (per-tenant data substrate)
                                    ▲ │
                              IN    │ │   OUT
                          (Collection)│   (Notifications)
                                    │ ▼
              ┌─────────────────────┼─────────────────────┐
              │                                           │
        events → distill                          notifications service
        → decisions/actions                       → tenant route resolver
        → vault                                   → template render (i18n)
              │                                           │
              ▼                                           ▼
         Sediment 답변                            webhook POST per target
         (citations)                              audit_log row
```

---

## 1. Use cases (in priority order — drives phased build)

| Pri | Use case | Trigger | Channel(s) | Format | Frequency |
|-----|---|---|---|---|---|
| P0 | Deploy ships | GHA `fly-deploy.yml` success | tenant's `#sediment` (or `#releases`) | release_notes template | per deploy (~daily) |
| P0 | Ops alert (smoke/recall/cron failure) | GHA failure + cron job exception | `#sediment` + optional admin DM | ops_alert template | only on failure, 30-min cooldown |
| P1 | Daily digest | Sediment APScheduler 09:00 KST | `#sediment` | digest template (decisions+actions+top queries) | daily |
| P1 | Cost budget alert | cost_monitor cron + threshold | `#sediment` + admin DM | budget template | when over |
| P2 | Cross-product broadcast (HypeProof Studio release etc.) | Jay manual via `POST /v1/broadcast` or `@sediment broadcast` | any tenant-allowed channel | free_text + Claude draft | ad-hoc |
| P2 | Weekly knowledge summary | Sediment cron Sunday | `#sediment` | weekly_summary (top decisions, vault growth, member contribs) | weekly |
| P3 | Per-member personal digest | per-member cron + email pref | DM or email | personal_digest | configurable |
| P3 | Decision-needed nudge | when extracted decision flagged `needs_review` | DM to owner | review_nudge | on-trigger |

P0 ships this sprint (1 webhook URL away). P1 follows once dogfood validates
template noise level. P2/P3 require multi-tenant onboarding flow.

---

## 2. Multi-tenant model

Three layers, mirroring `collection-and-distillation` §RBAC:

```
┌─────────────────────────────────────────────────────────┐
│ PLATFORM (HypeProof)                                    │
│  - owns notifications service code + templates          │
│  - operates alerting for the SaaS itself                │
└─────────────────────────────────────────────────────────┘
         │
         ▼  serves
┌─────────────────────────────────────────────────────────┐
│ TENANT (HypeProof Lab dogfood, AcmeCo, ...)             │
│  - configures their own channels (webhook URLs)         │
│  - configures their own routes (event → channel)        │
│  - configures their own templates (language, branding)  │
│  - configures schedule (digest at 09:00 vs 18:00)       │
└─────────────────────────────────────────────────────────┘
         │
         ▼  member-visible
┌─────────────────────────────────────────────────────────┐
│ MEMBER (Jay, JeHyeong, ...)                             │
│  - can subscribe to personal_digest, decision_nudge     │
│  - can mute specific event types in their DMs           │
└─────────────────────────────────────────────────────────┘
```

### Isolation guarantees

- Webhook URLs are tenant-scoped secrets (`notification_channels.webhook_secret_ref`
  points at a KMS-encrypted blob; raw URL never appears in app code or logs)
- Templates render with tenant context — RLS-aware data only
  (e.g., digest for tenant A never includes tenant B's decisions)
- Audit log row carries `tenant_id`, `route_id`, `delivered_at`, `status` —
  cross-tenant query forbidden by RLS at the table level
- Webhook failure on tenant A's channel does not block tenant B's
  notifications — per-channel circuit breaker

### What's per-tenant configurable

| Aspect | v1 (dogfood) | v2 (first paying tenant) | v3+ (GA) |
|---|---|---|---|
| Channels | hardcoded in config/notifications.yaml | per-tenant in `notification_channels` table + admin UI | + Slack OAuth flow, email connector |
| Routes (event→channel) | hardcoded | per-tenant `notification_routes` table | + per-member personal routes |
| Templates | global only | tenant override allowed | + WYSIWYG editor |
| Language | hardcoded `ko` | tenant pref column | + per-member |
| Branding (color, footer link) | none | tenant `branding` JSONB (already exists) | + template variables |
| Cooldown / rate limit | global default | per-tenant override | + per-route override |
| Mute schedule (quiet hours) | none | tenant `quiet_hours` column | + per-member DND |

---

## 3. Data model

Two new tables, both RLS-protected. Both keep `tenant_id NOT NULL` (RLS
policy `tenant_id = current_tenant_id()`):

```sql
CREATE TABLE notification_channels (
  id              uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id       uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  name            text NOT NULL,            -- "sediment_main", "alerts_dm"
  kind            text NOT NULL,            -- discord_webhook | slack_webhook | email | http
  config          jsonb NOT NULL DEFAULT '{}',  -- {webhook_secret_ref, channel_id, etc.}
  is_active       boolean NOT NULL DEFAULT true,
  failure_count   integer NOT NULL DEFAULT 0, -- circuit-breaker support
  last_success    timestamptz,
  last_failure    timestamptz,
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, name)
);

CREATE TABLE notification_routes (
  id              uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id       uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  event_type      text NOT NULL,            -- "deploy.succeeded", "smoke.failed", "digest.daily", ...
  channel_id      uuid NOT NULL REFERENCES notification_channels(id) ON DELETE CASCADE,
  template_name   text NOT NULL,            -- "release_notes", "ops_alert", ...
  template_override jsonb,                  -- optional per-tenant override
  filters         jsonb NOT NULL DEFAULT '{}',  -- {severity_min: "warn", branch: "main", ...}
  cooldown_seconds integer NOT NULL DEFAULT 0,
  schedule        text,                     -- cron expr for scheduled events (digest)
  is_active       boolean NOT NULL DEFAULT true,
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX notification_routes_event_idx ON notification_routes (tenant_id, event_type) WHERE is_active;

CREATE TABLE notification_log (
  id              uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id       uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  route_id        uuid REFERENCES notification_routes(id) ON DELETE SET NULL,
  event_type      text NOT NULL,
  channel_kind    text NOT NULL,
  template_name   text NOT NULL,
  payload         jsonb NOT NULL,           -- rendered final message body (auditable)
  status          text NOT NULL,            -- sent | failed | suppressed_cooldown | suppressed_quiet_hours
  status_detail   text,                     -- error message if failed
  attempt         integer NOT NULL DEFAULT 1,
  sent_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX notification_log_tenant_sent_idx ON notification_log (tenant_id, sent_at DESC);
```

`v1 simplification`: in dogfood phase, only one tenant uses this. Tables
exist + RLS enabled, but config can stay in `config/notifications.yaml`
as a seed source. The seed_lab script bootstraps the channels + routes
on first run.

---

## 4. Service architecture

```
lab_lib/
├── notifications/
│   ├── __init__.py
│   ├── service.py          # notify(event_type, payload, tenant_id) — entry
│   ├── router.py           # event_type → route(s) lookup (RLS-aware)
│   ├── renderer.py         # template render (Jinja2 with locale switch)
│   ├── delivery.py         # per-kind delivery (discord, slack, email, http)
│   ├── circuit_breaker.py  # per-channel failure tracking + auto-disable
│   ├── audit.py            # notification_log insert
│   └── transports/
│       ├── discord_webhook.py
│       ├── slack_webhook.py   # P2
│       ├── email_smtp.py      # P2
│       └── http_generic.py
└── notifications/templates/
    ├── ko/
    │   ├── release_notes.md.j2
    │   ├── ops_alert.md.j2
    │   ├── daily_digest.md.j2
    │   └── ...
    └── en/
        └── ... (same set, English)
```

### Entry contract

```python
from lab_lib.notifications import notify

await notify(
    event_type="deploy.succeeded",
    tenant_id=tenant_id,
    payload={
        "version": "v42",
        "deployed_by": "github-actions",
        "commit_count": 3,
        "smoke_passed": True,
        "url": "https://hypeproof-sediment.fly.dev",
    },
)
```

Returns immediately (fire-and-forget); delivery happens in a background
task. notify() is idempotent on (event_type, payload digest, route) within
the cooldown window — same payload triggered twice = sent once.

### Sources of `notify()` calls

| Trigger | Where the call lives |
|---|---|
| GHA deploy success | `.github/workflows/fly-deploy.yml` posts to platform `/v1/internal/notify` (admin-token authed) |
| GHA smoke failure | same — `if: failure()` step |
| GHA nightly recall failure | `.github/workflows/nightly-recall.yml` `if: failure()` |
| APScheduler daily digest | `scripts/scheduler.py` adds a `_run_digest` job that calls `notify()` directly |
| Cost monitor | already in scheduler; add `notify()` if over_budget |
| Slash command (`@sediment broadcast`) | langgraph tool that calls `notify()` after Jay approval |

### Why not just a Discord webhook from each caller?

That'd work for dogfood — but:
- No audit trail
- No per-tenant template / language / branding
- No cooldown (alert storms)
- No multi-target (release goes to #sediment AND #releases AND email digest)
- No failure handling (webhook 404 = silent loss)
- Can't add Slack/email without rewriting every caller

The `notify()` indirection is what makes the system multi-tenant-ready
from day one. v1 implementation is ~150 lines; pays for itself the first
time we add a second tenant.

---

## 5. Templates + i18n

Jinja2 templates per (locale, event_type). Variables come from the event
payload + tenant context (`tenant.display_name`, `tenant.branding`).

Example `templates/ko/release_notes.md.j2`:

```jinja
🚀 **{{ tenant.display_name }} 배포** — `{{ payload.version }}`

- 커밋 {{ payload.commit_count }}개
- Smoke: {{ "✅ 통과" if payload.smoke_passed else "❌ 실패" }}
- {{ payload.url }}

{% if payload.notable_commits %}
**주요 변경:**
{% for c in payload.notable_commits %}
- {{ c.subject }} ({{ c.sha[:7] }})
{% endfor %}
{% endif %}
```

Tenant can override a single template by inserting a row into
`notification_routes.template_override` (JSONB with a `body` field that
takes precedence over the file).

For Discord, the rendered markdown is wrapped in the embed object before
POST. For Slack, into Block Kit. For email, into MJML → HTML. The
`transports/` modules handle the format conversion.

---

## 6. Security model

| Threat | Mitigation |
|---|---|
| Webhook URL leak (env dump, log scrape) | Store URL encrypted at rest (`notification_channels.config.webhook_secret_ref` → KMS). Application code never sees raw URL; transport module fetches at send time. v1 simplification: Fly secrets `DISCORD_WEBHOOK_<TENANT_SLUG>_<CHANNEL>` until KMS is in place |
| Cross-tenant template rendering (tenant A's data sent to tenant B's channel) | All DB reads in `renderer.py` are RLS-scoped via `SET LOCAL app.tenant_id`. Render context is built from one tenant_id only. Unit test: render template with two tenants' data, assert no overlap |
| Webhook URL replay (attacker captures + reuses) | Discord webhook URLs include a secret token. If leaked, rotate by deleting the webhook in Discord; old URL 404s. Document rotation in tenant ops runbook |
| Inbound from Discord pretending to be a release event | Internal `/v1/internal/notify` endpoint requires `INTERNAL_TOKEN` (Fly secret), not exposed publicly. Notifications never triggered from the inbound Discord MCP path |
| Notification flood (bug/cron loop) | Per-channel rate limit (default 30 messages/min), per-tenant rate limit (default 200/hour), circuit breaker auto-disables a channel after 5 consecutive failures |
| Audit gap | Every send (including suppressed by cooldown or quiet hours) writes a `notification_log` row. Tenant admins can query their own log via `GET /v1/notifications/log` (RLS-scoped) |

---

## 7. Failure handling

| Failure | Response |
|---|---|
| Webhook returns 4xx (auth fail, channel deleted) | Mark channel `is_active=false`, increment `failure_count`, log to audit. Email tenant admin (separate fallback channel) if configured |
| Webhook returns 5xx | Retry with exponential backoff: 1s, 5s, 30s. After 3 attempts → audit as failed, do not auto-disable (likely transient) |
| Discord rate limit (429) | Honor `Retry-After` header. Queue subsequent messages for this channel until window opens |
| Template render error (missing variable etc.) | Log error to `notification_log.status_detail`. Do NOT block other routes for the same event (one bad template ≠ everything fails) |
| All channels for tenant offline | Surface in platform admin alert (PagerDuty-style escalation) — but for v1, just log loudly |

---

## 8. v1 (dogfood) → v2 (first paying tenant) → v3 (GA) phased build

### v1 — 2026-05-21 sprint (this week)

Minimum viable: 1 tenant (HypeProof Lab), 1 channel (#sediment), 3 event types.

- [ ] `lab_lib/notifications/` skeleton — service, router, renderer, audit
- [ ] `transports/discord_webhook.py` — single transport
- [ ] `templates/ko/{release_notes, ops_alert, daily_digest}.md.j2`
- [ ] Migration adding `notification_channels`, `notification_routes`, `notification_log` (RLS)
- [ ] `config/notifications.yaml` seed (Jay's webhook URL → channel + 3 routes)
- [ ] `seed_lab.py` extension: idempotent upsert from notifications.yaml
- [ ] Wire GHA `fly-deploy.yml` to POST `/v1/internal/notify` on success + failure
- [ ] Wire nightly-recall failure path
- [ ] Add `_run_digest` job to APScheduler (calls notify() directly, in-process)
- [ ] Smoke test: simulate event → assert message in Discord (manual eyeball)

Effort: ~1.5 days. Unblocks: every deploy + every smoke failure is visible
in the dogfood channel, daily digest closes the dogfood feedback loop.

### v2 — first paying tenant (2-4주 후)

- [ ] Tenant admin UI: list channels, add/remove webhook URLs (encrypted at rest)
- [ ] Tenant admin UI: route table (event_type → channel + template)
- [ ] Per-tenant locale + branding (already in tenant.branding JSONB)
- [ ] Slack transport — Slack Bot OAuth + webhook fallback
- [ ] Per-tenant quiet hours + rate limit overrides
- [ ] `notification_log` query API for tenant audit

### v3 — GA (1-2달 후)

- [ ] Email transport (SMTP via SES/Sendgrid)
- [ ] Per-member personal digest + DM transport
- [ ] Decision-needed nudge (extracted decisions where `needs_review=true`)
- [ ] Template WYSIWYG editor in admin UI
- [ ] KMS-backed webhook URL encryption (v1 used Fly secrets; v3 uses per-tenant KMS keys for residency compliance)
- [ ] Multi-region delivery routing (EU tenants → EU SMTP, etc.)

---

## 9. Open decisions for Jay (single source of truth)

See sibling GitHub issue (will be filed alongside this doc) for the live checklist:

1. **Webhook for #sediment** — Discord settings → Integrations → Webhooks → New. URL → Jay → `fly secrets set DISCORD_WEBHOOK_SEDIMENT=...`
2. **Separate `#releases` channel?** — recommendation: no, keep all dogfood signal in #sediment for v1; add #releases when team grows past 10
3. **Digest schedule** — 09:00 KST (morning standup) vs 18:00 KST (end-of-day) vs both. Recommendation: 09:00 only for v1, see if 18:00 adds value
4. **Templates language** — KO only for dogfood v1, KO+EN for v2 (when first non-Korean tenant lands). Confirm OK?
5. **Cost budget threshold** — currently `daily_budget_usd: 5.00` in cron.yaml. Should `cost.over_budget` event also escalate to email/SMS if 10x over? Or chat-only?
6. **Mute/quiet hours** — does Jay want a quiet window (e.g. 23:00-07:00 KST no alerts to #sediment)? Recommendation: no for v1, all alerts go through; revisit if there's noise

---

## 10. Why this design is a 4-week SaaS unlock

The hardest part of multi-tenant notifications isn't writing the
templates — it's the muddle of WHERE the config lives, WHO can change
it, and HOW failures don't take down other tenants. This design:

- **Tables + RLS from day one** — no "v1 is single-tenant, we'll refactor later" trap
- **Templates as files, overrides as JSONB rows** — devs ship template fixes via PR (auditable), tenants override via UI (without code access)
- **Webhook URLs as encrypted refs** — same code path for HypeProof Lab dogfood and AcmeCo's locked-down Slack workspace
- **Audit log as a queryable artifact** — tenants get visibility for free; we get compliance evidence for free
- **Phased build matches paying tenant ladder** — v1 ships in days, v2 in weeks, v3 when first GA customer signs

After v1, every new tenant onboarding adds rows, not code.

---

## 11. Glossary

- **Substrate**: the surface where a tenant's team actually communicates (Discord today, Slack/Notion/email tomorrow). Symmetric for in (Collection Agent) and out (Notifications service)
- **Route**: a row in `notification_routes` mapping (tenant, event_type) → (channel, template)
- **Cooldown**: minimum interval before the same (event_type, payload digest) sends again
- **Quiet hours**: time window during which a tenant suppresses (logs only) non-critical events
- **Circuit breaker**: per-channel failure counter that auto-disables a channel after N consecutive failures

---

*Last updated: 2026-05-21 — companion to `collection-and-distillation.md` v0.3*
