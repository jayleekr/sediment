import type { ReactNode } from "react";
import Providers from "./Providers";

export const metadata = {
  title: "Sediment — HypeProof Lab",
  description: "Where doing becomes knowing. Evidence-grounded memory for HypeProof Lab — every answer comes with citations.",
};

export default function CuratorLayout({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen bg-neutral-50 text-neutral-900">
      <div className="mx-auto max-w-6xl px-4 py-6">
        <header className="mb-6 flex items-center justify-between">
          <div className="flex items-baseline gap-3">
            <h1 className="text-2xl font-bold tracking-tight">Sediment</h1>
            <span className="text-xs text-neutral-500 italic">
              where doing becomes knowing
            </span>
            <span className="rounded-full bg-neutral-100 px-2 py-0.5 text-xs font-medium text-neutral-700">
              local · MVP
            </span>
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
