# 10 — Frontend

> **One-line:** Next.js 14 App Router on Vercel, mounted at `/sediment`. Routes are tenant-agnostic — the JWT supplied by NextAuth carries `tenant_id`; the backend handles all scoping. Brand badge ("dev"/"prod") proves the deploy matches the env.

## 1. Executive view

The frontend is intentionally thin. It's an authentication shell + a few tenant-agnostic pages (chat, library, members, admin, onboard, pricing) that render whatever the backend returns. The hard work — retrieval, citation, RBAC — lives server-side. This keeps the UI fast to iterate and avoids per-tenant frontend builds.

Two design pressures:
1. **Fast feedback for the dogfood user.** Live SSE streaming, no full-page reloads on chat turns
2. **Multi-env operable.** Same bundle deploys to dev (with Mint dev-token UI) and prod (with GitHub OAuth)

The cross-cut: an env badge in the header (`dev` / `prod`) renders from a build-time env var. If a user ever sees `dev` on production URL, the deploy is wrong — caught by E2E-12.

## 2. Route map

```
frontend/app/
├── api/
│   └── auth/[...nextauth]/route.ts        NextAuth.js GitHub provider
└── sediment/                              ← all UX here, mounted under /sediment
    ├── layout.tsx                         shared header (brand + nav + env badge)
    ├── page.tsx                           landing: "Try one" suggested queries + recent convs
    ├── Providers.tsx                      TanStack Query + auth context
    ├── AuthBridge.tsx                     NextAuth session ↔ backend JWT exchange
    ├── FreshnessBadge.tsx                 "last ingest 3min ago" stale indicator
    ├── lib/
    │   ├── api.ts                         fetch wrapper + JWT injection
    │   ├── sse.ts                         SSE client (streamSediment)
    │   └── ...                            other client helpers
    ├── c/[id]/                            conversation detail (chat + SSE)
    ├── library/                           artifact browser + filters
    ├── members/                           team roster
    ├── admin/                             admin-only (integrations, cost dashboard, member mgmt)
    ├── onboard/                           first-run wizard
    ├── pricing/                           public pricing page (marketing)
    └── auth/                              sign-in / sign-out UI
```

## 3. Page-by-page

### 3.1 `/sediment` (landing)

- Three "Try one" suggested queries (one library, one member, one decision intent)
- Recent conversations sidebar (last 30)
- "+ New conversation" → POST `/api/v1/conversations`, redirect to `/sediment/c/<id>`
- For unsigned-in users: shows the Mint dev-token form (dev) or "Sign in with GitHub" button (prod)

### 3.2 `/sediment/c/[id]` (conversation)

The main value-bearing page. Three panes:
- Messages — user + assistant turns with inline `[N]` citations
- Stream — real-time delta + citation list as they arrive
- Input — multi-line text + "Send" + suggested follow-ups

**Hot path** (`page.tsx` `ask()`):
```ts
async function ask(q: string) {
  // 1. Persist user turn
  await api(`/api/v1/conversations/${id}/messages`, { method: "POST", body: { content: q, role: "user" }});
  setMessages(m => [...m, optimisticUserMsg]);
  
  // 2. Stream the assistant turn
  setStream({ status: "thinking…", ... });
  await streamSediment(id, q, {
    onStatus: (msg, meta) => setStream(...),
    onCitation: (c) => setStream(s => ({ ...s, citations: [...s.citations, c] })),
    onDelta: (token) => setStream(s => ({ ...s, buffer: s.buffer + token })),
    onAnswerEnd: () => setStream(s => ({ ...s, done: true })),
    onDone: async () => { await load(); }   // re-pull persisted messages
  });
}
```

**Citation render**: each `[N]` in the answer is a hover-card showing the cited artifact's ref + content excerpt. Click → opens `/sediment/library/<ref>` in new tab.

**Multi-turn**: same `conv_id` for the whole session. LangGraph MemorySaver carries state. Input is re-enabled only after `done: true` to prevent overlap.

### 3.3 `/sediment/library`

- Filterable list of all artifacts in the tenant
- Filters: type (column/research/decision/...), date range, author
- Click row → `/sediment/library/<ref>` — full markdown render + frontmatter + "cited in N conversations" reverse-link
- "Re-ingest" button (admin only) → POST `/v1/ingest/document` with current body

### 3.4 `/sediment/members`

- Card grid of all members in the tenant
- Each card: avatar + name + title + expertise + recent contributions
- Admin: edit role inline (admin/creator/viewer)

### 3.5 `/sediment/admin`

Admin-gated (`role=admin`). Multiple tabs:
- **Integrations** — list `integrations` rows, edit JSON config, see last_sync_at
- **Cost** — per-day spend chart (from `usage_daily`), per-agent breakdown
- **Members** — invite, role change, deactivate
- **Cron health** — last 7 days of each cron job's run status
- **Notification routes** (v2) — UI editor for `routes.yaml`

