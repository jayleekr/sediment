"use client";

import { Suspense, useEffect, useState } from "react";
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
        <div className="flex flex-wrap gap-2">
          {["", "column", "research", "novel", "note", "meeting"].map((t) => (
            <button
              key={t || "all"}
              onClick={() => setType(t)}
              className={`rounded px-3 py-1 text-sm ${
                type === t ? "bg-ink text-paper" : "bg-paper-2"
              }`}
            >
              {t || "all"}
            </button>
          ))}
          <div className="ml-auto flex gap-2">
            <input
              className="rounded border px-3 py-1 text-sm"
              placeholder="search…"
              aria-label="Search the vault by ref, type, author, or content"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && doSearch()}
            />
            <button onClick={() => doSearch()} className="rounded bg-accent px-3 py-1 text-sm text-paper">
              Search
            </button>
          </div>
        </div>
      </Surface>

      {search.length > 0 && (
        <Surface className="p-4">
          <h3 className="mb-2 text-sm font-semibold">Search results</h3>
          <ul className="space-y-3 text-sm">
            {search.map((s, i) => (
              <li key={i} className="border-b pb-2 last:border-0">
                <div className="font-medium">{s.ref}</div>
                <div className="line-clamp-3 text-xs text-ink-2">{s.content}</div>
                <div className="text-xs text-ink-3">score {Number(s.score).toFixed(3)}</div>
              </li>
            ))}
          </ul>
        </Surface>
      )}

      <Surface className="p-4">
        <h3 className="mb-2 text-sm font-semibold">
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
              <thead className="text-left text-xs text-ink-3">
                <tr>
                  <th scope="col" className="py-1">ref</th>
                  <th scope="col">type</th>
                  <th scope="col">date</th>
                  <th scope="col">author</th>
                  <th scope="col">lang</th>
                </tr>
              </thead>
              <tbody>
                {items.map((it) => (
                  <tr key={it.id} className="border-t">
                    <td className="py-1 font-mono text-xs">{it.ref}</td>
                    <td>{it.type}</td>
                    <td>{it.date}</td>
                    <td>{it.author_name}</td>
                    <td>{it.lang}</td>
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
