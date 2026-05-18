"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, ApiError, clearToken, getToken, mintDevToken, type Conversation } from "./lib/api";

export default function CuratorHome() {
  const [mounted, setMounted] = useState(false);
  const [convs, setConvs] = useState<Conversation[]>([]);
  const [signedInAs, setSignedInAs] = useState<string | null>(null);
  const [email, setEmail] = useState("jay.lee@sonatus.com");
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
      const r = await mintDevToken(email);
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
      <div className="mx-auto max-w-md rounded-xl border bg-white p-6 shadow-sm">
        <h2 className="mb-2 text-lg font-semibold">Sign in (local dev)</h2>
        <p className="mb-4 text-sm text-neutral-600">
          Local dev mints a JWT for any seeded member email. Phase 5 replaces this with
          NextAuth.js magic link + Discord OAuth.
        </p>
        <input
          className="mb-3 w-full rounded border px-3 py-2"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="member email"
        />
        <button
          onClick={signIn}
          disabled={loading}
          className="w-full rounded bg-neutral-900 px-4 py-2 text-white hover:bg-neutral-700 disabled:opacity-50"
        >
          {loading ? "..." : "Mint dev token"}
        </button>
        {error && <p className="mt-3 text-sm text-red-600">{error}</p>}
      </div>
    );
  }

  return (
    <div className="grid grid-cols-12 gap-6">
      {error && (
        <div
          role="alert"
          className="col-span-12 rounded-md border border-red-300 bg-red-50 px-4 py-2 text-sm text-red-800"
        >
          {error}
        </div>
      )}
      <aside className="col-span-12 md:col-span-4">
        <div className="rounded-xl border bg-white p-4 shadow-sm">
          <div className="mb-2 flex items-center justify-between">
            <h3 className="text-sm font-semibold">Conversations</h3>
            {convs.length > 0 && (
              <button
                onClick={() => newConversation()}
                aria-label="Start a new conversation"
                className="rounded bg-neutral-900 px-3 py-2 text-xs text-white"
              >
                + New
              </button>
            )}
          </div>
          {convs.length === 0 ? (
            <div className="mt-3 flex flex-col gap-3">
              <p className="text-base font-semibold">Start your first conversation</p>
              <p className="text-sm text-slate-600">
                Ask anything from the lab&apos;s memory — research, columns, decisions.
              </p>
              <div className="flex flex-col gap-2">
                {EXAMPLES.slice(0, 2).map((ex) => (
                  <button
                    key={ex}
                    onClick={() => newConversation(ex)}
                    className="rounded border border-neutral-300 px-3 py-1.5 text-left text-sm text-neutral-700 hover:bg-neutral-50"
                  >
                    {ex}
                  </button>
                ))}
              </div>
              <button
                onClick={() => newConversation()}
                className="w-full rounded bg-neutral-900 px-4 py-2 text-sm text-white hover:bg-neutral-700"
              >
                + New conversation
              </button>
            </div>
          ) : (
            <ul className="space-y-1">
              {convs.map((c) => (
                <li key={c.id}>
                  <Link
                    href={`/sediment/c/${c.id}`}
                    className="block truncate rounded px-2 py-1 text-sm hover:bg-neutral-100"
                  >
                    {c.title || "(untitled)"}
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </div>
      </aside>

      <main className="col-span-12 md:col-span-8">
        <div className="rounded-xl border bg-white p-6 shadow-sm">
          <h2 className="mb-3 text-lg font-semibold">Ask the lab&apos;s memory</h2>
          <QuickAsk onSubmit={(q) => newConversation(q)} />

          <div className="mt-6">
            <h3 className="mb-2 text-sm font-semibold text-neutral-600">Try one</h3>
            <ul className="space-y-2 text-sm">
              {EXAMPLES.map((ex) => (
                <li key={ex}>
                  <button
                    onClick={() => newConversation(ex)}
                    className="text-left text-blue-700 hover:underline"
                  >
                    {ex}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        </div>
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
      className="flex gap-2"
    >
      <input
        className="flex-1 rounded border px-3 py-2"
        placeholder="e.g., 라이언(ryan)의 4월 mirror-loop 칼럼"
        data-testid="ask-input"
        value={q}
        onChange={(e) => setQ(e.target.value)}
      />
      <button className="rounded bg-blue-600 px-4 py-2 text-white">Ask</button>
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
