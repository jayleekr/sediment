"use client";

import { useEffect, useState } from "react";
import { useSession } from "next-auth/react";
import { getToken, setToken } from "./lib/api";

// Mirrors the NextAuth session's backend JWT into the existing
// localStorage `curator.token` the 11 UI files already read. This is the
// only glue needed — no other file changes — to bolt GitHub SSO onto the
// existing bearer-token model.
export default function AuthBridge() {
  const { data: session } = useSession();
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    const s = session as
      | { curatorToken?: string; curatorError?: string }
      | null;
    if (!s) return;
    if (s.curatorError) {
      setErr(s.curatorError);
      return;
    }
    if (s.curatorToken && getToken() !== s.curatorToken) {
      setToken(s.curatorToken);
      // Re-enter so page.tsx re-reads the token and renders the app.
      window.location.replace("/sediment");
    }
  }, [session]);

  if (!err) return null;
  return (
    <div
      role="alert"
      className="mx-auto mb-4 max-w-md rounded-md border border-red-300 bg-red-50 px-4 py-2 text-sm text-red-800"
    >
      GitHub 로그인은 됐지만 Sediment 멤버 매칭에 실패했습니다 — {err}
    </div>
  );
}
