"use client";

import { useEffect, useState, type CSSProperties } from "react";
import Link from "next/link";
import { signIn as githubSignIn } from "next-auth/react";
import { api, ApiError, clearToken, getToken, mintDevToken, type Conversation } from "./lib/api";
import { EmptyState, SectionHeader, Surface, TrustBadge } from "./components/ui";

// FIX-B 2026-05-23: previously this also accepted NEXT_PUBLIC_SEDIMENT_DEV_AUTH=1
// which is inlined into the production bundle — a single Vercel env misconfig
// opened auth bypass (default email in input is jayleekr0125@gmail.com, backend
// accepts /api/v1/auth/dev-token and returns a real admin JWT).
// Now: only NODE_ENV=development enables the dev-token flow. There is no
// production-side toggle. To re-enable for staging, gate via server-only env
// (SEDIMENT_DEV_AUTH without NEXT_PUBLIC_ prefix) read in a route handler.
const ENABLE_DEV_AUTH = process.env.NODE_ENV === "development";
const AUTH_TENANT_SLUG =
  process.env.NEXT_PUBLIC_SEDIMENT_AUTH_TENANT_SLUG ||
  "";

export default function CuratorHome() {
  const [mounted, setMounted] = useState(false);
  const [convs, setConvs] = useState<Conversation[]>([]);
  const [signedInAs, setSignedInAs] = useState<string | null>(null);
  const [email, setEmail] = useState("jayleekr0125@gmail.com");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function refresh() {
    if (!getToken()) return;
    try {
      const data = await api<{ items: Conversation[] }>("/api/v1/conversations");
      setConvs(data.items);
    } catch (e: unknown) {
      if (e instanceof ApiError && e.status === 401) {
        // Token expired — already wiped by api(). Force sign-in form.
        setSignedInAs(null);
        setError("세션이 만료됐습니다. 다시 로그인해주세요.");
        return;
      }
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  useEffect(() => {
    // Defer all localStorage-dependent render decisions until after mount so
    // SSR HTML matches the first client paint (avoids React 19 hydration error
    // when getToken() reads localStorage during render).
    setMounted(true);
    if (getToken()) refresh();
  }, []);

  // SSR + first paint: render nothing visible to keep markup deterministic.
  if (!mounted) {
    return <div className="mx-auto max-w-md p-6" aria-hidden="true" />;
  }

  async function signIn() {
    setLoading(true);
    setError(null);
    try {
      const r = await mintDevToken(email, AUTH_TENANT_SLUG || undefined);
      setSignedInAs(r.display_name);
      await refresh();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  async function newConversation(initialQuery?: string) {
    setError(null);
    try {
      const c = await api<{ id: string }>("/api/v1/conversations", {
        method: "POST",
        body: JSON.stringify({ title: initialQuery?.slice(0, 60) ?? null }),
      });
      if (initialQuery) {
        await api(`/api/v1/conversations/${c.id}/messages`, {
          method: "POST",
          body: JSON.stringify({ content: initialQuery, role: "user" }),
        });
      }
      window.location.href = `/sediment/c/${c.id}${initialQuery ? "?ask=1" : ""}`;
    } catch (e: unknown) {
      if (e instanceof ApiError && e.status === 401) {
        // api() already cleared the bad token. Re-render to surface the sign-in form.
        clearToken();
        setSignedInAs(null);
        setError("세션이 만료됐습니다. 다시 로그인해주세요.");
        return;
      }
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  if (!getToken() && !signedInAs) {
    return (
      <Surface className="reveal-item mx-auto max-w-md p-7 md:p-8">
        <p className="font-mono text-[11px] uppercase tracking-[0.18em] text-ink-3">
          HypeProof Lab · the memory layer
        </p>
        <h1 className="mt-2.5 font-display text-4xl font-semibold tracking-tight text-ink">
          Sediment
        </h1>
        <p className="mt-1 font-body text-[15px] italic text-ink-2">
          where doing becomes knowing
        </p>
        <hr className="rule-dotted my-6" />
        <h2 className="font-display text-lg font-semibold text-ink">Sign in</h2>
        <p className="mb-6 mt-1.5 text-[15px] leading-7 text-ink-2">
          Sign in with the GitHub account that has access to this repo. Your
          GitHub username is matched to a Sediment member.
        </p>
        <button
          onClick={() => githubSignIn("github", { callbackUrl: "/sediment" })}
          className="mb-4 flex w-full items-center justify-center gap-2 rounded-md bg-ink px-4 py-2.5 text-[15px] font-medium text-paper transition-colors hover:bg-accent-ink"
        >
          <svg viewBox="0 0 16 16" width="16" height="16" fill="currentColor" aria-hidden="true">
            <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0016 8c0-4.42-3.58-8-8-8z" />
          </svg>
          Sign in with GitHub
        </button>
        {ENABLE_DEV_AUTH && (
          <>
            <div className="mb-4 flex items-center gap-3 font-mono text-[10px] uppercase tracking-[0.14em] text-ink-3">
              <span className="h-px flex-1 bg-rule" />
              local dev fallback
              <span className="h-px flex-1 bg-rule" />
            </div>
            <input
              className="mb-3 w-full rounded-md border border-rule bg-paper-2/40 px-3 py-2 text-[15px] outline-none transition-colors focus:border-accent"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="member email"
            />
            <button
              onClick={signIn}
              disabled={loading}
              className="w-full rounded-md border border-rule-2 px-4 py-2 text-[15px] text-ink-2 transition-colors hover:bg-paper-2 disabled:opacity-50"
            >
              {loading ? "…" : "Mint dev token (seeded email)"}
            </button>
          </>
        )}
        {error && <p className="mt-3 text-sm text-accent">{error}</p>}
      </Surface>
    );
  }

  return (
    <div className="grid grid-cols-12 gap-6">
      {error && (
        <div
          role="alert"
          className="col-span-12 rounded-md border border-accent/30 bg-claret-soft/50 px-4 py-2.5 text-sm text-accent-ink"
        >
          {error}
        </div>
      )}
      <aside className="reveal-item col-span-12 md:col-span-4" style={{ "--i": 0 } as CSSProperties}>
        <Surface as="aside" className="p-5">
          <div className="mb-4 flex items-center justify-between border-b border-rule pb-3">
            <h3 className="font-mono text-[11px] uppercase tracking-[0.16em] text-ink-3">
              Conversations
            </h3>
            {convs.length > 0 && (
              <button
                onClick={() => newConversation()}
                aria-label="Start a new conversation"
                className="rounded-md bg-accent px-3 py-1.5 font-mono text-[11px] uppercase tracking-wider text-paper transition-colors hover:bg-accent-ink"
              >
                + New
              </button>
            )}
          </div>
          {convs.length === 0 ? (
            <EmptyState
              title="Start your first conversation"
              description="Ask anything from the lab's memory: research, columns, decisions, and recent operating context."
              action={
                <div className="flex flex-col gap-2">
                  {EXAMPLES.slice(0, 2).map((ex) => (
                    <button
                      key={ex}
                      onClick={() => newConversation(ex)}
                      className="rounded-md border border-rule px-3 py-2 text-left text-[15px] text-ink-2 transition-colors hover:border-rule-2 hover:bg-card"
                    >
                      {ex}
                    </button>
                  ))}
                  <button
                    onClick={() => newConversation()}
                    className="w-full rounded-md bg-accent px-4 py-2 text-[15px] text-paper transition-colors hover:bg-accent-ink"
                  >
                    + New conversation
                  </button>
                </div>
              }
            />
          ) : (
            <ul className="-mx-2 space-y-0.5">
              {convs.map((c) => (
                <li key={c.id}>
                  <Link
                    href={`/sediment/c/${c.id}`}
                    className="block rounded-md px-2 py-2 transition-colors hover:bg-paper-2"
                  >
                    <span className="block truncate text-[15px] font-medium text-ink">
                      {c.title || "(untitled)"}
                    </span>
                    <span className="font-mono text-[11px] uppercase tracking-wide text-ink-3">
                      updated {new Date(c.updated_at).toLocaleDateString()}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Surface>
      </aside>

      <main className="reveal-item col-span-12 md:col-span-8" style={{ "--i": 1 } as CSSProperties}>
        <Surface className="p-6 md:p-7">
          <SectionHeader
            title="Ask the lab's memory"
            description="Use Sediment when you need a cited answer you can carry into work."
            action={<TrustBadge tone="info">evidence first</TrustBadge>}
          />
          <div className="mt-6">
            <QuickAsk onSubmit={(q) => newConversation(q)} />
          </div>

          <div className="mt-8">
            <h3 className="mb-3 font-mono text-[11px] uppercase tracking-[0.16em] text-ink-3">
              Try one
            </h3>
            <ul className="divide-y divide-rule border-y border-rule">
              {EXAMPLES.map((ex) => (
                <li key={ex}>
                  <button
                    onClick={() => newConversation(ex)}
                    className="group flex w-full items-baseline gap-2 py-2.5 text-left text-[15px] text-ink-2 transition-colors hover:text-accent"
                  >
                    <span className="font-mono text-ink-3 transition-colors group-hover:text-accent">
                      ›
                    </span>
                    <span className="underline decoration-rule-2 decoration-1 underline-offset-4 group-hover:decoration-accent">
                      {ex}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        </Surface>
      </main>
    </div>
  );
}

function QuickAsk({ onSubmit }: { onSubmit: (q: string) => void }) {
  const [q, setQ] = useState("");
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (q.trim()) onSubmit(q.trim());
      }}
      className="flex flex-col gap-2 sm:flex-row"
    >
      <input
        className="min-h-11 flex-1 rounded-md border border-rule bg-paper-2/30 px-3.5 py-2.5 text-[15px] outline-none transition-colors placeholder:text-ink-3 focus:border-accent focus:bg-card"
        placeholder="e.g., 라이언(ryan)의 4월 mirror-loop 칼럼"
        data-testid="ask-input"
        value={q}
        onChange={(e) => setQ(e.target.value)}
      />
      <button className="min-h-11 rounded-md bg-accent px-5 py-2 text-[15px] font-medium text-paper transition-colors hover:bg-accent-ink">
        Ask
      </button>
    </form>
  );
}

const EXAMPLES = [
  "라이언이 4월에 쓴 mirror-loop 칼럼",
  "Daily research 중 Claude Code 관련 high-confidence 결론",
  "JY가 작성한 글 중 agent 관련 주제",
  "최근 결정된 5/5 파일럿 관련 액션",
  "지난 30일 신규 칼럼 수",
];
