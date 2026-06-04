import type { ReactNode } from "react";
import Link from "next/link";
import Providers from "./Providers";
import FreshnessBadge from "./FreshnessBadge";
import { TrustBadge } from "./components/ui";

export const metadata = {
  title: "Sediment — HypeProof Lab",
  description: "Where doing becomes knowing. Evidence-grounded memory for HypeProof Lab — every answer comes with citations.",
};

// Vercel sets VERCEL_ENV ∈ {production, preview, development}. Anything
// else (incl. unset) = treat as local. Badge styling tells you at a glance
// where you are.
function envBadge() {
  const e = process.env.VERCEL_ENV;
  if (e === "production")
    return { label: "prod", cls: "bg-emerald-100 text-emerald-800" };
  if (e === "preview")
    return { label: "preview", cls: "bg-amber-100 text-amber-800" };
  return { label: "local · dev", cls: "bg-neutral-100 text-neutral-700" };
}

export default function CuratorLayout({ children }: { children: ReactNode }) {
  const badge = envBadge();
  return (
    <div className="min-h-screen bg-neutral-50 text-neutral-900">
      <div className="mx-auto max-w-6xl px-4 py-6">
        <header className="mb-6 flex flex-col gap-4 border-b border-neutral-200 pb-4 md:flex-row md:items-center md:justify-between">
          <div className="flex flex-wrap items-center gap-2 md:gap-3">
            <Link href="/sediment" className="text-2xl font-bold tracking-tight">
              Sediment
            </Link>
            <span className="text-xs italic text-neutral-500">
              where doing becomes knowing
            </span>
            <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${badge.cls}`}>
              {badge.label}
            </span>
            <FreshnessBadge />
          </div>
          <nav aria-label="Sediment" className="flex flex-wrap items-center gap-2 text-sm">
            <Link href="/sediment" className="rounded px-2.5 py-1.5 hover:bg-neutral-100">
              Chat
            </Link>
            <Link href="/sediment/library" className="rounded px-2.5 py-1.5 hover:bg-neutral-100">
              Library
            </Link>
            <Link href="/sediment/members" className="rounded px-2.5 py-1.5 hover:bg-neutral-100">
              Members
            </Link>
            <Link href="/sediment/admin" className="rounded px-2.5 py-1.5 hover:bg-neutral-100">
              Admin
            </Link>
            <TrustBadge tone="info">vault only</TrustBadge>
          </nav>
        </header>
        <Providers>{children}</Providers>
      </div>
    </div>
  );
}
