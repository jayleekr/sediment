"use client";

import { useEffect, useState, type CSSProperties } from "react";
import { api } from "../lib/api";
import { EmptyState, Surface, TrustBadge } from "../components/ui";

type Member = {
  id: string;
  external_id: string | null;
  email: string;
  display_name: string;
  real_name: string | null;
  role: string;
  title: string | null;
  expertise: string[];
  interests: string[];
};

export default function MembersPage() {
  const [items, setItems] = useState<Member[]>([]);

  useEffect(() => {
    api<{ items: Member[] }>("/api/v1/members").then((d) => setItems(d.items));
  }, []);

  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
      {items.length === 0 && (
        <div className="md:col-span-2 lg:col-span-3">
          <EmptyState
            title="No members loaded"
            description="Members appear here after sign-in and tenant-scoped member retrieval succeed."
          />
        </div>
      )}
      {items.map((m, i) => (
        // The roster arrives in one response, so the cascade reads as the grid
        // being dealt out rather than as staggered loading. Capped so a large
        // team does not turn the last card into a wait.
        <Surface
          key={m.id}
          className="enter-item p-4"
          style={{ "--i": Math.min(i, 9) } as CSSProperties}
        >
          <div className="flex items-baseline justify-between">
            <h3 className="text-lg font-semibold">{m.display_name}</h3>
            <TrustBadge>{m.role}</TrustBadge>
          </div>
          {m.real_name && <p className="text-sm text-ink-2">{m.real_name}</p>}
          {m.title && <p className="mt-1 text-xs">{m.title}</p>}
          <div className="mt-3 flex flex-wrap gap-1">
            {m.expertise?.map((e) => (
              <span key={e} className="rounded-sm border border-rule bg-paper-2 px-2 py-0.5 font-mono text-[11px] uppercase tracking-wide text-ink-2">
                {e}
              </span>
            ))}
          </div>
        </Surface>
      ))}
    </div>
  );
}
