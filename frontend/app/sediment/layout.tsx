import type { ReactNode } from "react";
import Link from "next/link";
import Providers from "./Providers";
import FreshnessBadge from "./FreshnessBadge";
import SiteNav from "./SiteNav";

export const metadata = {
  title: "Sediment — HypeProof Lab",
  description: "Where doing becomes knowing. Evidence-grounded memory for HypeProof Lab — every answer comes with citations.",
};

// Vercel sets VERCEL_ENV ∈ {production, preview, development}. Anything
// else (incl. unset) = treat as local. Rendered as a dateline marker in the
// masthead strip so you can tell at a glance which edition you're reading.
function envBadge() {
  const e = process.env.VERCEL_ENV;
  if (e === "production") return { label: "prod edition", cls: "text-sage" };
  if (e === "preview") return { label: "preview", cls: "text-ochre" };
  return { label: "local · dev", cls: "text-ink-3" };
}

export default function SedimentLayout({ children }: { children: ReactNode }) {
  const badge = envBadge();
  return (
    <div className="min-h-screen bg-paper text-ink">
      <div className="mx-auto max-w-6xl px-5 py-8 md:px-8">
        <header className="mb-8">
          {/* Masthead: wordmark + running-head navigation */}
          <div className="flex flex-col gap-5 md:flex-row md:items-end md:justify-between">
            <div>
              <Link
                href="/sediment"
                className="font-display text-4xl font-semibold leading-none tracking-tight text-ink md:text-5xl"
              >
                Sediment
              </Link>
              <p className="mt-2 font-body text-[15px] italic text-ink-2">
                where doing becomes knowing
              </p>
            </div>
            <div className="md:pb-1">
              <SiteNav />
            </div>
          </div>

          {/* Dateline strip — a masthead double-rule with ledger microcopy. */}
          <div className="mt-5 border-t-2 border-ink">
            <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2 border-b border-rule py-2 font-mono text-[11px] uppercase tracking-[0.14em] text-ink-3">
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
                <span className={badge.cls}>● {badge.label}</span>
                <FreshnessBadge />
              </div>
              <span className="text-ink-3">vault-only · evidence first</span>
            </div>
          </div>
        </header>

        <Providers>{children}</Providers>

        <footer className="mt-16 border-t border-rule pt-4 font-mono text-[11px] uppercase tracking-[0.14em] text-ink-3">
          Sediment — HypeProof Lab · every answer carries its citations
        </footer>
      </div>
    </div>
  );
}
