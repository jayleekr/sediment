# Sediment Auth (Phase 5)

MVP uses `/api/v1/auth/dev-token` to mint JWTs for any seeded member email.
This is **local dev only** — not safe for production.

Phase 5 replaces this with NextAuth.js v5:

```ts
// web/src/app/sediment/auth/[...nextauth]/route.ts (Phase 5)
import NextAuth from "next-auth";
import EmailProvider from "next-auth/providers/email";
import DiscordProvider from "next-auth/providers/discord";

export const { handlers, auth } = NextAuth({
  providers: [
    EmailProvider({ from: process.env.EMAIL_FROM, server: { /* resend */ } }),
    DiscordProvider({
      clientId: process.env.DISCORD_CLIENT_ID!,
      clientSecret: process.env.DISCORD_CLIENT_SECRET!,
    }),
  ],
  callbacks: {
    async jwt({ token, account, user }) {
      // resolve org_id from members table by email or external_id
      if (user?.email) {
        const member = await fetch(`${process.env.SEDIMENT_API_BASE}/api/v1/members/by-email`, {
          headers: { "X-Service-Key": process.env.SEDIMENT_SERVICE_KEY! },
          body: JSON.stringify({ email: user.email }),
        }).then(r => r.json());
        token.org_id = member.tenant_id;
        token.role = member.role;
      }
      return token;
    },
    async session({ session, token }) {
      session.user.org_id = token.org_id as string;
      session.user.role = token.role as string;
      return session;
    },
  },
});
```

Then mint a service-issued JWT (HS256, same secret) on every request and forward as Bearer.
The Sediment backend doesn't need to know about NextAuth — only that the JWT carries `org_id`.
