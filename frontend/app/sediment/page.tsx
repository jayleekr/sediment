"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { signIn as githubSignIn } from "next-auth/react";
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
        <h2 className="mb-2 text-lg font-semibold">Sign in</h2>
        <p className="mb-4 text-sm text-neutral-600">
          Sign in with the GitHub account that has access to this repo. Your
          GitHub username is matched to a Sediment member.
        </p>
        <button
          onClick={() => githubSignIn("github", { callbackUrl: "/sediment" })}
          className="mb-4 flex w-full items-center justify-center gap-2 rounded bg-neutral-900 px-4 py-2 text-white hover:bg-neutral-700"
        >
          <svg viewBox="0 0 16 16" width="16" height="16" fill="currentColor" aria-hidden="true">
            <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0016 8c0-4.42-3.58-8-8-8z" />
          </svg>
          Sign in with GitHub
        </button>
        <div className="mb-4 flex items-center gap-2 text-xs text-neutral-400">
          <span className="h-px flex-1 bg-neutral-200" />
          local dev fallback
          <span className="h-px flex-1 bg-neutral-200" />
        </div>
        <input
          className="mb-3 w-full rounded border px-3 py-2"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="member email"
        />
        <button
          onClick={signIn}
          disabled={loading}
          className="w-full rounded border border-neutral-300 px-4 py-2 text-neutral-700 hover:bg-neutral-50 disabled:opacity-50"
        >
          {loading ? "..." : "Mint dev token (seeded email)"}
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
