"use client";

// RFC 8628 device-flow approval page.
//
// The Sediment CLI's `sediment auth login` prints a URL like
//   https://sediment.hypeproof-ai.xyz/sediment/device?user_code=ABCD-EFGH
// The user clicks it (or the CLI opens it). This page:
//   1. Reads user_code from the query string (pre-fills the input)
//   2. Confirms the user is signed in — if not, kicks them through the
//      existing NextAuth GitHub flow and brings them back here
//   3. POSTs /api/v1/auth/oauth-device/approve with the bearer JWT to
//      mark the device row as approved → the CLI's poll then succeeds

import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { PLATFORM_BASE, getToken } from "../lib/api";
import { Surface } from "../components/ui";

type Phase = "loading" | "needs_auth" | "ready" | "approving" | "approved" | "error";

function DeviceApproveInner() {
  const search = useSearchParams();
  const initialCode = (search?.get("user_code") || "").toUpperCase();
  const [code, setCode] = useState(initialCode);
  const [phase, setPhase] = useState<Phase>("loading");
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [identity, setIdentity] = useState<{ email: string; display_name: string } | null>(null);

  // Resolve identity on mount
  useEffect(() => {
    (async () => {
      const token = getToken();
      if (!token) {
        setPhase("needs_auth");
        return;
      }
      try {
        const r = await fetch(`${PLATFORM_BASE}/api/v1/auth/whoami`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (!r.ok) {
          setPhase("needs_auth");
          return;
        }
        const w = await r.json();
        setIdentity({ email: w.email, display_name: w.display_name });
        setPhase("ready");
      } catch {
        setPhase("needs_auth");
      }
    })();
  }, []);

  async function approve() {
    const token = getToken();
    if (!token) {
      setPhase("needs_auth");
      return;
    }
    const uc = code.trim().toUpperCase();
    if (!uc.match(/^[A-Z]{4}-[A-Z]{4}$/)) {
      setErrorMsg("Code must be 8 letters in the format ABCD-EFGH");
      return;
    }
    setPhase("approving");
    setErrorMsg(null);
    try {
      const r = await fetch(`${PLATFORM_BASE}/api/v1/auth/oauth-device/approve`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ user_code: uc }),
      });
      if (!r.ok) {
        const body = await r.text();
        setPhase("error");
        setErrorMsg(`Server returned ${r.status}: ${body.slice(0, 200)}`);
        return;
      }
      setPhase("approved");
    } catch (e: unknown) {
      setPhase("error");
      setErrorMsg(e instanceof Error ? e.message : String(e));
    }
  }

  if (phase === "loading") {
    return (
      <Wrapper title="Loading…">
        <p className="text-ink-2">Checking your sign-in status…</p>
      </Wrapper>
    );
  }

  if (phase === "needs_auth") {
    // Use NextAuth's client-side signIn() helper — calling it with an
    // explicit provider + callbackUrl bypasses the /api/auth/signin UI
    // (which has its own redirect handling that strips our query string).
    const cb = typeof window !== "undefined" ? window.location.href : "/sediment/device";
    return (
      <Wrapper title="Sign in to approve">
        <p className="mb-4 text-ink-2">
          You need to be signed in to Sediment before you can grant a new
          device access to your account.
        </p>
        {initialCode && (
          <p className="mb-4 rounded-md border border-ochre/30 bg-ochre-soft/40 p-3 text-sm text-ink-2">
            We&apos;ll bring you back here with the code{" "}
            <code className="rounded-sm bg-card px-1.5 py-0.5 font-mono text-ink">
              {initialCode}
            </code>{" "}
            pre-filled after sign-in.
          </p>
        )}
        <button
          onClick={() => {
            // Dynamic import — avoids forcing next-auth/react into every page.
            import("next-auth/react").then(({ signIn }) => {
              signIn("github", { callbackUrl: cb });
            });
          }}
          className="inline-flex items-center justify-center rounded-md bg-ink px-4 py-2 text-[15px] font-medium text-paper transition-colors hover:bg-accent-ink"
        >
          Sign in with GitHub
        </button>
      </Wrapper>
    );
  }

  if (phase === "approved") {
    return (
      <Wrapper title="✓ Device authorized">
        <p className="text-ink-2">
          You can close this tab. Your terminal should finish the login
          within a few seconds.
        </p>
        <p className="mt-4 text-xs text-ink-3">
          The code <code className="font-mono">{code}</code> is now bound to{" "}
          <strong>{identity?.email}</strong>.
        </p>
      </Wrapper>
    );
  }

  return (
    <Wrapper title="Authorize a new device">
      <p className="mb-4 text-ink-2">
        Signed in as <strong>{identity?.display_name}</strong> (
        <span className="font-mono">{identity?.email}</span>).
      </p>
      <p className="mb-4 text-ink-2">
        The Sediment CLI you ran in your terminal printed an 8-character
        code. Type or paste it here to grant it access:
      </p>
      <input
        type="text"
        value={code}
        onChange={(e) => setCode(e.target.value.toUpperCase())}
        placeholder="ABCD-EFGH"
        autoFocus
        spellCheck={false}
        autoComplete="off"
        className="w-full rounded-md border border-rule bg-paper-2/30 px-3 py-2.5 font-mono text-lg tracking-[0.25em] text-ink outline-none transition-colors placeholder:text-ink-3 focus:border-accent focus:bg-card"
      />
      {errorMsg && (
        <p className="mt-3 rounded-md border border-accent/30 bg-claret-soft/50 p-3 text-sm text-accent-ink">{errorMsg}</p>
      )}
      <button
        onClick={approve}
        disabled={phase === "approving"}
        className="mt-4 inline-flex items-center justify-center rounded-md bg-sage px-4 py-2 text-[15px] font-medium text-paper transition-colors hover:brightness-95 disabled:opacity-60"
      >
        {phase === "approving" ? "Approving…" : "Approve device"}
      </button>
      <p className="mt-6 text-xs text-ink-3">
        Codes expire 10 minutes after the CLI prints them. If your CLI
        already gave up, re-run <code className="font-mono">sediment auth login</code>{" "}
        to get a fresh one.
      </p>
    </Wrapper>
  );
}

export default function DeviceApprovePage() {
  return (
    <Suspense fallback={<Wrapper title="Loading…"><p>…</p></Wrapper>}>
      <DeviceApproveInner />
    </Suspense>
  );
}

function Wrapper({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mx-auto max-w-md py-10">
      <Surface className="reveal-item p-7">
        <p className="mb-2 font-mono text-[11px] uppercase tracking-[0.16em] text-ink-3">
          Device authorization
        </p>
        <h1 className="mb-4 font-display text-2xl font-semibold text-ink">{title}</h1>
        {children}
      </Surface>
    </div>
  );
}
