"use client";

import { Suspense, useEffect, useState, type CSSProperties } from "react";
import { useSearchParams } from "next/navigation";
import { api, type LibraryItem } from "../lib/api";
import { EmptyState, SectionHeader, Surface } from "../components/ui";

// useSearchParams() forces client-side bailout for static prerendering in
// Next 15+, so the consuming component must be wrapped in <Suspense>. Keeping
// the inner component separate gives Next a server-renderable shell while
// the URL-param-aware part hydrates on the client.
export default function LibraryPage() {
  return (
    <Suspense fallback={<div className="text-sm text-ink-3">Loading…</div>}>
      <LibraryPageInner />
    </Suspense>
  );
}

function LibraryPageInner() {
  const sp = useSearchParams();
  const initialQ = sp.get("q") || "";
  const [items, setItems] = useState<LibraryItem[]>([]);
  const [type, setType] = useState<string>("");
  const [q, setQ] = useState(initialQ);
  const [search, setSearch] = useState<{ ref: string; content: string; score: number; date?: string }[]>([]);
  const [loading, setLoading] = useState(false);

  async function browse() {
    setLoading(true);
    try {
      const url = `/api/v1/library${type ? `?type=${type}` : ""}`;
      const d = await api<{ items: LibraryItem[] }>(url);
      setItems(d.items);
    } finally {
      setLoading(false);
    }
  }

  async function doSearch(query?: string) {
    const v = (query ?? q).trim();
    if (!v) {
      setSearch([]);
      return;
    }
    const d = await api<{ items: any[] }>(`/api/v1/library/search?q=${encodeURIComponent(v)}`);
    setSearch(d.items);
  }

  useEffect(() => {
    browse();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [type]);

  // Auto-run search when arriving via ?q=... deep-link (e.g. from the chat
  // page's "Search vault for X instead" no-evidence fallback).
  useEffect(() => {
    if (initialQ) {
      doSearch(initialQ);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="space-y-6">
      <Surface className="p-4">
        <SectionHeader
          title="Library"
          description="Browse and search the vault behind Sediment answers."
        />
        <div className="mt-5 flex flex-wrap items-center gap-2">
          {["", "column", "research", "novel", "note", "meeting"].map((t) => (
            <button
              key={t || "all"}
              onClick={() => setType(t)}
              aria-pressed={type === t}
              className={`min-h-8 rounded-md border px-3 py-1 font-mono text-[11px] uppercase tracking-[0.12em] transition-colors ${
                type === t
                  ? "border-ink bg-ink text-paper"
                  : "border-rule text-ink-2 hover:border-rule-2 hover:bg-paper-2"
              }`}
            >
              {t || "all"}
            </button>
          ))}
          <div className="ml-auto flex gap-2">
            <input
              className="min-h-9 w-52 rounded-md border border-rule bg-paper-2/30 px-3 py-1.5 text-sm outline-none transition-colors placeholder:text-ink-3 focus:border-accent focus:bg-card"
              placeholder="search…"
              aria-label="Search the vault by ref, type, author, or content"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && doSearch()}
            />
            <button
              onClick={() => doSearch()}
              className="min-h-9 rounded-md bg-accent px-4 py-1.5 text-sm font-medium text-paper transition-colors hover:bg-accent-ink"
            >
              Search
            </button>
          </div>
        </div>
      </Surface>

      {search.length > 0 && (
        <Surface className="p-5">
          <h3 className="mb-3 border-b border-rule pb-3 font-mono text-[11px] uppercase tracking-[0.16em] text-ink-3">
            Search results
          </h3>
          <ul className="space-y-3 text-sm">
            {search.map((s, i) => (
              <li
                key={i}
                className="enter-item border-b border-rule pb-3 last:border-0 last:pb-0"
                style={{ "--i": Math.min(i, 6) } as CSSProperties}
              >
                <div className="font-mono text-[13px] font-medium text-ink">{s.ref}</div>
                <div className="mt-1 line-clamp-3 font-body text-[13px] italic leading-6 text-ink-2">
                  {s.content}
                </div>
                <div className="mt-1 font-mono text-[11px] uppercase tracking-wide text-ink-3">
                  score {Number(s.score).toFixed(3)}
                </div>
              </li>
            ))}
          </ul>
        </Surface>
      )}

      <Surface className="p-5">
        <h3 className="mb-3 border-b border-rule pb-3 font-mono text-[11px] uppercase tracking-[0.16em] text-ink-3">
          Vault ({loading ? "…" : items.length})
        </h3>
        {items.length === 0 && !loading ? (
          <EmptyState
            title="No artifacts in this view"
            description="Clear the filters or search for a specific ref, title, author, or phrase."
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px] text-sm">
              <caption className="sr-only">Vault artifacts</caption>
              <thead className="border-b border-rule-2 text-left font-mono text-[11px] uppercase tracking-[0.12em] text-ink-3">
                <tr>
                  <th scope="col" className="py-2 font-medium">ref</th>
                  <th scope="col" className="font-medium">type</th>
                  <th scope="col" className="font-medium">date</th>
                  <th scope="col" className="font-medium">author</th>
                  <th scope="col" className="font-medium">lang</th>
                </tr>
              </thead>
              <tbody>
                {/* Only the very first load gets placeholder rows. Re-filtering
                    keeps the current rows on screen until the new set lands —
                    swapping a populated table for skeletons on every filter
                    click is more flicker than it saves, and <thead> must stay
                    mounted throughout (e2e_spec.yaml E2E-06 waits on it). */}
                {loading && items.length === 0
                  ? Array.from({ length: 6 }, (_, i) => (
                      <tr key={`skeleton-${i}`} className="border-t border-rule">
                        {Array.from({ length: 5 }, (_, col) => (
                          <td key={col} className="py-2 pr-4">
                            <span className="skeleton block h-3 rounded-sm" />
                          </td>
                        ))}
                      </tr>
                    ))
                  : items.map((it, i) => (
                      <tr
                        key={it.id}
                        className="enter-item border-t border-rule transition-colors hover:bg-paper-2/50"
                        style={{ "--i": Math.min(i, 10) } as CSSProperties}
                      >
                        <td className="py-2 font-mono text-xs text-ink">{it.ref}</td>
                        <td className="text-ink-2">{it.type}</td>
                        <td className="font-mono text-xs text-ink-2">{it.date}</td>
                        <td className="text-ink-2">{it.author_name}</td>
                        <td className="font-mono text-xs text-ink-3">{it.lang}</td>
                      </tr>
                    ))}
              </tbody>
            </table>
          </div>
        )}
      </Surface>
    </div>
  );
}
