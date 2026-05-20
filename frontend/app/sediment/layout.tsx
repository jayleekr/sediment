import type { ReactNode } from "react";
import Providers from "./Providers";
import FreshnessBadge from "./FreshnessBadge";

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
        <header className="mb-6 flex items-center justify-between">
          <div className="flex items-baseline gap-3">
            <h1 className="text-2xl font-bold tracking-tight">Sediment</h1>
            <span className="text-xs text-neutral-500 italic">
              where doing becomes knowing
            </span>
            <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${badge.cls}`}>
              {badge.label}
            </span>
            <FreshnessBadge />
          </div>
          <nav className="flex gap-4 text-sm">
            <a href="/sediment" className="hover:underline">Chat</a>
            <a href="/sediment/library" className="hover:underline">Library</a>
            <a href="/sediment/members" className="hover:underline">Members</a>
            <a href="/sediment/admin" className="hover:underline">Admin</a>
          </nav>
        </header>
        <Providers>{children}</Providers>
      </div>
    </div>
  );
}
