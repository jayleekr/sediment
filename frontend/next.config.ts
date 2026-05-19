import type { NextConfig } from "next";

// Standalone Sediment UI. Self-contained — no shared community-web deps.
// API base is injected at build/run time via NEXT_PUBLIC_CURATOR_* env
// (see .env.local.example). Defaults in app/sediment/lib/api.ts point at
// localhost for `next dev`.
const nextConfig: NextConfig = {
  turbopack: {
    root: __dirname,
  },

  // Local-dev escape hatch for the Fly CORS allowlist. Prod does NOT set
  // SEDIMENT_DEV_API_PROXY, so rewrites() is empty and the browser talks to
  // the API directly (its origin must be on the backend's CORS allowlist).
  // When set (via .env.local), API calls go same-origin to this dev server
  // and Next proxies them to Fly server-side — no CORS.
  async rewrites() {
    const target = process.env.SEDIMENT_DEV_API_PROXY;
    if (!target) return [];
    // Scoped to /api/v1/* (backend) and /v1/* (langgraph SSE) ONLY.
    // Must NOT catch /api/auth/* — those are NextAuth's own routes and
    // have to be served by Next, not proxied to Fly.
    return [
      { source: "/api/v1/:path*", destination: `${target}/api/v1/:path*` },
      { source: "/v1/:path*", destination: `${target}/v1/:path*` },
    ];
  },
};

export default nextConfig;