### 3.6 `/sediment/onboard`

First-run wizard for new admins:
1. "Connect your knowledge sources" — GitHub repo URL / Discord guild ID / future Slack workspace
2. "Invite teammates" — bulk email + role picker
3. "Set notification preferences" — channel pick + event types
4. → redirects to `/sediment` with first integration row created

Not built yet (v2).

### 3.7 `/sediment/pricing`

Public, no auth. Three plans (Free / Pro / Enterprise) with seat/quota matrix. CTA: "Start trial" → onboard flow.

## 4. State management

Two libraries:
- **TanStack Query 5** — server state (conversations, messages, members, library)
- **Zustand 5** — client state (chat input draft, modal open/closed, dark mode)

Why split? Server state has caching/refetch/invalidation needs that React state can't model well. Client state is just UI ephemera.

**Cache keys** convention: `["api", path, params]` — e.g., `["api", "/api/v1/conversations", { limit: 30 }]`. Invalidation on mutation: invalidate by prefix `["api", "/api/v1/conversations"]` after POST.

## 5. Auth integration

`AuthBridge.tsx`:
```tsx
"use client";
import { useSession } from "next-auth/react";
import { useEffect } from "react";

export function AuthBridge() {
  const { data: session, status } = useSession();
  useEffect(() => {
    if (status !== "authenticated") return;
    // Exchange NextAuth session for Sediment JWT
    fetch("/api/v1/auth/oauth-exchange", {
      method: "POST",
      body: JSON.stringify({
        provider: "github",
        github_login: session.user?.username,
        verified_emails: [session.user?.email].filter(Boolean),
      }),
    })
      .then(r => r.json())
      .then(t => localStorage.setItem("sediment.token", t.token));
  }, [status]);
  return null;
}
```

In `Providers.tsx`:
```tsx
<SessionProvider>
  <AuthBridge />
  <QueryClientProvider client={qc}>{children}</QueryClientProvider>
</SessionProvider>
```

Once token is in `localStorage`, every API call adds `Authorization: Bearer <token>` via `api()` wrapper.

## 6. Env-aware build

`frontend/app/sediment/layout.tsx` reads `NEXT_PUBLIC_SEDIMENT_ENV` (build-time):
```tsx
<span className={`rounded-full px-2 py-0.5 text-xs font-medium
                  ${env === "prod" ? "bg-emerald-100 text-emerald-800" : "bg-amber-100 text-amber-800"}`}>
  {env}
</span>
```

Set in Vercel project env: `prod` for `sediment.hypeproof-ai.xyz`, `dev` for branch previews / local.

E2E-12 asserts this badge reads `prod` on the prod URL — catches deploys that accidentally pick up dev env.

## 7. Backend URL resolution

The backend URL is set at build time but overrideable at runtime:

```ts
// frontend/app/sediment/lib/api.ts
export const BACKEND_BASE = 
  (typeof window !== "undefined" && (window as any).__BACKEND_BASE__) ||
  process.env.NEXT_PUBLIC_BACKEND_BASE ||
  "http://localhost:8080";

export const LANGGRAPH_BASE = 
  (typeof window !== "undefined" && (window as any).__LANGGRAPH_BASE__) ||
  process.env.NEXT_PUBLIC_LANGGRAPH_BASE ||
  BACKEND_BASE;
```

`window.__BACKEND_BASE__` lets us swap targets per-deploy without rebuilding. Useful for Cloudflare-tunnel-style demos where the URL changes per session.

## 8. Stack summary

| Layer | Tech | Pinned in |
|---|---|---|
| Framework | Next.js 14 App Router | `package.json` |
| React | 18 | `package.json` |
| Styling | Tailwind 4 + shadcn/ui + Radix primitives | `tailwind.config.ts` |
| Server state | TanStack Query 5 | `package.json` |
| Client state | Zustand 5 | `package.json` |
| Forms | react-hook-form 7 + Zod 4 | `package.json` |
| HTTP | fetch + own `api()` wrapper | `lib/api.ts` |
| SSE | raw fetch + ReadableStream reader (no @microsoft/fetch-event-source dep) | `lib/sse.ts` |
| Auth | NextAuth.js GitHub provider | `app/api/auth/[...nextauth]/route.ts` |
| Markdown render | react-markdown + remark-gfm | per page |
| Charts | recharts | admin cost page |
| i18n | none in v1 (KO/EN texts hardcoded; future next-intl) | — |
| Analytics | none | — |
| Testing | Vitest (unit), Playwright (e2e via validator) | `vitest.config.ts` |
| Hosting | Vercel | `vercel.json` |

