import NextAuth from "next-auth";
import GitHub from "next-auth/providers/github";

// Server-side API base for the backend exchange. This call is server→server
// (Next runtime → Fly), so CORS does not apply and we hit the API directly.
// SEDIMENT_DEV_API_PROXY is the Fly URL in local dev (.env.local); in prod set
// SEDIMENT_API_BASE to the Fly app URL.
const API_BASE =
  process.env.SEDIMENT_API_BASE ||
  process.env.SEDIMENT_DEV_API_PROXY ||
  process.env.NEXT_PUBLIC_SEDIMENT_PLATFORM_URL ||
  // legacy fallback — Vercel prod still sets the CURATOR name; drop once
  // NEXT_PUBLIC_SEDIMENT_* is set there (see CLAUDE.md "Brand")
  process.env.NEXT_PUBLIC_CURATOR_PLATFORM_URL ||
  "http://localhost:10100";
const AUTH_TENANT_SLUG =
  process.env.SEDIMENT_AUTH_TENANT_SLUG ||
  process.env.NEXT_PUBLIC_SEDIMENT_AUTH_TENANT_SLUG ||
  "";

export const { handlers, signIn, signOut, auth } = NextAuth({
  trustHost: true,
  providers: [
    GitHub({
      authorization: {
        params: {
          scope: "read:user user:email",
        },
      },
    }),
  ],
  callbacks: {
    // Runs server-side. On the initial sign-in `profile` + `account` are set;
    // we pass the GitHub access token to the backend for server-side identity
    // verification, then stash the Sediment JWT on the NextAuth token.
    async jwt({ token, profile, account }) {
      if (account?.provider !== "github" || !profile) return token;

      const ghLogin = (profile as { login?: string }).login ?? "";
      const accessToken = typeof account.access_token === "string" ? account.access_token : "";
      if (!accessToken) {
        token.sedimentToken = undefined;
        token.sedimentError = "github access_token missing from NextAuth account";
        return token;
      }

      try {
        const ex = await fetch(`${API_BASE}/api/v1/auth/oauth-exchange`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            provider: "github",
            github_login: ghLogin,
            verified_emails: [],
            tenant_slug: AUTH_TENANT_SLUG || undefined,
            access_token: accessToken,
          }),
        });
        if (ex.ok) {
          const d = (await ex.json()) as {
            token: string;
            role: string;
            display_name: string;
            member_id: string;
          };
          token.sedimentToken = d.token;
          token.sedimentRole = d.role;
          token.sedimentName = d.display_name;
          token.sedimentMemberId = d.member_id;
          token.sedimentError = undefined;
        } else {
          token.sedimentError = `${ex.status}: ${(await ex.text()).slice(0, 300)}`;
        }
      } catch (e) {
        token.sedimentError = `exchange failed: ${(e as Error).message}`;
      }
      return token;
    },

    async session({ session, token }) {
      const s = session as typeof session & {
        sedimentToken?: string;
        sedimentError?: string;
        sedimentRole?: string;
        sedimentName?: string;
      };
      s.sedimentToken = (token.sedimentToken as string) ?? undefined;
      s.sedimentError = (token.sedimentError as string) ?? undefined;
      s.sedimentRole = (token.sedimentRole as string) ?? undefined;
      s.sedimentName = (token.sedimentName as string) ?? undefined;
      return s;
    },
  },
});
