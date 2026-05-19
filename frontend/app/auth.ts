import NextAuth from "next-auth";
import GitHub from "next-auth/providers/github";

// Server-side API base for the backend exchange. This call is server→server
// (Next runtime → Fly), so CORS does not apply and we hit the API directly.
// SEDIMENT_DEV_API_PROXY is the Fly URL in local dev (.env.local); in prod set
// SEDIMENT_API_BASE to the Fly app URL.
const API_BASE =
  process.env.SEDIMENT_API_BASE ||
  process.env.SEDIMENT_DEV_API_PROXY ||
  process.env.NEXT_PUBLIC_CURATOR_PLATFORM_URL ||
  "http://localhost:10100";

export const { handlers, signIn, signOut, auth } = NextAuth({
  trustHost: true,
  providers: [
    // Auth.js GitHub provider defaults to scope "read:user user:email",
    // which is what we need to read the verified-email list.
    GitHub,
  ],
  callbacks: {
    // Runs server-side. On the initial sign-in `profile` + `account` are set;
    // we resolve the Sediment member via the backend and stash the backend
    // JWT on the NextAuth token so the client bridge can mirror it into the
    // existing localStorage `curator.token` (keeps the 11 UI files unchanged).
    async jwt({ token, profile, account }) {
      if (account?.provider !== "github" || !profile) return token;

      const ghLogin = (profile as { login?: string }).login ?? "";
      const emails: string[] = [];
      if (account.access_token) {
        try {
          const r = await fetch("https://api.github.com/user/emails", {
            headers: {
              Authorization: `Bearer ${account.access_token}`,
              Accept: "application/vnd.github+json",
              "User-Agent": "sediment-auth",
            },
          });
          if (r.ok) {
            const list = (await r.json()) as Array<{ email: string; verified: boolean }>;
            for (const e of list) if (e.verified) emails.push(e.email);
          }
        } catch {
          /* fall back to profile.email below */
        }
      }
      if (typeof profile.email === "string" && profile.email) emails.push(profile.email);

      try {
        const ex = await fetch(`${API_BASE}/api/v1/auth/oauth-exchange`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            provider: "github",
            github_login: ghLogin,
            verified_emails: emails,
          }),
        });
        if (ex.ok) {
          const d = (await ex.json()) as {
            token: string;
            role: string;
            display_name: string;
            member_id: string;
          };
          token.curatorToken = d.token;
          token.curatorRole = d.role;
          token.curatorName = d.display_name;
          token.curatorMemberId = d.member_id;
          token.curatorError = undefined;
        } else {
          token.curatorError = `${ex.status}: ${(await ex.text()).slice(0, 300)}`;
        }
      } catch (e) {
        token.curatorError = `exchange failed: ${(e as Error).message}`;
      }
      return token;
    },

    async session({ session, token }) {
      const s = session as typeof session & {
        curatorToken?: string;
        curatorError?: string;
        curatorRole?: string;
        curatorName?: string;
      };
      s.curatorToken = (token.curatorToken as string) ?? undefined;
      s.curatorError = (token.curatorError as string) ?? undefined;
      s.curatorRole = (token.curatorRole as string) ?? undefined;
      s.curatorName = (token.curatorName as string) ?? undefined;
      return s;
    },
  },
});
