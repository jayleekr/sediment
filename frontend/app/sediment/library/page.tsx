"use client";

import { useEffect, useState } from "react";
import { api, type LibraryItem } from "../lib/api";

export default function LibraryPage() {
  const [items, setItems] = useState<LibraryItem[]>([]);
  const [type, setType] = useState<string>("");
  const [q, setQ] = useState("");
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

  async function doSearch() {
    if (!q.trim()) {
      setSearch([]);
      return;
    }
    const d = await api<{ items: any[] }>(`/api/v1/library/search?q=${encodeURIComponent(q)}`);
    setSearch(d.items);
  }

  useEffect(() => {
    browse();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [type]);

  return (
    <div className="space-y-6">
      <div className="rounded-xl border bg-white p-4 shadow-sm">
        <div className="flex flex-wrap gap-2">
          {["", "column", "research", "novel", "note", "meeting"].map((t) => (
            <button
              key={t || "all"}
              onClick={() => setType(t)}
              className={`rounded px-3 py-1 text-sm ${
                type === t ? "bg-neutral-900 text-white" : "bg-neutral-100"
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
            <button onClick={doSearch} className="rounded bg-blue-600 px-3 py-1 text-sm text-white">
              Search
            </button>
          </div>
        </div>
      </div>

      {search.length > 0 && (
        <div className="rounded-xl border bg-white p-4 shadow-sm">
          <h3 className="mb-2 text-sm font-semibold">Search results</h3>
          <ul className="space-y-3 text-sm">
            {search.map((s, i) => (
              <li key={i} className="border-b pb-2 last:border-0">
                <div className="font-medium">{s.ref}</div>
                <div className="line-clamp-3 text-xs text-neutral-700">{s.content}</div>
                <div className="text-xs text-neutral-500">score {Number(s.score).toFixed(3)}</div>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="rounded-xl border bg-white p-4 shadow-sm">
        <h3 className="mb-2 text-sm font-semibold">
          Vault ({loading ? "…" : items.length})
        </h3>
        <table className="w-full text-sm">
          <thead className="text-left text-xs text-neutral-500">
            <tr>
              <th className="py-1">ref</th>
              <th>type</th>
              <th>date</th>
              <th>author</th>
              <th>lang</th>
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
    </div>
  );
}
