"use client";

import { useEffect, useState } from "react";
import { useSession } from "next-auth/react";
import { getToken, setToken, isTokenRejected } from "./lib/api";

// Mirrors the NextAuth session's backend JWT into the existing
// localStorage `curator.token` the 11 UI files already read. This is the
// only glue needed — no other file changes — to bolt GitHub SSO onto the
// existing bearer-token model.
export default function AuthBridge() {
  const { data: session } = useSession();
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    const s = session as
      | { sedimentToken?: string; sedimentError?: string }
      | null;
    if (!s) return;
    if (s.sedimentError) {
      setErr(s.sedimentError);
      return;
    }
    if (s.sedimentToken && getToken() !== s.sedimentToken) {
      // Loop guard: api() marks tokens as `rejected` on 401. NextAuth's
      // periodic session refetch produces a NEW session object even when
      // sedimentToken is unchanged → without this gate, AuthBridge sees
      // localStorage empty (api cleared it) + session.sedimentToken (still
      // the bad JWT) → reinstalls → 401 → clearToken → repeat. Infinite
      // reload. If the same JWT just got rejected, leave localStorage
      // empty and let page.tsx render the sign-in card.
      if (isTokenRejected(s.sedimentToken)) {
        return;
      }
      setToken(s.sedimentToken);
      // After sign-in lands, nudge the page to re-read the new token.
      //   - At /sediment landing → land on the chat shell.
      //   - Anywhere else (e.g. /sediment/device for device-flow approval,
      //     /sediment/library) → KEEP the current path; reload so the
      //     effect that gated on getToken() runs again. Previously this
      //     hard-coded a redirect to /sediment, which broke device flow
      //     (user signed in, got bounced to /sediment, never approved).
      const path = window.location.pathname;
      if (path === "/sediment" || path === "/sediment/") {
        window.location.replace("/sediment");
      } else {
        window.location.reload();
      }
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
