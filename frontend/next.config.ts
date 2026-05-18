import type { NextConfig } from "next";

// Standalone Sediment UI. Self-contained — no shared community-web deps.
// API base is injected at build/run time via NEXT_PUBLIC_CURATOR_* env
// (see .env.local.example). Defaults in app/sediment/lib/api.ts point at
// localhost for `next dev`.
const nextConfig: NextConfig = {
  turbopack: {
    root: __dirname,
  },
};

export default nextConfig;