## 9. Feature flag conventions

`NEXT_PUBLIC_FEATURE_<NAME>` — all default OFF. Currently unused (v1 has nothing flag-gated), reserved for:
- `FILE_UPLOAD` — drag-drop ingest (planned v1.5)
- `DICTATION` — voice memo capture (Phase A)
- `SEARCH_CHATS` — full-text search of past conversations
- `PROJECTS` — group conversations into projects (v2)
- `MESSAGE_EDIT` — edit user turns (v2)
- `MEMBER_DIGEST` — opt-in personal weekly email (v3)

Lifecycle: flag OFF in prod → wire feature behind flag → flag ON via Vercel env → soak 1 week → remove flag (code defaults to ON).

## 10. Configuration model

| Setting | Storage | Default |
|---|---|---|
| `NEXT_PUBLIC_SEDIMENT_ENV` | Vercel env per project | `prod` (prod project), `dev` (dev/local) |
| `NEXT_PUBLIC_BACKEND_BASE` | Vercel env | prod: `https://hypeproof-sediment.fly.dev`; dev: `http://localhost:8080` |
| `NEXT_PUBLIC_LANGGRAPH_BASE` | Vercel env | (same as backend base unless split) |
| `AUTH_GITHUB_ID` / `AUTH_GITHUB_SECRET` | Vercel env (secret) | required for prod |
| `NEXTAUTH_SECRET` | Vercel env (secret) | required |
| `NEXTAUTH_URL` | Vercel env | `https://sediment.hypeproof-ai.xyz` |
| `NEXT_PUBLIC_FEATURE_*` | Vercel env per feature | OFF |

## 11. Boundary principle (for this doc)

> **No page-level code constructs a JWT, calls the database, or holds tenant routing state.**
>
> Allowed: read `tenant_id` from JWT (for display only — header shows tenant name); pass JWT to backend
> Forbidden: parsing JWT for routing decisions; storing tenant secrets in localStorage; client-side database calls

The single test: *"If two users from two tenants signed in on the same browser at the same time, would either of them see the other's data?"* If no, boundary intact (because all scoping is server-side via JWT).

## 12. Coverage matrix

| Capability | hypeproof-lab | kids-edu | future tenant |
|---|---|---|---|
| Sign-in flow | ✅ dev-token + GitHub OAuth | ✅ same | ✅ same |
| `/sediment` landing | ✅ | ✅ | ✅ |
| `/sediment/c/[id]` chat | ✅ multi-turn, citations, SSE | ✅ | ✅ |
| `/sediment/library` browse | ✅ | ✅ 192 artifacts visible | ✅ |
| `/sediment/members` | ✅ 8 members | ✅ 2 members | ✅ |
| `/sediment/admin/integrations` | ⏳ basic JSON editor | ⏳ | ⏳ |
| `/sediment/admin/cost` | ⏳ v2 | ⏳ v2 | ⏳ |
| `/sediment/onboard` wizard | ❌ v2 | ❌ | ❌ |
| `/sediment/pricing` | ✅ static | n/a | ✅ |
| Env badge | ✅ "prod" on prod | ✅ same | ✅ same |
| Feature flags | none active | none active | none active |

## 13. Open questions

- **Q1**: Multi-tenant browser session — when a user is a member of 2 tenants, picker UI lives where? *Options:* (a) `/sediment` landing with tenant chooser, (b) URL prefix `/sediment/<tenant>/...`, (c) subdomain. *Recommended:* (b) for path clarity; URL hint helps bookmark + share.
- **Q2**: Mobile UX — currently optimized for desktop. *Trigger:* first user complaint or first mobile-first tenant. *Estimate:* 1 week of layout work.
- **Q3**: Real-time co-presence (see other members' cursors in same conversation) — useful or noise? *Recommended:* skip until requested.
- **Q4**: Embeddable widget — can a tenant embed Sediment chat into their own site? *Recommended:* v3 — iframe + JWT exchange via postMessage. Not urgent.

## 14. References

- `frontend/app/sediment/` — all pages
- `frontend/app/sediment/lib/api.ts` — fetch wrapper
- `frontend/app/sediment/lib/sse.ts` — `streamSediment` client
- `frontend/app/api/auth/[...nextauth]/route.ts` — NextAuth config
- `frontend/package.json` — pinned versions
- `validator/e2e_spec.yaml` E2E-01–E2E-13 — UI coverage
- [03-auth.md](./03-auth.md) — JWT exchange contract
- [06-retrieval-and-chat.md §6](./06-retrieval-and-chat.md) — SSE wrapper convention
- [11-deployment.md](./11-deployment.md) — Vercel + CD pipeline

## Changelog
- 2026-05-22 — v0.1 — codified route map, stack, env-aware build, auth bridge pattern.
